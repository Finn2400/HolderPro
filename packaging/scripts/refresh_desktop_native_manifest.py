#!/usr/bin/env python3
"""Refresh the desktop engine digest manifest after platform signing mutates code."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from verify_native_stage import (
    NATIVE_MANIFEST_NAME,
    TARGETS,
    native_digest_manifest,
    verify_native_stage,
)


def _unique_runtime_engine(bundle: Path) -> Path:
    candidates: dict[Path, Path] = {}
    names = {target.engine_name for target in TARGETS.values()}
    for path in bundle.rglob("*"):
        if path.is_file() and path.name in names:
            candidates[path.resolve()] = path
    if len(candidates) != 1:
        raise RuntimeError(
            f"desktop bundle has {len(candidates)} physical HolderPro engines"
        )
    return next(iter(candidates))


def _reported_identity(engine: Path) -> tuple[str, str, str]:
    completed = subprocess.run(
        [str(engine), "--version-json"],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("desktop engine provenance is not an object")
    product = payload.get("product")
    version = product.get("version") if isinstance(product, dict) else None
    product_name = product.get("name") if isinstance(product, dict) else None
    os_name = payload.get("os")
    architecture = payload.get("architecture")
    matches = [
        name
        for name, target in TARGETS.items()
        if target.os_name == os_name and target.architecture == architecture
    ]
    if (
        not isinstance(version, str)
        or not version
        or not isinstance(product_name, str)
        or not product_name
        or len(matches) != 1
    ):
        raise RuntimeError("desktop engine reports an unsupported product/target")
    return version, matches[0], product_name


def refresh_manifest(
    bundle: Path,
    expected_version: str | None = None,
    expected_target: str | None = None,
    expected_build_id: str | None = None,
) -> Path:
    bundle = bundle.resolve()
    engine = _unique_runtime_engine(bundle)
    reported_version, reported_target, product_name = _reported_identity(engine)
    if product_name != "HolderPro":
        raise RuntimeError(f"desktop engine product is {product_name!r}, expected HolderPro")
    if expected_version is not None and expected_version != reported_version:
        raise RuntimeError(
            f"desktop engine version is {reported_version!r}, expected {expected_version!r}"
        )
    if expected_target is not None and expected_target != reported_target:
        raise RuntimeError(
            f"desktop engine target is {reported_target!r}, expected {expected_target!r}"
        )

    source_directory = engine.parent
    runtime_files = [engine]
    if TARGETS[reported_target].os_name == "windows":
        runtime_files.extend(
            sorted(
                path.resolve()
                for path in source_directory.iterdir()
                if path.is_file() and path.suffix.lower() == ".dll"
            )
        )
    with tempfile.TemporaryDirectory(prefix="holderpro-desktop-native-") as temporary:
        native_bin = Path(temporary) / "bin"
        native_bin.mkdir()
        for source in runtime_files:
            destination = native_bin / source.name
            shutil.copy2(source, destination)
        provenance = verify_native_stage(
            native_bin,
            reported_version,
            reported_target,
            expected_build_id,
        )
        manifest = native_digest_manifest(native_bin, reported_target, provenance)

    manifests = {
        path.resolve()
        for path in bundle.rglob(NATIVE_MANIFEST_NAME)
        if path.is_file() and path.as_posix().endswith("holderpro/_native/MANIFEST.json")
    }
    if len(manifests) != 1:
        raise RuntimeError(
            f"desktop bundle has {len(manifests)} physical native manifests"
        )
    destination = next(iter(manifests))
    destination.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-target", choices=sorted(TARGETS))
    parser.add_argument("--expected-build-id")
    args = parser.parse_args()
    try:
        path = refresh_manifest(
            args.bundle,
            args.expected_version,
            args.expected_target,
            args.expected_build_id,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SystemExit(f"could not refresh desktop native manifest: {exc}") from exc
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
