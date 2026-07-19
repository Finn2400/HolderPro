#!/usr/bin/env python3
"""Build a non-pure py3 wheel containing one tested native engine."""

from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import importlib.metadata
import io
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from package_identity import (
    PROJECT_NAME,
    expected_dist_info,
    validate_release_identity,
)
from verify_native_stage import native_digest_manifest, verify_native_stage


def verify_build_backend(repository: Path) -> None:
    constraints: dict[str, str] = {}
    for raw in (repository / "packaging/release-constraints.txt").read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            name, version = line.split("==", 1)
            constraints[name.lower()] = version
    for name in ("build", "setuptools", "wheel"):
        expected = constraints.get(name)
        actual = importlib.metadata.version(name)
        if expected is None or actual != expected:
            raise RuntimeError(
                f"wheel backend {name} is {actual}, expected exact release input {expected}"
            )


def record_digest(data: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return "sha256=" + encoded.decode("ascii")


def rewrite_wheel(
    source: Path,
    destination: Path,
    platform_tag: str,
    native_files: list[Path],
    native_manifest: dict[str, object],
    project_name: str,
    version: str,
) -> None:
    with zipfile.ZipFile(source, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("build backend produced duplicate wheel entries")
        entries = {name: archive.read(name) for name in names}
        info = {name: archive.getinfo(name) for name in names}
    dist_info = expected_dist_info(project_name, version)
    wheel_name = f"{dist_info}/WHEEL"
    metadata_name = f"{dist_info}/METADATA"
    record_name = f"{dist_info}/RECORD"
    if any(name not in entries for name in (wheel_name, metadata_name, record_name)):
        raise RuntimeError("wheel has an invalid dist-info layout")
    metadata = BytesParser().parsebytes(entries[metadata_name])
    if metadata.get("Name") != PROJECT_NAME or metadata.get("Version") != version:
        raise RuntimeError(
            "build backend changed package identity: "
            f"Name={metadata.get('Name')!r}, Version={metadata.get('Version')!r}"
        )

    for native_file in native_files:
        name = f"holderpro/_native/{native_file.name}"
        entries[name] = native_file.read_bytes()
        metadata = zipfile.ZipInfo(name)
        metadata.create_system = 3
        mode = 0o100755 if native_file.name.startswith("holderpro-organic-engine") else 0o100644
        metadata.external_attr = (mode & 0xFFFF) << 16
        info[name] = metadata
    manifest_name = "holderpro/_native/MANIFEST.json"
    entries[manifest_name] = (
        json.dumps(native_manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    manifest_info = zipfile.ZipInfo(manifest_name)
    manifest_info.create_system = 3
    manifest_info.external_attr = (0o100644 & 0xFFFF) << 16
    info[manifest_name] = manifest_info

    lines = entries[wheel_name].decode("utf-8").splitlines()
    lines = [
        line
        for line in lines
        if line.strip()
        and not line.startswith(("Root-Is-Purelib:", "Tag:"))
    ]
    lines.extend(["Root-Is-Purelib: false", f"Tag: py3-none-{platform_tag}"])
    entries[wheel_name] = ("\n".join(lines) + "\n").encode("utf-8")

    rows = [
        [name, record_digest(data), str(len(data))]
        for name, data in sorted(entries.items())
        if name != record_name
    ]
    rows.append([record_name, "", ""])
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    entries[record_name] = output.getvalue().encode("utf-8")

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(entries):
            metadata = info[name]
            metadata.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(metadata, entries[name])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--native-bin", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--platform-tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = args.repository.resolve()
    native_bin = args.native_bin.resolve()
    try:
        project_name, source_version = validate_release_identity(
            repository, args.version, args.target, args.platform_tag
        )
        verify_build_backend(repository)
        provenance = verify_native_stage(
            native_bin, args.version, args.target, args.build_id
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if not native_bin.is_dir():
        raise SystemExit(f"native install bin directory does not exist: {native_bin}")
    native_files = sorted(path for path in native_bin.iterdir() if path.is_file())
    engine_names = {"holderpro-organic-engine", "holderpro-organic-engine.exe"}
    engines = [path for path in native_files if path.name in engine_names]
    if len(engines) != 1:
        raise SystemExit(f"native bin must contain exactly one engine: {native_bin}")
    companions = [path for path in native_files if path not in engines]
    if engines[0].suffix == ".exe":
        rejected = [path.name for path in companions if path.suffix.lower() != ".dll"]
    else:
        rejected = [path.name for path in companions]
    if rejected:
        raise SystemExit("unexpected native install files: " + ", ".join(rejected))

    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="holderpro-wheel-") as temporary:
        stage = Path(temporary) / "source"
        stage.mkdir()
        for name in ("pyproject.toml", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(repository / name, stage / name)
        upstream_license = stage / "upstream/prusaslicer-2.9.6-organic/LICENSE"
        upstream_license.parent.mkdir(parents=True)
        shutil.copy2(
            repository / "upstream/prusaslicer-2.9.6-organic/LICENSE",
            upstream_license,
        )
        shutil.copytree(
            repository / "src",
            stage / "src",
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.egg-info",
                "*.pyc",
                "*.pyo",
                "*.so",
                "*.dylib",
                "*.dll",
                "*.exe",
                "holderpro-organic-engine",
                "MANIFEST.json",
                "build",
                "dist",
                ".mypy_cache",
                ".pytest_cache",
                ".ruff_cache",
            ),
        )

        with tempfile.TemporaryDirectory(prefix="holderpro-wheel-output-") as wheel_temporary:
            raw = Path(wheel_temporary) / "raw"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--wheel",
                    "--no-isolation",
                    "--outdir",
                    str(raw),
                ],
                cwd=stage,
                check=True,
            )
            wheels = list(raw.glob("*.whl"))
            if len(wheels) != 1:
                raise RuntimeError(f"expected one wheel, found {len(wheels)}")
            source = wheels[0]
            destination = args.output / (
                f"{PROJECT_NAME}-{source_version}-py3-none-{args.platform_tag}.whl"
            )
            manifest = native_digest_manifest(
                native_bin, args.target, provenance
            )
            rewrite_wheel(
                source,
                destination,
                args.platform_tag,
                native_files,
                manifest,
                project_name,
                source_version,
            )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
