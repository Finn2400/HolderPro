"""Surface metrics and colors for the interactive support-painting preview."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

import numpy as np
import trimesh


PAINT_NONE = np.uint8(0)
PAINT_ENFORCER = np.uint8(1)
PAINT_BLOCKER = np.uint8(2)


@dataclass(frozen=True, slots=True)
class SurfaceAnalysis:
    """Per-face quantities in the currently displayed model pose."""

    downwardness: np.ndarray
    underside_angle_deg: np.ndarray
    relative_height: np.ndarray
    concavity: np.ndarray
    face_centers: np.ndarray
    face_normals: np.ndarray


def mesh_fingerprint(mesh: trimesh.Trimesh) -> str:
    """Return a stable digest tying painted face indices to one loaded mesh."""

    vertices = np.ascontiguousarray(mesh.vertices, dtype="<f8")
    faces = np.ascontiguousarray(mesh.faces, dtype="<u8")
    digest = hashlib.sha256()
    digest.update(np.asarray(vertices.shape, dtype="<u8").tobytes())
    digest.update(vertices.tobytes())
    digest.update(np.asarray(faces.shape, dtype="<u8").tobytes())
    digest.update(faces.tobytes())
    return digest.hexdigest()


def rotation_matrix(x_deg: float, y_deg: float, z_deg: float) -> np.ndarray:
    """Return the same static-XYZ rotation convention used by the runner."""

    x, y, z = (math.radians(float(value)) for value in (x_deg, y_deg, z_deg))
    sx, cx = math.sin(x), math.cos(x)
    sy, cy = math.sin(y), math.cos(y)
    sz, cz = math.sin(z), math.cos(z)
    rx = np.array(((1, 0, 0), (0, cx, -sx), (0, sx, cx)), dtype=float)
    ry = np.array(((cy, 0, sy), (0, 1, 0), (-sy, 0, cy)), dtype=float)
    rz = np.array(((cz, -sz, 0), (sz, cz, 0), (0, 0, 1)), dtype=float)
    return rz @ ry @ rx


def posed_geometry(
    mesh: trimesh.Trimesh,
    x_deg: float,
    y_deg: float,
    z_deg: float,
    bottom_height_mm: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return posed vertices, face centers, and normals for the preview."""

    vertices = np.asarray(mesh.vertices, dtype=float)
    center = np.asarray(mesh.bounds, dtype=float).mean(axis=0)
    rotation = rotation_matrix(x_deg, y_deg, z_deg)
    posed_vertices = (vertices - center) @ rotation.T
    bounds = np.stack((posed_vertices.min(axis=0), posed_vertices.max(axis=0)))
    translation = np.array(
        (
            -bounds[:, 0].mean(),
            -bounds[:, 1].mean(),
            float(bottom_height_mm) - bounds[0, 2],
        )
    )
    posed_vertices += translation

    faces = np.asarray(mesh.faces, dtype=np.int64)
    centers = posed_vertices[faces].mean(axis=1)
    normals = np.asarray(mesh.face_normals, dtype=float) @ rotation.T
    return posed_vertices, centers, normals


def _face_concavity(mesh: trimesh.Trimesh) -> np.ndarray:
    face_count = len(mesh.faces)
    score = np.zeros(face_count, dtype=np.float32)
    degree = np.zeros(face_count, dtype=np.float32)
    adjacency = np.asarray(mesh.face_adjacency, dtype=np.int64)
    if not len(adjacency):
        return score

    convex = np.asarray(mesh.face_adjacency_convex, dtype=bool)
    angles = np.asarray(mesh.face_adjacency_angles, dtype=float)
    weights = np.where(convex, 0.0, np.clip(angles / math.pi, 0.0, 1.0))
    np.add.at(score, adjacency[:, 0], weights)
    np.add.at(score, adjacency[:, 1], weights)
    np.add.at(degree, adjacency[:, 0], 1.0)
    np.add.at(degree, adjacency[:, 1], 1.0)
    np.divide(score, degree, out=score, where=degree > 0)
    return np.clip(score, 0.0, 1.0)


def analyze_surface(
    mesh: trimesh.Trimesh,
    *,
    rotation_x_deg: float = 0.0,
    rotation_y_deg: float = 0.0,
    rotation_z_deg: float = 0.0,
    bottom_height_mm: float = 0.0,
    concavity: np.ndarray | None = None,
) -> SurfaceAnalysis:
    """Measure underside angle, posed height, and concavity for every face."""

    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
        raise ValueError("surface analysis requires a non-empty triangle mesh")

    _vertices, centers, normals = posed_geometry(
        mesh,
        rotation_x_deg,
        rotation_y_deg,
        rotation_z_deg,
        bottom_height_mm,
    )
    downwardness = np.clip(-normals[:, 2], 0.0, 1.0).astype(np.float32)
    underside_angle = np.degrees(
        np.arccos(np.clip(downwardness, 0.0, 1.0))
    ).astype(np.float32)

    z = centers[:, 2]
    z_span = float(np.ptp(z))
    if z_span > 0.0:
        relative_height = ((z - float(z.min())) / z_span).astype(np.float32)
    else:
        relative_height = np.zeros(len(z), dtype=np.float32)

    if concavity is None:
        concavity = _face_concavity(mesh)
    else:
        concavity = np.asarray(concavity, dtype=np.float32)
        if concavity.shape != (len(mesh.faces),):
            raise ValueError("concavity must contain one value per mesh face")

    return SurfaceAnalysis(
        downwardness=downwardness,
        underside_angle_deg=underside_angle,
        relative_height=relative_height,
        concavity=concavity,
        face_centers=centers,
        face_normals=normals,
    )


def surface_colors(
    analysis: SurfaceAnalysis,
    paint_states: np.ndarray | None = None,
    *,
    low_height_fraction: float = 0.35,
) -> np.ndarray:
    """Create RGB cell colors with painted facets overriding the heatmap."""

    downward = np.asarray(analysis.downwardness, dtype=float)
    height = np.asarray(analysis.relative_height, dtype=float)
    concavity = np.asarray(analysis.concavity, dtype=float)
    count = len(downward)

    neutral = np.array((67.0, 75.0, 84.0))
    blue = np.array((45.0, 139.0, 190.0))
    gold = np.array((246.0, 190.0, 55.0))
    red = np.array((235.0, 70.0, 58.0))
    colors = np.repeat(neutral[None, :], count, axis=0)

    underside = downward > 0.015
    low_half = np.clip(downward * 2.0, 0.0, 1.0)[:, None]
    high_half = np.clip((downward - 0.5) * 2.0, 0.0, 1.0)[:, None]
    gradient = blue * (1.0 - low_half) + gold * low_half
    gradient = gradient * (1.0 - high_half) + red * high_half
    # Lower faces are brighter, making the nearest usable pockets read first.
    gradient *= (0.74 + 0.26 * (1.0 - height))[:, None]
    colors[underside] = gradient[underside]

    threshold = float(np.clip(low_height_fraction, 0.02, 1.0))
    low = np.clip((threshold - height) / threshold, 0.0, 1.0)
    # Concavity is measured as a fraction of a full-turn dihedral and is
    # naturally small on finely tessellated organic surfaces. Normalize its
    # useful range so low pockets remain immediately visible at normal zoom.
    concave_emphasis = np.clip(concavity / 0.04, 0.0, 1.0)
    pocket_strength = np.clip(
        downward * low * (0.45 + 0.55 * concave_emphasis), 0.0, 0.92
    )
    pocket = np.array((210.0, 62.0, 226.0))
    colors = colors * (1.0 - pocket_strength[:, None]) + pocket * pocket_strength[:, None]

    if paint_states is not None:
        states = np.asarray(paint_states, dtype=np.uint8)
        if states.shape != (count,):
            raise ValueError("paint_states must contain one value per mesh face")
        colors[states == PAINT_ENFORCER] = (53.0, 220.0, 105.0)
        colors[states == PAINT_BLOCKER] = (239.0, 72.0, 83.0)

    return np.ascontiguousarray(np.clip(colors, 0, 255), dtype=np.uint8)


__all__ = [
    "PAINT_BLOCKER",
    "PAINT_ENFORCER",
    "PAINT_NONE",
    "SurfaceAnalysis",
    "analyze_surface",
    "mesh_fingerprint",
    "posed_geometry",
    "rotation_matrix",
    "surface_colors",
]
