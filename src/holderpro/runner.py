"""End-to-end runner for the exact PrusaSlicer Organic Supports engine.

This module never imports HolderPro's independent Python support generator.  It
poses the reference mesh, calls the pinned native ``libslic3r`` adapter, and
turns the adapter's filled support islands into a support-only solid STL.
"""

from __future__ import annotations

import json
import math
from numbers import Integral
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from .engine import (
    LAYER_SCHEMA,
    PINNED_PRUSASLICER_COMMIT,
    PINNED_PRUSASLICER_VERSION,
    EngineInfo,
    find_engine,
    inspect_engine,
)
from .engine import (
    project_root as project_root,
)
from .errors import (
    EngineError,
    EngineExecutionError,
    EngineNotFoundError,
    EngineProvenanceError,
    GenerationCancelled,
    GenerationError,
    GenerationValidationError,
)
from .mesh_io import MeshLoadError, load_reference_mesh
from .network_base import NetworkBaseStats, add_network_base
from .solidify import (
    LayerFormatError,
    SolidificationError,
    export_mesh_stl,
    solidify_layers,
)
from .surface_analysis import mesh_fingerprint
from .threemf import write_3mf_mesh

ProgressCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]

BED_CENTER_MM = np.array([100.0, 100.0], dtype=float)
BED_SIZE_MM = np.array([200.0, 200.0], dtype=float)
BED_MARGIN_MM = 5.0
STANDARD_BUILD_HEIGHT_MM = 200.0
PINNED_ENGINE_NAME = "PrusaSlicer Organic"


@dataclass(frozen=True, slots=True)
class GenerationJob:
    input_path: Path
    output_path: Path
    bottom_height_mm: float = 25.0
    rotation_x_deg: float = 0.0
    rotation_y_deg: float = 0.0
    rotation_z_deg: float = 0.0
    layer_height_mm: float = 0.30
    branch_diameter_mm: float = 2.0
    branch_diameter_angle_deg: float = 15.0
    tip_diameter_mm: float = 0.8
    branch_angle_deg: float = 40.0
    branch_angle_slow_deg: float = 25.0
    contact_distance_mm: float = 0.0
    network_base_enabled: bool = True
    base_thickness_mm: float = 20.0
    base_beam_width_mm: float = 3.0
    base_node_diameter_mm: float = 8.0
    painted_enforcer_faces: tuple[int, ...] = ()
    painted_blocker_faces: tuple[int, ...] = ()
    paint_face_count: int | None = None
    paint_mesh_fingerprint: str | None = None
    enforcers_only: bool = False
    engine_path: Path | None = None
    retain_failed_geometry: bool = False

    def validated(self) -> GenerationJob:
        """Return a normalized job or raise :class:`GenerationValidationError`."""

        try:
            return self._validated()
        except GenerationValidationError:
            raise
        except GenerationError as exc:
            raise GenerationValidationError(str(exc)) from exc
        except (OSError, TypeError, ValueError, OverflowError) as exc:
            raise GenerationValidationError(f"Invalid generation job: {exc}") from exc

    def _validated(self) -> GenerationJob:
        input_path = Path(self.input_path).expanduser().resolve()
        output_path = Path(self.output_path).expanduser().resolve()
        if not input_path.is_file():
            raise GenerationError(f"Input model does not exist: {input_path}")
        if output_path.suffix.lower() != ".stl":
            raise GenerationError("Support output must use the .stl extension")
        if output_path.exists() and not output_path.is_file():
            raise GenerationError(
                "Support output must be a regular file, not a directory or device"
            )
        if input_path == output_path or (
            output_path.exists() and input_path.samefile(output_path)
        ):
            raise GenerationError("Input and output paths must be different")
        if not output_path.parent.is_dir():
            raise GenerationError(
                f"Support output directory does not exist: {output_path.parent}"
            )

        numeric = {
            "bottom height": self.bottom_height_mm,
            "rotation X": self.rotation_x_deg,
            "rotation Y": self.rotation_y_deg,
            "rotation Z": self.rotation_z_deg,
            "layer height": self.layer_height_mm,
            "branch diameter": self.branch_diameter_mm,
            "branch diameter growth angle": self.branch_diameter_angle_deg,
            "tip diameter": self.tip_diameter_mm,
            "maximum branch angle": self.branch_angle_deg,
            "preferred branch angle": self.branch_angle_slow_deg,
            "contact distance": self.contact_distance_mm,
            "base thickness": self.base_thickness_mm,
            "blob margin": self.base_beam_width_mm,
            "root lobe diameter": self.base_node_diameter_mm,
        }
        for name, value in numeric.items():
            if not math.isfinite(float(value)):
                raise GenerationError(f"{name} must be finite")
        if self.bottom_height_mm <= 0.0:
            raise GenerationError("Bottom height must be greater than zero")
        if not 0.01 <= self.layer_height_mm <= 1.0:
            raise GenerationError("Layer height must be between 0.01 and 1.0 mm")
        if not 0.1 <= self.tip_diameter_mm <= 100.0:
            raise GenerationError("Tip diameter must be between 0.1 and 100 mm")
        if not 0.1 <= self.branch_diameter_mm <= 100.0:
            raise GenerationError("Branch diameter must be between 0.1 and 100 mm")
        if not 0.0 <= self.branch_diameter_angle_deg <= 15.0:
            raise GenerationError(
                "Branch diameter growth angle must be between 0 and 15 degrees"
            )
        if self.branch_diameter_mm < self.tip_diameter_mm:
            raise GenerationError("Branch diameter cannot be smaller than tip diameter")
        if not 10.0 <= self.branch_angle_slow_deg <= self.branch_angle_deg <= 85.0:
            raise GenerationError(
                "Branch angles must satisfy 10° ≤ preferred ≤ maximum ≤ 85°"
            )
        if self.contact_distance_mm < 0.0:
            raise GenerationError("Contact distance cannot be negative")
        if not isinstance(self.network_base_enabled, bool):
            raise GenerationError("network_base_enabled must be true or false")
        if not 0.2 <= self.base_thickness_mm <= 50.0:
            raise GenerationError("Base taper height must be between 0.2 and 50 mm")
        if not 0.5 <= self.base_beam_width_mm <= 30.0:
            raise GenerationError("Blob margin must be between 0.5 and 30 mm")
        if not 0.5 <= self.base_node_diameter_mm <= 50.0:
            raise GenerationError("Root lobe diameter must be between 0.5 and 50 mm")
        if (
            self.network_base_enabled
            and self.base_thickness_mm >= self.bottom_height_mm
        ):
            raise GenerationError(
                "Base taper height must stay below the model's bottom height"
            )

        if not isinstance(self.enforcers_only, bool):
            raise GenerationError("enforcers_only must be true or false")
        if not isinstance(self.retain_failed_geometry, bool):
            raise GenerationError("retain_failed_geometry must be true or false")
        def normalize_faces(values: object) -> tuple[int, ...]:
            try:
                items: tuple[object, ...] = tuple(values)  # type: ignore[arg-type]
            except TypeError as exc:
                raise GenerationError(
                    "Painted support faces must be integer indices"
                ) from exc
            normalized: set[int] = set()
            for face in items:
                if isinstance(face, bool) or not isinstance(face, Integral):
                    raise GenerationError(
                        "Painted support faces must be integer indices"
                    )
                normalized.add(int(face))
            return tuple(sorted(normalized))

        try:
            enforcers = normalize_faces(self.painted_enforcer_faces)
            blockers = normalize_faces(self.painted_blocker_faces)
        except GenerationError:
            raise
        except (TypeError, ValueError, OverflowError) as exc:
            raise GenerationError(
                "Painted support faces must be integer indices"
            ) from exc
        if any(face < 0 for face in enforcers + blockers):
            raise GenerationError("Painted support face indices cannot be negative")
        overlap = set(enforcers).intersection(blockers)
        if overlap:
            raise GenerationError(
                "A face cannot be both a support enforcer and blocker"
            )
        if (enforcers or blockers) and self.paint_face_count is None:
            raise GenerationError("Painted support faces require a source face count")
        paint_face_count = self.paint_face_count
        if paint_face_count is not None:
            if (
                isinstance(paint_face_count, bool)
                or not isinstance(paint_face_count, Integral)
                or paint_face_count <= 0
            ):
                raise GenerationError("Paint source face count must be a positive integer")
            paint_face_count = int(paint_face_count)
            if any(face >= paint_face_count for face in enforcers + blockers):
                raise GenerationError("Painted support face index is out of range")
        fingerprint = self.paint_mesh_fingerprint
        if fingerprint is not None:
            fingerprint = str(fingerprint).lower()
            if len(fingerprint) != 64 or any(
                character not in "0123456789abcdef" for character in fingerprint
            ):
                raise GenerationError("Paint source fingerprint is invalid")
        if self.enforcers_only and not enforcers:
            raise GenerationError(
                "Painted-regions-only mode requires at least one green enforcer face"
            )
        # Green paint is an explicit support mask, not a hint layered on top of
        # automatic overhang detection.  Normalize every painted job to
        # enforcers-only so no caller can accidentally generate supports on
        # unpainted facets.
        enforcers_only = self.enforcers_only or bool(enforcers)

        return GenerationJob(
            input_path=input_path,
            output_path=output_path,
            bottom_height_mm=float(self.bottom_height_mm),
            rotation_x_deg=float(self.rotation_x_deg),
            rotation_y_deg=float(self.rotation_y_deg),
            rotation_z_deg=float(self.rotation_z_deg),
            layer_height_mm=float(self.layer_height_mm),
            branch_diameter_mm=float(self.branch_diameter_mm),
            branch_diameter_angle_deg=float(self.branch_diameter_angle_deg),
            tip_diameter_mm=float(self.tip_diameter_mm),
            branch_angle_deg=float(self.branch_angle_deg),
            branch_angle_slow_deg=float(self.branch_angle_slow_deg),
            contact_distance_mm=float(self.contact_distance_mm),
            network_base_enabled=self.network_base_enabled,
            base_thickness_mm=float(self.base_thickness_mm),
            base_beam_width_mm=float(self.base_beam_width_mm),
            base_node_diameter_mm=float(self.base_node_diameter_mm),
            painted_enforcer_faces=enforcers,
            painted_blocker_faces=blockers,
            paint_face_count=paint_face_count,
            paint_mesh_fingerprint=fingerprint,
            enforcers_only=enforcers_only,
            retain_failed_geometry=self.retain_failed_geometry,
            engine_path=(
                Path(self.engine_path).expanduser().resolve()
                if self.engine_path is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class GenerationResult:
    output_path: Path
    engine_path: Path
    engine_version: str
    layer_count: int
    component_count: int
    triangle_count: int
    volume_mm3: float
    elapsed_seconds: float
    base_node_count: int = 0
    warnings: tuple[str, ...] = ()
    engine_info: EngineInfo | None = None


def _count_connected_components(mesh: trimesh.Trimesh) -> int:
    """Count face components using only shared vertex indices.

    The final support mesh is already validated as a watertight triangle mesh.
    Keeping this small union-find here avoids ``trimesh.Trimesh.split``, whose
    graph implementation can pull optional NetworkX or SciPy dependencies into
    an otherwise core-only HolderPro installation.
    """

    faces = np.asarray(mesh.faces, dtype=np.intp)
    if not len(faces):
        return 0
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise GenerationError("Final support mesh does not contain triangles")

    vertex_count = len(mesh.vertices)
    if np.any(faces < 0) or np.any(faces >= vertex_count):
        raise GenerationError("Final support mesh contains an invalid vertex index")

    parents = np.arange(vertex_count, dtype=np.intp)
    ranks = np.zeros(vertex_count, dtype=np.uint8)

    def find(index: int) -> int:
        while int(parents[index]) != index:
            parents[index] = parents[int(parents[index])]
            index = int(parents[index])
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if ranks[left_root] < ranks[right_root]:
            left_root, right_root = right_root, left_root
        parents[right_root] = left_root
        if ranks[left_root] == ranks[right_root]:
            ranks[left_root] += 1

    for raw_a, raw_b, raw_c in faces:
        a, b, c = int(raw_a), int(raw_b), int(raw_c)
        union(a, b)
        union(a, c)

    return len({find(int(face[0])) for face in faces})


def _count_material_components(mesh: trimesh.Trimesh) -> int:
    """Count disconnected positive-volume regions, excluding cavity shells.

    A valid solid may contain inward-oriented closed shells around enclosed air
    pockets.  Those shells are separate surface components, but they are not
    separate pieces of printable material.  Organic layer unions occasionally
    create very small enclosed cavities where polygon topology changes between
    adjacent layers, so the single-trunk check must use material connectivity
    rather than raw surface-shell connectivity.

    ``mesh`` has already passed the watertight and winding-consistency checks
    before this helper is used.  Positive signed-volume shells represent
    material components; negative shells represent cavities.
    """

    faces = np.asarray(mesh.faces, dtype=np.intp)
    vertices = np.asarray(mesh.vertices, dtype=float)
    if not len(faces):
        return 0
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise GenerationError("Final support mesh does not contain triangles")
    if np.any(faces < 0) or np.any(faces >= len(vertices)):
        raise GenerationError("Final support mesh contains an invalid vertex index")

    parent = np.arange(len(vertices), dtype=np.intp)

    def find(vertex: int) -> int:
        while parent[vertex] != vertex:
            parent[vertex] = parent[parent[vertex]]
            vertex = int(parent[vertex])
        return vertex

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for first, second, third in faces:
        union(int(first), int(second))
        union(int(first), int(third))

    grouped_faces: dict[int, list[int]] = {}
    for face_index, face in enumerate(faces):
        grouped_faces.setdefault(find(int(face[0])), []).append(face_index)

    material_components = 0
    for face_indices in grouped_faces.values():
        component_faces = faces[np.asarray(face_indices, dtype=np.intp)]
        component_vertices = vertices[np.unique(component_faces)]
        origin = component_vertices.mean(axis=0)
        triangles = vertices[component_faces] - origin
        signed_six_volumes = np.einsum(
            "ij,ij->i",
            triangles[:, 0],
            np.cross(triangles[:, 1], triangles[:, 2]),
        )
        signed_volume = math.fsum(float(value) for value in signed_six_volumes) / 6.0
        if signed_volume > 0.0:
            material_components += 1
    return material_components


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _check_cancelled(cancelled: CancelCallback | None) -> None:
    if cancelled is not None and cancelled():
        raise GenerationCancelled("Organic support generation was cancelled")


def _load_reference_mesh(path: Path) -> trimesh.Trimesh:
    """Compatibility alias; use :func:`holderpro.mesh_io.load_reference_mesh`."""

    try:
        return load_reference_mesh(path)
    except MeshLoadError as exc:
        raise GenerationError(str(exc)) from exc


def _pose_reference_mesh(mesh: trimesh.Trimesh, job: GenerationJob) -> trimesh.Trimesh:
    posed = mesh.copy()
    bounds = np.asarray(posed.bounds, dtype=float)
    center = bounds.mean(axis=0)
    posed.apply_translation(-center)
    rotation = trimesh.transformations.euler_matrix(
        math.radians(job.rotation_x_deg),
        math.radians(job.rotation_y_deg),
        math.radians(job.rotation_z_deg),
        axes="sxyz",
    )
    posed.apply_transform(rotation)

    bounds = np.asarray(posed.bounds, dtype=float)
    xy_center = bounds[:, :2].mean(axis=0)
    translation = np.array(
        [
            BED_CENTER_MM[0] - xy_center[0],
            BED_CENTER_MM[1] - xy_center[1],
            job.bottom_height_mm - bounds[0, 2],
        ]
    )
    posed.apply_translation(translation)

    return posed


def _canonicalize_painted_stl_mesh(
    mesh: trimesh.Trimesh, job: GenerationJob
) -> tuple[trimesh.Trimesh, GenerationJob, int]:
    """Remove STL-collapsed faces and remap paint without changing face order."""

    triangles = np.asarray(mesh.triangles, dtype=np.float32)
    collapsed = (
        np.all(triangles[:, 0] == triangles[:, 1], axis=1)
        | np.all(triangles[:, 1] == triangles[:, 2], axis=1)
        | np.all(triangles[:, 0] == triangles[:, 2], axis=1)
    )
    removed = int(np.count_nonzero(collapsed))
    if not removed:
        return mesh, job, 0

    keep = ~collapsed
    old_to_new = np.cumsum(keep, dtype=np.int64) - 1

    def remap(faces: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(int(old_to_new[face]) for face in faces if keep[face])

    canonical = trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices, dtype=float).copy(),
        faces=np.asarray(mesh.faces, dtype=np.int64)[keep].copy(),
        process=False,
    )
    paint_job = replace(
        job,
        painted_enforcer_faces=remap(job.painted_enforcer_faces),
        painted_blocker_faces=remap(job.painted_blocker_faces),
        paint_face_count=len(canonical.faces),
    )
    return canonical, paint_job, removed


def _standard_bed_warning(mesh: trimesh.Trimesh) -> str | None:
    bounds = np.asarray(mesh.bounds, dtype=float)
    if not (
        np.any(bounds[0, :2] < BED_MARGIN_MM)
        or np.any(bounds[1, :2] > BED_SIZE_MM - BED_MARGIN_MM)
    ):
        return None
    extents = bounds[1, :2] - bounds[0, :2]
    return (
        "The posed model exceeds the 200 x 200 mm build plate with a "
        f"{BED_MARGIN_MM:g} mm margin (XY extents: {extents[0]:.2f} x "
        f"{extents[1]:.2f} mm). Generation continued on an expanded virtual bed; "
        "the exported stand may need to be split or printed on a larger machine."
    )


def _standard_height_warning(mesh: trimesh.Trimesh) -> str | None:
    top_z = float(np.asarray(mesh.bounds, dtype=float)[1, 2])
    if top_z <= STANDARD_BUILD_HEIGHT_MM:
        return None
    return (
        f"The posed model reaches Z={top_z:.2f} mm, above the standard "
        f"{STANDARD_BUILD_HEIGHT_MM:g} mm build height. Generation continued "
        "in an expanded virtual build volume; the exported stand may need to "
        "be split or fabricated on a larger machine."
    )


def _virtual_bed_bounds(mesh: trimesh.Trimesh, job: GenerationJob) -> np.ndarray:
    bounds = np.asarray(mesh.bounds, dtype=float)[:, :2]
    trunk_clearance = (
        job.base_node_diameter_mm * 0.5 + job.base_beam_width_mm * 0.5
        if job.network_base_enabled
        else 0.0
    )
    padding = BED_MARGIN_MM + trunk_clearance
    return np.asarray(
        (
            np.minimum(np.zeros(2), bounds[0] - padding),
            np.maximum(BED_SIZE_MM, bounds[1] + padding),
        ),
        dtype=float,
    )


def _bed_shape_override(bounds: np.ndarray) -> str:
    lower, upper = np.asarray(bounds, dtype=float)
    return (
        f"bed_shape={lower[0]:.9g}x{lower[1]:.9g},"
        f"{upper[0]:.9g}x{lower[1]:.9g},"
        f"{upper[0]:.9g}x{upper[1]:.9g},"
        f"{lower[0]:.9g}x{upper[1]:.9g}"
    )


def _virtual_build_height(mesh: trimesh.Trimesh) -> float:
    top_z = float(np.asarray(mesh.bounds, dtype=float)[1, 2])
    return max(STANDARD_BUILD_HEIGHT_MM, top_z + BED_MARGIN_MM)


def _run_engine(
    engine: Path,
    posed_input: Path,
    layers_output: Path,
    job: GenerationJob,
    log_path: Path,
    support_paint_path: Path | None = None,
    generation_bed_bounds: np.ndarray | None = None,
    generation_max_height: float | None = None,
    *,
    cancelled: CancelCallback | None,
) -> str:
    command = [
        str(engine),
        "--input",
        str(posed_input),
        "--output",
        str(layers_output),
        "--layer-height",
        f"{job.layer_height_mm:.9g}",
        "--branch-diameter",
        f"{job.branch_diameter_mm:.9g}",
        "--tip-diameter",
        f"{job.tip_diameter_mm:.9g}",
        "--branch-angle",
        f"{job.branch_angle_deg:.9g}",
        "--branch-angle-slow",
        f"{job.branch_angle_slow_deg:.9g}",
        "--contact-distance",
        f"{job.contact_distance_mm:.9g}",
        "--set",
        f"support_tree_branch_diameter_angle={job.branch_diameter_angle_deg:.9g}",
    ]
    if generation_bed_bounds is not None:
        command.extend(("--set", _bed_shape_override(generation_bed_bounds)))
    if generation_max_height is not None:
        command.extend(
            ("--set", f"max_print_height={float(generation_max_height):.9g}")
        )
    if support_paint_path is not None:
        command.extend(("--support-paint", str(support_paint_path)))
    if job.enforcers_only:
        command.append("--enforcers-only")

    with log_path.open("wb") as log:
        try:
            process = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT
            )
        except OSError as exc:
            raise EngineExecutionError(
                f"Could not start the HolderPro Organic engine: {exc}"
            ) from exc
        while process.poll() is None:
            if cancelled is not None and cancelled():
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise GenerationCancelled("Organic support generation was cancelled")
            time.sleep(0.10)
        return_code = process.returncode

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    if return_code != 0:
        detail = log_text.strip()[-4000:]
        raise EngineExecutionError(
            f"The HolderPro Organic engine exited with status {return_code}."
            + (f"\n\n{detail}" if detail else "")
        )
    if not layers_output.is_file():
        raise EngineExecutionError(
            "The HolderPro Organic engine produced no layer document"
        )
    return log_text


def _write_support_paint(path: Path, job: GenerationJob) -> None:
    if job.paint_face_count is None:
        raise GenerationError("Internal error: support paint has no face count")
    lines = [
        "HOLDERPRO_SUPPORT_PAINT_V1",
        f"faces {job.paint_face_count}",
    ]
    lines.extend(f"E {face}" for face in job.painted_enforcer_faces)
    lines.extend(f"B {face}" for face in job.painted_blocker_faces)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _normalize_layer_payload(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GenerationError(
            f"Invalid layer document from native engine: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise GenerationError("Native layer document root is not an object")
    if payload.get("schema") != LAYER_SCHEMA:
        raise GenerationError(
            "Native engine returned an unrecognized layer schema; refusing to "
            "treat it as exact Organic output"
        )
    if payload.get("version") != 1:
        raise GenerationError("Native engine layer format version is not 1")
    if payload.get("units") != "mm":
        raise GenerationError("Native engine layer units are not millimetres")

    engine_data = payload.get("engine")
    expected_engine = {
        "name": PINNED_ENGINE_NAME,
        "version": PINNED_PRUSASLICER_VERSION,
        "commit": PINNED_PRUSASLICER_COMMIT,
    }
    if not isinstance(engine_data, Mapping) or any(
        engine_data.get(key) != value for key, value in expected_engine.items()
    ):
        raise EngineProvenanceError(
            "Native engine provenance does not match pinned PrusaSlicer "
            f"{PINNED_PRUSASLICER_VERSION} ({PINNED_PRUSASLICER_COMMIT})"
        )

    if not isinstance(payload.get("layers"), list):
        raise GenerationError("Native layer document contains no layers array")
    for layer in payload["layers"]:
        if not isinstance(layer, Mapping) or not isinstance(
            layer.get("polygons"), list
        ):
            raise GenerationError("Native layer document contains an invalid layer")
    if not isinstance(payload.get("summary"), Mapping):
        raise GenerationError("Native layer document contains no summary object")
    _sanitize_native_layer_geometry(payload)
    return payload, PINNED_PRUSASLICER_VERSION


def _native_ring_has_area(ring: object) -> bool:
    """Return false only for a well-formed ring with zero geometric area."""

    if not isinstance(ring, list):
        return True
    points: list[tuple[float, float]] = []
    try:
        for raw in ring:
            if not isinstance(raw, list) or len(raw) != 2:
                return True
            point = (float(raw[0]), float(raw[1]))
            if not all(math.isfinite(value) for value in point):
                return True
            if not points or point != points[-1]:
                points.append(point)
    except (TypeError, ValueError, OverflowError):
        return True
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    if len(points) < 3 or len(set(points)) < 3:
        return False
    twice_area = math.fsum(
        point[0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * point[1]
        for index, point in enumerate(points)
    )
    return math.isfinite(twice_area) and abs(twice_area) > 1e-12


def _sanitize_native_layer_geometry(payload: dict[str, Any]) -> None:
    """Remove only zero-area rings introduced by native JSON rounding."""

    removed_holes = 0
    removed_polygons = 0
    for layer in payload["layers"]:
        cleaned_polygons: list[dict[str, Any]] = []
        for polygon in layer["polygons"]:
            if not isinstance(polygon, dict) or not _native_ring_has_area(
                polygon.get("contour")
            ):
                removed_polygons += 1
                continue
            holes = polygon.get("holes", [])
            if not isinstance(holes, list):
                cleaned_polygons.append(polygon)
                continue
            cleaned_holes = [hole for hole in holes if _native_ring_has_area(hole)]
            removed_holes += len(holes) - len(cleaned_holes)
            cleaned = dict(polygon)
            cleaned["holes"] = cleaned_holes
            cleaned_polygons.append(cleaned)
        layer["polygons"] = cleaned_polygons
    if removed_holes or removed_polygons:
        payload["geometry_sanitization"] = {
            "zero_area_holes_removed": removed_holes,
            "zero_area_polygons_removed": removed_polygons,
        }


def _validate_printable_stl(engine: Path, path: Path) -> bool:
    """Ask the pinned PrusaSlicer loader whether an STL is printable."""

    try:
        completed = subprocess.run(
            (str(engine), "--validate-solid", str(path), "--quiet"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _atomic_export_mesh(
    mesh: trimesh.Trimesh,
    output: Path,
    *,
    engine: Path | None = None,
    require_single_component: bool = False,
) -> trimesh.Trimesh:
    """Write STL only after its float32 representation passes volume checks."""

    try:
        return export_mesh_stl(
            mesh,
            output,
            printable_validator=(
                (lambda candidate: _validate_printable_stl(engine, candidate))
                if engine is not None
                else None
            ),
            serialized_validator=(
                lambda candidate: _count_material_components(candidate) == 1
            )
            if require_single_component
            else None,
            serialized_validation_error=(
                "STL encoding would not preserve the required single connected "
                "support solid"
            ),
        )
    except SolidificationError as exc:
        raise GenerationError(str(exc)) from exc
    except OSError as exc:
        raise GenerationError(f"Could not write support STL: {exc}") from exc


def _retain_failed_export_geometry(
    mesh: trimesh.Trimesh, payload: Mapping[str, Any]
) -> tuple[Path, ...]:
    """Retain explicitly requested geometry in a private, unique directory."""

    retained: list[Path] = []
    directory = Path(tempfile.mkdtemp(prefix="holderpro-failed-export-"))
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    solid_path = directory / "support-solid.npz"
    layers_path = directory / "support-layers.json"
    try:
        descriptor = os.open(solid_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            np.savez_compressed(
                handle,
                vertices=np.asarray(mesh.vertices, dtype=np.float64),
                faces=np.asarray(mesh.faces, dtype=np.int64),
            )
        retained.append(solid_path)
    except (OSError, ValueError):
        solid_path.unlink(missing_ok=True)
        pass
    try:
        descriptor = os.open(layers_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        retained.append(layers_path)
    except (OSError, TypeError, ValueError):
        layers_path.unlink(missing_ok=True)
        pass
    if not retained:
        try:
            directory.rmdir()
        except OSError:
            pass
    return tuple(retained)


def generate(
    job: GenerationJob,
    *,
    progress: ProgressCallback | None = None,
    cancelled: CancelCallback | None = None,
) -> GenerationResult:
    """Generate a filled, support-only STL using the genuine Organic engine."""

    started = time.monotonic()
    job = job.validated()
    _check_cancelled(cancelled)
    engine = find_engine(job.engine_path)
    engine_info = inspect_engine(engine)

    _notify(progress, "Loading and posing the reference model…")
    reference = _load_reference_mesh(job.input_path)
    if job.paint_face_count is not None:
        if len(reference.faces) != job.paint_face_count:
            raise GenerationError(
                "The source mesh changed after support painting; reload it in "
                "the preview"
            )
        if (
            job.paint_mesh_fingerprint is not None
            and mesh_fingerprint(reference) != job.paint_mesh_fingerprint
        ):
            raise GenerationError(
                "The source mesh changed after support painting; reload it in "
                "the preview"
            )
    posed = _pose_reference_mesh(reference, job)
    warnings: list[str] = []
    if warning := _standard_bed_warning(posed):
        warnings.append(warning)
        _notify(progress, f"WARNING: {warning}")
    if warning := _standard_height_warning(posed):
        warnings.append(warning)
        _notify(progress, f"WARNING: {warning}")
    generation_bed_bounds = _virtual_bed_bounds(posed, job)
    generation_max_height = _virtual_build_height(posed)
    _check_cancelled(cancelled)

    with tempfile.TemporaryDirectory(prefix="holderpro-organic-") as temp_name:
        temp = Path(temp_name)
        paint_active = bool(
            job.painted_enforcer_faces or job.painted_blocker_faces
        )
        posed_input = temp / (
            "posed-reference.3mf" if paint_active else "posed-reference.stl"
        )
        layer_path = temp / "filled-support-layers.json"
        log_path = temp / "engine.log"
        support_paint_path: Path | None = None
        engine_mesh = posed
        paint_job = job
        if paint_active:
            engine_mesh, paint_job, removed_faces = _canonicalize_painted_stl_mesh(
                posed, job
            )
            if removed_faces:
                _notify(
                    progress,
                    f"Removed {removed_faces} non-printable collapsed reference "
                    "triangle(s) and remapped support paint…",
                )
            write_3mf_mesh(posed_input, engine_mesh)
        else:
            engine_mesh.export(posed_input, file_type="stl")
        if paint_active:
            support_paint_path = temp / "support-paint.txt"
            _write_support_paint(support_paint_path, paint_job)

        _notify(progress, "Running PrusaSlicer 2.9.6 Organic Supports…")
        _run_engine(
            engine,
            posed_input,
            layer_path,
            job,
            log_path,
            support_paint_path,
            generation_bed_bounds,
            generation_max_height,
            cancelled=cancelled,
        )
        _check_cancelled(cancelled)

        _notify(progress, "Building the filled support solid…")
        payload, engine_version = _normalize_layer_payload(layer_path)
        sanitization = payload.get("geometry_sanitization")
        if isinstance(sanitization, Mapping):
            removed = int(sanitization.get("zero_area_holes_removed", 0)) + int(
                sanitization.get("zero_area_polygons_removed", 0)
            )
            _notify(
                progress,
                f"Removed {removed} zero-area native rounding artifact(s)…",
            )
        base_stats = NetworkBaseStats(0)
        if job.network_base_enabled:
            _notify(
                progress,
                "Fusing every support root into one short Organic trunk…",
            )
            try:
                payload, base_stats = add_network_base(
                    payload,
                    thickness_mm=job.base_thickness_mm,
                    beam_width_mm=job.base_beam_width_mm,
                    node_diameter_mm=job.base_node_diameter_mm,
                )
            except (TypeError, ValueError) as exc:
                raise GenerationError(f"Could not build connected base: {exc}") from exc
        try:
            support_mesh = solidify_layers(payload)
        except (LayerFormatError, SolidificationError) as exc:
            raise GenerationError(
                f"Could not solidify Organic support layers: {exc}"
            ) from exc

        if job.network_base_enabled:
            base_bounds = np.asarray(support_mesh.bounds, dtype=float)
            if np.any(base_bounds[0, :2] < 0.0) or np.any(
                base_bounds[1, :2] > BED_SIZE_MM
            ):
                base_warning = (
                    "The generated stand extends outside the 200 x 200 mm build "
                    "plate. The complete geometry was still exported and may need "
                    "to be split or printed on a larger machine."
                )
                if base_warning not in warnings:
                    warnings.append(base_warning)
                    _notify(progress, f"WARNING: {base_warning}")

    # Native generation uses a 200 mm bed centered at (100, 100).  Return a
    # conventional STL centered around the origin while leaving Z=0 on the bed.
    support_mesh.apply_translation((-BED_CENTER_MM[0], -BED_CENTER_MM[1], 0.0))
    if not support_mesh.is_watertight or support_mesh.volume <= 0.0:
        raise GenerationError(
            "Final support export is not a watertight positive-volume solid"
        )
    component_count = _count_material_components(support_mesh)
    if job.network_base_enabled and component_count != 1:
        raise GenerationError(
            "Connected-base generation produced "
            f"{component_count} separate support components; output was not written"
        )
    _check_cancelled(cancelled)

    _notify(progress, "Writing support-only STL…")
    try:
        support_mesh = _atomic_export_mesh(
            support_mesh,
            job.output_path,
            engine=engine,
            require_single_component=job.network_base_enabled,
        )
    except GenerationError as exc:
        retained = (
            _retain_failed_export_geometry(support_mesh, payload)
            if job.retain_failed_geometry
            else ()
        )
        detail = (
            " Private diagnostic geometry retained at your request: "
            + ", ".join(str(path) for path in retained)
            if retained
            else ""
        )
        raise GenerationError(f"{exc}{detail}") from exc
    component_count = _count_material_components(support_mesh)
    result = GenerationResult(
        output_path=job.output_path,
        engine_path=engine,
        engine_version=engine_version,
        layer_count=len(payload["layers"]),
        component_count=component_count,
        triangle_count=len(support_mesh.faces),
        volume_mm3=float(support_mesh.volume),
        elapsed_seconds=time.monotonic() - started,
        base_node_count=base_stats.node_count,
        warnings=tuple(warnings),
        engine_info=engine_info,
    )
    _notify(
        progress,
        f"Finished: {result.layer_count} layers, {result.volume_mm3:.1f} mm³.",
    )
    return result


__all__ = [
    "GenerationCancelled",
    "GenerationError",
    "GenerationValidationError",
    "GenerationJob",
    "GenerationResult",
    "EngineError",
    "EngineExecutionError",
    "EngineInfo",
    "EngineNotFoundError",
    "EngineProvenanceError",
    "LAYER_SCHEMA",
    "PINNED_ENGINE_NAME",
    "PINNED_PRUSASLICER_COMMIT",
    "PINNED_PRUSASLICER_VERSION",
    "find_engine",
    "generate",
    "project_root",
]
