"""Fuse every support root into one short, rounded Organic trunk blob."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
from shapely import concave_hull, maximum_inscribed_circle
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points, unary_union

from .solidify import FilledPolygon, load_layer_document


@dataclass(frozen=True, slots=True)
class NetworkBaseStats:
    node_count: int


def _shape(polygon: FilledPolygon) -> Polygon:
    return Polygon(polygon.contour, polygon.holes)


def _polygon_parts(geometry: Any) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    return [item for item in getattr(geometry, "geoms", ()) if isinstance(item, Polygon)]


def _serialized_polygons(geometry: Any) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for polygon in _polygon_parts(geometry):
        if polygon.is_empty or polygon.area <= 0.0:
            continue
        serialized.append(
            {
                "contour": [
                    [float(x), float(y)] for x, y in polygon.exterior.coords[:-1]
                ],
                "holes": [
                    [[float(x), float(y)] for x, y in ring.coords[:-1]]
                    for ring in polygon.interiors
                ],
            }
        )
    return serialized


def connect_polygon_parts(geometry: Any, *, width_mm: float) -> Any:
    """Join polygon parts with a minimum-length rounded printable web."""

    width_mm = float(width_mm)
    if not math.isfinite(width_mm) or width_mm <= 0.0:
        raise ValueError("connector width must be positive and finite")
    connected = geometry
    while True:
        parts = _polygon_parts(connected)
        if len(parts) <= 1:
            return connected
        best: tuple[float, int, int, Any, Any] | None = None
        for left_index, left in enumerate(parts):
            for right_index in range(left_index + 1, len(parts)):
                right = parts[right_index]
                left_point, right_point = nearest_points(left, right)
                candidate = (
                    float(left_point.distance(right_point)),
                    left_index,
                    right_index,
                    left_point,
                    right_point,
                )
                if best is None or candidate[:3] < best[:3]:
                    best = candidate
        if best is None:  # pragma: no cover - guarded by len(parts) above
            raise ValueError("Could not connect Organic trunk regions")
        _distance, _left, _right, left_point, right_point = best
        bridge = LineString((left_point, right_point)).buffer(
            width_mm * 0.5, quad_segs=16, cap_style="round", join_style="round"
        )
        updated = unary_union((connected, bridge))
        if len(_polygon_parts(updated)) >= len(parts):
            # Numerical point contact can prevent a union at the exact nearest
            # endpoint. A tiny overlap remains far below print resolution.
            bridge = LineString((left_point, right_point)).buffer(
                width_mm * 0.5005,
                quad_segs=16,
                cap_style="round",
                join_style="round",
            )
            updated = unary_union((connected, bridge))
        if len(_polygon_parts(updated)) >= len(parts):
            raise ValueError("Could not form a positive-width Organic trunk connector")
        connected = updated


def add_network_base(
    payload: Mapping[str, Any],
    *,
    thickness_mm: float,
    beam_width_mm: float,
    node_diameter_mm: float,
) -> tuple[dict[str, Any], NetworkBaseStats]:
    """Append one continuous rounded trunk slab to a support-layer payload."""

    for name, value in (
        ("base thickness", thickness_mm),
        ("blob margin", beam_width_mm),
        ("root lobe diameter", node_diameter_mm),
    ):
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError(f"{name} must be positive and finite")
    document = load_layer_document(payload)
    root_layer = next((layer for layer in document.layers if layer.polygons), None)
    if root_layer is None:
        raise ValueError("Organic supports contain no bed-root polygons")

    root_union = unary_union([_shape(item) for item in root_layer.polygons])
    roots = [item for item in _polygon_parts(root_union) if not item.is_empty]
    if not roots:
        raise ValueError("Organic support roots have no usable area")

    points = np.asarray(
        [[point.x, point.y] for point in (root.representative_point() for root in roots)],
        dtype=float,
    )
    node_radius = float(node_diameter_mm) * 0.5
    lobes = unary_union(
        [root_union]
        + [Point(*point).buffer(node_radius, quad_segs=16) for point in points]
    )
    # A concave hull gives one continuous mass without the visible graph beams
    # of the previous design. Ratio 0.58 keeps substantial organic bays while
    # filling enough of the interior to read and print as one large trunk.
    blob = concave_hull(lobes, ratio=0.58, allow_holes=False)
    blob = blob.buffer(float(beam_width_mm) * 0.5, quad_segs=16, join_style="round")
    polygons = _polygon_parts(blob)
    if not polygons:
        raise ValueError("Organic trunk blob has no filled area")

    if not _serialized_polygons(blob):
        raise ValueError("Organic trunk blob has no positive-area polygons")

    # The former taper rebuilt a concave hull from the roots at every height.
    # That hull retained its broad connecting web until the final layer and
    # therefore ended in a shelf. Erode the actual bed footprint instead. The
    # largest inscribed-circle radius is its natural extinction distance: just
    # beyond it, none of the added footprint remains and only native branches
    # continue upward.
    extinction_distance = max(
        float(maximum_inscribed_circle(part, tolerance=0.02).length)
        for part in polygons
    ) * 1.05
    if not math.isfinite(extinction_distance) or extinction_distance <= 0.0:
        raise ValueError("Organic trunk blob has no usable taper radius")

    result = dict(payload)
    integrated_layers: list[dict[str, Any]] = []
    for raw_layer, layer in zip(payload["layers"], document.layers, strict=True):
        updated = dict(raw_layer)
        if layer.polygons and layer.bottom_z < thickness_mm:
            linear_fullness = float(
                np.clip((thickness_mm - max(0.0, layer.bottom_z)) / thickness_mm, 0.0, 1.0)
            )
            # Smoothstep makes the taper tangent at both the broad bed footprint
            # and the native branches, avoiding a visible kink at either end.
            fullness = linear_fullness * linear_fullness * (
                3.0 - 2.0 * linear_fullness
            )
            layer_shape = unary_union([_shape(item) for item in layer.polygons])
            tapered = blob.buffer(
                -extinction_distance * (1.0 - fullness),
                quad_segs=16,
                join_style="round",
            )
            combined = unary_union((layer_shape, tapered))
            connector_width = max(
                1.2,
                min(2.4, float(beam_width_mm) * (0.5 + 0.5 * fullness)),
            )
            combined = connect_polygon_parts(
                combined, width_mm=connector_width
            )
            integrated = _serialized_polygons(combined)
            if not integrated:
                raise ValueError("Integrated Organic trunk layer has no filled area")
            updated["polygons"] = integrated
            updated["kind"] = "integrated_single_organic_trunk"
            updated["trunk_fullness"] = fullness
            updated["trunk_linear_fullness"] = linear_fullness
            updated["trunk_connector_width_mm"] = connector_width
        integrated_layers.append(updated)
    result["layers"] = integrated_layers
    return result, NetworkBaseStats(node_count=len(points))


__all__ = [
    "NetworkBaseStats",
    "add_network_base",
    "connect_polygon_parts",
]
