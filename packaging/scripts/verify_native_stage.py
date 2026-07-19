#!/usr/bin/env python3
"""Verify a native runtime's identity, machine format, and real adapter behavior."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PRUSA_COMMIT = "b028299c770b8380ee81c921a2867d522f288123"
ENGINE_BASENAME = "holderpro-organic-engine"
NATIVE_MANIFEST_NAME = "MANIFEST.json"


@dataclass(frozen=True)
class TargetIdentity:
    os_name: str
    architecture: str
    engine_name: str
    wheel_tag: str
    executable_format: str


TARGETS = {
    "macos-arm64": TargetIdentity(
        "macos", "arm64", ENGINE_BASENAME, "macosx_13_0_arm64", "mach-o-arm64"
    ),
    "macos-x86_64": TargetIdentity(
        "macos", "x86_64", ENGINE_BASENAME, "macosx_13_0_x86_64", "mach-o-x86_64"
    ),
    "linux-x86_64": TargetIdentity(
        "linux", "x86_64", ENGINE_BASENAME, "manylinux_2_35_x86_64", "elf-x86_64"
    ),
    "windows-x86_64": TargetIdentity(
        "windows", "x86_64", f"{ENGINE_BASENAME}.exe", "win_amd64", "pe-x86_64"
    ),
}


def file_sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _require_mach_o(data: bytes, architecture: str) -> None:
    if len(data) < 12:
        raise RuntimeError("native engine has a truncated Mach-O header")
    if data[:4] == b"\xcf\xfa\xed\xfe":
        endian = "<"
    elif data[:4] == b"\xfe\xed\xfa\xcf":
        endian = ">"
    else:
        raise RuntimeError("native engine is not a thin 64-bit Mach-O executable")
    cpu_type = struct.unpack_from(f"{endian}I", data, 4)[0]
    expected = {"x86_64": 0x01000007, "arm64": 0x0100000C}[architecture]
    if cpu_type != expected:
        raise RuntimeError(
            f"Mach-O CPU type is 0x{cpu_type:08x}, expected {architecture}"
        )


def _require_elf_x86_64(data: bytes) -> None:
    if len(data) < 20 or data[:4] != b"\x7fELF":
        raise RuntimeError("native engine is not an ELF executable")
    if data[4] != 2 or data[5] not in {1, 2}:
        raise RuntimeError("native engine is not a supported 64-bit ELF executable")
    endian = "<" if data[5] == 1 else ">"
    machine = struct.unpack_from(f"{endian}H", data, 18)[0]
    if machine != 62:
        raise RuntimeError(f"ELF machine is {machine}, expected x86_64 (62)")


def _require_pe_x86_64(path: Path) -> None:
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise RuntimeError(f"{path.name} is not a PE executable")
        pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
        stream.seek(pe_offset)
        pe_header = stream.read(6)
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        raise RuntimeError(f"{path.name} has no valid PE header")
    machine = struct.unpack_from("<H", pe_header, 4)[0]
    if machine != 0x8664:
        raise RuntimeError(
            f"{path.name} PE machine is 0x{machine:04x}, expected x86_64"
        )


def verify_executable_format(path: Path, target: TargetIdentity) -> None:
    if target.executable_format.startswith("mach-o"):
        _require_mach_o(path.read_bytes()[:32], target.architecture)
    elif target.executable_format == "elf-x86_64":
        _require_elf_x86_64(path.read_bytes()[:32])
    elif target.executable_format == "pe-x86_64":
        _require_pe_x86_64(path)
    else:  # pragma: no cover - TARGETS is static and reviewed
        raise RuntimeError(f"unsupported executable format {target.executable_format}")


def _tetrahedron_stl(path: Path) -> None:
    vertices = {
        "a": (0.0, 0.0, 0.0),
        "b": (1.0, 0.0, 0.0),
        "c": (0.0, 1.0, 0.0),
        "d": (0.0, 0.0, 1.0),
    }
    faces = (("a", "c", "b"), ("a", "b", "d"), ("a", "d", "c"), ("b", "c", "d"))
    lines = ["solid holderpro_provenance_tetrahedron"]
    for face in faces:
        lines.extend(("  facet normal 0 0 0", "    outer loop"))
        lines.extend(
            f"      vertex {vertices[name][0]} {vertices[name][1]} {vertices[name][2]}"
            for name in face
        )
        lines.extend(("    endloop", "  endfacet"))
    lines.append("endsolid holderpro_provenance_tetrahedron")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _validate_real_solid_path(engine: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="holderpro-native-proof-") as temporary:
        solid = Path(temporary) / "positive-volume-tetrahedron.stl"
        _tetrahedron_stl(solid)
        completed = subprocess.run(
            [str(engine), "--validate-solid", str(solid), "--quiet"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
    match = re.fullmatch(
        r"printable support solid: (\d+) facets, ([0-9eE+.-]+) mm\^3\s*",
        completed.stdout,
    )
    if match is None:
        raise RuntimeError("native engine did not execute the solid-validation path")
    facets = int(match.group(1))
    volume = float(match.group(2))
    if facets < 4 or not math.isfinite(volume) or volume <= 0.0:
        raise RuntimeError("native engine rejected the positive-volume proof solid")


def _runtime_files(native_bin: Path, target: TargetIdentity) -> tuple[Path, list[Path]]:
    entries = sorted(native_bin.iterdir(), key=lambda item: item.name.lower())
    invalid_entries = [item.name for item in entries if not item.is_file() or item.is_symlink()]
    if invalid_entries:
        raise RuntimeError("unexpected native stage entries: " + ", ".join(invalid_entries))
    engines = [path for path in entries if path.name == target.engine_name]
    if len(engines) != 1:
        raise RuntimeError(f"expected exactly one {target.engine_name} in {native_bin}")
    companions = [path for path in entries if path not in engines]
    if target.os_name == "windows":
        rejected = [path.name for path in companions if path.suffix.lower() != ".dll"]
    else:
        rejected = [path.name for path in companions]
    if rejected:
        raise RuntimeError("unexpected native stage files: " + ", ".join(rejected))
    return engines[0], companions


def native_digest_manifest(
    native_bin: Path,
    target_name: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    target = TARGETS[target_name]
    engine, companions = _runtime_files(native_bin, target)
    files = [engine, *companions]
    return {
        "schema": "holderpro.native-artifact-manifest/v1",
        "target": target_name,
        "provenance": payload,
        "files": [
            {
                "name": path.name,
                "sha256": file_sha256(path),
                "size": path.stat().st_size,
            }
            for path in sorted(files, key=lambda item: item.name.lower())
        ],
    }


def verify_native_stage(
    native_bin: Path,
    expected_version: str,
    expected_target: str,
    expected_build_id: str | None = None,
) -> dict[str, Any]:
    if expected_target not in TARGETS:
        raise RuntimeError(f"unknown HolderPro target: {expected_target}")
    target = TARGETS[expected_target]
    if not native_bin.is_dir():
        raise RuntimeError(f"native install bin does not exist: {native_bin}")
    engine, companions = _runtime_files(native_bin, target)
    if target.os_name != "windows" and not os.access(engine, os.X_OK):
        raise RuntimeError(f"native engine is not executable: {engine}")
    verify_executable_format(engine, target)
    if target.os_name == "windows":
        for companion in companions:
            _require_pe_x86_64(companion)

    completed = subprocess.run(
        [str(engine), "--version-json"],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("native --version-json output is not an object")
    expected = {
        ("product", "name"): "HolderPro",
        ("product", "version"): expected_version,
        ("adapter", "name"): ENGINE_BASENAME,
        ("prusaslicer", "version"): "2.9.6",
        ("prusaslicer", "commit"): PRUSA_COMMIT,
        ("schemas", "layers"): "holderpro.organic-support-layers/v1",
        ("schemas", "paint"): "HOLDERPRO_SUPPORT_PAINT_V1",
    }
    failures = []
    for (section, key), wanted in expected.items():
        section_value = payload.get(section)
        actual = section_value.get(key) if isinstance(section_value, dict) else None
        if actual != wanted:
            failures.append(f"{section}.{key}={actual!r}, expected {wanted!r}")
    if payload.get("os") != target.os_name:
        failures.append(f"os={payload.get('os')!r}, expected {target.os_name!r}")
    if payload.get("architecture") != target.architecture:
        failures.append(
            f"architecture={payload.get('architecture')!r}, expected {target.architecture!r}"
        )
    adapter = payload.get("adapter")
    if not isinstance(adapter, dict) or not adapter.get("version") or not payload.get("build_id"):
        failures.append("adapter.version and build_id must be non-empty")
    if expected_build_id is not None and payload.get("build_id") != expected_build_id:
        failures.append(
            f"build_id={payload.get('build_id')!r}, expected {expected_build_id!r}"
        )
    if failures:
        raise RuntimeError("native provenance verification failed:\n- " + "\n- ".join(failures))

    _validate_real_solid_path(engine)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-bin", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--expected-build-id")
    parser.add_argument(
        "--manifest-out",
        type=Path,
        help="write a digest-bound manifest after all checks pass",
    )
    args = parser.parse_args()
    native_bin = args.native_bin.resolve()
    payload = verify_native_stage(
        native_bin,
        args.expected_version,
        args.expected_target,
        args.expected_build_id,
    )
    result: dict[str, object] = payload
    if args.manifest_out is not None:
        result = native_digest_manifest(native_bin, args.expected_target, payload)
        args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_out.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
