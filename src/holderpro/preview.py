"""VTK-backed interactive model preview and support-facet painting widget."""

from __future__ import annotations

import math
from pathlib import Path
import time
from typing import Any

import numpy as np
import trimesh

try:  # Optional GUI dependencies; CLI imports must remain headless-safe.
    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401 - registers OpenGL backend
    from PySide6 import QtCore, QtWidgets
    from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
    from vtkmodules.util.numpy_support import (
        numpy_to_vtk,
        numpy_to_vtkIdTypeArray,
        vtk_to_numpy,
    )
    from vtkmodules.vtkCommonCore import (
        VTK_UNSIGNED_CHAR,
        reference,
        vtkIdList,
        vtkPoints,
    )
    from vtkmodules.vtkCommonDataModel import (
        vtkCellArray,
        vtkPolyData,
        vtkStaticCellLocator,
    )
    from vtkmodules.vtkFiltersCore import (
        vtkPolyDataNormals,
        vtkQuadricDecimation,
        vtkTubeFilter,
    )
    from vtkmodules.vtkFiltersSources import (
        vtkLineSource,
        vtkRegularPolygonSource,
        vtkSphereSource,
    )
    from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
    from vtkmodules.vtkRenderingCore import (
        vtkActor,
        vtkBillboardTextActor3D,
        vtkCellPicker,
        vtkPolyDataMapper,
        vtkRenderer,
    )
except ImportError as exc:  # pragma: no cover - environment dependent
    QtCore = QtWidgets = None  # type: ignore[assignment]
    _VTK_IMPORT_ERROR: ImportError | None = exc
else:
    _VTK_IMPORT_ERROR = None

from .surface_analysis import (
    PAINT_BLOCKER,
    PAINT_ENFORCER,
    PAINT_NONE,
    SurfaceAnalysis,
    analyze_posed_surface,
    face_concavity,
    mesh_fingerprint,
    rotation_matrix,
    surface_colors,
)

PAINT_MODE_INSPECT = "inspect"
PAINT_MODE_POSE = "pose"
PAINT_MODE_ENFORCER = "enforcer"
PAINT_MODE_BLOCKER = "blocker"
PAINT_MODE_ERASE = "erase"

_POSE_AXES = {
    "x": np.asarray((1.0, 0.0, 0.0)),
    "y": np.asarray((0.0, 1.0, 0.0)),
    "z": np.asarray((0.0, 0.0, 1.0)),
}
_INTERACTIVE_ROTATION_DECIMALS = 2
_INTERACTIVE_HEIGHT_DECIMALS = 2


def _linearized_srgb(value: float) -> float:
    value = float(np.clip(value, 0.0, 1.0))
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _relative_luminance(color: tuple[float, float, float]) -> float:
    red, green, blue = (_linearized_srgb(value) for value in color)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(
    first: tuple[float, float, float], second: tuple[float, float, float]
) -> float:
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def contrasting_grid_color(
    *backgrounds: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Choose black or white for the strongest worst-case background contrast."""

    if not backgrounds:
        raise ValueError("at least one background color is required")
    candidates = ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    return max(
        candidates,
        key=lambda candidate: min(
            _contrast_ratio(candidate, background) for background in backgrounds
        ),
    )


def bed_grid_segments(
    *,
    half_extent: float = 100.0,
    minor_spacing: float = 10.0,
    major_spacing: float = 50.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return disjoint minor, major, and perimeter lines for the build plate."""

    half_extent = float(half_extent)
    minor_spacing = float(minor_spacing)
    major_spacing = float(major_spacing)
    if not all(np.isfinite((half_extent, minor_spacing, major_spacing))):
        raise ValueError("build-plate dimensions must be finite")
    if half_extent <= 0.0 or minor_spacing <= 0.0 or major_spacing <= 0.0:
        raise ValueError("build-plate dimensions must be positive")
    ratio = major_spacing / minor_spacing
    if not np.isclose(ratio, round(ratio), atol=1e-9):
        raise ValueError("major spacing must be a multiple of minor spacing")

    coordinates = np.arange(
        -half_extent, half_extent + minor_spacing * 0.5, minor_spacing, dtype=float
    )
    minor: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    major: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for coordinate in coordinates:
        if np.isclose(abs(coordinate), half_extent, atol=1e-9):
            continue
        target = major if np.isclose(
            coordinate / major_spacing, round(coordinate / major_spacing), atol=1e-9
        ) else minor
        target.extend(
            (
                ((-half_extent, coordinate, 0.0), (half_extent, coordinate, 0.0)),
                ((coordinate, -half_extent, 0.0), (coordinate, half_extent, 0.0)),
            )
        )
    border = (
        ((-half_extent, -half_extent, 0.0), (half_extent, -half_extent, 0.0)),
        ((half_extent, -half_extent, 0.0), (half_extent, half_extent, 0.0)),
        ((half_extent, half_extent, 0.0), (-half_extent, half_extent, 0.0)),
        ((-half_extent, half_extent, 0.0), (-half_extent, -half_extent, 0.0)),
    )
    return (
        np.asarray(minor, dtype=float).reshape((-1, 2, 3)),
        np.asarray(major, dtype=float).reshape((-1, 2, 3)),
        np.asarray(border, dtype=float).reshape((-1, 2, 3)),
    )


def signed_axis_angle_degrees(
    axis: np.ndarray, start: np.ndarray, end: np.ndarray
) -> float:
    """Return the signed angle from start to end around a normalized axis."""

    axis = np.asarray(axis, dtype=float)
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    if axis.shape != (3,) or start.shape != (3,) or end.shape != (3,):
        raise ValueError("axis and drag vectors must contain three coordinates")
    axis_length = float(np.linalg.norm(axis))
    start_length = float(np.linalg.norm(start))
    end_length = float(np.linalg.norm(end))
    if min(axis_length, start_length, end_length) <= 1e-12:
        return 0.0
    axis /= axis_length
    start /= start_length
    end /= end_length
    sine = float(np.dot(axis, np.cross(start, end)))
    cosine = float(np.clip(np.dot(start, end), -1.0, 1.0))
    return math.degrees(math.atan2(sine, cosine))


def _axis_angle_rotation_matrix(axis: np.ndarray, angle_deg: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    length = float(np.linalg.norm(axis))
    if axis.shape != (3,) or length <= 1e-12:
        raise ValueError("rotation axis must be a non-zero 3D vector")
    x, y, z = axis / length
    angle = math.radians(float(angle_deg))
    sine = math.sin(angle)
    cosine = math.cos(angle)
    one_minus_cosine = 1.0 - cosine
    return np.asarray(
        (
            (
                cosine + x * x * one_minus_cosine,
                x * y * one_minus_cosine - z * sine,
                x * z * one_minus_cosine + y * sine,
            ),
            (
                y * x * one_minus_cosine + z * sine,
                cosine + y * y * one_minus_cosine,
                y * z * one_minus_cosine - x * sine,
            ),
            (
                z * x * one_minus_cosine - y * sine,
                z * y * one_minus_cosine + x * sine,
                cosine + z * z * one_minus_cosine,
            ),
        ),
        dtype=float,
    )


def _euler_xyz_degrees(rotation: np.ndarray) -> tuple[float, float, float]:
    """Decompose ``Rz @ Ry @ Rx`` into HolderPro's static-XYZ angles."""

    rotation = np.asarray(rotation, dtype=float)
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError("rotation must be a finite 3 x 3 matrix")
    sine_y = float(np.clip(-rotation[2, 0], -1.0, 1.0))
    y = math.asin(sine_y)
    if abs(math.cos(y)) > 1e-8:
        x = math.atan2(float(rotation[2, 1]), float(rotation[2, 2]))
        z = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
    elif sine_y > 0.0:
        x = math.atan2(float(rotation[0, 1]), float(rotation[0, 2]))
        z = 0.0
    else:
        x = math.atan2(float(-rotation[0, 1]), float(-rotation[0, 2]))
        z = 0.0
    angles = tuple(
        (math.degrees(value) + 180.0) % 360.0 - 180.0
        for value in (x, y, z)
    )
    return angles[0], angles[1], angles[2]


def rotate_euler_about_world_axis(
    rotation_x_deg: float,
    rotation_y_deg: float,
    rotation_z_deg: float,
    axis: np.ndarray,
    angle_deg: float,
) -> tuple[float, float, float]:
    """Compose a visible world-axis rotation and return static-XYZ angles."""

    current = rotation_matrix(rotation_x_deg, rotation_y_deg, rotation_z_deg)
    composed = _axis_angle_rotation_matrix(axis, angle_deg) @ current
    return _euler_xyz_degrees(composed)


def adjusted_bottom_height(
    bottom_height_mm: float,
    vertical_drag_pixels: float,
    *,
    sensitivity_mm_per_pixel: float = 0.15,
) -> float:
    """Convert an upward viewport drag into a printable Z translation."""

    height = float(bottom_height_mm) - float(vertical_drag_pixels) * float(
        sensitivity_mm_per_pixel
    )
    return float(np.clip(height, 0.01, 1000.0))


def point_triangle_distance_squared(point: np.ndarray, triangle: np.ndarray) -> float:
    """Return the exact squared distance from a point to a 3D triangle."""

    point = np.asarray(point, dtype=float)
    triangle = np.asarray(triangle, dtype=float)
    if point.shape != (3,) or triangle.shape != (3, 3):
        raise ValueError("point and triangle must have shapes (3,) and (3, 3)")
    a, b, c = triangle
    ab = b - a
    ac = c - a
    ap = point - a
    d00 = float(np.dot(ab, ab))
    d01 = float(np.dot(ab, ac))
    d11 = float(np.dot(ac, ac))
    d20 = float(np.dot(ap, ab))
    d21 = float(np.dot(ap, ac))
    denominator = d00 * d11 - d01 * d01
    if denominator > np.finfo(float).eps * max(d00 * d11, 1.0):
        v = (d11 * d20 - d01 * d21) / denominator
        w = (d00 * d21 - d01 * d20) / denominator
        if v >= 0.0 and w >= 0.0 and v + w <= 1.0:
            closest = a + v * ab + w * ac
            delta = point - closest
            return float(np.dot(delta, delta))

    starts = triangle
    ends = np.roll(triangle, -1, axis=0)
    edges = ends - starts
    lengths_sq = np.einsum("ij,ij->i", edges, edges)
    parameters = np.zeros(3, dtype=float)
    valid = lengths_sq > 0.0
    parameters[valid] = np.clip(
        np.einsum("ij,ij->i", point - starts[valid], edges[valid]) / lengths_sq[valid],
        0.0,
        1.0,
    )
    closest = starts + parameters[:, None] * edges
    distances_sq = np.einsum("ij,ij->i", point - closest, point - closest)
    return float(distances_sq.min())


def triangle_faces_within_sphere(
    triangles: np.ndarray,
    cell_locator: Any,
    point: np.ndarray,
    radius: float,
) -> np.ndarray:
    """Return every triangle whose surface intersects a 3D brush sphere.

    ``vtkStaticCellLocator`` supplies only a broad bounding-box candidate set.
    The final selection still uses the exact point-to-triangle distance, so a
    brush includes long or off-centre faces whose surface is within its radius.
    """

    triangles = np.asarray(triangles, dtype=float)
    point = np.asarray(point, dtype=float)
    radius = float(radius)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3):
        raise ValueError("triangles must have shape (n, 3, 3)")
    if point.shape != (3,) or not np.isfinite(point).all():
        raise ValueError("brush point must be a finite 3D position")
    if not np.isfinite(radius) or radius <= 0.0:
        raise ValueError("brush radius must be positive and finite")
    low = point - radius
    high = point + radius
    # VTK bounds are interleaved by axis, unlike the (min, min, min, max,
    # max, max) layout used by Rtree. Keeping this ordering explicit avoids
    # dropping triangles to the left or right of the brush centre.
    bounds = (
        float(low[0]),
        float(high[0]),
        float(low[1]),
        float(high[1]),
        float(low[2]),
        float(high[2]),
    )
    if (candidate_query := getattr(cell_locator, "candidate_faces", None)) is not None:
        candidates = np.fromiter(candidate_query(bounds), dtype=np.int64)
    else:
        require_preview_dependencies()
        ids = vtkIdList()
        cell_locator.FindCellsWithinBounds(bounds, ids)
        candidates = np.fromiter(
            (ids.GetId(index) for index in range(ids.GetNumberOfIds())),
            dtype=np.int64,
        )
    if not len(candidates):
        return candidates
    radius_sq = radius * radius
    selected = [
        int(face)
        for face in candidates
        if point_triangle_distance_squared(point, triangles[face]) <= radius_sq + 1e-12
    ]
    return np.asarray(selected, dtype=np.int64)


def _polydata_from_mesh(mesh: trimesh.Trimesh) -> Any:
    """Create VTK triangle polydata without relying on PyVista."""

    require_preview_dependencies()
    vertices = np.ascontiguousarray(mesh.vertices, dtype=np.float64)
    faces = np.ascontiguousarray(mesh.faces, dtype=np.int64)
    points = vtkPoints()
    points.SetData(numpy_to_vtk(vertices, deep=True))
    polydata = vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(_vtk_triangle_cells(faces))
    return polydata


def _vtk_triangle_cells(faces: np.ndarray) -> Any:
    """Create VTK's modern offsets/connectivity representation for triangles."""

    faces = np.ascontiguousarray(faces, dtype=np.int64)
    offsets = np.arange(0, (len(faces) + 1) * 3, 3, dtype=np.int64)
    cells = vtkCellArray()
    cells.SetData(
        numpy_to_vtkIdTypeArray(offsets, deep=True),
        numpy_to_vtkIdTypeArray(faces.ravel(), deep=True),
    )
    return cells


def build_triangle_locator(mesh: trimesh.Trimesh) -> tuple[Any, Any]:
    """Build the VTK spatial index used for painting and face registration.

    The returned polydata must remain alive for the lifetime of the locator,
    which is why callers retain both objects.
    """

    polydata = _polydata_from_mesh(mesh)
    locator = vtkStaticCellLocator()
    locator.SetDataSet(polydata)
    locator.BuildLocator()
    return polydata, locator


def _mesh_from_polydata(polydata: Any) -> trimesh.Trimesh:
    points = polydata.GetPoints()
    if points is None or not polydata.GetNumberOfPolys():
        raise ValueError("VTK decimation produced no triangle geometry")
    vertices = np.asarray(vtk_to_numpy(points.GetData()), dtype=np.float64)
    cells = polydata.GetPolys()
    connectivity = np.asarray(
        vtk_to_numpy(cells.GetConnectivityArray()), dtype=np.int64
    )
    offsets = np.asarray(vtk_to_numpy(cells.GetOffsetsArray()), dtype=np.int64)
    if len(offsets) != polydata.GetNumberOfPolys() + 1 or not np.all(
        np.diff(offsets) == 3
    ):
        raise ValueError("VTK decimation produced non-triangle cells")
    faces = connectivity.reshape((-1, 3))
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def decimate_mesh_for_preview(
    mesh: trimesh.Trimesh,
    *,
    target_face_count: int,
) -> trimesh.Trimesh:
    """Reduce an unusually dense preview with VTK's quadric decimator."""

    target = max(4, int(target_face_count))
    if len(mesh.faces) <= target:
        return mesh.copy()
    decimator = vtkQuadricDecimation()
    decimator.SetInputData(_polydata_from_mesh(mesh))
    decimator.SetTargetReduction(1.0 - target / float(len(mesh.faces)))
    decimator.VolumePreservationOn()
    decimator.Update()
    return _mesh_from_polydata(decimator.GetOutput())


def merged_topology_copy(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Weld duplicate display vertices without changing source face indices.

    Binary STL commonly repeats all three vertices for every triangle.  Keeping
    that triangle soup for painting/export is important, but sending millions
    of duplicate points through every pose update is both wasteful and prevents
    face adjacency from revealing concave pockets.  ``merge_vertices`` only
    rewrites the vertex table and face references; it retains face count/order.
    """

    display = mesh.copy()
    face_count = len(display.faces)
    # UV/normal seams are irrelevant to flat, per-face analysis colors. Merge
    # them explicitly so textured OBJ/3MF inputs receive the same fast topology
    # path as STL triangle soup.
    display.merge_vertices(merge_tex=True, merge_norm=True)
    if len(display.faces) != face_count:
        raise ValueError("preview vertex welding changed the source face order")
    return display


def closest_source_faces(cell_locator: Any, points: np.ndarray) -> np.ndarray:
    """Map preview points to source triangle ids with VTK's static locator."""

    points = np.asarray(points, dtype=np.float64)
    result = np.empty(len(points), dtype=np.int64)
    closest = [0.0, 0.0, 0.0]
    cell_id = reference(0)
    sub_id = reference(0)
    distance_sq = reference(0.0)
    for index, point in enumerate(points):
        cell_locator.FindClosestPoint(
            point,
            closest,
            cell_id,
            sub_id,
            distance_sq,
        )
        result[index] = int(cell_id)
    return result


def require_preview_dependencies() -> None:
    if QtWidgets is None:
        raise RuntimeError(
            "HolderPro's 3D preview dependencies are not installed. Run "
            '`python -m pip install "holderpro[gui]"` and try again.'
        ) from _VTK_IMPORT_ERROR


def load_support_preview_mesh(path: str | Path) -> trimesh.Trimesh:
    """Load generated STL triangles for display, without revalidating export.

    Generation already validates the exact file with both HolderPro's topology
    checks and, for the tangent-shell fallback, PrusaSlicer's own importer. A
    generic coordinate welder may still label that printable fallback as
    non-manifold; rendering does not require it to repeat the manufacturing
    validator's decision.
    """

    mesh = trimesh.load_mesh(Path(path), file_type="stl", process=True)
    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
        raise ValueError("Generated support STL did not load as one mesh")
    if (
        not np.isfinite(np.asarray(mesh.vertices, dtype=float)).all()
        or not np.isfinite(np.asarray(mesh.faces, dtype=float)).all()
    ):
        raise ValueError("Generated support STL contains non-finite geometry")
    return mesh


if QtWidgets is not None:

    class _StableQVTKRenderWindowInteractor(QVTKRenderWindowInteractor):
        """Coalesce Qt paint events that otherwise recurse on macOS/PySide."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            self._render_requested = True
            self._render_in_progress = False
            self._paint_preview: ModelPreviewWidget | None = None
            self._painting = False
            self._posing = False
            self._pose_target: str | None = None
            self._last_pose_position: Any | None = None
            self._last_pose_update = 0.0
            super().__init__(*args, **kwargs)
            self.setMouseTracking(True)
            self._wheel_pose_timer = QtCore.QTimer(self)
            self._wheel_pose_timer.setSingleShot(True)
            self._wheel_pose_timer.setInterval(140)
            self._wheel_pose_timer.timeout.connect(self._finish_wheel_pose)

        def set_paint_preview(self, preview: ModelPreviewWidget) -> None:
            self._paint_preview = preview

        def _is_paint_locked(self) -> bool:
            return bool(
                self._paint_preview is not None
                and self._paint_preview.paint_mode
                in {PAINT_MODE_ENFORCER, PAINT_MODE_BLOCKER, PAINT_MODE_ERASE}
            )

        def _is_pose_mode(self) -> bool:
            return bool(
                self._paint_preview is not None
                and self._paint_preview.paint_mode == PAINT_MODE_POSE
            )

        def _paint_at_event(self, event: Any) -> None:
            assert self._paint_preview is not None
            x, y = self._vtk_event_position(event)
            self._paint_preview.paint_at(x, y)

        def _vtk_event_position(self, event: Any) -> tuple[int, int]:
            position = event.position()
            scale = self._getPixelRatio()
            x = int(round(float(position.x()) * scale))
            y = int(round((self.height() - float(position.y()) - 1.0) * scale))
            return x, y

        def _set_pose_cursor(self, target: str | None, *, active: bool = False) -> None:
            if active:
                cursor = QtCore.Qt.CursorShape.ClosedHandCursor
            elif target in _POSE_AXES:
                cursor = QtCore.Qt.CursorShape.PointingHandCursor
            elif target == "free":
                cursor = QtCore.Qt.CursorShape.OpenHandCursor
            else:
                cursor = QtCore.Qt.CursorShape.ArrowCursor
            self.setCursor(cursor)

        def _update_pose_hover(self, event: Any) -> None:
            assert self._paint_preview is not None
            x, y = self._vtk_event_position(event)
            target = self._paint_preview.pose_target_at(x, y)
            self._paint_preview.set_pose_hover_target(target)
            self._set_pose_cursor(target)

        def _drag_pose_at_event(self, event: Any, *, force: bool = False) -> None:
            assert self._paint_preview is not None
            assert self._last_pose_position is not None
            assert self._pose_target is not None
            current = event.position()
            now = time.monotonic()
            if not force and now - self._last_pose_update < 1.0 / 30.0:
                return
            dx = float(current.x() - self._last_pose_position.x())
            dy = float(current.y() - self._last_pose_position.y())
            if dx == 0.0 and dy == 0.0:
                return
            x, y = self._vtk_event_position(event)
            self._paint_preview.drag_pose_target(
                self._pose_target,
                dx,
                dy,
                display_x=x,
                display_y=y,
            )
            self._last_pose_position = current
            self._last_pose_update = now

        def cancel_pose_drag(self) -> None:
            self._posing = False
            self._pose_target = None
            self._last_pose_position = None
            self._last_pose_update = 0.0

        def _finish_wheel_pose(self) -> None:
            if self._paint_preview is not None:
                self._paint_preview.finalize_pose_analysis()

        def request_render(self) -> None:
            self._render_requested = True
            self.update()

        def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if self._render_in_progress or not self._render_requested:
                event.accept()
                return
            self._render_requested = False
            self._render_in_progress = True
            try:
                self._Iren.Render()
            finally:
                self._render_in_progress = False
            event.accept()

        def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            self._render_requested = True
            super().resizeEvent(event)

        def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if self._is_paint_locked():
                if event.button() == QtCore.Qt.MouseButton.LeftButton:
                    self._painting = True
                    self._paint_at_event(event)
                event.accept()
                return
            if self._is_pose_mode():
                if event.button() == QtCore.Qt.MouseButton.LeftButton:
                    assert self._paint_preview is not None
                    x, y = self._vtk_event_position(event)
                    target = self._paint_preview.begin_pose_drag(
                        x,
                        y,
                        height=bool(
                            event.modifiers()
                            & QtCore.Qt.KeyboardModifier.AltModifier
                        ),
                    )
                    if target is not None:
                        self._posing = True
                        self._pose_target = target
                        self._last_pose_position = event.position()
                        self._last_pose_update = time.monotonic()
                        self._set_pose_cursor(target, active=True)
                        event.accept()
                        return
                # Empty-space drags retain the normal trackball-camera controls.
                super().mousePressEvent(event)
                return
            if (
                self._paint_preview is not None
                and event.button() == QtCore.Qt.MouseButton.LeftButton
            ):
                self._paint_at_event(event)
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if self._is_paint_locked():
                if self._painting and (
                    event.buttons() & QtCore.Qt.MouseButton.LeftButton
                ):
                    self._paint_at_event(event)
                event.accept()
                return
            if self._is_pose_mode():
                if (
                    self._posing
                    and self._last_pose_position is not None
                    and self._pose_target is not None
                    and (event.buttons() & QtCore.Qt.MouseButton.LeftButton)
                ):
                    self._drag_pose_at_event(event)
                    event.accept()
                    return
                if event.buttons() == QtCore.Qt.MouseButton.NoButton:
                    self._update_pose_hover(event)
                super().mouseMoveEvent(event)
                return
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if self._is_paint_locked():
                if event.button() == QtCore.Qt.MouseButton.LeftButton:
                    self._painting = False
                event.accept()
                return
            if self._is_pose_mode():
                if (
                    event.button() == QtCore.Qt.MouseButton.LeftButton
                    and self._posing
                    and self._pose_target is not None
                    and self._last_pose_position is not None
                ):
                    self._drag_pose_at_event(event, force=True)
                    assert self._paint_preview is not None
                    self._paint_preview.end_pose_drag()
                    self.cancel_pose_drag()
                    self._update_pose_hover(event)
                    event.accept()
                    return
                super().mouseReleaseEvent(event)
                return
            super().mouseReleaseEvent(event)

        def wheelEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if self._is_paint_locked():
                event.accept()
                return
            if self._is_pose_mode():
                assert self._paint_preview is not None
                self._paint_preview.translate_pose_z(
                    float(event.angleDelta().y()) / 120.0,
                    interactive=True,
                )
                self._wheel_pose_timer.start()
                event.accept()
                return
            super().wheelEvent(event)

    class ModelPreviewWidget(QtWidgets.QWidget):
        """Render posed surface metrics and paint PrusaSlicer support facets."""

        paintChanged = QtCore.Signal(int, int)
        paintModeChanged = QtCore.Signal(bool)
        interactionModeChanged = QtCore.Signal(str)
        poseEdited = QtCore.Signal(float, float, float, float)
        surfacePicked = QtCore.Signal(float, float, float)

        def __init__(self, parent: Any | None = None) -> None:
            super().__init__(parent)
            self._mesh: trimesh.Trimesh | None = None
            self._display_mesh: trimesh.Trimesh | None = None
            self._pose_bounds_vertices_source = np.empty((0, 3), dtype=float)
            self._display_to_source = np.empty(0, dtype=np.int64)
            self._fingerprint: str | None = None
            self._analysis: SurfaceAnalysis | None = None
            self._analysis_pose: tuple[float, float, float, float] | None = None
            self._source_polydata: Any | None = None
            self._cell_locator: Any | None = None
            self._source_hit_transform = np.eye(4, dtype=float)
            self._concavity: np.ndarray | None = None
            self._paint_states = np.empty(0, dtype=np.uint8)
            self._pose = (0.0, 0.0, 0.0, 25.0)
            self._center_of_mass_source: np.ndarray | None = None
            self._support_mesh: trimesh.Trimesh | None = None
            self._support_pose: tuple[float, float, float, float] | None = None
            self._low_height_fraction = 0.35
            self.paint_mode = PAINT_MODE_INSPECT
            self.brush_radius_mm = 3.0
            self._pose_handle_colors = {
                "x": (0.95, 0.22, 0.18),
                "y": (0.24, 0.84, 0.34),
                "z": (0.24, 0.50, 1.0),
            }
            self._pose_handle_sources: dict[str, Any] = {}
            self._pose_handle_tubes: dict[str, Any] = {}
            self._pose_handle_actors: dict[str, Any] = {}
            self._pose_handle_labels: dict[str, Any] = {}
            self._pose_hover_target: str | None = None
            self._pose_active_target: str | None = None
            self._pose_drag_vector: np.ndarray | None = None
            self._pose_drag_center: np.ndarray | None = None
            self._pose_gizmo_center = np.zeros(3, dtype=float)

            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            self.vtk_widget = _StableQVTKRenderWindowInteractor(self)
            self.vtk_widget.set_paint_preview(self)
            self.vtk_widget.setMinimumSize(540, 460)
            layout.addWidget(self.vtk_widget)

            self.renderer = vtkRenderer()
            self.renderer.SetBackground(0.055, 0.064, 0.075)
            self.renderer.SetBackground2(0.12, 0.14, 0.17)
            self.renderer.GradientBackgroundOn()
            self.vtk_widget.GetRenderWindow().AddRenderer(self.renderer)
            self.interactor = self.vtk_widget.GetRenderWindow().GetInteractor()
            self._interactor_style = vtkInteractorStyleTrackballCamera()
            self.interactor.SetInteractorStyle(self._interactor_style)

            self.polydata = vtkPolyData()
            self.mapper = vtkPolyDataMapper()
            self.mapper.SetInputData(self.polydata)
            self.mapper.SetScalarModeToUseCellData()
            self.mapper.SetColorModeToDirectScalars()
            self.mapper.ScalarVisibilityOn()
            self.actor = vtkActor()
            self.actor.SetMapper(self.mapper)
            self.actor.GetProperty().SetInterpolationToFlat()
            self.actor.GetProperty().SetAmbient(0.42)
            self.actor.GetProperty().SetDiffuse(0.70)
            self.actor.GetProperty().SetSpecular(0.08)
            self.renderer.AddActor(self.actor)

            self._create_pose_gizmo()

            self._support_polydata = vtkPolyData()
            self._support_normals = vtkPolyDataNormals()
            self._support_normals.SetInputData(self._support_polydata)
            self._support_normals.ComputePointNormalsOn()
            self._support_normals.ComputeCellNormalsOff()
            self._support_normals.SplittingOff()
            self._support_normals.ConsistencyOn()
            self._support_normals.AutoOrientNormalsOn()
            self._support_mapper = vtkPolyDataMapper()
            self._support_mapper.SetInputConnection(
                self._support_normals.GetOutputPort()
            )
            self._support_actor = vtkActor()
            self._support_actor.SetMapper(self._support_mapper)
            self._support_actor.GetProperty().SetColor(0.18, 0.78, 0.84)
            self._support_actor.GetProperty().SetOpacity(1.0)
            self._support_actor.GetProperty().SetInterpolationToPhong()
            self._support_actor.GetProperty().SetAmbient(0.20)
            self._support_actor.GetProperty().SetDiffuse(0.82)
            self._support_actor.GetProperty().SetSpecular(0.18)
            self._support_actor.GetProperty().SetSpecularPower(22.0)
            self._support_actor.SetVisibility(False)
            self.renderer.AddActor(self._support_actor)

            self._com_sphere = vtkSphereSource()
            self._com_sphere.SetThetaResolution(24)
            self._com_sphere.SetPhiResolution(16)
            self._com_mapper = vtkPolyDataMapper()
            self._com_mapper.SetInputConnection(self._com_sphere.GetOutputPort())
            self._com_actor = vtkActor()
            self._com_actor.SetMapper(self._com_mapper)
            self._com_actor.GetProperty().SetColor(1.0, 0.88, 0.16)
            self._com_actor.GetProperty().SetAmbient(0.7)
            self._com_actor.GetProperty().SetDiffuse(0.5)
            self._com_actor.SetVisibility(False)
            self.renderer.AddActor(self._com_actor)

            self._com_line = vtkLineSource()
            self._com_line_mapper = vtkPolyDataMapper()
            self._com_line_mapper.SetInputConnection(self._com_line.GetOutputPort())
            self._com_line_actor = vtkActor()
            self._com_line_actor.SetMapper(self._com_line_mapper)
            self._com_line_actor.GetProperty().SetColor(1.0, 0.78, 0.08)
            self._com_line_actor.GetProperty().SetLineWidth(2.5)
            self._com_line_actor.SetVisibility(False)
            self.renderer.AddActor(self._com_line_actor)

            self._com_bed_sphere = vtkSphereSource()
            self._com_bed_sphere.SetThetaResolution(20)
            self._com_bed_sphere.SetPhiResolution(12)
            self._com_bed_mapper = vtkPolyDataMapper()
            self._com_bed_mapper.SetInputConnection(
                self._com_bed_sphere.GetOutputPort()
            )
            self._com_bed_actor = vtkActor()
            self._com_bed_actor.SetMapper(self._com_bed_mapper)
            self._com_bed_actor.GetProperty().SetColor(1.0, 0.58, 0.06)
            self._com_bed_actor.SetVisibility(False)
            self.renderer.AddActor(self._com_bed_actor)

            self._com_label = vtkBillboardTextActor3D()
            self._com_label.SetInput("CENTER OF MASS")
            self._com_label.GetTextProperty().SetColor(1.0, 0.9, 0.25)
            self._com_label.GetTextProperty().SetFontSize(16)
            self._com_label.GetTextProperty().BoldOn()
            self._com_label.SetVisibility(False)
            self.renderer.AddActor(self._com_label)

            (
                self._bed_minor_actor,
                self._bed_major_actor,
                self._bed_border_actor,
            ) = self._make_bed_actors()
            self._bed_actors = (
                self._bed_minor_actor,
                self._bed_major_actor,
                self._bed_border_actor,
            )
            for bed_actor in self._bed_actors:
                self.renderer.AddActor(bed_actor)
            self._bed_label = vtkBillboardTextActor3D()
            self._bed_label.SetInput("Z=0  |  200 x 200 mm BUILD PLATE")
            self._bed_label.SetPosition(-98.0, -98.0, 0.25)
            self._bed_label.GetTextProperty().SetFontSize(14)
            self._bed_label.GetTextProperty().BoldOn()
            self._bed_label.PickableOff()
            self._bed_label.UseBoundsOff()
            self.renderer.AddActor(self._bed_label)
            self._refresh_bed_contrast()
            self.picker = vtkCellPicker()
            self.picker.SetTolerance(0.0006)
            self.picker.PickFromListOn()
            self.picker.AddPickList(self.actor)

            self._pose_picker = vtkCellPicker()
            self._pose_picker.SetTolerance(0.004)
            self._pose_picker.PickFromListOn()
            for pose_actor in self._pose_handle_actors.values():
                self._pose_picker.AddPickList(pose_actor)
            # One depth-aware picker contains both model and handles. Separate
            # pickers would let an occluded ring win through the visible model.
            self._pose_picker.AddPickList(self.actor)

            self.vtk_widget.Initialize()

        @staticmethod
        def _line_actor(segments: np.ndarray, *, opacity: float, width: float) -> Any:
            segments = np.asarray(segments, dtype=float)
            points = vtkPoints()
            points.SetData(
                numpy_to_vtk(
                    np.ascontiguousarray(segments.reshape((-1, 3))), deep=True
                )
            )
            lines = vtkCellArray()
            for index in range(len(segments)):
                lines.InsertNextCell(2)
                lines.InsertCellPoint(index * 2)
                lines.InsertCellPoint(index * 2 + 1)
            polydata = vtkPolyData()
            polydata.SetPoints(points)
            polydata.SetLines(lines)
            mapper = vtkPolyDataMapper()
            mapper.SetInputData(polydata)
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetOpacity(float(opacity))
            actor.GetProperty().SetLineWidth(float(width))
            actor.GetProperty().LightingOff()
            actor.PickableOff()
            actor.UseBoundsOff()
            return actor

        @classmethod
        def _make_bed_actors(cls) -> tuple[Any, Any, Any]:
            minor, major, border = bed_grid_segments()
            return (
                cls._line_actor(minor, opacity=0.30, width=1.0),
                cls._line_actor(major, opacity=0.62, width=1.8),
                cls._line_actor(border, opacity=0.95, width=3.0),
            )

        def _create_pose_gizmo(self) -> None:
            for axis, normal in _POSE_AXES.items():
                ring = vtkRegularPolygonSource()
                ring.SetNumberOfSides(96)
                ring.SetNormal(*normal)
                ring.GeneratePolygonOff()
                ring.SetRadius(10.0)

                tube = vtkTubeFilter()
                tube.SetInputConnection(ring.GetOutputPort())
                tube.SetNumberOfSides(12)
                tube.SetRadius(0.5)
                tube.CappingOn()

                mapper = vtkPolyDataMapper()
                mapper.SetInputConnection(tube.GetOutputPort())
                actor = vtkActor()
                actor.SetMapper(mapper)
                actor.GetProperty().SetColor(*self._pose_handle_colors[axis])
                actor.GetProperty().SetAmbient(1.0)
                actor.GetProperty().SetDiffuse(0.0)
                actor.GetProperty().SetOpacity(0.92)
                actor.SetVisibility(False)
                actor.PickableOff()
                actor.UseBoundsOff()
                self.renderer.AddActor(actor)

                label = vtkBillboardTextActor3D()
                label.SetInput(axis.upper())
                label.GetTextProperty().SetColor(*self._pose_handle_colors[axis])
                label.GetTextProperty().SetFontSize(18)
                label.GetTextProperty().BoldOn()
                label.SetVisibility(False)
                label.PickableOff()
                label.UseBoundsOff()
                self.renderer.AddActor(label)

                self._pose_handle_sources[axis] = ring
                self._pose_handle_tubes[axis] = tube
                self._pose_handle_actors[axis] = actor
                self._pose_handle_labels[axis] = label

        def set_background_colors(
            self,
            bottom: tuple[float, float, float],
            top: tuple[float, float, float] | None = None,
        ) -> None:
            """Set the preview background and preserve readable plate contrast."""

            bottom = (
                float(np.clip(bottom[0], 0.0, 1.0)),
                float(np.clip(bottom[1], 0.0, 1.0)),
                float(np.clip(bottom[2], 0.0, 1.0)),
            )
            self.renderer.SetBackground(*bottom)
            if top is None:
                self.renderer.GradientBackgroundOff()
                self.renderer.SetBackground2(*bottom)
            else:
                top = (
                    float(np.clip(top[0], 0.0, 1.0)),
                    float(np.clip(top[1], 0.0, 1.0)),
                    float(np.clip(top[2], 0.0, 1.0)),
                )
                self.renderer.SetBackground2(*top)
                self.renderer.GradientBackgroundOn()
            self._refresh_bed_contrast()
            self.vtk_widget.request_render()

        def _refresh_bed_contrast(self) -> None:
            background = self.renderer.GetBackground()
            backgrounds: list[tuple[float, float, float]] = [
                (float(background[0]), float(background[1]), float(background[2]))
            ]
            if self.renderer.GetGradientBackground():
                top = self.renderer.GetBackground2()
                backgrounds.append(
                    (float(top[0]), float(top[1]), float(top[2]))
                )
            color = contrasting_grid_color(*backgrounds)
            for actor in self._bed_actors:
                actor.GetProperty().SetColor(*color)
            self._bed_label.GetTextProperty().SetColor(*color)

        def _set_pose_gizmo_visible(self, visible: bool) -> None:
            visible = bool(visible and self._mesh is not None)
            for actor in self._pose_handle_actors.values():
                actor.SetVisibility(visible)
                actor.SetPickable(visible)
            for label in self._pose_handle_labels.values():
                label.SetVisibility(visible)

        def _refresh_pose_handle_styles(self) -> None:
            for axis, actor in self._pose_handle_actors.items():
                if axis == self._pose_active_target:
                    color = (1.0, 0.88, 0.12)
                    opacity = 1.0
                elif axis == self._pose_hover_target:
                    color = (1.0, 1.0, 1.0)
                    opacity = 1.0
                else:
                    color = self._pose_handle_colors[axis]
                    opacity = 0.92
                actor.GetProperty().SetColor(*color)
                actor.GetProperty().SetOpacity(opacity)
                self._pose_handle_labels[axis].GetTextProperty().SetColor(*color)

        def _update_pose_gizmo(self, bounds: np.ndarray) -> None:
            bounds = np.asarray(bounds, dtype=float)
            if bounds.shape != (2, 3):
                raise ValueError("pose gizmo bounds must have shape (2, 3)")
            center = bounds.mean(axis=0)
            extents = np.ptp(bounds, axis=0)
            radius = max(6.0, float(extents.max()) * 0.56)
            tube_radius = float(np.clip(radius * 0.022, 0.38, 2.2))
            self._pose_gizmo_center = center
            label_margin = float(np.clip(extents.max() * 0.08, 3.0, 12.0))
            self._bed_label.SetPosition(
                float(np.clip(bounds[0, 0] - label_margin, -96.0, 96.0)),
                float(np.clip(bounds[0, 1] - label_margin, -96.0, 96.0)),
                0.25,
            )
            for axis in _POSE_AXES:
                source = self._pose_handle_sources[axis]
                source.SetCenter(*center)
                source.SetRadius(radius)
                source.Modified()
                tube = self._pose_handle_tubes[axis]
                tube.SetRadius(tube_radius)
                tube.Modified()
            label_positions = {
                "x": center + np.asarray((0.0, radius * 1.04, 0.0)),
                "y": center + np.asarray((0.0, 0.0, radius * 1.04)),
                "z": center + np.asarray((radius * 1.04, 0.0, 0.0)),
            }
            for axis, position in label_positions.items():
                self._pose_handle_labels[axis].SetPosition(*position)
            self._set_pose_gizmo_visible(self.paint_mode == PAINT_MODE_POSE)

        @property
        def mesh(self) -> trimesh.Trimesh | None:
            return self._mesh

        @property
        def fingerprint(self) -> str | None:
            return self._fingerprint

        @property
        def face_count(self) -> int:
            return len(self._paint_states)

        def load_mesh(self, mesh: trimesh.Trimesh) -> None:
            if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
                raise ValueError("The preview model contains no triangle faces")
            self._mesh = mesh.copy()
            self.clear_supports(render=False)
            self._fingerprint = mesh_fingerprint(self._mesh)
            self._paint_states = np.zeros(len(mesh.faces), dtype=np.uint8)
            self._source_polydata, self._cell_locator = build_triangle_locator(
                self._mesh
            )

            # Weld only the rendering/topology copy. Source face order and the
            # exact export mesh remain untouched, while STL triangle soup no
            # longer forces millions of duplicate points through every drag.
            topology_mesh = merged_topology_copy(self._mesh)
            self._pose_bounds_vertices_source = np.asarray(
                topology_mesh.vertices, dtype=float
            )

            # Concavity does not change under rigid pose rotations. Computing
            # it on the welded topology also restores real adjacency for STL.
            self._concavity = face_concavity(topology_mesh)
            self._center_of_mass_source = self._calculate_center_of_mass(self._mesh)

            # Keep ordinary and moderately dense reference models at their
            # original resolution. Besides improving the view, this makes each
            # visible/pickable triangle correspond directly to a source face.
            # Only protect the interactive renderer from exceptionally large
            # meshes, and retain far more detail when that safeguard is needed.
            if len(topology_mesh.faces) > 1_750_000:
                self._display_mesh = decimate_mesh_for_preview(
                    topology_mesh,
                    target_face_count=1_250_000,
                )
                self._display_to_source = closest_source_faces(
                    self._cell_locator,
                    self._display_mesh.triangles_center,
                )
            else:
                self._display_mesh = topology_mesh
                self._display_to_source = np.arange(
                    len(self._mesh.faces), dtype=np.int64
                )

            faces = np.asarray(self._display_mesh.faces, dtype=np.int64)
            self.polydata.SetPolys(_vtk_triangle_cells(faces))
            self.set_pose(*self._pose)
            self.fit_camera()
            self.paintChanged.emit(0, 0)

        @staticmethod
        def _calculate_center_of_mass(mesh: trimesh.Trimesh) -> np.ndarray:
            if mesh.is_volume:
                center = np.asarray(mesh.center_mass, dtype=float)
                if center.shape == (3,) and np.isfinite(center).all():
                    return center
            areas = np.asarray(mesh.area_faces, dtype=float)
            centers = np.asarray(mesh.triangles_center, dtype=float)
            if len(areas) and np.isfinite(areas).all() and float(areas.sum()) > 0.0:
                return np.average(centers, axis=0, weights=areas)
            return np.asarray(mesh.vertices, dtype=float).mean(axis=0)

        @staticmethod
        def _set_polydata_mesh(polydata: Any, mesh: trimesh.Trimesh) -> None:
            vertices = np.ascontiguousarray(mesh.vertices, dtype=np.float64)
            faces = np.asarray(mesh.faces, dtype=np.int64)
            points = vtkPoints()
            points.SetData(numpy_to_vtk(vertices, deep=True))
            polydata.SetPoints(points)
            polydata.SetPolys(_vtk_triangle_cells(faces))
            polydata.Modified()

        def load_support_mesh(self, mesh: trimesh.Trimesh) -> None:
            """Display a generated support solid in preview coordinates."""

            if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
                raise ValueError("The generated support file contains no triangles")
            display = mesh
            # A few hundred thousand welded triangles render smoothly in VTK
            # and preserve every small Organic branch. Decimate only unusually
            # large outputs, and reject a preview approximation that changes
            # volume or bounds materially.
            if len(display.faces) > 750_000:
                candidate = decimate_mesh_for_preview(
                    display,
                    target_face_count=300_000,
                )
                source_volume = abs(float(display.volume))
                candidate_volume = abs(float(candidate.volume))
                bounds_error = float(
                    np.max(
                        np.abs(
                            np.asarray(candidate.bounds) - np.asarray(display.bounds)
                        )
                    )
                )
                volume_ratio = (
                    candidate_volume / source_volume if source_volume > 0.0 else 0.0
                )
                if 0.97 <= volume_ratio <= 1.03 and bounds_error <= 0.5:
                    display = candidate
            self._support_mesh = mesh
            self._support_pose = self._pose
            self._set_polydata_mesh(self._support_polydata, display)
            self._support_normals.Modified()
            self._support_actor.SetVisibility(True)
            self.fit_camera()

        def clear_supports(self, *, render: bool = True) -> None:
            self._support_mesh = None
            self._support_pose = None
            if hasattr(self, "_support_actor"):
                self._support_actor.SetVisibility(False)
            if render and hasattr(self, "vtk_widget"):
                self.vtk_widget.request_render()

        def set_pose(
            self,
            rotation_x_deg: float,
            rotation_y_deg: float,
            rotation_z_deg: float,
            bottom_height_mm: float,
            *,
            update_analysis: bool = True,
        ) -> None:
            new_pose = (
                float(rotation_x_deg),
                float(rotation_y_deg),
                float(rotation_z_deg),
                float(bottom_height_mm),
            )
            if self._support_pose is not None and new_pose != self._support_pose:
                self.clear_supports(render=False)
            self._pose = new_pose
            if self._mesh is None:
                return
            assert self._display_mesh is not None
            source_center = np.asarray(self._mesh.bounds, dtype=float).mean(axis=0)
            rotation = rotation_matrix(*self._pose[:3])
            posed_bounds_vertices = (
                self._pose_bounds_vertices_source - source_center
            ) @ rotation.T
            unshifted_bounds = np.stack(
                (
                    posed_bounds_vertices.min(axis=0),
                    posed_bounds_vertices.max(axis=0),
                )
            )
            translation = np.asarray(
                (
                    -unshifted_bounds[:, 0].mean(),
                    -unshifted_bounds[:, 1].mean(),
                    self._pose[3] - unshifted_bounds[0, 2],
                ),
                dtype=float,
            )
            inverse_rotation = rotation.T
            self._source_hit_transform = np.eye(4, dtype=float)
            self._source_hit_transform[:3, :3] = inverse_rotation
            self._source_hit_transform[:3, 3] = (
                source_center - inverse_rotation @ translation
            )
            display_source_vertices = np.asarray(
                self._display_mesh.vertices, dtype=float
            )
            if np.shares_memory(
                display_source_vertices, self._pose_bounds_vertices_source
            ):
                vertices = posed_bounds_vertices + translation
            else:
                vertices = (
                    display_source_vertices - source_center
                ) @ rotation.T + translation
            points = vtkPoints()
            points.SetData(numpy_to_vtk(np.ascontiguousarray(vertices), deep=True))
            self.polydata.SetPoints(points)
            self.polydata.Modified()
            assert self._center_of_mass_source is not None
            center_of_mass = (
                self._center_of_mass_source - source_center
            ) @ rotation.T + translation
            posed_bounds = unshifted_bounds + translation
            diagonal = float(np.linalg.norm(np.ptp(posed_bounds, axis=0)))
            marker_radius = max(0.9, min(3.0, diagonal * 0.014))
            self._com_sphere.SetCenter(*center_of_mass)
            self._com_sphere.SetRadius(marker_radius)
            self._com_sphere.Modified()
            self._com_line.SetPoint1(center_of_mass[0], center_of_mass[1], 0.0)
            self._com_line.SetPoint2(*center_of_mass)
            self._com_line.Modified()
            self._com_bed_sphere.SetCenter(center_of_mass[0], center_of_mass[1], 0.15)
            self._com_bed_sphere.SetRadius(max(0.55, marker_radius * 0.48))
            self._com_bed_sphere.Modified()
            self._com_label.SetPosition(
                center_of_mass[0] + marker_radius * 1.35,
                center_of_mass[1],
                center_of_mass[2] + marker_radius * 1.15,
            )
            self._com_label.SetInput(
                "CENTER OF MASS\n"
                f"({center_of_mass[0]:.1f}, {center_of_mass[1]:.1f}, "
                f"{center_of_mass[2]:.1f}) mm"
            )
            for actor in (
                self._com_actor,
                self._com_line_actor,
                self._com_bed_actor,
                self._com_label,
            ):
                actor.SetVisibility(True)
            self._update_pose_gizmo(posed_bounds)
            if update_analysis:
                display_faces = np.asarray(self._display_mesh.faces, dtype=np.int64)
                centers = vertices[display_faces].mean(axis=1)
                normals = (
                    np.asarray(self._display_mesh.face_normals, dtype=float)
                    @ rotation.T
                )
                assert self._concavity is not None
                self._analysis = analyze_posed_surface(
                    self._display_mesh,
                    centers,
                    normals,
                    concavity=self._concavity[self._display_to_source],
                )
                self._analysis_pose = self._pose
                self._update_colors()
            else:
                self.vtk_widget.request_render()

        def set_low_height_fraction(self, value: float) -> None:
            self._low_height_fraction = float(np.clip(value, 0.02, 1.0))
            self._update_colors()

        def set_paint_mode(self, mode: str) -> None:
            if mode not in {
                PAINT_MODE_INSPECT,
                PAINT_MODE_POSE,
                PAINT_MODE_ENFORCER,
                PAINT_MODE_BLOCKER,
                PAINT_MODE_ERASE,
            }:
                raise ValueError(f"Unknown paint mode: {mode}")
            self.vtk_widget.cancel_pose_drag()
            self.end_pose_drag()
            self.set_pose_hover_target(None)
            self.paint_mode = mode
            locked = mode in {
                PAINT_MODE_ENFORCER,
                PAINT_MODE_BLOCKER,
                PAINT_MODE_ERASE,
            }
            self._set_pose_gizmo_visible(mode == PAINT_MODE_POSE)
            self.vtk_widget.setCursor(
                QtCore.Qt.CursorShape.CrossCursor
                if locked
                else QtCore.Qt.CursorShape.ArrowCursor
            )
            self.paintModeChanged.emit(locked)
            self.interactionModeChanged.emit(mode)
            self.vtk_widget.request_render()

        def _display_ray(self, x: int, y: int) -> tuple[np.ndarray, np.ndarray]:
            world_points: list[np.ndarray] = []
            for depth in (0.0, 1.0):
                self.renderer.SetDisplayPoint(float(x), float(y), depth)
                self.renderer.DisplayToWorld()
                homogeneous = np.asarray(self.renderer.GetWorldPoint(), dtype=float)
                if abs(float(homogeneous[3])) <= 1e-12:
                    raise ValueError("could not project the pointer into the 3D view")
                world_points.append(homogeneous[:3] / homogeneous[3])
            return world_points[0], world_points[1]

        def _axis_plane_vector(self, axis: str, x: int, y: int) -> np.ndarray | None:
            center = (
                self._pose_drag_center
                if self._pose_drag_center is not None
                else self._pose_gizmo_center
            )
            normal = _POSE_AXES[axis]
            near, far = self._display_ray(x, y)
            direction = far - near
            denominator = float(np.dot(normal, direction))
            if abs(denominator) <= 1e-10:
                return None
            distance = float(np.dot(normal, center - near) / denominator)
            point = near + direction * distance
            vector = point - center
            vector -= normal * float(np.dot(normal, vector))
            length = float(np.linalg.norm(vector))
            return None if length <= 1e-10 else vector / length

        def pose_target_at(self, x: int, y: int) -> str | None:
            """Return the visible pose handle or model under a viewport point."""

            if self.paint_mode != PAINT_MODE_POSE or self._mesh is None:
                return None
            if self._pose_picker.Pick(float(x), float(y), 0.0, self.renderer):
                picked = self._pose_picker.GetActor()
                for axis, actor in self._pose_handle_actors.items():
                    if picked == actor:
                        return axis
                if picked == self.actor:
                    return "free"
            return None

        def set_pose_hover_target(self, target: str | None) -> None:
            target = target if target in _POSE_AXES else None
            if target == self._pose_hover_target:
                return
            self._pose_hover_target = target
            self._refresh_pose_handle_styles()
            self.vtk_widget.request_render()

        def begin_pose_drag(
            self, x: int, y: int, *, height: bool = False
        ) -> str | None:
            """Start a model-only, axis-handle, or height pose interaction."""

            if self.paint_mode != PAINT_MODE_POSE or self._mesh is None:
                return None
            target = "height" if height else self.pose_target_at(x, y)
            if target is None:
                return None
            self._pose_active_target = target
            self._pose_drag_center = self._pose_gizmo_center.copy()
            self._pose_drag_vector = None
            if target in _POSE_AXES:
                picked = np.asarray(self._pose_picker.GetPickPosition(), dtype=float)
                axis = _POSE_AXES[target]
                vector = picked - self._pose_drag_center
                vector -= axis * float(np.dot(axis, vector))
                length = float(np.linalg.norm(vector))
                self._pose_drag_vector = (
                    vector / length
                    if length > 1e-10
                    else self._axis_plane_vector(target, x, y)
                )
            self._refresh_pose_handle_styles()
            self.vtk_widget.request_render()
            return target

        def drag_pose_target(
            self,
            target: str,
            dx: float,
            dy: float,
            *,
            display_x: int,
            display_y: int,
        ) -> None:
            """Apply one throttled pose step to the selected visible target."""

            if target == "height":
                self.translate_pose_z_drag(dy, interactive=True)
                return
            if target == "free":
                self.rotate_pose_drag(dx, dy, interactive=True)
                return
            if target not in _POSE_AXES:
                return
            current = self._axis_plane_vector(target, display_x, display_y)
            if self._pose_drag_vector is not None and current is not None:
                angle = signed_axis_angle_degrees(
                    _POSE_AXES[target], self._pose_drag_vector, current
                )
                # Losing pointer-plane intersection near an edge-on ring can
                # otherwise produce a single implausibly large jump.
                if abs(angle) <= 60.0:
                    self.rotate_pose_axis(target, angle, interactive=True)
                    self._pose_drag_vector = current
                    return
            fallback = dy if target == "x" else dx
            self.rotate_pose_axis(
                target, float(fallback) * 0.35, interactive=True
            )
            self._pose_drag_vector = current

        def finalize_pose_analysis(self) -> None:
            """Refresh pose-dependent surface colors after a fast interaction."""

            if self._mesh is not None and self._analysis_pose != self._pose:
                self.set_pose(*self._pose, update_analysis=True)

        def end_pose_drag(self) -> None:
            self.finalize_pose_analysis()
            self._pose_active_target = None
            self._pose_drag_vector = None
            self._pose_drag_center = None
            self._refresh_pose_handle_styles()
            if hasattr(self, "vtk_widget"):
                self.vtk_widget.request_render()

        def rotate_pose_axis(
            self, axis: str, angle_deg: float, *, interactive: bool = False
        ) -> None:
            """Rotate the printable model pose around one explicit world axis."""

            if axis not in _POSE_AXES:
                raise ValueError(f"Unknown pose axis: {axis}")
            x_deg, y_deg, z_deg, bottom_height = self._pose
            x_deg, y_deg, z_deg = rotate_euler_about_world_axis(
                x_deg,
                y_deg,
                z_deg,
                _POSE_AXES[axis],
                angle_deg,
            )
            x_deg, y_deg, z_deg = (
                round(value, _INTERACTIVE_ROTATION_DECIMALS)
                for value in (x_deg, y_deg, z_deg)
            )
            values = (x_deg, y_deg, z_deg, bottom_height)
            self.set_pose(*values, update_analysis=not interactive)
            self.poseEdited.emit(*values)

        def rotate_pose_drag(
            self,
            dx: float,
            dy: float,
            *,
            around_z: bool = False,
            interactive: bool = False,
        ) -> None:
            """Freely rotate the printable pose by dragging directly on the model."""

            x_deg, y_deg, z_deg, bottom_height = self._pose
            sensitivity = 0.35
            axes_and_angles: tuple[tuple[np.ndarray, float], ...]
            if around_z:
                axes_and_angles = ((_POSE_AXES["z"], float(dx) * sensitivity),)
            else:
                camera = self.renderer.GetActiveCamera()
                position = np.asarray(camera.GetPosition(), dtype=float)
                focal = np.asarray(camera.GetFocalPoint(), dtype=float)
                forward = focal - position
                forward /= max(float(np.linalg.norm(forward)), 1e-12)
                up = np.asarray(camera.GetViewUp(), dtype=float)
                up -= forward * float(np.dot(up, forward))
                up /= max(float(np.linalg.norm(up)), 1e-12)
                right = np.cross(forward, up)
                right /= max(float(np.linalg.norm(right)), 1e-12)
                axes_and_angles = (
                    (up, float(dx) * sensitivity),
                    (right, float(dy) * sensitivity),
                )
            rotation = rotation_matrix(x_deg, y_deg, z_deg)
            for axis, angle in axes_and_angles:
                rotation = _axis_angle_rotation_matrix(axis, angle) @ rotation
            x_deg, y_deg, z_deg = _euler_xyz_degrees(rotation)
            x_deg, y_deg, z_deg = (
                round(value, _INTERACTIVE_ROTATION_DECIMALS)
                for value in (x_deg, y_deg, z_deg)
            )
            self.set_pose(
                x_deg,
                y_deg,
                z_deg,
                bottom_height,
                update_analysis=not interactive,
            )
            self.poseEdited.emit(x_deg, y_deg, z_deg, bottom_height)

        def translate_pose_z_drag(
            self, vertical_pixels: float, *, interactive: bool = False
        ) -> None:
            """Raise or lower the printable model from an Option/Alt-drag."""

            x_deg, y_deg, z_deg, bottom_height = self._pose
            new_height = adjusted_bottom_height(bottom_height, vertical_pixels)
            self.translate_pose_z(
                new_height - bottom_height, interactive=interactive
            )

        def translate_pose_z(
            self, delta_mm: float, *, interactive: bool = False
        ) -> None:
            """Move the model vertically while preserving its rotation."""

            x_deg, y_deg, z_deg, bottom_height = self._pose
            bottom_height = float(
                np.clip(bottom_height + float(delta_mm), 0.01, 1000.0)
            )
            bottom_height = round(bottom_height, _INTERACTIVE_HEIGHT_DECIMALS)
            self.set_pose(
                x_deg,
                y_deg,
                z_deg,
                bottom_height,
                update_analysis=not interactive,
            )
            self.poseEdited.emit(x_deg, y_deg, z_deg, bottom_height)

        def set_brush_radius(self, radius_mm: float) -> None:
            self.brush_radius_mm = max(0.05, float(radius_mm))

        def _update_colors(self) -> None:
            if self._analysis is None:
                return
            colors = surface_colors(
                self._analysis,
                self._paint_states[self._display_to_source],
                low_height_fraction=self._low_height_fraction,
            )
            vtk_colors = numpy_to_vtk(
                colors,
                deep=True,
                array_type=VTK_UNSIGNED_CHAR,
            )
            vtk_colors.SetName("Surface and support paint")
            vtk_colors.SetNumberOfComponents(3)
            self.polydata.GetCellData().SetScalars(vtk_colors)
            self.polydata.GetCellData().Modified()
            self.polydata.Modified()
            self.vtk_widget.request_render()

        def paint_at(self, x: int, y: int) -> None:
            if self._mesh is None or self._analysis is None:
                return
            if not self.picker.Pick(float(x), float(y), 0.0, self.renderer):
                return
            display_face = int(self.picker.GetCellId())
            if display_face < 0 or display_face >= len(self._display_to_source):
                return
            seed = int(self._display_to_source[display_face])
            hit = np.asarray(self.picker.GetPickPosition(), dtype=float)
            selected = self._brush_faces(seed, hit)
            state = {
                PAINT_MODE_ENFORCER: PAINT_ENFORCER,
                PAINT_MODE_BLOCKER: PAINT_BLOCKER,
                PAINT_MODE_ERASE: PAINT_NONE,
            }.get(self.paint_mode)
            if state is None:
                self._emit_surface(display_face)
                return
            self.clear_supports(render=False)
            self._paint_states[selected] = state
            self._update_colors()
            self._emit_surface(display_face)
            self.paintChanged.emit(
                int(np.count_nonzero(self._paint_states == PAINT_ENFORCER)),
                int(np.count_nonzero(self._paint_states == PAINT_BLOCKER)),
            )

        def _brush_faces(self, seed: int, hit: np.ndarray) -> np.ndarray:
            assert self._mesh is not None
            assert self._cell_locator is not None
            source_hit = (
                np.append(np.asarray(hit, dtype=float), 1.0)
                @ self._source_hit_transform.T
            )[:3]
            selected = triangle_faces_within_sphere(
                self._mesh.triangles,
                self._cell_locator,
                source_hit,
                self.brush_radius_mm,
            )
            if seed not in selected:
                selected = np.append(selected, seed)
            return np.unique(selected)

        def _emit_surface(self, face: int) -> None:
            assert self._analysis is not None
            self.surfacePicked.emit(
                float(self._analysis.underside_angle_deg[face]),
                float(self._analysis.relative_height[face]),
                float(self._analysis.concavity[face]),
            )

        def clear_paint(self) -> None:
            if not len(self._paint_states):
                return
            self.clear_supports(render=False)
            self._paint_states.fill(PAINT_NONE)
            self._update_colors()
            self.paintChanged.emit(0, 0)

        def painted_faces(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
            enforcers = tuple(
                int(value)
                for value in np.flatnonzero(self._paint_states == PAINT_ENFORCER)
            )
            blockers = tuple(
                int(value)
                for value in np.flatnonzero(self._paint_states == PAINT_BLOCKER)
            )
            return enforcers, blockers

        def fit_camera(self) -> None:
            if self._mesh is None:
                return
            self.renderer.ResetCamera(*self._content_bounds())
            self.renderer.ResetCameraClippingRange()
            self.vtk_widget.request_render()

        def _content_bounds(self) -> tuple[float, ...]:
            """Return model/support bounds without the oversized bed grid."""

            model = np.asarray(self.actor.GetBounds(), dtype=float).reshape(3, 2)
            if not self._support_actor.GetVisibility():
                return tuple(float(value) for value in model.ravel())
            support = np.asarray(self._support_actor.GetBounds(), dtype=float).reshape(
                3, 2
            )
            combined = np.column_stack(
                (
                    np.minimum(model[:, 0], support[:, 0]),
                    np.maximum(model[:, 1], support[:, 1]),
                )
            )
            return tuple(float(value) for value in combined.ravel())

        def view_isometric(self) -> None:
            if self._mesh is None:
                return
            self.renderer.ResetCamera(*self._content_bounds())
            camera = self.renderer.GetActiveCamera()
            focal = np.asarray(camera.GetFocalPoint())
            distance = float(camera.GetDistance())
            direction = np.array((1.0, -1.0, 0.72))
            direction /= np.linalg.norm(direction)
            camera.SetPosition(*(focal + direction * distance))
            camera.SetViewUp(0.0, 0.0, 1.0)
            self.renderer.ResetCameraClippingRange()
            self.vtk_widget.request_render()

        def view_under_isometric(self) -> None:
            if self._mesh is None:
                return
            self.renderer.ResetCamera(*self._content_bounds())
            camera = self.renderer.GetActiveCamera()
            focal = np.asarray(camera.GetFocalPoint())
            distance = float(camera.GetDistance())
            direction = np.array((1.0, -1.0, -0.72))
            direction /= np.linalg.norm(direction)
            camera.SetPosition(*(focal + direction * distance))
            camera.SetViewUp(0.0, 0.0, 1.0)
            self.renderer.ResetCameraClippingRange()
            self.vtk_widget.request_render()

        def view_bottom(self) -> None:
            if self._mesh is None:
                return
            self.renderer.ResetCamera(*self._content_bounds())
            camera = self.renderer.GetActiveCamera()
            focal = np.asarray(camera.GetFocalPoint())
            distance = float(camera.GetDistance())
            camera.SetPosition(focal[0], focal[1], focal[2] - distance)
            camera.SetViewUp(0.0, 1.0, 0.0)
            self.renderer.ResetCameraClippingRange()
            self.vtk_widget.request_render()

        def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            self.vtk_widget.Finalize()
            event.accept()


else:

    class ModelPreviewWidget:  # type: ignore[no-redef]  # pragma: no cover
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            require_preview_dependencies()


__all__ = [
    "ModelPreviewWidget",
    "PAINT_MODE_BLOCKER",
    "PAINT_MODE_ENFORCER",
    "PAINT_MODE_ERASE",
    "PAINT_MODE_INSPECT",
    "PAINT_MODE_POSE",
    "build_triangle_locator",
    "closest_source_faces",
    "decimate_mesh_for_preview",
    "load_support_preview_mesh",
    "merged_topology_copy",
    "require_preview_dependencies",
    "rotate_euler_about_world_axis",
    "signed_axis_angle_degrees",
]
