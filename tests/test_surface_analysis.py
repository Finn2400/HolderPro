from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest
import trimesh


PROJECT_PYTHON = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_PYTHON))

from holderpro.surface_analysis import (  # noqa: E402
    PAINT_BLOCKER,
    PAINT_ENFORCER,
    analyze_surface,
    mesh_fingerprint,
    surface_colors,
)


def test_downward_faces_are_distinguished_from_upward_faces() -> None:
    mesh = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
    analysis = analyze_surface(mesh)
    downward = mesh.face_normals[:, 2] < -0.9
    upward = mesh.face_normals[:, 2] > 0.9

    assert np.all(analysis.downwardness[downward] == pytest.approx(1.0))
    assert np.all(analysis.underside_angle_deg[downward] == pytest.approx(0.0))
    assert np.all(analysis.downwardness[upward] == pytest.approx(0.0))
    assert np.all(analysis.underside_angle_deg[upward] == pytest.approx(90.0))


def test_pose_rotation_updates_underside_classification() -> None:
    mesh = trimesh.creation.box(extents=(4.0, 2.0, 1.0))
    original = analyze_surface(mesh)
    flipped = analyze_surface(mesh, rotation_x_deg=180.0)

    np.testing.assert_allclose(
        original.downwardness,
        np.clip(mesh.face_normals[:, 2] * -1.0, 0.0, 1.0),
        atol=1e-6,
    )
    np.testing.assert_allclose(
        flipped.downwardness,
        np.clip(mesh.face_normals[:, 2], 0.0, 1.0),
        atol=1e-6,
    )


def test_paint_colors_override_surface_heatmap() -> None:
    mesh = trimesh.creation.box()
    analysis = analyze_surface(mesh)
    states = np.zeros(len(mesh.faces), dtype=np.uint8)
    states[0] = PAINT_ENFORCER
    states[1] = PAINT_BLOCKER

    colors = surface_colors(analysis, states)

    np.testing.assert_array_equal(colors[0], (53, 220, 105))
    np.testing.assert_array_equal(colors[1], (239, 72, 83))


def test_mesh_fingerprint_tracks_face_index_identity() -> None:
    mesh = trimesh.creation.box()
    same = mesh.copy()
    reordered = mesh.copy()
    reordered.faces = reordered.faces[::-1]

    assert mesh_fingerprint(mesh) == mesh_fingerprint(same)
    assert mesh_fingerprint(mesh) != mesh_fingerprint(reordered)
