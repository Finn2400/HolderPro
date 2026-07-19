"""Turn filled 2-D support layers into a solid, support-only STL mesh.

The interchange format is deliberately small and independent of PrusaSlicer::

.. code-block:: json

    {
      "version": 1,
      "layers": [
        {
          "print_z": 0.4,
          "height": 0.2,
          "polygons": [
            {
              "contour": [[0, 0], [10, 0], [10, 10], [0, 10]],
              "holes": [[[2, 2], [2, 8], [8, 8], [8, 2]]]
            }
          ]
        }
      ]
    }

Coordinates are millimetres. ``print_z`` is the top of a layer, so its slab
occupies ``[print_z - height, print_z]``. Ring winding is intentionally not
part of the format: contours and holes are re-oriented before they reach
Manifold. Overlapping polygons and slabs are boolean-unioned, not merely
concatenated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, BinaryIO, Callable, TextIO, TypeAlias, TypeGuard

import numpy as np
import trimesh
from manifold3d import CrossSection, Error, FillRule, Manifold, Mesh, OpType


FORMAT_VERSION = 1
_Z_SNAP_EPSILON_MM = 1e-9
_STL_VOLUME_RELATIVE_TOLERANCE = 1e-6
_STL_VOLUME_ABSOLUTE_TOLERANCE_MM3 = 1e-9
_STL_ENCODING_ERROR = (
    "STL encoding would not preserve a watertight positive-volume solid"
)

Point2D: TypeAlias = tuple[float, float]
Ring: TypeAlias = tuple[Point2D, ...]


class LayerFormatError(ValueError):
    """Raised when a filled-layer document does not match format version 1."""


class SolidificationError(RuntimeError):
    """Raised when valid layer data cannot produce a positive-volume solid."""


@dataclass(frozen=True, slots=True)
class FilledPolygon:
    """One filled contour with zero or more explicitly identified holes."""

    contour: Ring
    holes: tuple[Ring, ...] = ()


@dataclass(frozen=True, slots=True)
class FilledLayer:
    """Filled XY polygons occupying one layer-height slab."""

    print_z: float
    height: float
    polygons: tuple[FilledPolygon, ...]

    @property
    def bottom_z(self) -> float:
        return self.print_z - self.height


@dataclass(frozen=True, slots=True)
class LayerDocument:
    """Validated, immutable form of the JSON interchange document."""

    version: int
    layers: tuple[FilledLayer, ...]


JsonSource: TypeAlias = (
    LayerDocument
    | Mapping[str, Any]
    | str
    | bytes
    | bytearray
    | Path
    | TextIO
    | BinaryIO
)


__all__ = [
    "FORMAT_VERSION",
    "FilledLayer",
    "FilledPolygon",
    "LayerDocument",
    "LayerFormatError",
    "SolidificationError",
    "export_mesh_stl",
    "export_stl",
    "load_layer_document",
    "parse_layer_document",
    "prepare_stl_mesh",
    "solidify_layers",
    "solidify_manifold",
]


def _is_array(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _finite_number(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LayerFormatError(f"{location} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise LayerFormatError(f"{location} must be a finite number")
    return result


def _signed_area(ring: Sequence[Point2D], location: str) -> float:
    try:
        twice_area = math.fsum(
            point[0] * ring[(index + 1) % len(ring)][1]
            - ring[(index + 1) % len(ring)][0] * point[1]
            for index, point in enumerate(ring)
        )
    except (OverflowError, ValueError) as exc:
        raise LayerFormatError(f"{location} has coordinates too large to process") from exc
    area = 0.5 * twice_area
    if not math.isfinite(area):
        raise LayerFormatError(f"{location} has coordinates too large to process")
    return area


def _parse_ring(value: object, location: str, *, clockwise: bool) -> Ring:
    if not _is_array(value):
        raise LayerFormatError(f"{location} must be an array of [x, y] points")

    points: list[Point2D] = []
    for point_index, raw_point in enumerate(value):
        point_location = f"{location}[{point_index}]"
        if not _is_array(raw_point) or len(raw_point) != 2:
            raise LayerFormatError(f"{point_location} must be exactly [x, y]")
        point = (
            _finite_number(raw_point[0], f"{point_location}[0]"),
            _finite_number(raw_point[1], f"{point_location}[1]"),
        )
        # Clipper/Manifold does not need an explicit closing point, and removing
        # adjacent duplicates prevents zero-length boundary edges.
        if not points or point != points[-1]:
            points.append(point)

    if len(points) > 1 and points[-1] == points[0]:
        points.pop()
    if len(points) < 3 or len(set(points)) < 3:
        raise LayerFormatError(f"{location} must contain at least 3 distinct points")

    area = _signed_area(points, location)
    if area == 0.0:
        raise LayerFormatError(f"{location} must enclose a non-zero area")

    is_clockwise = area < 0.0
    if is_clockwise != clockwise:
        points.reverse()
    return tuple(points)


def parse_layer_document(value: Mapping[str, Any]) -> LayerDocument:
    """Validate and normalize an already-decoded version-1 document.

    Duplicate closing points and adjacent duplicate points are removed. Outer
    rings are normalized counter-clockwise and hole rings clockwise.
    """

    if not isinstance(value, Mapping):
        raise LayerFormatError("document root must be a JSON object")

    version = value.get("version")
    if isinstance(version, bool) or version != FORMAT_VERSION:
        raise LayerFormatError(
            f"version must be {FORMAT_VERSION}; received {version!r}"
        )

    raw_layers = value.get("layers")
    if not _is_array(raw_layers):
        raise LayerFormatError("layers must be an array")

    layers: list[FilledLayer] = []
    for layer_index, raw_layer in enumerate(raw_layers):
        layer_location = f"layers[{layer_index}]"
        if not isinstance(raw_layer, Mapping):
            raise LayerFormatError(f"{layer_location} must be an object")

        print_z = _finite_number(raw_layer.get("print_z"), f"{layer_location}.print_z")
        height = _finite_number(raw_layer.get("height"), f"{layer_location}.height")
        if height <= 0.0:
            raise LayerFormatError(f"{layer_location}.height must be greater than zero")
        bottom_z = print_z - height
        if not math.isfinite(bottom_z):
            raise LayerFormatError(f"{layer_location} has an invalid Z extent")

        raw_polygons = raw_layer.get("polygons")
        if not _is_array(raw_polygons):
            raise LayerFormatError(f"{layer_location}.polygons must be an array")

        polygons: list[FilledPolygon] = []
        for polygon_index, raw_polygon in enumerate(raw_polygons):
            polygon_location = f"{layer_location}.polygons[{polygon_index}]"
            if not isinstance(raw_polygon, Mapping):
                raise LayerFormatError(f"{polygon_location} must be an object")
            if "contour" not in raw_polygon:
                raise LayerFormatError(f"{polygon_location}.contour is required")

            contour = _parse_ring(
                raw_polygon["contour"],
                f"{polygon_location}.contour",
                clockwise=False,
            )
            raw_holes = raw_polygon.get("holes", [])
            if not _is_array(raw_holes):
                raise LayerFormatError(f"{polygon_location}.holes must be an array")
            holes = tuple(
                _parse_ring(
                    raw_hole,
                    f"{polygon_location}.holes[{hole_index}]",
                    clockwise=True,
                )
                for hole_index, raw_hole in enumerate(raw_holes)
            )
            polygons.append(FilledPolygon(contour=contour, holes=holes))

        layers.append(
            FilledLayer(print_z=print_z, height=height, polygons=tuple(polygons))
        )

    return LayerDocument(version=FORMAT_VERSION, layers=tuple(layers))


def _decode_json(payload: str | bytes | bytearray) -> Mapping[str, Any]:
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LayerFormatError(f"invalid layer JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise LayerFormatError("document root must be a JSON object")
    return value


def load_layer_document(source: JsonSource) -> LayerDocument:
    """Read and validate a document from a mapping, JSON text, path, or stream.

    A string beginning with ``{`` (ignoring whitespace) is treated as inline
    JSON. Other strings are treated as filesystem paths.
    """

    if isinstance(source, LayerDocument):
        return source
    if isinstance(source, Mapping):
        return parse_layer_document(source)

    if isinstance(source, Path):
        payload: str | bytes | bytearray = source.read_bytes()
    elif isinstance(source, str):
        if source.lstrip().startswith("{"):
            payload = source
        else:
            payload = Path(source).read_bytes()
    elif isinstance(source, (bytes, bytearray)):
        payload = source
    elif hasattr(source, "read"):
        payload = source.read()
        if not isinstance(payload, (str, bytes, bytearray)):
            raise LayerFormatError("layer stream read() must return str or bytes")
    else:
        raise TypeError(
            "source must be a LayerDocument, mapping, JSON text, path, or readable stream"
        )

    return parse_layer_document(_decode_json(payload))


def _polygon_cross_section(polygon: FilledPolygon, location: str) -> CrossSection:
    # Ring orientation was normalized during parsing, so Positive gives the
    # declared outer region less all explicitly declared holes.
    contours = [np.asarray(polygon.contour, dtype=np.float64)]
    contours.extend(np.asarray(hole, dtype=np.float64) for hole in polygon.holes)
    cross_section = CrossSection(contours, FillRule.Positive)
    if cross_section.is_empty() or not math.isfinite(cross_section.area()):
        raise SolidificationError(f"{location} does not define a finite filled area")
    if cross_section.area() <= 0.0:
        raise SolidificationError(f"{location} has no filled area after applying holes")
    return cross_section


def _snapped_bottom_z(layer: FilledLayer, layer_tops: Sequence[float]) -> float:
    """Remove sub-nanometre JSON round-off gaps between adjacent layers."""

    bottom_z = layer.bottom_z
    candidates = (top for top in layer_tops if top < layer.print_z)
    nearest = min(candidates, key=lambda top: abs(top - bottom_z), default=None)
    if nearest is None:
        return bottom_z

    # The absolute tolerance covers ordinary decimal JSON round-off. The ULP
    # term keeps the same protection when coordinates are unusually large.
    tolerance = max(
        _Z_SNAP_EPSILON_MM,
        32.0 * math.ulp(max(abs(bottom_z), abs(nearest))),
    )
    if abs(nearest - bottom_z) <= tolerance:
        return nearest
    return bottom_z


def solidify_manifold(source: JsonSource) -> Manifold:
    """Extrude and union all non-empty layers into a Manifold solid."""

    document = load_layer_document(source)
    slabs: list[Manifold] = []
    layer_tops = tuple(layer.print_z for layer in document.layers)

    for layer_index, layer in enumerate(document.layers):
        polygon_sections: list[CrossSection] = []
        for polygon_index, polygon in enumerate(layer.polygons):
            polygon_sections.append(
                _polygon_cross_section(
                    polygon, f"layers[{layer_index}].polygons[{polygon_index}]"
                )
            )

        if not polygon_sections:
            continue
        layer_section = CrossSection.compose(polygon_sections)
        bottom_z = _snapped_bottom_z(layer, layer_tops)
        effective_height = layer.print_z - bottom_z
        slab = layer_section.extrude(effective_height).translate((0.0, 0.0, bottom_z))
        if slab.status() != Error.NoError:
            raise SolidificationError(
                f"layers[{layer_index}] extrusion failed: {slab.status().name}"
            )
        slabs.append(slab)

    if not slabs:
        raise SolidificationError("document contains no filled support polygons")

    solid = Manifold.batch_boolean(slabs, OpType.Add)
    if solid.status() != Error.NoError:
        raise SolidificationError(f"layer union failed: {solid.status().name}")
    volume = solid.volume()
    if solid.is_empty() or not math.isfinite(volume) or volume <= 0.0:
        raise SolidificationError("layer union did not produce positive-volume geometry")
    return solid


def solidify_layers(source: JsonSource) -> trimesh.Trimesh:
    """Return a watertight, outward-wound trimesh of the unioned layer slabs."""

    solid = solidify_manifold(source)
    # Preserve double-precision slicer coordinates until the final STL encoder
    # performs STL's required float32 conversion.
    manifold_mesh = solid.to_mesh64()
    vertices = np.asarray(manifold_mesh.vert_properties, dtype=np.float64)[:, :3]
    faces = np.asarray(manifold_mesh.tri_verts, dtype=np.int64)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)

    # These checks are cheap compared with the boolean and protect callers from
    # ever exporting a surface-only or inverted result if a backend regresses.
    if not mesh.is_watertight:
        raise SolidificationError("solidification produced a non-watertight mesh")
    if not mesh.is_winding_consistent or mesh.volume <= 0.0:
        raise SolidificationError("solidification produced a non-volume mesh")
    return mesh


def prepare_stl_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Return a float32 mesh whose topology survives binary STL encoding.

    Binary STL stores three independent float32 positions per triangle and
    cannot carry Manifold's vertex/provenance relations.  Very short Organic
    edges can therefore round to one position even though the double-
    precision support solid is valid.  Reconstructing the solid *after* the
    unavoidable float32 conversion lets Manifold collapse only those zero-
    length edges.  ``as_original().simplify(0)`` removes provenance seams
    using Manifold's inherited float32 baseline tolerance.

    The result is still validated after an actual STL round trip by callers.
    A positive-volume check and a tight volume-drift bound keep this repair
    fail-closed for geometry that STL genuinely cannot represent.
    """

    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
        raise SolidificationError(_STL_ENCODING_ERROR)

    vertices64 = np.asarray(mesh.vertices, dtype=np.float64)
    faces64 = np.asarray(mesh.faces, dtype=np.int64)
    original_volume = float(mesh.volume)
    if (
        vertices64.ndim != 2
        or vertices64.shape[1] != 3
        or faces64.ndim != 2
        or faces64.shape[1] != 3
        or not np.isfinite(vertices64).all()
        or not math.isfinite(original_volume)
        or original_volume <= 0.0
        or faces64.min(initial=0) < 0
        or faces64.max(initial=-1) >= len(vertices64)
        or len(vertices64) > np.iinfo(np.uint32).max
    ):
        raise SolidificationError(_STL_ENCODING_ERROR)

    try:
        stl_source = Mesh(
            np.ascontiguousarray(vertices64, dtype=np.float32),
            np.ascontiguousarray(faces64, dtype=np.uint32),
        )
        # Best-effort recovery of any coincident boundary vertices before the
        # constructor performs its exact float32 degenerate-edge collapse.
        stl_source.merge()
        repaired = Manifold(stl_source)
        if repaired.status() != Error.NoError:
            raise SolidificationError(_STL_ENCODING_ERROR)

        # Distinct support components can become exactly tangent at float32.
        # Union them so STL's coordinate-based vertex welding cannot create a
        # non-manifold edge where those components meet.
        components = repaired.decompose()
        if len(components) > 1:
            repaired = Manifold.batch_boolean(components, OpType.Add)

        if repaired.status() != Error.NoError:
            raise SolidificationError(_STL_ENCODING_ERROR)

        # Boolean provenance may split otherwise identical vertices.  STL has
        # no place to store that provenance, so reset it and simplify at the
        # inherited float32 baseline tolerance before exporting the surface.
        repaired = repaired.as_original()
        if repaired.status() != Error.NoError:
            raise SolidificationError(_STL_ENCODING_ERROR)
        repaired = repaired.simplify(0.0)
        repaired_volume = float(repaired.volume())
        if (
            repaired.status() != Error.NoError
            or repaired.is_empty()
            or not math.isfinite(repaired_volume)
            or repaired_volume <= 0.0
        ):
            raise SolidificationError(_STL_ENCODING_ERROR)

        allowed_volume_drift = max(
            _STL_VOLUME_ABSOLUTE_TOLERANCE_MM3,
            abs(original_volume) * _STL_VOLUME_RELATIVE_TOLERANCE,
        )
        if abs(repaired_volume - original_volume) > allowed_volume_drift:
            raise SolidificationError(_STL_ENCODING_ERROR)

        manifold_mesh = repaired.to_mesh()
        vertices = np.asarray(manifold_mesh.vert_properties, dtype=np.float32)[:, :3]
        faces = np.asarray(manifold_mesh.tri_verts, dtype=np.int64)
        result = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    except SolidificationError:
        raise
    except (MemoryError, RuntimeError, TypeError, ValueError) as exc:
        raise SolidificationError(_STL_ENCODING_ERROR) from exc

    if (
        not len(result.faces)
        or not result.is_watertight
        or not result.is_winding_consistent
        or not result.is_volume
        or not math.isfinite(float(result.volume))
        or result.volume <= 0.0
    ):
        raise SolidificationError(_STL_ENCODING_ERROR)

    quantized_bounds = np.stack(
        (
            np.asarray(vertices64, dtype=np.float32).min(axis=0),
            np.asarray(vertices64, dtype=np.float32).max(axis=0),
        )
    ).astype(np.float64)
    bounds_scale = np.maximum(np.abs(quantized_bounds), 1.0)
    bounds_tolerance = np.maximum(
        4.0 * np.finfo(np.float32).eps * bounds_scale,
        4.0 * repaired.get_tolerance(),
    )
    if np.any(np.abs(result.bounds - quantized_bounds) > bounds_tolerance):
        raise SolidificationError(_STL_ENCODING_ERROR)
    return result


def separate_coincident_vertex_fans_for_stl(
    mesh: trimesh.Trimesh, *, offset_mm: float = 0.001
) -> trimesh.Trimesh:
    """Separate distinct indexed vertex fans that STL would weld together."""

    offset_mm = float(offset_mm)
    if not math.isfinite(offset_mm) or not 0.0 < offset_mm <= 0.02:
        raise SolidificationError(_STL_ENCODING_ERROR)
    if not _is_valid_stl_mesh(mesh):
        raise SolidificationError(_STL_ENCODING_ERROR)
    vertices = np.asarray(mesh.vertices, dtype=np.float64).copy()
    keys = np.asarray(vertices, dtype=np.float32)
    _unique, inverse, counts = np.unique(
        keys, axis=0, return_inverse=True, return_counts=True
    )
    duplicate_groups = np.flatnonzero(counts > 1)
    if not len(duplicate_groups):
        return mesh

    original_bounds = np.asarray(mesh.bounds, dtype=float)
    original_volume = float(mesh.volume)
    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    maximum_shift = 0.0
    for group in duplicate_groups:
        indices = np.flatnonzero(inverse == group)
        for rank, vertex in enumerate(indices[1:], start=1):
            direction = normals[vertex]
            length = float(np.linalg.norm(direction))
            if not math.isfinite(length) or length <= 1e-12:
                direction = np.asarray((1.0, 0.0, 0.0))
                length = 1.0
            shift = offset_mm * rank
            vertices[vertex] += direction / length * shift
            maximum_shift = max(maximum_shift, shift)

    result = trimesh.Trimesh(
        vertices=vertices,
        faces=np.asarray(mesh.faces, dtype=np.int64).copy(),
        process=False,
    )
    if result.bounds[0, 2] < original_bounds[0, 2]:
        result.apply_translation(
            (0.0, 0.0, original_bounds[0, 2] - result.bounds[0, 2])
        )
    quantized = np.asarray(result.vertices, dtype=np.float32)
    if len(np.unique(quantized, axis=0)) != len(quantized):
        raise SolidificationError(_STL_ENCODING_ERROR)
    if not _is_valid_stl_mesh(result):
        raise SolidificationError(_STL_ENCODING_ERROR)
    if np.any(
        np.abs(np.asarray(result.bounds) - original_bounds)
        > max(2.5 * maximum_shift, 1e-8)
    ):
        raise SolidificationError(_STL_ENCODING_ERROR)
    allowed_volume_drift = max(
        _STL_VOLUME_ABSOLUTE_TOLERANCE_MM3,
        float(mesh.area) * maximum_shift * 2.5,
    )
    if abs(float(result.volume) - original_volume) > allowed_volume_drift:
        raise SolidificationError(_STL_ENCODING_ERROR)
    return result


def regularize_tangent_components_for_stl(
    mesh: trimesh.Trimesh, *, overlap_mm: float = 0.001
) -> trimesh.Trimesh:
    """Turn exact component contacts into a bounded positive-volume union.

    STL carries coordinates but no vertex identity. Two closed components that
    touch only along an edge or point therefore become non-manifold when an STL
    importer welds equal coordinates. Expand each component by a sub-printing-
    resolution amount and union the resulting microscopic overlaps.
    """

    overlap_mm = float(overlap_mm)
    if not math.isfinite(overlap_mm) or not 0.0 < overlap_mm <= 0.02:
        raise SolidificationError(_STL_ENCODING_ERROR)
    if not _is_valid_stl_mesh(mesh):
        raise SolidificationError(_STL_ENCODING_ERROR)
    original_bounds = np.asarray(mesh.bounds, dtype=float)
    original_volume = float(mesh.volume)
    try:
        source_mesh = Mesh(
            np.ascontiguousarray(mesh.vertices, dtype=np.float32),
            np.ascontiguousarray(mesh.faces, dtype=np.uint32),
        )
        source_mesh.merge()
        source = Manifold(source_mesh)
        if source.status() != Error.NoError:
            raise SolidificationError(_STL_ENCODING_ERROR)
        components = source.decompose()
        # First simplify the complete signed solid without decomposition. This
        # preserves inward-oriented nested shells as cavities instead of
        # accidentally treating them as positive material components.
        signed = source.as_original().set_tolerance(overlap_mm).simplify(overlap_mm)
        if signed.status() == Error.NoError and not signed.is_empty():
            signed_mesh = signed.to_mesh()
            signed_result = trimesh.Trimesh(
                vertices=np.asarray(
                    signed_mesh.vert_properties, dtype=np.float32
                )[:, :3],
                faces=np.asarray(signed_mesh.tri_verts, dtype=np.int64),
                process=False,
            )
            signed_bounds_drift = np.abs(
                np.asarray(signed_result.bounds, dtype=float) - original_bounds
            )
            signed_volume_drift = abs(float(signed_result.volume) - original_volume)
            signed_allowed_volume_drift = max(
                _STL_VOLUME_ABSOLUTE_TOLERANCE_MM3,
                float(mesh.area) * overlap_mm * 2.5,
            )
            if (
                _is_valid_stl_mesh(signed_result)
                and not np.any(signed_bounds_drift > 2.5 * overlap_mm)
                and signed_volume_drift <= signed_allowed_volume_drift
            ):
                try:
                    return separate_coincident_vertex_fans_for_stl(
                        signed_result, offset_mm=overlap_mm
                    )
                except SolidificationError:
                    pass

        # Expanding components is safe only when every component represents
        # outward-oriented material. Negative-volume components are cavity
        # boundaries and must never be expanded or unioned as solids.
        if any(float(component.volume()) < 0.0 for component in components):
            raise SolidificationError(_STL_ENCODING_ERROR)
        if len(components) == 1:
            # A single component can still carry float32-scale provenance
            # seams. Reset provenance and simplify at the explicit tolerance.
            regularized = source.as_original().set_tolerance(overlap_mm)
            regularized = regularized.simplify(overlap_mm)
        else:
            expanded: list[Manifold] = []
            for component in components:
                bounds = np.asarray(component.bounding_box(), dtype=float).reshape(2, 3)
                center = bounds.mean(axis=0)
                extents = bounds[1] - bounds[0]
                if np.any(extents <= 0.0) or not np.isfinite(extents).all():
                    raise SolidificationError(_STL_ENCODING_ERROR)
                scale = (extents + 2.0 * overlap_mm) / extents
                expanded.append(
                    component.translate(tuple(-center))
                    .scale(tuple(scale))
                    .translate(tuple(center))
                )

            regularized = Manifold.batch_boolean(expanded, OpType.Add).as_original()
            regularized = regularized.simplify(0.0)
        if regularized.status() != Error.NoError or regularized.is_empty():
            raise SolidificationError(_STL_ENCODING_ERROR)
        # Expansion around each component center would put the bed face below
        # Z=0. Restore the original minimum Z without removing the overlap.
        regularized_bounds = np.asarray(
            regularized.bounding_box(), dtype=float
        ).reshape(2, 3)
        if regularized_bounds[0, 2] < original_bounds[0, 2]:
            regularized = regularized.translate(
                (0.0, 0.0, original_bounds[0, 2] - regularized_bounds[0, 2])
            )

        manifold_mesh = regularized.to_mesh()
        result = trimesh.Trimesh(
            vertices=np.asarray(manifold_mesh.vert_properties, dtype=np.float32)[:, :3],
            faces=np.asarray(manifold_mesh.tri_verts, dtype=np.int64),
            process=False,
        )
    except SolidificationError:
        raise
    except (MemoryError, RuntimeError, TypeError, ValueError) as exc:
        raise SolidificationError(_STL_ENCODING_ERROR) from exc

    if not _is_valid_stl_mesh(result):
        raise SolidificationError(_STL_ENCODING_ERROR)
    bounds_drift = np.abs(np.asarray(result.bounds, dtype=float) - original_bounds)
    if np.any(bounds_drift > 2.5 * overlap_mm):
        raise SolidificationError(_STL_ENCODING_ERROR)
    allowed_volume_drift = max(
        _STL_VOLUME_ABSOLUTE_TOLERANCE_MM3,
        float(mesh.area) * overlap_mm * 2.5,
    )
    if abs(float(result.volume) - original_volume) > allowed_volume_drift:
        raise SolidificationError(_STL_ENCODING_ERROR)
    return separate_coincident_vertex_fans_for_stl(
        result, offset_mm=overlap_mm
    )


def export_mesh_stl(
    mesh: trimesh.Trimesh,
    destination: str | os.PathLike[str],
    *,
    printable_validator: Callable[[Path], bool] | None = None,
    serialized_validator: Callable[[trimesh.Trimesh], bool] | None = None,
    serialized_validation_error: str = "Serialized STL failed topology validation",
) -> trimesh.Trimesh:
    """Atomically write ``mesh`` as a validated binary or ASCII STL.

    A directly serialized binary mesh is preferred whenever it already
    survives the STL round trip. The bounded float32 topology repair is
    attempted next. If binary STL's mandatory float32 coordinates still
    collapse valid Organic geometry, a full-precision ASCII STL is written.
    ASCII STL is part of the STL format and does not impose binary STL's
    float32 coordinate limit.
    """

    output = Path(destination).expanduser()
    if output.suffix.lower() != ".stl":
        raise ValueError("STL destination must end in .stl")
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = trimesh.exchange.stl.export_stl(mesh)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output.stem}.",
            suffix=".stl",
            dir=output.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        serialized = trimesh.load_mesh(temporary_name, file_type="stl", process=True)
        if not _is_valid_stl_mesh(serialized):
            repaired: trimesh.Trimesh | None = None
            try:
                repaired = prepare_stl_mesh(mesh)
            except SolidificationError:
                pass
            if repaired is not None:
                with open(temporary_name, "wb") as temporary:
                    temporary.write(trimesh.exchange.stl.export_stl(repaired))
                    temporary.flush()
                    os.fsync(temporary.fileno())
                serialized = trimesh.load_mesh(
                    temporary_name, file_type="stl", process=True
                )
                if not _is_valid_stl_mesh(serialized) and printable_validator is not None:
                    # The bounded Manifold repair has already passed volume and
                    # bounds fidelity checks. Ask Prusa about this candidate
                    # before an ASCII fallback overwrites it: generic coordinate
                    # welders may reject tangent closed shells that Prusa imports
                    # as a manifold printable solid.
                    if printable_validator(Path(temporary_name)):
                        serialized = repaired.copy()

            if not _is_valid_stl_mesh(serialized):
                regularization_source = repaired if repaired is not None else mesh
                try:
                    regularized = regularize_tangent_components_for_stl(
                        regularization_source
                    )
                except SolidificationError:
                    regularized = None
                if regularized is not None:
                    with open(temporary_name, "wb") as temporary:
                        temporary.write(trimesh.exchange.stl.export_stl(regularized))
                        temporary.flush()
                        os.fsync(temporary.fileno())
                    serialized = trimesh.load_mesh(
                        temporary_name, file_type="stl", process=True
                    )
                    if not _is_valid_stl_mesh(serialized):
                        if printable_validator is not None and printable_validator(
                            Path(temporary_name)
                        ):
                            serialized = regularized.copy()

        if not _is_valid_stl_mesh(serialized):
            _write_ascii_stl(mesh, temporary_name)
            serialized = trimesh.load_mesh(
                temporary_name, file_type="stl", process=True
            )
            if not _is_valid_stl_mesh(serialized):
                if printable_validator is None or not printable_validator(
                    Path(temporary_name)
                ):
                    raise SolidificationError(_STL_ENCODING_ERROR)
                # Coordinate-welding validators may call exactly tangent closed
                # shells non-manifold even though PrusaSlicer's import repair
                # accepts and prints them. The source Manifold remains the
                # authoritative geometry for result statistics.
                serialized = mesh.copy()
        if serialized_validator is not None and not serialized_validator(serialized):
            raise SolidificationError(serialized_validation_error)
        os.replace(temporary_name, output)
        temporary_name = None
        return serialized
    except BaseException:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        raise


def _write_ascii_stl(mesh: trimesh.Trimesh, destination: str | os.PathLike[str]) -> None:
    """Stream a float64 ASCII STL without constructing a huge text blob."""

    triangles = np.asarray(mesh.triangles, dtype=np.float64)
    normals = np.asarray(mesh.face_normals, dtype=np.float64)
    if (
        triangles.shape != (len(mesh.faces), 3, 3)
        or normals.shape != (len(mesh.faces), 3)
        or not np.isfinite(triangles).all()
        or not np.isfinite(normals).all()
    ):
        raise SolidificationError(_STL_ENCODING_ERROR)

    with open(destination, "w", encoding="ascii", newline="\n") as output:
        output.write("solid organic_supports\n")
        records: list[str] = []
        for normal, triangle in zip(normals, triangles, strict=True):
            values = (*normal, *triangle[0], *triangle[1], *triangle[2])
            records.append(
                (
                    "facet normal {:.17g} {:.17g} {:.17g}\n"
                    "outer loop\n"
                    "vertex {:.17g} {:.17g} {:.17g}\n"
                    "vertex {:.17g} {:.17g} {:.17g}\n"
                    "vertex {:.17g} {:.17g} {:.17g}\n"
                    "endloop\nendfacet\n"
                ).format(*values)
            )
            if len(records) == 4096:
                output.writelines(records)
                records.clear()
        output.writelines(records)
        output.write("endsolid organic_supports\n")
        output.flush()
        os.fsync(output.fileno())


def _is_valid_stl_mesh(mesh: object) -> bool:
    return bool(
        isinstance(mesh, trimesh.Trimesh)
        and len(mesh.faces)
        and mesh.is_watertight
        and mesh.is_winding_consistent
        and mesh.is_volume
        and math.isfinite(float(mesh.volume))
        and mesh.volume > 0.0
    )


def export_stl(source: JsonSource, destination: str | os.PathLike[str]) -> Path:
    """Solidify ``source`` and atomically write a validated STL."""

    output = Path(destination).expanduser()
    export_mesh_stl(solidify_layers(source), output)
    return output
