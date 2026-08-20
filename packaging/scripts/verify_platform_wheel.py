#!/usr/bin/env python3
"""Verify every byte, identity field, and native artifact in a release wheel."""

from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
from email.message import Message
import hashlib
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from build_native_license_bundle import verify_native_license_mapping
from package_identity import (
    PROJECT_NAME,
    expected_dist_info,
    validate_release_identity,
)
from release_version import SOURCE_OFFER_NAME, identity_from_pep440, source_offer
from verify_native_stage import NATIVE_MANIFEST_NAME, TARGETS, verify_native_stage


PROHIBITED_VENDORED_COMPONENTS = {
    "_pyinstaller_hooks_contrib",
    "manifold3d",
    "numpy",
    "pyinstaller",
    "pyside2",
    "pyside6",
    "pyqt5",
    "pyqt6",
    "qt5",
    "qt6",
    "shapely",
    "shiboken2",
    "shiboken6",
    "trimesh",
    "vtk",
    "vtkmodules",
}
WINDOWS_SYSTEM_DLLS = {
    "advapi32.dll",
    "bcrypt.dll",
    "comdlg32.dll",
    "crypt32.dll",
    "gdi32.dll",
    "kernel32.dll",
    "ole32.dll",
    "oleaut32.dll",
    "opengl32.dll",
    "shell32.dll",
    "user32.dll",
    "ws2_32.dll",
}


def _record_digest(data: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return "sha256=" + encoded.decode("ascii")


def _safe_archive_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
    ):
        raise RuntimeError(f"wheel contains an unsafe path: {name!r}")


def _mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0o777


def _verify_corresponding_source_project_url(
    metadata: Message, version: str
) -> None:
    expected_source_url = identity_from_pep440(version).corresponding_source_url
    corresponding_urls = [
        value
        for value in metadata.get_all("Project-URL", [])
        if value.partition(",")[0].strip().casefold() == "corresponding source"
    ]
    expected_project_url = f"Corresponding Source, {expected_source_url}"
    if corresponding_urls != [expected_project_url]:
        raise RuntimeError(
            "wheel must contain exactly one version-matched Corresponding "
            "Source Project-URL"
        )


def _expected_source_entries(repository: Path) -> dict[str, bytes]:
    package = repository.resolve() / "src/holderpro"
    expected: dict[str, bytes] = {}
    candidates = [*package.rglob("*.py"), *(package / "assets").glob("*.svg")]
    for path in sorted(set(candidates)):
        if "__pycache__" in path.parts or path.is_symlink() or not path.is_file():
            continue
        name = (Path("holderpro") / path.relative_to(package)).as_posix()
        expected[name] = path.read_bytes()
    return expected


def _prohibited_vendored_paths(names: set[str]) -> list[str]:
    prohibited: list[str] = []
    for name in sorted(names):
        parts = PurePosixPath(name).parts
        components = {
            part.lower().replace("-", "_").split(".", 1)[0] for part in parts
        }
        basename = parts[-1].lower()
        is_msvc_runtime = bool(
            re.fullmatch(
                r"(?:msvcp|vcruntime|concrt|vcomp|vcamp)\d[^/]*\.dll",
                basename,
            )
            or basename == "ucrtbase.dll"
            or basename.startswith("api-ms-win-")
            or basename in WINDOWS_SYSTEM_DLLS
        )
        if components & PROHIBITED_VENDORED_COMPONENTS or is_msvc_runtime:
            prohibited.append(name)
    return prohibited


def _verify_closed_inventory(
    entries: dict[str, bytes],
    repository: Path,
    dist_info: str,
    native_names: set[str],
    version: str,
) -> None:
    """Require only reviewed HolderPro sources, metadata, and one native engine."""

    prohibited = _prohibited_vendored_paths(set(entries))
    if prohibited:
        raise RuntimeError(
            "wheel vendors prohibited Python, GUI, build, or system runtime files: "
            + ", ".join(prohibited)
        )

    native_license_prefix = f"{dist_info}/licenses/native/"
    native_license_files = {
        name.removeprefix(native_license_prefix): data
        for name, data in entries.items()
        if name.startswith(native_license_prefix)
    }
    try:
        reviewed_native_manifest = json.loads(
            (
                repository
                / "packaging/prusaslicer-native-dependency-sources.json"
            ).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("could not read the reviewed native dependency manifest") from exc
    verify_native_license_mapping(native_license_files, reviewed_native_manifest)

    expected_sources = _expected_source_entries(repository)
    expected_metadata = {
        f"{dist_info}/METADATA",
        f"{dist_info}/WHEEL",
        f"{dist_info}/entry_points.txt",
        f"{dist_info}/top_level.txt",
        f"{dist_info}/RECORD",
        f"{dist_info}/{SOURCE_OFFER_NAME}",
        f"{dist_info}/licenses/LICENSE",
        f"{dist_info}/licenses/THIRD_PARTY_NOTICES.md",
        (
            f"{dist_info}/licenses/upstream/"
            "prusaslicer-2.9.6-organic/LICENSE"
        ),
    }
    expected = set(expected_sources) | expected_metadata | native_names
    expected.update(native_license_prefix + name for name in native_license_files)
    actual = set(entries)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(
            f"wheel inventory is not closed: missing={missing}, extra={extra}"
        )
    changed = sorted(
        name for name, data in expected_sources.items() if entries[name] != data
    )
    if changed:
        raise RuntimeError(
            "wheel HolderPro sources differ from the release tree: "
            + ", ".join(changed)
        )

    expected_licenses = {
        f"{dist_info}/licenses/LICENSE": repository / "LICENSE",
        (
            f"{dist_info}/licenses/THIRD_PARTY_NOTICES.md"
        ): repository / "THIRD_PARTY_NOTICES.md",
        (
            f"{dist_info}/licenses/upstream/"
            "prusaslicer-2.9.6-organic/LICENSE"
        ): repository / "upstream/prusaslicer-2.9.6-organic/LICENSE",
    }
    changed_licenses = sorted(
        name
        for name, path in expected_licenses.items()
        if entries[name] != path.read_bytes()
    )
    if changed_licenses:
        raise RuntimeError(
            "wheel license files differ from the release tree: "
            + ", ".join(changed_licenses)
        )

    offer_name = f"{dist_info}/{SOURCE_OFFER_NAME}"
    if entries[offer_name] != source_offer(version):
        raise RuntimeError(
            "wheel corresponding-source offer is missing or version-mismatched"
        )


def _verify_record(
    entries: dict[str, bytes],
    record_name: str,
) -> None:
    rows = list(csv.reader(io.StringIO(entries[record_name].decode("utf-8"))))
    if any(len(row) != 3 for row in rows):
        raise RuntimeError("wheel RECORD contains a malformed row")
    names = [row[0] for row in rows]
    if len(names) != len(set(names)):
        raise RuntimeError("wheel RECORD contains duplicate paths")
    if set(names) != set(entries):
        missing = sorted(set(entries) - set(names))
        extra = sorted(set(names) - set(entries))
        raise RuntimeError(f"wheel RECORD path mismatch: missing={missing}, extra={extra}")
    for name, digest, size in rows:
        if name == record_name:
            if digest or size:
                raise RuntimeError("wheel RECORD must leave its own digest and size empty")
            continue
        data = entries[name]
        if digest != _record_digest(data) or size != str(len(data)):
            raise RuntimeError(f"wheel RECORD digest/size mismatch: {name}")


def _manifest_files(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = document.get("files")
    if not isinstance(records, list) or not records:
        raise RuntimeError("native digest manifest contains no files")
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("native digest manifest has a malformed file record")
        name = record.get("name")
        if (
            not isinstance(name, str)
            or not name
            or PurePosixPath(name).name != name
            or name in by_name
        ):
            raise RuntimeError(f"invalid native digest-manifest filename: {name!r}")
        digest = record.get("sha256")
        size = record.get("size")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise RuntimeError(f"invalid native SHA-256 for {name}")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise RuntimeError(f"invalid native size for {name}")
        by_name[name] = record
    return by_name


def _verify_native_manifest(
    entries: dict[str, bytes],
    native_names: set[str],
    target_name: str,
    version: str,
) -> dict[str, Any]:
    manifest_path = f"holderpro/_native/{NATIVE_MANIFEST_NAME}"
    try:
        document = json.loads(entries[manifest_path])
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("wheel lacks a valid native digest manifest") from exc
    if not isinstance(document, dict):
        raise RuntimeError("native digest manifest is not an object")
    if document.get("schema") != "holderpro.native-artifact-manifest/v1":
        raise RuntimeError("native digest manifest has the wrong schema")
    if document.get("target") != target_name:
        raise RuntimeError("native digest manifest target does not match the wheel")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        raise RuntimeError("native digest manifest has no provenance object")
    product = provenance.get("product")
    if not isinstance(product, dict) or product.get("version") != version:
        raise RuntimeError("native digest manifest version does not match the wheel")

    records = _manifest_files(document)
    manifested_paths = {f"holderpro/_native/{name}" for name in records}
    runtime_paths = native_names - {
        "holderpro/_native/__init__.py",
        manifest_path,
    }
    if manifested_paths != runtime_paths:
        raise RuntimeError(
            "native digest manifest file set does not match wheel contents"
        )
    for name, record in records.items():
        data = entries[f"holderpro/_native/{name}"]
        if hashlib.sha256(data).hexdigest() != record["sha256"] or len(data) != record["size"]:
            raise RuntimeError(f"native digest manifest mismatch: {name}")
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--platform-tag", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        project_name, source_version = validate_release_identity(
            args.repository, args.version, args.target, args.platform_tag
        )
        target = TARGETS[args.target]
        expected_filename = (
            f"{PROJECT_NAME}-{source_version}-py3-none-{target.wheel_tag}.whl"
        )
        if args.wheel.name != expected_filename:
            raise RuntimeError(
                f"wheel filename is {args.wheel.name!r}, expected {expected_filename!r}"
            )

        with zipfile.ZipFile(args.wheel) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise RuntimeError("wheel contains duplicate ZIP members")
            if any(name.endswith("/") for name in names):
                raise RuntimeError("wheel contains unexpected directory entries")
            for name in names:
                _safe_archive_name(name)
            entries = {name: archive.read(name) for name in names}
            infos = {name: archive.getinfo(name) for name in names}

        dist_info = expected_dist_info(project_name, source_version)
        dist_info_roots = {
            name.split("/", 1)[0]
            for name in names
            if name.split("/", 1)[0].endswith(".dist-info")
        }
        if dist_info_roots != {dist_info}:
            raise RuntimeError(f"wheel dist-info identity mismatch: {dist_info_roots}")
        metadata_name = f"{dist_info}/METADATA"
        wheel_name = f"{dist_info}/WHEEL"
        record_name = f"{dist_info}/RECORD"
        if any(name not in entries for name in (metadata_name, wheel_name, record_name)):
            raise RuntimeError("wheel is missing required dist-info metadata")

        metadata = BytesParser().parsebytes(entries[metadata_name])
        if metadata.get("Name") != PROJECT_NAME or metadata.get("Version") != source_version:
            raise RuntimeError(
                "wheel METADATA identity mismatch: "
                f"Name={metadata.get('Name')!r}, Version={metadata.get('Version')!r}"
            )
        _verify_corresponding_source_project_url(metadata, source_version)
        wheel_metadata = BytesParser().parsebytes(entries[wheel_name])
        expected_tag = f"py3-none-{target.wheel_tag}"
        if wheel_metadata.get("Root-Is-Purelib") != "false":
            raise RuntimeError("wheel incorrectly declares itself pure")
        if wheel_metadata.get_all("Tag", []) != [expected_tag]:
            raise RuntimeError("wheel WHEEL metadata has a wrong or ambiguous tag")

        _verify_record(entries, record_name)

        native_prefix = "holderpro/_native/"
        native_names = {name for name in names if name.startswith(native_prefix)}
        expected_engine = f"{native_prefix}{target.engine_name}"
        if expected_engine not in native_names:
            raise RuntimeError("wheel lacks the exact target engine")
        engine_occurrences = [
            name
            for name in names
            if PurePosixPath(name).name
            in {"holderpro-organic-engine", "holderpro-organic-engine.exe"}
        ]
        if engine_occurrences != [expected_engine]:
            raise RuntimeError(f"wheel engine cardinality/root mismatch: {engine_occurrences}")
        allowed_fixed = {
            f"{native_prefix}__init__.py",
            f"{native_prefix}{NATIVE_MANIFEST_NAME}",
            expected_engine,
        }
        extras = native_names - allowed_fixed
        if target.os_name == "windows":
            rejected = [
                name
                for name in extras
                if not name.lower().endswith(".dll")
                or _prohibited_vendored_paths({name})
            ]
        else:
            rejected = list(extras)
        if rejected:
            raise RuntimeError(
                "wheel contains unexpected or prohibited native files: "
                + ", ".join(sorted(rejected))
            )

        _verify_closed_inventory(
            entries, args.repository, dist_info, native_names, source_version
        )
        offer_name = f"{dist_info}/{SOURCE_OFFER_NAME}"
        if _mode(infos[offer_name]) != 0o644:
            raise RuntimeError("wheel corresponding-source offer mode must be 0644")
        native_license_prefix = f"{dist_info}/licenses/native/"
        invalid_license_modes = sorted(
            name
            for name in names
            if name.startswith(native_license_prefix) and _mode(infos[name]) != 0o644
        )
        if invalid_license_modes:
            raise RuntimeError(
                "wheel native license files must have mode 0644: "
                + ", ".join(invalid_license_modes)
            )

        for name in native_names:
            expected_mode = 0o755 if name == expected_engine else 0o644
            if _mode(infos[name]) != expected_mode:
                raise RuntimeError(
                    f"wheel native mode mismatch for {name}: "
                    f"{_mode(infos[name]):04o}, expected {expected_mode:04o}"
                )

        manifested_provenance = _verify_native_manifest(
            entries, native_names, args.target, source_version
        )
        with tempfile.TemporaryDirectory(prefix="holderpro-verify-wheel-") as temporary:
            native_bin = Path(temporary) / "bin"
            native_bin.mkdir()
            for name in native_names:
                basename = PurePosixPath(name).name
                if basename in {"__init__.py", NATIVE_MANIFEST_NAME}:
                    continue
                destination = native_bin / basename
                destination.write_bytes(entries[name])
                destination.chmod(_mode(infos[name]))
            actual_provenance = verify_native_stage(
                native_bin, source_version, args.target, args.build_id
            )
        if actual_provenance != manifested_provenance:
            raise RuntimeError("native manifest provenance does not match the executable")
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"platform wheel verification failed: {exc}") from exc

    print(f"platform wheel OK: {args.wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
