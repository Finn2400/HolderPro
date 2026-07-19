#!/usr/bin/env python3
"""Enforce inspectable PyInstaller module, size, and legal-notice policy."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from verify_native_stage import PRUSA_COMMIT, TARGETS
from verify_python_licenses import verify_license_directory


def _unique_resolved(bundle: Path, suffix: str) -> list[Path]:
    root = bundle.resolve()
    resolved: dict[Path, Path] = {}
    for path in bundle.rglob("*"):
        if not path.is_file() or not path.as_posix().endswith(suffix):
            continue
        target = path.resolve()
        if not target.is_relative_to(root):
            raise RuntimeError(f"desktop bundle link escapes its root: {path}")
        resolved[target] = path
    return sorted(resolved)


def verify_embedded_material(bundle: Path) -> None:
    license_manifests = _unique_resolved(
        bundle, "THIRD_PARTY_LICENSES/MANIFEST.json"
    )
    if len(license_manifests) != 1:
        raise RuntimeError(
            f"desktop bundle has {len(license_manifests)} physical license manifests"
        )
    verify_license_directory(license_manifests[0].parent)

    native_manifests = _unique_resolved(bundle, "holderpro/_native/MANIFEST.json")
    if len(native_manifests) != 1:
        raise RuntimeError(
            f"desktop bundle has {len(native_manifests)} physical native manifests"
        )
    document = json.loads(native_manifests[0].read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("desktop native manifest is not an object")
    if document.get("schema") != "holderpro.native-artifact-manifest/v1":
        raise RuntimeError("desktop native manifest has the wrong schema")
    target = document.get("target")
    if target not in TARGETS:
        raise RuntimeError(f"desktop native manifest has an unknown target: {target!r}")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        raise RuntimeError("desktop native manifest has no provenance")
    product = provenance.get("product")
    prusa = provenance.get("prusaslicer")
    if (
        not isinstance(product, dict)
        or product.get("name") != "HolderPro"
        or not product.get("version")
        or not isinstance(prusa, dict)
        or prusa.get("version") != "2.9.6"
        or prusa.get("commit") != PRUSA_COMMIT
    ):
        raise RuntimeError("desktop native manifest has invalid product/upstream provenance")
    records = document.get("files")
    if not isinstance(records, list) or not records:
        raise RuntimeError("desktop native manifest contains no file records")
    record_names: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("desktop native manifest has a malformed file record")
        name = record.get("name")
        expected_hash = record.get("sha256")
        expected_size = record.get("size")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or name in record_names
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size <= 0
        ):
            raise RuntimeError(f"desktop native manifest has an invalid record: {record!r}")
        record_names.add(name)
        candidates = _unique_resolved(bundle, f"holderpro/_native/{name}")
        if len(candidates) != 1:
            raise RuntimeError(
                f"desktop bundle has {len(candidates)} physical copies of native file {name}"
            )
        path = candidates[0]
        if path.stat().st_size != expected_size or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError(f"desktop native artifact digest mismatch: {name}")
    if TARGETS[target].engine_name not in record_names:
        raise RuntimeError("desktop native manifest lacks the target engine")


def verify_bundle(bundle: Path, policy: dict[str, Any]) -> dict[str, int]:
    if policy.get("schema") != "holderpro.pyinstaller-bundle-policy/v1":
        raise RuntimeError("desktop bundle policy has the wrong schema")
    if not bundle.is_dir():
        raise RuntimeError(f"desktop bundle is not a directory: {bundle}")
    files = [path for path in bundle.rglob("*") if path.is_file()]
    relative = [path.relative_to(bundle).as_posix() for path in files]
    # PyInstaller's macOS bundle intentionally exposes files through symlinked
    # Frameworks and Resources views. Count each physical inode once so policy
    # measures the distributable payload instead of following aliases.
    physical: dict[tuple[int, int], int] = {}
    for path in files:
        stat = path.stat()
        physical[(stat.st_dev, stat.st_ino)] = stat.st_size
    size = sum(physical.values())
    maximum = policy.get("maximum_bytes")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
        raise RuntimeError("desktop bundle policy has no valid maximum size")
    if size > maximum:
        raise RuntimeError(f"desktop bundle is {size} bytes; policy maximum is {maximum}")

    normalized = [path.lower() for path in relative]
    forbidden = policy.get("forbidden_top_level_modules")
    if not isinstance(forbidden, list) or not forbidden:
        raise RuntimeError("desktop bundle policy has no forbidden-module closure")
    for module in forbidden:
        if not isinstance(module, str) or not module:
            raise RuntimeError("desktop bundle policy has an invalid forbidden module")
        token = module.lower()
        bad = [
            path
            for path in normalized
            if f"/{token}/" in f"/{path}/"
            or f"/{token}.py" in f"/{path}"
            or f"/{token}.pyc" in f"/{path}"
        ]
        if bad:
            raise RuntimeError(f"forbidden bundled module {module}: {bad[0]}")

    required = policy.get("required_path_fragments")
    if not isinstance(required, list) or not required:
        raise RuntimeError("desktop bundle policy has no required paths")
    for fragment in required:
        if not isinstance(fragment, str) or not fragment:
            raise RuntimeError("desktop bundle policy has an invalid required path")
        if not any(fragment.lower() in path for path in normalized):
            raise RuntimeError(f"desktop bundle lacks required content: {fragment}")
    return {"files": len(physical), "bytes": size}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "pyinstaller/bundle-policy.json",
    )
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        summary = verify_bundle(args.bundle, policy)
        verify_embedded_material(args.bundle)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"desktop bundle verification failed: {exc}") from exc
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
