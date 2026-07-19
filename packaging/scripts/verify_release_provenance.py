#!/usr/bin/env python3
"""Bind every release payload and source archive to one HolderPro commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from verify_native_stage import NATIVE_MANIFEST_NAME, PRUSA_COMMIT, TARGETS
from write_release_asset_manifest import expected_names


def load_object(data: bytes | str, label: str) -> dict[str, Any]:
    try:
        document = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{label} is not valid JSON") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"{label} is not a JSON object")
    return document


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def require_binding(
    document: dict[str, Any], target: str, version: str, build_id: str
) -> None:
    if document.get("schema") != "holderpro.native-artifact-manifest/v1":
        raise RuntimeError(f"{target} native manifest has the wrong schema")
    if document.get("target") != target:
        raise RuntimeError(f"{target} native manifest reports another target")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        raise RuntimeError(f"{target} native manifest lacks provenance")
    product = provenance.get("product")
    prusa = provenance.get("prusaslicer")
    if (
        not isinstance(product, dict)
        or product.get("name") != "HolderPro"
        or product.get("version") != version
        or provenance.get("build_id") != build_id
        or not isinstance(prusa, dict)
        or prusa.get("commit") != PRUSA_COMMIT
    ):
        raise RuntimeError(f"{target} native provenance does not match the release")


def archive_json(archive: Path, suffix: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="holderpro-release-source-") as temporary:
        tar_path = Path(temporary) / "source.tar"
        with tar_path.open("wb") as stream:
            subprocess.run(
                ["zstd", "-q", "-d", "-c", str(archive)],
                check=True,
                stdout=stream,
            )
        with tarfile.open(tar_path, "r:") as source:
            matches = [member for member in source.getmembers() if member.name.endswith(suffix)]
            if len(matches) != 1 or not matches[0].isfile():
                raise RuntimeError(f"corresponding source lacks one {suffix}")
            stream = source.extractfile(matches[0])
            if stream is None:
                raise RuntimeError(f"could not read {suffix} from corresponding source")
            return load_object(stream.read(), f"corresponding-source {suffix}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--pep440-version", required=True)
    parser.add_argument("--display-version", required=True)
    parser.add_argument("--build-id", required=True)
    args = parser.parse_args()
    try:
        dependencies_path = args.directory / "dependency-sources.json"
        dependencies = load_object(
            dependencies_path.read_bytes(), "dependency-sources.json"
        )
        if (
            dependencies.get("schema") != "holderpro.dependency-sources/v1"
            or dependencies.get("holderpro_build_id") != args.build_id
            or dependencies.get("prusa_source_commit") != PRUSA_COMMIT
        ):
            raise RuntimeError("dependency source manifest has wrong release provenance")

        covered_assets: set[str] = set()
        for target_name, target in TARGETS.items():
            standalone = load_object(
                (
                    args.directory
                    / f"HolderPro-{target_name}-native-manifest.json"
                ).read_bytes(),
                f"{target_name} native manifest",
            )
            require_binding(
                standalone, target_name, args.pep440_version, args.build_id
            )
            build_environment = load_object(
                (
                    args.directory
                    / f"HolderPro-{target_name}-build-environment.json"
                ).read_bytes(),
                f"{target_name} build environment",
            )
            if (
                build_environment.get("schema")
                != "holderpro.build-environment/v1"
                or build_environment.get("target") != target_name
                or build_environment.get("version") != args.pep440_version
                or build_environment.get("holderpro_build_id") != args.build_id
            ):
                raise RuntimeError(
                    f"{target_name} build environment is bound to another release"
                )
            wheel = (
                args.directory
                / f"holderpro-{args.pep440_version}-py3-none-{target.wheel_tag}.whl"
            )
            with zipfile.ZipFile(wheel) as package:
                embedded = load_object(
                    package.read(f"holderpro/_native/{NATIVE_MANIFEST_NAME}"),
                    f"{target_name} wheel native manifest",
                )
            require_binding(embedded, target_name, args.pep440_version, args.build_id)

            asset_manifest = load_object(
                (
                    args.directory
                    / f"HolderPro-{target_name}-asset-manifest.json"
                ).read_bytes(),
                f"{target_name} release asset manifest",
            )
            if (
                asset_manifest.get("schema")
                != "holderpro.release-asset-manifest/v1"
                or asset_manifest.get("target") != target_name
                or asset_manifest.get("version") != args.pep440_version
                or asset_manifest.get("display_version") != args.display_version
                or asset_manifest.get("holderpro_build_id") != args.build_id
            ):
                raise RuntimeError(
                    f"{target_name} asset manifest is bound to another release"
                )
            records = asset_manifest.get("files")
            if not isinstance(records, list) or not records:
                raise RuntimeError(f"{target_name} asset manifest contains no files")
            names: set[str] = set()
            for record in records:
                if not isinstance(record, dict):
                    raise RuntimeError(f"{target_name} has a malformed asset record")
                name = record.get("name")
                digest = record.get("sha256")
                size = record.get("size")
                if (
                    not isinstance(name, str)
                    or Path(name).name != name
                    or name in names
                    or not isinstance(digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                    or not isinstance(size, int)
                    or isinstance(size, bool)
                    or size <= 0
                ):
                    raise RuntimeError(
                        f"{target_name} asset manifest has an invalid record"
                    )
                path = args.directory / name
                if (
                    not path.is_file()
                    or path.stat().st_size != size
                    or sha256(path) != digest
                ):
                    raise RuntimeError(f"release asset digest mismatch: {name}")
                names.add(name)
            expected_target = expected_names(
                target_name, args.display_version, args.pep440_version
            )
            if names != expected_target:
                raise RuntimeError(
                    f"{target_name} asset manifest has the wrong closed file set"
                )
            covered_assets.update(names)

        source = (
            args.directory
            / f"holderpro-{args.display_version}-corresponding-source.tar.zst"
        )
        build_inputs = archive_json(source, "/BUILD-INPUTS.json")
        if (
            build_inputs.get("schema") != "holderpro.build-inputs/v1"
            or build_inputs.get("holderpro_commit") != args.build_id
            or build_inputs.get("prusa_commit") != PRUSA_COMMIT
            or build_inputs.get("version") != args.display_version
        ):
            raise RuntimeError("corresponding source is bound to another release")
        embedded_dependencies = archive_json(source, "/dependency-sources.json")
        if embedded_dependencies != dependencies:
            raise RuntimeError(
                "attached dependency manifest differs from corresponding source"
            )
        manifest_names = {
            f"HolderPro-{target}-asset-manifest.json" for target in TARGETS
        }
        common = {
            dependencies_path.name,
            source.name,
            "SHA256SUMS",
            *manifest_names,
        }
        actual_payloads = {
            path.name
            for path in args.directory.iterdir()
            if path.is_file() and path.name not in common
        }
        if covered_assets != actual_payloads:
            raise RuntimeError(
                "target asset manifests do not cover the complete binary payload set"
            )
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        tarfile.TarError,
        zipfile.BadZipFile,
        KeyError,
    ) as exc:
        raise SystemExit(f"release provenance verification failed: {exc}") from exc
    print(f"release provenance OK: all payloads bind to {args.build_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
