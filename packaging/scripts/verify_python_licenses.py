#!/usr/bin/env python3
"""Verify the exact, closed license-material set bundled with the desktop app."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


REQUIRED = {
    "cpython",
    "holderpro",
    "manifold3d",
    "numpy",
    "pyinstaller",
    "pyside6-essentials",
    "qt",
    "shapely",
    "shiboken6",
    "trimesh",
    "vtk",
}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _safe_basename(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{label} is empty")
    path = PurePosixPath(value)
    if path.name != value or value in {".", ".."} or "\\" in value:
        raise RuntimeError(f"{label} is unsafe: {value!r}")
    return value


def _distribution_records(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    distributions = document.get("distributions")
    if not isinstance(distributions, list) or not distributions:
        raise RuntimeError("third-party license manifest contains no distributions")
    records: dict[str, dict[str, Any]] = {}
    for distribution in distributions:
        if not isinstance(distribution, dict):
            raise RuntimeError("third-party license manifest has a malformed distribution")
        raw_name = distribution.get("name")
        if not isinstance(raw_name, str) or not raw_name:
            raise RuntimeError("license distribution has no name")
        name = canonical(raw_name)
        if name in records:
            raise RuntimeError(f"duplicate license distribution: {name}")
        version = distribution.get("version")
        declared = distribution.get("declared_license")
        files = distribution.get("files")
        if not isinstance(version, str) or not version:
            raise RuntimeError(f"license distribution {name} has no version")
        if not isinstance(declared, str) or not declared.strip():
            raise RuntimeError(f"license distribution {name} has no declared license")
        if not isinstance(files, list) or not files:
            raise RuntimeError(f"license distribution {name} has no manifested material")
        records[name] = distribution
    if set(records) != REQUIRED:
        raise RuntimeError(
            "third-party license closure mismatch: "
            f"missing={sorted(REQUIRED - set(records))}, "
            f"extra={sorted(set(records) - REQUIRED)}"
        )
    holderpro_license = records["holderpro"]["declared_license"]
    if "AGPL-3.0-or-later" not in holderpro_license:
        raise RuntimeError("HolderPro distribution metadata is not AGPL-3.0-or-later")
    return records


def verify_license_directory(directory: Path) -> None:
    directory = directory.resolve()
    manifest_path = directory / "MANIFEST.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RuntimeError("third-party license bundle has no regular MANIFEST.json")
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("third-party license manifest is not an object")
    if document.get("schema") != "holderpro.third-party-licenses/v1":
        raise RuntimeError("third-party license manifest has the wrong schema")
    distributions = _distribution_records(document)

    expected_files = {manifest_path}
    expected_directories = {directory}
    for name, distribution in distributions.items():
        version = distribution["version"]
        folder = directory / f"{name}-{version}"
        expected_directories.add(folder)
        if not folder.is_dir() or folder.is_symlink():
            raise RuntimeError(f"license directory is missing or unsafe: {folder.name}")
        record_names: set[str] = set()
        for record in distribution["files"]:
            if not isinstance(record, dict):
                raise RuntimeError(f"{name} has a malformed license-file record")
            filename = _safe_basename(record.get("file"), f"{name} license filename")
            if filename in record_names:
                raise RuntimeError(f"{name} manifests {filename} more than once")
            record_names.add(filename)
            expected_hash = record.get("sha256")
            if (
                not isinstance(expected_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
            ):
                raise RuntimeError(f"{name} has an invalid SHA-256 for {filename}")
            path = folder / filename
            expected_files.add(path)
            if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
                raise RuntimeError(f"{name} license material is missing/empty: {filename}")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected_hash:
                raise RuntimeError(f"{name} license material changed: {filename}")

    actual_files = {path for path in directory.rglob("*") if path.is_file()}
    actual_directories = {directory, *(path for path in directory.rglob("*") if path.is_dir())}
    if actual_files != expected_files:
        raise RuntimeError(
            "unmanifested or missing license files: "
            f"missing={sorted(str(path.relative_to(directory)) for path in expected_files - actual_files)}, "
            f"extra={sorted(str(path.relative_to(directory)) for path in actual_files - expected_files)}"
        )
    if actual_directories != expected_directories:
        raise RuntimeError(
            "unmanifested or missing license directories: "
            f"missing={sorted(str(path.relative_to(directory)) for path in expected_directories - actual_directories)}, "
            f"extra={sorted(str(path.relative_to(directory)) for path in actual_directories - expected_directories)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        verify_license_directory(args.directory)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"third-party license verification failed: {exc}") from exc
    print("third-party license bundle OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
