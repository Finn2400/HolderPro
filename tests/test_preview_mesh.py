from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

PROJECT_PYTHON = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_PYTHON))

from holderpro.preview import (  # noqa: E402
    adjusted_bottom_height,
    bed_grid_segments,
    contrasting_grid_color,
    decimate_mesh_for_preview,
    load_support_preview_mesh,
    merged_topology_copy,
    point_triangle_distance_squared,
    rotate_euler_about_world_axis,
    signed_axis_angle_degrees,
    triangle_faces_within_sphere,
)
from holderpro.surface_analysis import rotation_matrix  # noqa: E402
from holderpro.solidify import export_mesh_stl  # noqa: E402


def test_support_preview_load_welds_stl_triangle_soup(tmp_path: Path) -> None:
    output = tmp_path / "support.stl"
    source = trimesh.creation.cylinder(radius=4.0, height=20.0, sections=48)
    source.export(output)

    display = load_support_preview_mesh(output)

    assert display.is_watertight and display.is_volume
    assert len(display.vertices) < len(display.faces) * 3
    assert display.volume == pytest.approx(source.volume, rel=1e-6)


def test_support_preview_displays_prusa_valid_tangent_shell_fallback(
    tmp_path: Path,
) -> None:
    left = trimesh.creation.box()
    right = trimesh.creation.box()
    right.apply_translation((1.0, 1.0, 0.0))
    source = trimesh.util.concatenate((left, right))
    output = tmp_path / "prusa-valid-tangent-shells.stl"
    export_mesh_stl(source, output, printable_validator=lambda _path: True)
    strict = trimesh.load_mesh(output, file_type="stl", process=True)
    assert not strict.is_volume

    display = load_support_preview_mesh(output)

    assert len(display.faces) > 0
    assert np.isfinite(display.vertices).all()


def test_upward_pose_drag_raises_model_and_clamps_to_bed() -> None:
    assert adjusted_bottom_height(25.0, -20.0) == pytest.approx(28.0)
    assert adjusted_bottom_height(2.0, 100.0) == pytest.approx(0.01)


def test_axis_ring_drag_has_stable_signed_rotation() -> None:
    assert signed_axis_angle_degrees(
        np.asarray((0.0, 0.0, 1.0)),
        np.asarray((1.0, 0.0, 0.0)),
        np.asarray((0.0, 1.0, 0.0)),
    ) == pytest.approx(90.0)
    assert signed_axis_angle_degrees(
        np.asarray((0.0, 0.0, 2.0)),
        np.asarray((0.0, 3.0, 0.0)),
        np.asarray((4.0, 0.0, 0.0)),
    ) == pytest.approx(-90.0)


def test_axis_ring_composes_the_axis_shown_in_world_space() -> None:
    initial = (30.0, 40.0, 50.0)
    result = rotate_euler_about_world_axis(
        *initial,
        np.asarray((1.0, 0.0, 0.0)),
        12.0,
    )

    expected = rotation_matrix(12.0, 0.0, 0.0) @ rotation_matrix(*initial)
    assert rotation_matrix(*result) == pytest.approx(expected, abs=1e-10)


def test_preview_welds_stl_triangle_soup_without_changing_faces() -> None:
    box = trimesh.creation.box(extents=(4.0, 5.0, 6.0))
    triangle_vertices = np.asarray(box.triangles).reshape((-1, 3))
    soup = trimesh.Trimesh(
        vertices=triangle_vertices,
        faces=np.arange(len(triangle_vertices)).reshape((-1, 3)),
        process=False,
    )

    display = merged_topology_copy(soup)

    assert len(display.faces) == len(soup.faces)
    assert len(display.vertices) == 8
    assert len(display.face_adjacency) > 0
    np.testing.assert_allclose(display.triangles, soup.triangles)


def test_build_plate_grid_uses_maximum_background_contrast() -> None:
    assert contrasting_grid_color((0.055, 0.064, 0.075), (0.12, 0.14, 0.17)) == (
        1.0,
        1.0,
        1.0,
    )
    assert contrasting_grid_color((0.88, 0.90, 0.94), (1.0, 1.0, 1.0)) == (
        0.0,
        0.0,
        0.0,
    )


def test_build_plate_grid_has_disjoint_minor_major_and_border_lines() -> None:
    minor, major, border = bed_grid_segments()

    assert minor.shape == (32, 2, 3)
    assert major.shape == (6, 2, 3)
    assert border.shape == (4, 2, 3)
    combined = np.concatenate((minor, major, border))
    assert np.all(combined[:, :, 2] == 0.0)
    assert np.max(np.abs(combined[:, :, :2])) == pytest.approx(100.0)
    canonical = {
        tuple(np.round(segment, 8).ravel()) for segment in combined
    }
    assert len(canonical) == len(combined)


def test_brush_distance_uses_triangle_surface_not_triangle_center() -> None:
    triangle = np.asarray(((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)))

    distance_sq = point_triangle_distance_squared(np.asarray((0.1, 0.1, 0.2)), triangle)

    assert distance_sq == pytest.approx(0.04)
    assert np.linalg.norm(triangle.mean(axis=0) - (0.1, 0.1, 0.2)) > 3.0


def test_brush_distance_handles_points_beyond_triangle_edges() -> None:
    triangle = np.asarray(((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0)))

    assert point_triangle_distance_squared(
        np.asarray((2.0, 2.0, 0.0)), triangle
    ) == pytest.approx(2.0)


def test_brush_spatial_query_includes_disconnected_left_and_right_faces() -> None:
    triangles = np.asarray(
        (
            ((-2.5, -0.2, 0.0), (-1.5, -0.2, 0.0), (-2.0, 0.3, 0.0)),
            ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.3, 0.0)),
            ((1.5, -0.2, 0.0), (2.5, -0.2, 0.0), (2.0, 0.3, 0.0)),
            ((4.0, -0.2, 0.0), (5.0, -0.2, 0.0), (4.5, 0.3, 0.0)),
        ),
        dtype=float,
    )

    class BoundsLocator:
        def candidate_faces(self, bounds: tuple[float, ...]) -> list[int]:
            low = np.asarray((bounds[0], bounds[2], bounds[4]), dtype=float)
            high = np.asarray((bounds[1], bounds[3], bounds[5]), dtype=float)
            triangle_low = triangles.min(axis=1)
            triangle_high = triangles.max(axis=1)
            intersects = np.all(triangle_high >= low, axis=1) & np.all(
                triangle_low <= high, axis=1
            )
            return [int(value) for value in np.flatnonzero(intersects)]

    selected = triangle_faces_within_sphere(
        triangles, BoundsLocator(), np.zeros(3), radius=3.0
    )

    assert set(selected) == {0, 1, 2}


def test_vtk_quadric_preview_decimation_preserves_closed_support_volume() -> None:
    pytest.importorskip("PySide6")
    pytest.importorskip("vtkmodules")
    source = trimesh.creation.icosphere(subdivisions=4, radius=12.0)

    preview = decimate_mesh_for_preview(source, target_face_count=1_200)

    assert len(preview.faces) <= 1_220
    assert preview.is_watertight and preview.is_volume
    assert preview.volume == pytest.approx(source.volume, rel=0.02)
