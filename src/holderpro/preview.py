"""VTK-backed interactive model preview and support-facet painting widget."""

from __future__ import annotations

from pathlib import Path
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
    from vtkmodules.vtkFiltersCore import vtkPolyDataNormals, vtkQuadricDecimation
    from vtkmodules.vtkFiltersSources import (
        vtkLineSource,
        vtkPlaneSource,
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
    analyze_surface,
    mesh_fingerprint,
    posed_geometry,
    rotation_matrix,
    surface_colors,
)

PAINT_MODE_INSPECT = "inspect"
PAINT_MODE_POSE = "pose"
PAINT_MODE_ENFORCER = "enforcer"
PAINT_MODE_BLOCKER = "blocker"
PAINT_MODE_ERASE = "erase"


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
            self._last_pose_position: Any | None = None
            super().__init__(*args, **kwargs)

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
            position = event.position()
            scale = self._getPixelRatio()
            x = int(round(float(position.x()) * scale))
            y = int(round((self.height() - float(position.y()) - 1.0) * scale))
            self._paint_preview.paint_at(x, y)

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
                    self._posing = True
                    self._last_pose_position = event.position()
                event.accept()
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
                    and (event.buttons() & QtCore.Qt.MouseButton.LeftButton)
                ):
                    current = event.position()
                    dx = float(current.x() - self._last_pose_position.x())
                    dy = float(current.y() - self._last_pose_position.y())
                    self._last_pose_position = current
                    assert self._paint_preview is not None
                    if event.modifiers() & QtCore.Qt.KeyboardModifier.AltModifier:
                        self._paint_preview.translate_pose_z_drag(dy)
                    else:
                        self._paint_preview.rotate_pose_drag(
                            dx,
                            dy,
                            around_z=bool(
                                event.modifiers()
                                & QtCore.Qt.KeyboardModifier.ShiftModifier
                            ),
                        )
                event.accept()
                return
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if self._is_paint_locked():
                if event.button() == QtCore.Qt.MouseButton.LeftButton:
                    self._painting = False
                event.accept()
                return
            if self._is_pose_mode():
                if event.button() == QtCore.Qt.MouseButton.LeftButton:
                    self._posing = False
                    self._last_pose_position = None
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def wheelEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if self._is_paint_locked():
                event.accept()
                return
            if self._is_pose_mode():
                assert self._paint_preview is not None
                self._paint_preview.translate_pose_z(
                    float(event.angleDelta().y()) / 120.0
                )
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
            self._display_to_source = np.empty(0, dtype=np.int64)
            self._fingerprint: str | None = None
            self._analysis: SurfaceAnalysis | None = None
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

            self._bed_actor = self._make_bed_actor()
            self.renderer.AddActor(self._bed_actor)
            self.picker = vtkCellPicker()
            self.picker.SetTolerance(0.0006)
            self.picker.PickFromListOn()
            self.picker.AddPickList(self.actor)

            self.vtk_widget.Initialize()

        @staticmethod
        def _make_bed_actor() -> Any:
            plane = vtkPlaneSource()
            plane.SetOrigin(-100.0, -100.0, 0.0)
            plane.SetPoint1(100.0, -100.0, 0.0)
            plane.SetPoint2(-100.0, 100.0, 0.0)
            plane.SetXResolution(20)
            plane.SetYResolution(20)
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(plane.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetRepresentationToWireframe()
            actor.GetProperty().SetColor(0.22, 0.31, 0.37)
            actor.GetProperty().SetOpacity(0.38)
            return actor

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

            # Concavity does not change under rigid pose rotations, so compute
            # it once and reuse it during responsive angle updates.
            initial = analyze_surface(self._mesh)
            self._concavity = initial.concavity
            self._center_of_mass_source = self._calculate_center_of_mass(self._mesh)

            # Keep ordinary and moderately dense reference models at their
            # original resolution. Besides improving the view, this makes each
            # visible/pickable triangle correspond directly to a source face.
            # Only protect the interactive renderer from exceptionally large
            # meshes, and retain far more detail when that safeguard is needed.
            if len(self._mesh.faces) > 750_000:
                self._display_mesh = decimate_mesh_for_preview(
                    self._mesh,
                    target_face_count=500_000,
                )
                self._display_to_source = closest_source_faces(
                    self._cell_locator,
                    self._display_mesh.triangles_center,
                )
            else:
                self._display_mesh = self._mesh.copy()
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
            source_vertices, _centers, _normals = posed_geometry(
                self._mesh, *self._pose
            )
            source_center = np.asarray(self._mesh.bounds, dtype=float).mean(axis=0)
            rotation = rotation_matrix(*self._pose[:3])
            first_untranslated = (
                np.asarray(self._mesh.vertices[0], dtype=float) - source_center
            ) @ rotation.T
            translation = source_vertices[0] - first_untranslated
            inverse_rotation = rotation.T
            self._source_hit_transform = np.eye(4, dtype=float)
            self._source_hit_transform[:3, :3] = inverse_rotation
            self._source_hit_transform[:3, 3] = (
                source_center - inverse_rotation @ translation
            )
            vertices = (
                np.asarray(self._display_mesh.vertices, dtype=float) - source_center
            ) @ rotation.T + translation
            points = vtkPoints()
            points.SetData(numpy_to_vtk(np.ascontiguousarray(vertices), deep=True))
            self.polydata.SetPoints(points)
            self.polydata.Modified()
            assert self._center_of_mass_source is not None
            center_of_mass = (
                self._center_of_mass_source - source_center
            ) @ rotation.T + translation
            diagonal = float(np.linalg.norm(np.ptp(source_vertices, axis=0)))
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
            self._analysis = analyze_surface(
                self._mesh,
                rotation_x_deg=self._pose[0],
                rotation_y_deg=self._pose[1],
                rotation_z_deg=self._pose[2],
                bottom_height_mm=self._pose[3],
                concavity=self._concavity,
            )
            self._update_colors()

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
            self.paint_mode = mode
            locked = mode in {
                PAINT_MODE_ENFORCER,
                PAINT_MODE_BLOCKER,
                PAINT_MODE_ERASE,
            }
            self.vtk_widget.setCursor(
                QtCore.Qt.CursorShape.CrossCursor
                if locked
                else (
                    QtCore.Qt.CursorShape.SizeAllCursor
                    if mode == PAINT_MODE_POSE
                    else QtCore.Qt.CursorShape.ArrowCursor
                )
            )
            self.paintModeChanged.emit(locked)
            self.interactionModeChanged.emit(mode)

        def rotate_pose_drag(self, dx: float, dy: float, *, around_z: bool) -> None:
            """Rotate the printable model pose from a viewport drag."""

            x_deg, y_deg, z_deg, bottom_height = self._pose
            sensitivity = 0.35
            if around_z:
                z_deg += float(dx) * sensitivity
            else:
                x_deg += float(dy) * sensitivity
                y_deg += float(dx) * sensitivity
            x_deg = (x_deg + 180.0) % 360.0 - 180.0
            y_deg = (y_deg + 180.0) % 360.0 - 180.0
            z_deg = (z_deg + 180.0) % 360.0 - 180.0
            self.set_pose(x_deg, y_deg, z_deg, bottom_height)
            self.poseEdited.emit(x_deg, y_deg, z_deg, bottom_height)

        def translate_pose_z_drag(self, vertical_pixels: float) -> None:
            """Raise or lower the printable model from an Option/Alt-drag."""

            x_deg, y_deg, z_deg, bottom_height = self._pose
            new_height = adjusted_bottom_height(bottom_height, vertical_pixels)
            self.translate_pose_z(new_height - bottom_height)

        def translate_pose_z(self, delta_mm: float) -> None:
            """Move the model vertically while preserving its rotation."""

            x_deg, y_deg, z_deg, bottom_height = self._pose
            bottom_height = float(
                np.clip(bottom_height + float(delta_mm), 0.01, 1000.0)
            )
            self.set_pose(x_deg, y_deg, z_deg, bottom_height)
            self.poseEdited.emit(x_deg, y_deg, z_deg, bottom_height)

        def set_brush_radius(self, radius_mm: float) -> None:
            self.brush_radius_mm = max(0.05, float(radius_mm))

        def _update_colors(self) -> None:
            if self._analysis is None:
                return
            colors = surface_colors(
                self._analysis,
                self._paint_states,
                low_height_fraction=self._low_height_fraction,
            )
            colors = colors[self._display_to_source]
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
                self._emit_surface(seed)
                return
            self.clear_supports(render=False)
            self._paint_states[selected] = state
            self._update_colors()
            self._emit_surface(seed)
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
    "require_preview_dependencies",
]
