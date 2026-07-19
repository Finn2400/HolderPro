from __future__ import annotations

import json
import platform
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import trimesh

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from holderpro import (  # noqa: E402
    EngineError,
    EngineInfo,
    EngineNotFoundError,
    EngineProvenanceError,
    GenerationJob,
    GenerationValidationError,
)
from holderpro.cli import build_parser, main as cli_main  # noqa: E402
from holderpro.diagnostics import (  # noqa: E402
    DoctorCheck,
    DoctorReport,
    export_diagnostics,
)
from holderpro.engine import (  # noqa: E402
    LAYER_SCHEMA,
    PAINT_SCHEMA,
    PINNED_PRUSASLICER_COMMIT,
    PINNED_PRUSASLICER_VERSION,
    find_engine,
    inspect_engine,
)
from holderpro.mesh_io import load_reference_mesh  # noqa: E402
import holderpro.engine as engine_module  # noqa: E402
import holderpro.ui as ui_module  # noqa: E402
from holderpro.ui import _release_tag  # noqa: E402


def _executable(path: Path) -> Path:
    path.write_text("placeholder", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_public_job_uses_distinct_validation_error(tmp_path: Path) -> None:
    source = tmp_path / "reference.stl"
    trimesh.creation.box().export(source)

    with pytest.raises(GenerationValidationError, match="does not exist"):
        GenerationJob(source, tmp_path / "missing" / "stand.stl").validated()


def test_public_job_rejects_hardlink_output_alias(tmp_path: Path) -> None:
    source = tmp_path / "reference.stl"
    alias = tmp_path / "same inode.stl"
    trimesh.creation.box().export(source)
    alias.hardlink_to(source)

    with pytest.raises(GenerationValidationError, match="must be different"):
        GenerationJob(source, alias).validated()


def test_public_job_rejects_directory_output_and_nonintegral_paint(
    tmp_path: Path,
) -> None:
    source = tmp_path / "reference.stl"
    trimesh.creation.box().export(source)
    directory_output = tmp_path / "directory.stl"
    directory_output.mkdir()

    with pytest.raises(GenerationValidationError, match="regular file"):
        GenerationJob(source, directory_output).validated()
    for invalid in (True, 1.9, "2"):
        with pytest.raises(GenerationValidationError, match="integer indices"):
            GenerationJob(
                source,
                tmp_path / "stand.stl",
                painted_enforcer_faces=(invalid,),  # type: ignore[arg-type]
                paint_face_count=12,
            ).validated()
    for invalid_count in (True, 3.9, "3"):
        with pytest.raises(GenerationValidationError, match="positive integer"):
            GenerationJob(
                source,
                tmp_path / "stand.stl",
                paint_face_count=invalid_count,  # type: ignore[arg-type]
            ).validated()


def test_shared_mesh_loader_preserves_reference_faces(tmp_path: Path) -> None:
    source = tmp_path / "reference.stl"
    original = trimesh.creation.box()
    original.export(source)

    loaded = load_reference_mesh(source)

    assert len(loaded.faces) == len(original.faces)


def test_holderpro_engine_environment_variable_takes_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _executable(tmp_path / "holderpro-organic-engine")
    monkeypatch.setenv("HOLDERPRO_ENGINE", str(engine))
    monkeypatch.setenv("ORGANIC_SUPPORTS_ENGINE", str(tmp_path / "legacy"))

    assert find_engine() == engine.resolve()


def test_retired_engine_environment_variable_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOLDERPRO_ENGINE", raising=False)
    monkeypatch.setenv(
        "ORGANIC_SUPPORTS_ENGINE", str(_executable(tmp_path / "organic-support-engine"))
    )
    monkeypatch.setattr(engine_module, "_packaged_engine_candidates", lambda: [])
    monkeypatch.setattr(engine_module, "_source_engine_candidates", lambda: [])
    monkeypatch.setattr(engine_module.shutil, "which", lambda _name: None)

    with pytest.raises(EngineNotFoundError, match="was not found"):
        find_engine()


def test_find_engine_discovers_current_platform_cmake_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operating_system = {
        "darwin": "macos",
        "windows": "windows",
        "linux": "linux",
        "freebsd": "freebsd",
    }.get(platform.system().lower(), platform.system().lower())
    architecture = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
    }.get(platform.machine().lower(), platform.machine().lower())
    executable = "holderpro-organic-engine"
    if platform.system() == "Windows":
        executable += ".exe"
    engine = tmp_path / "native" / "build" / f"{operating_system}-{architecture}"
    engine.mkdir(parents=True)
    expected = _executable(engine / executable)

    monkeypatch.delenv("HOLDERPRO_ENGINE", raising=False)
    monkeypatch.delenv("ORGANIC_SUPPORTS_ENGINE", raising=False)
    monkeypatch.setattr(engine_module, "project_root", lambda: tmp_path)
    monkeypatch.setattr(engine_module, "_packaged_engine_candidates", lambda: [])
    monkeypatch.setattr(engine_module.shutil, "which", lambda _name: None)

    assert find_engine() == expected.resolve()


def test_engine_version_json_is_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _executable(tmp_path / "holderpro-organic-engine")
    expected_os = {
        "darwin": "macos",
        "windows": "windows",
        "linux": "linux",
    }.get(platform.system().lower(), platform.system().lower())
    expected_arch = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
    }.get(platform.machine().lower(), platform.machine().lower())
    payload = {
        "product": {"name": "HolderPro", "version": "0.1.0a1"},
        "adapter": {"name": "holderpro-organic-engine", "version": "0.1.0"},
        "prusaslicer": {
            "version": PINNED_PRUSASLICER_VERSION,
            "commit": PINNED_PRUSASLICER_COMMIT,
        },
        "schemas": {"layers": LAYER_SCHEMA, "paint": PAINT_SCHEMA},
        "os": expected_os,
        "architecture": expected_arch,
        "build_id": "fixture",
    }
    monkeypatch.setattr(
        "holderpro.engine.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=json.dumps(payload), stderr=""
        ),
    )

    info = inspect_engine(executable)

    assert info.verified
    assert info.prusaslicer_commit == PINNED_PRUSASLICER_COMMIT
    assert info.layer_schema == LAYER_SCHEMA
    assert info.build_id == "fixture"


def test_engine_version_json_rejects_wrong_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _executable(tmp_path / "holderpro-organic-engine")
    payload = {
        "product": {"name": "HolderPro", "version": "0.1.0a1"},
        "adapter": {"name": "holderpro-organic-engine", "version": "0.1.0"},
        "prusaslicer": {
            "version": PINNED_PRUSASLICER_VERSION,
            "commit": PINNED_PRUSASLICER_COMMIT,
        },
        "schemas": {"layers": LAYER_SCHEMA, "paint": PAINT_SCHEMA},
        "os": "definitely-not-this-os",
        "architecture": "definitely-not-this-architecture",
        "build_id": "wrong-wheel-fixture",
    }
    monkeypatch.setattr(
        "holderpro.engine.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=json.dumps(payload), stderr=""
        ),
    )

    with pytest.raises(EngineProvenanceError, match="operating system"):
        inspect_engine(executable)


def test_engine_without_version_json_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _executable(tmp_path / "holderpro-organic-engine")
    monkeypatch.setattr(
        "holderpro.engine.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="crashed"
        ),
    )

    with pytest.raises(EngineError, match="did not support --version-json"):
        inspect_engine(executable)


def test_engine_invalid_utf8_becomes_provenance_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _executable(tmp_path / "holderpro-organic-engine")
    monkeypatch.setattr(
        "holderpro.engine.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="\ufffd", stderr=""
        ),
    )

    with pytest.raises(EngineProvenanceError, match="invalid --version-json"):
        inspect_engine(executable)


def test_cli_exposes_required_subcommands() -> None:
    parser = build_parser()

    for command, arguments in (
        ("generate", ["input.stl", "output.stl"]),
        ("doctor", []),
        ("version", []),
    ):
        parsed = parser.parse_args([command, *arguments])
        assert parsed.command == command


def test_public_version_json_omits_private_engine_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    private_engine = tmp_path / "private user" / "holderpro-organic-engine"
    info = EngineInfo(
        path=private_engine,
        holderpro_version="0.1.0a1",
        adapter_version="1",
        prusaslicer_version=PINNED_PRUSASLICER_VERSION,
        prusaslicer_commit=PINNED_PRUSASLICER_COMMIT,
        layer_schema=LAYER_SCHEMA,
        paint_schema=PAINT_SCHEMA,
        os="test",
        architecture="test64",
        build_id="fixture",
        verified=True,
        provenance_source="version-json",
    )
    monkeypatch.setattr("holderpro.cli.find_engine", lambda _path=None: private_engine)
    monkeypatch.setattr("holderpro.cli.inspect_engine", lambda _path=None: info)

    assert cli_main(["version", "--json"]) == 0
    rendered = capsys.readouterr().out
    assert str(private_engine) not in rendered
    assert "path" not in json.loads(rendered)["engine"]


def test_gui_launcher_reports_missing_optional_dependencies_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ui_module, "QtWidgets", None)

    assert ui_module.main(["holderpro-gui"]) == 2
    error = capsys.readouterr().err
    assert "holderpro[gui]" in error
    assert "Traceback" not in error


@pytest.mark.parametrize(
    ("version", "tag"),
    (
        ("0.1.0a1", "v0.1.0-alpha.1"),
        ("0.1.0b2", "v0.1.0-beta.2"),
        ("0.1.0rc3", "v0.1.0-rc.3"),
        ("1.0.0", "v1.0.0"),
    ),
)
def test_about_links_use_release_tag_spelling(version: str, tag: str) -> None:
    assert _release_tag(version) == tag


def test_diagnostic_export_redacts_paths_and_omits_geometry(tmp_path: Path) -> None:
    private_engine = Path.home() / "private" / "holderpro-organic-engine"
    report = DoctorReport(
        holderpro_version="0.1.0a1",
        generated_at="2026-07-19T00:00:00+00:00",
        platform={
            "system": "TestOS",
            "release": "1",
            "architecture": "test64",
            "python": "3.11",
        },
        checks=(
            DoctorCheck(
                "Engine provenance",
                "ok",
                "verified",
                {"engine": str(private_engine)},
            ),
        ),
        engine_info=EngineInfo(
            path=private_engine,
            holderpro_version="0.1.0a1",
            adapter_version="0.1.0",
            prusaslicer_version=PINNED_PRUSASLICER_VERSION,
            prusaslicer_commit=PINNED_PRUSASLICER_COMMIT,
            layer_schema=LAYER_SCHEMA,
            paint_schema=PAINT_SCHEMA,
            os="TestOS",
            architecture="test64",
            build_id="fixture",
            verified=True,
            provenance_source="version-json",
        ),
    )

    destination = export_diagnostics(tmp_path / "diagnostics", report=report)
    data = destination.read_text(encoding="utf-8")
    payload = json.loads(data)

    assert str(Path.home()) not in data
    assert "[HOME]" in data
    assert payload["privacy"] == {
        "contains_model_geometry": False,
        "paths_redacted": True,
    }
    assert "vertices" not in data and "faces" not in data


def test_diagnostic_redaction_scrubs_external_posix_windows_and_unc_paths() -> None:
    report = DoctorReport(
        holderpro_version="0.1.0a1",
        generated_at="2026-07-19T00:00:00+00:00",
        platform={
            "system": "TestOS",
            "release": "1",
            "architecture": "test64",
            "python": "3.11",
        },
        checks=(
            DoctorCheck(
                "Output directory",
                "error",
                "Cannot write: /Volumes/Confidential Client/Output",
                {
                    "directory": "/Volumes/Confidential Client/Output",
                    "windows": r"C:\Users\Finn\Secret Project\output",
                    "unc": r"\\server\private share\model",
                },
            ),
        ),
    )

    rendered = report.to_json(redact_paths=True)

    assert "Confidential" not in rendered
    assert "Secret Project" not in rendered
    assert "private share" not in rendered
    assert rendered.count("[PATH]") >= 3


def test_doctor_distinguishes_generation_and_desktop_readiness() -> None:
    core_only = DoctorReport(
        holderpro_version="0.1.0a1",
        generated_at="2026-07-19T00:00:00+00:00",
        platform={"system": "x", "release": "x", "architecture": "x", "python": "x"},
        checks=(
            DoctorCheck("Engine provenance", "ok", "ok"),
            DoctorCheck("Qt GUI", "warning", "missing"),
            DoctorCheck("VTK renderer", "warning", "missing"),
            DoctorCheck("OpenGL", "warning", "missing"),
        ),
    )
    desktop = DoctorReport(
        holderpro_version=core_only.holderpro_version,
        generated_at=core_only.generated_at,
        platform=core_only.platform,
        checks=tuple(
            DoctorCheck(name, "ok", "ok")
            for name in ("Engine provenance", "Qt GUI", "VTK renderer", "OpenGL")
        ),
    )

    assert core_only.ok
    assert not core_only.desktop_ok
    assert desktop.ok and desktop.desktop_ok
    assert json.loads(core_only.to_json())["desktop_ok"] is False
