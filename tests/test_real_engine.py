from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import shutil
import stat
import sys
from threading import Event

import numpy as np
import pytest
import trimesh

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from holderpro import (  # noqa: E402
    GenerationCancelled,
    GenerationJob,
    GenerationResult,
    generate,
)
from holderpro.runner import _count_connected_components  # noqa: E402


def _real_engine() -> Path:
    configured = os.environ.get("HOLDERPRO_REAL_ENGINE")
    if not configured:
        pytest.skip("set HOLDERPRO_REAL_ENGINE to run native Organic-engine tests")
    engine = Path(configured).expanduser().resolve()
    if not engine.is_file():
        pytest.fail(f"HOLDERPRO_REAL_ENGINE does not name a file: {engine}")
    if os.name != "nt" and not os.access(engine, os.X_OK):
        pytest.fail(f"HOLDERPRO_REAL_ENGINE is not executable: {engine}")
    return engine


@pytest.fixture(scope="module")
def real_engine() -> Path:
    return _real_engine()


def _load_reference(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.load_scene(path, process=False).to_mesh()
    assert isinstance(mesh, trimesh.Trimesh)
    assert len(mesh.faces)
    return mesh


def _mesh_fingerprint(mesh: trimesh.Trimesh) -> str:
    """Match the public job's paint-registration fingerprint contract."""

    vertices = np.ascontiguousarray(mesh.vertices, dtype="<f8")
    faces = np.ascontiguousarray(mesh.faces, dtype="<u8")
    digest = hashlib.sha256()
    digest.update(np.asarray(vertices.shape, dtype="<u8").tobytes())
    digest.update(vertices.tobytes())
    digest.update(np.asarray(faces.shape, dtype="<u8").tobytes())
    digest.update(faces.tobytes())
    return digest.hexdigest()


def _downward_faces(mesh: trimesh.Trimesh) -> tuple[int, ...]:
    return tuple(
        int(index) for index in np.flatnonzero(mesh.face_normals[:, 2] < -0.99)
    )


def _posed_vertices(
    mesh: trimesh.Trimesh,
    *,
    rotation_x_deg: float = 0.0,
    rotation_y_deg: float = 0.0,
    rotation_z_deg: float = 0.0,
    bottom_height_mm: float,
) -> np.ndarray:
    centered = np.asarray(mesh.vertices, dtype=float) - np.asarray(
        mesh.bounds, dtype=float
    ).mean(axis=0)
    rotation = trimesh.transformations.euler_matrix(
        math.radians(rotation_x_deg),
        math.radians(rotation_y_deg),
        math.radians(rotation_z_deg),
        axes="sxyz",
    )
    homogeneous = np.column_stack((centered, np.ones(len(centered))))
    posed = (homogeneous @ rotation.T)[:, :3]
    bounds = np.stack((posed.min(axis=0), posed.max(axis=0)))
    posed += np.asarray(
        (
            -bounds[:, 0].mean(),
            -bounds[:, 1].mean(),
            bottom_height_mm - bounds[0, 2],
        )
    )
    return posed


def _point_in_triangle_xy(point: np.ndarray, triangle: np.ndarray) -> bool:
    origin = np.asarray(triangle[0, :2], dtype=float)
    basis = np.column_stack(
        (
            np.asarray(triangle[1, :2], dtype=float) - origin,
            np.asarray(triangle[2, :2], dtype=float) - origin,
        )
    )
    coordinates = np.linalg.solve(basis, np.asarray(point[:2], dtype=float) - origin)
    tolerance = 1e-6
    return bool(
        coordinates[0] >= -tolerance
        and coordinates[1] >= -tolerance
        and coordinates.sum() <= 1.0 + tolerance
    )


def _load_support(result: GenerationResult) -> trimesh.Trimesh:
    mesh = trimesh.load_mesh(result.output_path, file_type="stl", process=True)
    assert isinstance(mesh, trimesh.Trimesh)
    assert mesh.is_watertight
    assert mesh.is_volume
    assert mesh.volume > 0.0
    return mesh


def _two_suspended_pads(*, separation_mm: float, size_mm: float) -> trimesh.Trimesh:
    left = trimesh.creation.box(extents=(size_mm, size_mm, 2.0))
    right = left.copy()
    left.apply_translation((-separation_mm * 0.5, 0.0, 0.0))
    right.apply_translation((separation_mm * 0.5, 0.0, 0.0))
    return trimesh.util.concatenate((left, right))


def test_rotated_painted_only_contact_stays_registered_with_unicode_paths(
    tmp_path: Path, real_engine: Path
) -> None:
    workspace = tmp_path / "Unicode Ω and spaces"
    workspace.mkdir()
    source = workspace / "模型 rotated reference plate.stl"
    output = workspace / "支撑 only result.stl"
    trimesh.creation.box(extents=(14.0, 10.0, 2.0)).export(source)

    reference = _load_reference(source)
    bottom_faces = _downward_faces(reference)
    assert len(bottom_faces) == 2
    painted_face, unpainted_face = bottom_faces
    bottom_height = 10.0
    rotation_z = 31.0

    result = generate(
        GenerationJob(
            source,
            output,
            bottom_height_mm=bottom_height,
            rotation_z_deg=rotation_z,
            layer_height_mm=0.4,
            branch_diameter_mm=2.4,
            tip_diameter_mm=1.2,
            painted_enforcer_faces=(painted_face,),
            paint_face_count=len(reference.faces),
            paint_mesh_fingerprint=_mesh_fingerprint(reference),
            enforcers_only=True,
            network_base_enabled=False,
            engine_path=real_engine,
        )
    )

    support = _load_support(result)
    assert result.output_path == output.resolve()
    assert result.engine_info is not None and result.engine_info.verified
    assert support.bounds[1, 2] == pytest.approx(bottom_height, abs=0.5)

    posed = _posed_vertices(
        reference,
        rotation_z_deg=rotation_z,
        bottom_height_mm=bottom_height,
    )
    painted_triangle = posed[reference.faces[painted_face]]
    unpainted_triangle = posed[reference.faces[unpainted_face]]
    top = float(support.bounds[1, 2])
    top_vertices = np.asarray(support.vertices)[support.vertices[:, 2] >= top - 0.5]
    assert len(top_vertices)
    contact_center = top_vertices.mean(axis=0)

    assert _point_in_triangle_xy(contact_center, painted_triangle)
    assert not _point_in_triangle_xy(contact_center, unpainted_triangle)


def test_multiple_painted_roots_form_one_connected_watertight_base(
    tmp_path: Path, real_engine: Path
) -> None:
    source = tmp_path / "two painted roots.stl"
    output = tmp_path / "one connected base.stl"
    _two_suspended_pads(separation_mm=24.0, size_mm=6.0).export(source)
    reference = _load_reference(source)
    painted = _downward_faces(reference)
    assert len(painted) == 4

    result = generate(
        GenerationJob(
            source,
            output,
            bottom_height_mm=8.0,
            layer_height_mm=0.4,
            branch_diameter_mm=2.4,
            tip_diameter_mm=1.2,
            painted_enforcer_faces=painted,
            paint_face_count=len(reference.faces),
            paint_mesh_fingerprint=_mesh_fingerprint(reference),
            enforcers_only=True,
            network_base_enabled=True,
            base_thickness_mm=2.5,
            base_beam_width_mm=2.0,
            base_node_diameter_mm=5.0,
            engine_path=real_engine,
        )
    )

    support = _load_support(result)
    assert result.base_node_count >= 2
    assert result.component_count == 1
    assert _count_connected_components(support) == 1
    assert support.bounds[0, 2] == pytest.approx(0.0, abs=1e-6)


def test_oversized_sparse_model_warns_and_still_generates(
    tmp_path: Path, real_engine: Path
) -> None:
    source = tmp_path / "oversized sparse reference.stl"
    output = tmp_path / "oversized sparse supports.stl"
    _two_suspended_pads(separation_mm=210.0, size_mm=4.0).export(source)
    reference = _load_reference(source)
    painted = _downward_faces(reference)
    progress: list[str] = []

    result = generate(
        GenerationJob(
            source,
            output,
            bottom_height_mm=6.0,
            layer_height_mm=0.4,
            branch_diameter_mm=2.4,
            tip_diameter_mm=1.2,
            painted_enforcer_faces=painted,
            paint_face_count=len(reference.faces),
            paint_mesh_fingerprint=_mesh_fingerprint(reference),
            enforcers_only=True,
            network_base_enabled=False,
            engine_path=real_engine,
        ),
        progress=progress.append,
    )

    _load_support(result)
    assert any(
        "exceeds the 200 x 200 mm build plate" in warning
        for warning in result.warnings
    )
    assert any(message.startswith("WARNING:") for message in progress)
    assert output.is_file()


def test_painted_face_registration_survives_duplicate_and_collinear_stl_facets(
    tmp_path: Path, real_engine: Path
) -> None:
    source = tmp_path / "repair-prone painted reference.stl"
    output = tmp_path / "repair-prone painted supports.stl"
    box = trimesh.creation.box(extents=(10.0, 8.0, 2.0))
    triangles = np.asarray(box.triangles, dtype=float)
    triangles = np.concatenate(
        (
            triangles,
            triangles[:1],
            np.asarray([[[20.0, 0.0, -1.0], [21.0, 0.0, -1.0], [22.0, 0.0, -1.0]]]),
        )
    )
    vertices = triangles.reshape((-1, 3))
    faces = np.arange(len(vertices), dtype=np.int64).reshape((-1, 3))
    trimesh.Trimesh(vertices=vertices, faces=faces, process=False).export(source)
    reference = _load_reference(source)
    assert len(reference.faces) == 14
    painted = _downward_faces(reference)
    assert painted

    result = generate(
        GenerationJob(
            source,
            output,
            bottom_height_mm=8.0,
            layer_height_mm=0.4,
            branch_diameter_mm=2.4,
            tip_diameter_mm=1.2,
            painted_enforcer_faces=painted,
            paint_face_count=len(reference.faces),
            paint_mesh_fingerprint=_mesh_fingerprint(reference),
            enforcers_only=True,
            network_base_enabled=False,
            engine_path=real_engine,
        )
    )

    _load_support(result)
    assert result.layer_count > 0


def test_in_flight_cancellation_preserves_existing_output(
    tmp_path: Path, real_engine: Path
) -> None:
    source = tmp_path / "cancellation reference.stl"
    output = tmp_path / "existing cancellation output.stl"
    trimesh.creation.icosphere(subdivisions=3, radius=8.0).export(source)
    output.write_bytes(b"existing output")
    cancellation = Event()

    def progress(message: str) -> None:
        if message.startswith("Running PrusaSlicer"):
            cancellation.set()

    with pytest.raises(GenerationCancelled):
        generate(
            GenerationJob(
                source,
                output,
                bottom_height_mm=10.0,
                network_base_enabled=False,
                engine_path=real_engine,
            ),
            progress=progress,
            cancelled=cancellation.is_set,
        )

    assert output.read_bytes() == b"existing output"


def test_long_paths_generate_a_printable_support(
    tmp_path: Path, real_engine: Path
) -> None:
    workspace = tmp_path
    for index in range(8):
        workspace /= f"long holderpro path component {index:02d}"
    workspace.mkdir(parents=True)
    source = workspace / "long reference model name.stl"
    output = workspace / "long generated support name.stl"
    trimesh.creation.box(extents=(10.0, 8.0, 2.0)).export(source)

    result = generate(
        GenerationJob(
            source,
            output,
            bottom_height_mm=8.0,
            network_base_enabled=False,
            engine_path=real_engine,
        )
    )

    _load_support(result)
    assert len(str(output)) > 260


def test_read_only_engine_installation_is_not_mutated(
    tmp_path: Path, real_engine: Path
) -> None:
    install = tmp_path / "read-only installation"
    install.mkdir()
    companions = [real_engine]
    if os.name == "nt":
        companions.extend(real_engine.parent.glob("*.dll"))
    for source in companions:
        shutil.copy2(source, install / source.name)
    installed_engine = install / real_engine.name
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in install.iterdir()
        if path.is_file()
    }
    if os.name != "nt":
        for path in install.iterdir():
            path.chmod(stat.S_IRUSR | stat.S_IXUSR)
        install.chmod(stat.S_IRUSR | stat.S_IXUSR)

    source = tmp_path / "writable reference.stl"
    output = tmp_path / "writable supports.stl"
    trimesh.creation.box(extents=(10.0, 8.0, 2.0)).export(source)
    try:
        result = generate(
            GenerationJob(
                source,
                output,
                bottom_height_mm=8.0,
                network_base_enabled=False,
                engine_path=installed_engine,
            )
        )
    finally:
        if os.name != "nt":
            install.chmod(stat.S_IRWXU)
            for path in install.iterdir():
                path.chmod(stat.S_IRWXU)

    _load_support(result)
    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in install.iterdir()
        if path.is_file()
    }
    assert after == before
