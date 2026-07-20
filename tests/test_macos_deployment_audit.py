from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "packaging/scripts"))

from audit_macos_deployment_targets import (  # noqa: E402
    audit_bundle,
    is_mach_o,
    parse_version,
    parse_vtool_versions,
)


def test_vtool_parser_reads_universal_and_legacy_targets() -> None:
    output = """
fixture (architecture x86_64):
Load command 8
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 12.0
      sdk 15.0
fixture (architecture arm64):
Load command 8
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 13.0
      sdk 15.0
"""
    assert parse_vtool_versions(output) == ((12, 0, 0), (13, 0, 0))


def test_macho_magic_does_not_confuse_java_class_files(tmp_path: Path) -> None:
    executable = tmp_path / "executable"
    executable.write_bytes(b"\xcf\xfa\xed\xfe" + b"fixture")
    java_class = tmp_path / "Example.class"
    java_class.write_bytes(b"\xca\xfe\xba\xbe" + b"java")

    assert is_mach_o(executable)
    assert not is_mach_o(java_class)


def test_bundle_audit_rejects_newer_macos_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = tmp_path / "HolderPro.app"
    executable = application / "Contents/MacOS/HolderPro"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"\xcf\xfa\xed\xfe" + b"fixture")
    vtool = tmp_path / "vtool"
    vtool.write_text("fixture", encoding="utf-8")

    monkeypatch.setattr(
        "audit_macos_deployment_targets.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            "cmd LC_BUILD_VERSION\nplatform MACOS\nminos 14.0\n",
            "",
        ),
    )
    with pytest.raises(RuntimeError, match="newer than macOS 13.0.0"):
        audit_bundle(application, parse_version("13.0"), vtool=vtool)


def test_release_qt_pin_matches_macos_13_compatible_source_lock() -> None:
    constraints = (PROJECT / "packaging/release-constraints.txt").read_text(
        encoding="utf-8"
    )
    assert "PySide6-Essentials==6.9.3" in constraints
    assert "shiboken6==6.9.3" in constraints

    lock = json.loads(
        (PROJECT / "packaging/dependency-binary-source-lock.json").read_text(
            encoding="utf-8"
        )
    )
    components = {item["name"]: item for item in lock["components"]}
    assert components["PySide6-Essentials"]["version"] == "6.9.3"
    assert components["shiboken6"]["version"] == "6.9.3"
    assert components["Qt"]["version"] == "6.9.3"
