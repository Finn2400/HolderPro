from __future__ import annotations

import io
import os
import platform
import stat
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from holderpro.runner import (  # noqa: E402
    PINNED_PRUSASLICER_COMMIT,
    EngineNotFoundError,
    GenerationError,
    GenerationJob,
    _atomic_export_mesh,
    _canonicalize_painted_stl_mesh,
    _count_connected_components,
    _load_reference_mesh,
    _retain_failed_export_geometry,
    _sanitize_native_layer_geometry,
    _write_support_paint,
    find_engine,
    generate,
    project_root,
)
import holderpro.runner as runner_module  # noqa: E402
from holderpro.surface_analysis import mesh_fingerprint  # noqa: E402


def _fake_engine(
    path: Path, *, commit: str = PINNED_PRUSASLICER_COMMIT
) -> Path:
    expected_system = {
        "darwin": "macos",
        "windows": "windows",
        "linux": "linux",
    }.get(platform.system().lower(), platform.system().lower())
    machine = platform.machine().lower()
    expected_architecture = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
    }.get(machine, machine)
    path.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys

args = sys.argv[1:]
if '--version-json' in args:
    print(json.dumps({{
        'product': {{'name': 'HolderPro', 'version': '0.1.0a1'}},
        'adapter': {{'name': 'holderpro-organic-engine', 'version': 'fixture'}},
        'prusaslicer': {{'version': '2.9.6', 'commit': {PINNED_PRUSASLICER_COMMIT!r}}},
        'schemas': {{
            'layers': 'holderpro.organic-support-layers/v1',
            'paint': 'HOLDERPRO_SUPPORT_PAINT_V1',
        }},
        'os': {expected_system!r},
        'architecture': {expected_architecture!r},
        'build_id': 'test-fixture',
    }}))
    raise SystemExit(0)
if '--validate-solid' in args:
    raise SystemExit(0)
if '--support-paint' in args and pathlib.Path(args[args.index('--input') + 1]).suffix.lower() != '.3mf':
    print('painted posed references must use face-order-safe 3MF', file=sys.stderr)
    raise SystemExit(1)
output = pathlib.Path(args[args.index('--output') + 1])
payload = {{
    'schema': 'holderpro.organic-support-layers/v1',
    'version': 1,
    'engine': {{
        'name': 'PrusaSlicer Organic',
        'version': '2.9.6',
        'commit': {commit!r},
    }},
    'units': 'mm',
    'input': args[args.index('--input') + 1],
    'layers': [
        {{'print_z': 0.3, 'height': 0.3, 'bottom_z': 0.0,
         'polygons': [{{
             'contour': [[98, 98], [102, 98], [102, 102], [98, 102]],
             'holes': [],
         }}]}},
        {{'print_z': 0.6, 'height': 0.3, 'bottom_z': 0.3,
         'polygons': [{{
             'contour': [[99, 99], [101, 99], [101, 101], [99, 101]],
             'holes': [],
         }}]}},
    ],
    'summary': {{
        'layer_count': 2,
        'nonempty_layer_count': 2,
        'polygon_count': 2,
        'point_count': 8,
    }},
}}
output.write_text(json.dumps(payload), encoding='utf-8')
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _micro_edge_prism(edge_mm: float) -> trimesh.Trimesh:
    outline = np.array(
        [
            [50.0, 50.0],
            [50.0 + edge_mm, 50.0 + edge_mm],
            [51.0, 50.0],
            [51.0, 51.0],
            [50.0, 51.0],
        ],
        dtype=float,
    )
    count = len(outline)
    vertices = np.vstack(
        (
            np.column_stack((outline, np.zeros(count))),
            np.column_stack((outline, np.ones(count))),
        )
    )
    faces: list[list[int]] = []
    for index in range(1, count - 1):
        faces.extend(
            ([0, index + 1, index], [count, count + index, count + index + 1])
        )
    for index in range(count):
        following = (index + 1) % count
        faces.extend(
            (
                [index, following, count + following],
                [index, count + following, count + index],
            )
        )
    return trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces), process=False)


def test_component_count_handles_connected_and_disconnected_triangle_meshes() -> None:
    connected = trimesh.creation.box()
    left = trimesh.creation.box()
    right = trimesh.creation.box()
    right.apply_translation((3.0, 0.0, 0.0))
    disconnected = trimesh.util.concatenate((left, right))

    assert _count_connected_components(trimesh.Trimesh()) == 0
    assert _count_connected_components(connected) == 1
    assert _count_connected_components(disconnected) == 2


def test_single_component_export_validation_preserves_existing_output(
    tmp_path: Path,
) -> None:
    left = trimesh.creation.box()
    right = trimesh.creation.box()
    right.apply_translation((3.0, 0.0, 0.0))
    disconnected = trimesh.util.concatenate((left, right))
    output = tmp_path / "existing.stl"
    output.write_bytes(b"existing support")

    with pytest.raises(GenerationError, match="single connected"):
        _atomic_export_mesh(
            disconnected,
            output,
            require_single_component=True,
        )

    assert output.read_bytes() == b"existing support"
    assert not list(tmp_path.glob(".existing.*.stl"))


def test_generation_rejects_disconnected_single_trunk_before_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "reference.stl"
    output = tmp_path / "existing.stl"
    trimesh.creation.box().export(source)
    output.write_bytes(b"existing support")
    engine = _fake_engine(tmp_path / "holderpro-organic-engine")
    left = trimesh.creation.box()
    right = trimesh.creation.box()
    right.apply_translation((3.0, 0.0, 0.0))
    disconnected = trimesh.util.concatenate((left, right))
    monkeypatch.setattr(
        runner_module, "solidify_layers", lambda _payload: disconnected.copy()
    )

    with pytest.raises(GenerationError, match="separate support components"):
        generate(
            GenerationJob(
                source,
                output,
                bottom_height_mm=20.0,
                network_base_enabled=True,
                base_thickness_mm=3.0,
                engine_path=engine,
            )
        )

    assert output.read_bytes() == b"existing support"


def test_project_root_is_standalone_tool() -> None:
    assert project_root() == PROJECT


def test_native_zero_area_holes_are_removed_without_touching_valid_holes() -> None:
    valid_hole = [[1, 1], [2, 1], [2, 2], [1, 2]]
    payload = {
        "layers": [
            {
                "polygons": [
                    {
                        "contour": [[0, 0], [4, 0], [4, 4], [0, 4]],
                        "holes": [
                            valid_hole,
                            [[1, 1], [2, 1], [3, 1]],
                            [[2, 2], [2, 2], [2, 2]],
                        ],
                    }
                ]
            }
        ]
    }

    _sanitize_native_layer_geometry(payload)

    assert payload["layers"][0]["polygons"][0]["holes"] == [valid_hole]
    assert payload["geometry_sanitization"] == {
        "zero_area_holes_removed": 2,
        "zero_area_polygons_removed": 0,
    }


def test_explicit_missing_engine_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(EngineNotFoundError, match="not executable"):
        find_engine(tmp_path / "missing-engine")


def test_runner_creates_support_only_watertight_stl(tmp_path: Path) -> None:
    source = tmp_path / "reference.stl"
    trimesh.creation.box(extents=(20, 12, 4)).export(source)
    output = tmp_path / "support-only.stl"
    engine = _fake_engine(tmp_path / "holderpro-organic-engine")
    messages: list[str] = []

    result = generate(
        GenerationJob(
            source,
            output,
            bottom_height_mm=20,
            network_base_enabled=False,
            engine_path=engine,
        ),
        progress=messages.append,
    )

    mesh = trimesh.load_mesh(output, process=True)
    assert isinstance(mesh, trimesh.Trimesh)
    assert mesh.is_watertight and mesh.is_volume
    assert mesh.volume == pytest.approx(6.0, rel=1e-6)
    assert mesh.bounds[:, 0].tolist() == pytest.approx([-2.0, 2.0])
    assert mesh.bounds[:, 1].tolist() == pytest.approx([-2.0, 2.0])
    assert mesh.bounds[:, 2].tolist() == pytest.approx([0.0, 0.6])
    assert result.layer_count == 2
    assert result.component_count == 1
    assert result.output_path == output.resolve()
    assert any("PrusaSlicer 2.9.6" in message for message in messages)


def test_runner_warns_but_generates_model_larger_than_standard_bed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "too-wide.stl"
    trimesh.creation.box(extents=(196, 10, 4)).export(source)
    engine = _fake_engine(tmp_path / "holderpro-organic-engine")
    messages: list[str] = []

    result = generate(
        GenerationJob(source, tmp_path / "out.stl", engine_path=engine),
        progress=messages.append,
    )

    assert result.output_path.is_file()
    assert any(
        "exceeds the 200 x 200 mm build plate" in item for item in result.warnings
    )
    assert any(message.startswith("WARNING:") for message in messages)


def test_runner_warns_but_generates_above_standard_build_height(
    tmp_path: Path,
) -> None:
    source = tmp_path / "too-tall.stl"
    trimesh.creation.box(extents=(10, 10, 190)).export(source)
    engine = _fake_engine(tmp_path / "holderpro-organic-engine")
    messages: list[str] = []

    result = generate(
        GenerationJob(source, tmp_path / "out.stl", engine_path=engine),
        progress=messages.append,
    )

    assert result.output_path.is_file()
    assert any(
        "above the standard 200 mm build height" in item for item in result.warnings
    )
    assert any(message.startswith("WARNING:") for message in messages)


def test_runner_rejects_foreign_engine_provenance(tmp_path: Path) -> None:
    source = tmp_path / "reference.stl"
    trimesh.creation.box(extents=(20, 12, 4)).export(source)
    engine = _fake_engine(
        tmp_path / "holderpro-organic-engine", commit="not-prusa"
    )

    with pytest.raises(GenerationError, match="provenance does not match"):
        generate(
            GenerationJob(source, tmp_path / "out.stl", engine_path=engine)
        )


def test_generation_job_validates_support_paint_indices(tmp_path: Path) -> None:
    source = tmp_path / "reference.stl"
    trimesh.creation.box().export(source)
    output = tmp_path / "out.stl"

    with pytest.raises(GenerationError, match="both a support enforcer and blocker"):
        GenerationJob(
            source,
            output,
            painted_enforcer_faces=(2,),
            painted_blocker_faces=(2,),
            paint_face_count=12,
        ).validated()
    with pytest.raises(GenerationError, match="out of range"):
        GenerationJob(
            source,
            output,
            painted_enforcer_faces=(12,),
            paint_face_count=12,
        ).validated()
    with pytest.raises(GenerationError, match="requires at least one green"):
        GenerationJob(source, output, enforcers_only=True).validated()
    with pytest.raises(GenerationError, match="below the model's bottom height"):
        GenerationJob(
            source,
            output,
            bottom_height_mm=1.0,
            base_thickness_mm=1.2,
        ).validated()


def test_support_paint_sidecar_is_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "reference.stl"
    trimesh.creation.box().export(source)
    job = GenerationJob(
        source,
        tmp_path / "out.stl",
        painted_enforcer_faces=(8, 3),
        painted_blocker_faces=(10,),
        paint_face_count=12,
    ).validated()
    sidecar = tmp_path / "paint.txt"

    _write_support_paint(sidecar, job)

    assert sidecar.read_text(encoding="ascii") == (
        "HOLDERPRO_SUPPORT_PAINT_V1\nfaces 12\nE 3\nE 8\nB 10\n"
    )


def test_collapsed_stl_faces_are_removed_and_paint_indices_remapped(
    tmp_path: Path,
) -> None:
    base = trimesh.creation.box()
    vertices = np.vstack(
        (
            base.vertices,
            ((100.0, 0.0, 0.0), (100.0 + 1e-8, 0.0, 0.0), (100.0, 1.0, 0.0)),
        )
    )
    degenerate = np.asarray([[len(base.vertices) + index for index in range(3)]])
    faces = np.vstack((base.faces[:3], degenerate, base.faces[3:]))
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    job = GenerationJob(
        tmp_path / "reference.3mf",
        tmp_path / "out.stl",
        painted_enforcer_faces=(2, 3, 4),
        painted_blocker_faces=(12,),
        paint_face_count=13,
    )

    canonical, remapped, removed = _canonicalize_painted_stl_mesh(mesh, job)

    assert removed == 1
    assert len(canonical.faces) == 12
    assert remapped.paint_face_count == 12
    assert remapped.painted_enforcer_faces == (2, 3)
    assert remapped.painted_blocker_faces == (11,)


def test_green_paint_always_disables_automatic_supports(tmp_path: Path) -> None:
    source = tmp_path / "reference.stl"
    trimesh.creation.box().export(source)

    job = GenerationJob(
        source,
        tmp_path / "out.stl",
        painted_enforcer_faces=(3,),
        paint_face_count=12,
        enforcers_only=False,
    ).validated()

    assert job.enforcers_only is True


def test_painted_generation_uses_face_order_safe_3mf_for_native_engine(
    tmp_path: Path,
) -> None:
    source = tmp_path / "painted reference.stl"
    output = tmp_path / "painted supports.stl"
    trimesh.creation.box().export(source)
    reference = _load_reference_mesh(source)
    engine = _fake_engine(tmp_path / "holderpro-organic-engine")

    result = generate(
        GenerationJob(
            source,
            output,
            bottom_height_mm=8.0,
            network_base_enabled=False,
            painted_enforcer_faces=(0,),
            paint_face_count=len(reference.faces),
            paint_mesh_fingerprint=mesh_fingerprint(reference),
            engine_path=engine,
        )
    )

    assert result.output_path.is_file()


def test_runner_rejects_stale_paint_fingerprint(tmp_path: Path) -> None:
    source = tmp_path / "reference.stl"
    trimesh.creation.box().export(source)
    engine = _fake_engine(tmp_path / "holderpro-organic-engine")
    loaded = _load_reference_mesh(source)
    assert mesh_fingerprint(loaded) != "0" * 64

    with pytest.raises(GenerationError, match="changed after support painting"):
        generate(
            GenerationJob(
                source,
                tmp_path / "out.stl",
                painted_enforcer_faces=(3,),
                paint_face_count=len(loaded.faces),
                paint_mesh_fingerprint="0" * 64,
                engine_path=engine,
            )
        )


def test_atomic_export_uses_ascii_for_float32_collapsed_solid(tmp_path: Path) -> None:
    mesh = trimesh.creation.box(extents=(2e-6, 1.0, 1.0))
    mesh.apply_translation((50.0, 0.0, 0.0))
    output = tmp_path / "collapsed.stl"

    serialized = _atomic_export_mesh(mesh, output)

    assert output.read_bytes().startswith(b"solid organic_supports\n")
    assert serialized.is_watertight
    assert serialized.is_volume
    assert serialized.volume == pytest.approx(mesh.volume, rel=1e-6)


def test_atomic_export_repairs_float32_collapsed_redundant_edges(
    tmp_path: Path,
) -> None:
    mesh = _micro_edge_prism(1.5e-6)
    direct = trimesh.load_mesh(
        io.BytesIO(trimesh.exchange.stl.export_stl(mesh)),
        file_type="stl",
        process=True,
    )
    assert isinstance(direct, trimesh.Trimesh)
    assert not direct.is_volume

    output = tmp_path / "repaired.stl"
    serialized = _atomic_export_mesh(mesh, output)

    assert output.is_file()
    assert serialized.is_watertight
    assert serialized.is_winding_consistent
    assert serialized.is_volume
    assert serialized.volume == pytest.approx(1.0, rel=1e-6)


def test_atomic_export_keeps_directly_valid_micro_edges(tmp_path: Path) -> None:
    # This edge is directly representable by STL but deliberately exceeds the
    # fallback repair's conservative volume-drift bound.  The direct round
    # trip must therefore be preferred instead of needlessly repairing it.
    mesh = _micro_edge_prism(2e-6)
    output = tmp_path / "direct.stl"

    serialized = _atomic_export_mesh(mesh, output)

    assert serialized.is_watertight
    assert serialized.is_volume
    assert len(serialized.faces) == 16
    assert serialized.volume == pytest.approx(mesh.volume, rel=1e-6)


def test_failed_atomic_export_preserves_existing_output(tmp_path: Path) -> None:
    mesh = trimesh.creation.box(extents=(0.0, 1.0, 1.0))
    output = tmp_path / "existing.stl"
    output.write_bytes(b"existing output")

    with pytest.raises(GenerationError, match="STL encoding"):
        _atomic_export_mesh(mesh, output)

    assert output.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".existing.stl.*"))


def test_failure_geometry_retention_is_private_and_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private diagnostics"

    def make_private_directory(*_args: object, **_kwargs: object) -> str:
        private.mkdir(mode=0o700)
        return str(private)

    monkeypatch.setattr(runner_module.tempfile, "mkdtemp", make_private_directory)
    retained = _retain_failed_export_geometry(
        trimesh.creation.box(), {"layers": [], "input": "private"}
    )

    assert len(retained) == 2
    assert retained[0].parent == retained[1].parent == private
    if os.name != "nt":
        assert stat.S_IMODE(retained[0].parent.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in retained)


def test_generation_failure_does_not_retain_geometry_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "reference.stl"
    output = tmp_path / "stand.stl"
    trimesh.creation.box().export(source)
    engine = _fake_engine(tmp_path / "holderpro-organic-engine")

    def fail_export(*_args: object, **_kwargs: object) -> trimesh.Trimesh:
        raise GenerationError("forced STL serialization failure")

    monkeypatch.setattr(runner_module, "_atomic_export_mesh", fail_export)
    with pytest.raises(GenerationError, match="forced STL serialization failure") as caught:
        generate(
            GenerationJob(
                source,
                output,
                network_base_enabled=False,
                engine_path=engine,
            )
        )

    assert "retained" not in str(caught.value).lower()
    assert not list(tmp_path.glob("*.npz"))
    assert not list(tmp_path.glob("*support-layers*.json"))
