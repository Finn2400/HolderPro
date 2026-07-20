#!/usr/bin/env python3
"""Build and verify the native engine's closed legal-notice bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from fetch_dependency_sources import (
    load_manifest,
    native_source_records,
    verify_dependency_source_directory,
)


BUNDLE_SCHEMA = "holderpro.native-license-bundle/v1"
MANIFEST_NAME = "MANIFEST.json"
MAX_NOTICE_SIZE = 8 * 1024 * 1024
NOTICE_NAME = re.compile(
    r"^(?:licen[cs]e|copying|copyrights?|notices?|authors?)"
    r"(?:$|[._ -].*)",
    re.IGNORECASE,
)
NOTICE_DIRECTORIES = {"legal", "licence", "licences", "license", "licenses", "notices"}
SHA256 = re.compile(r"[0-9a-f]{64}")
GIT_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _canonical_json(document: object) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _semantic_digest(document: object) -> str:
    encoded = json.dumps(
        document, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative_path(value: object, *, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeError(f"{label} is not a safe relative path: {value!r}")
    raw_parts = value.split("/")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
        or any(ord(character) < 32 for character in value)
    ):
        raise RuntimeError(f"{label} is not a safe relative path: {value!r}")
    return path


def _safe_output_basename(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    if not result:
        result = "NOTICE"
    result = result[:100]
    if result.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        result = "NOTICE_" + result
    return result


def _component_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise RuntimeError(f"component has no filesystem-safe name: {value!r}")
    return slug[:64]


def _logical_archive_paths(paths: list[PurePosixPath]) -> dict[PurePosixPath, PurePosixPath]:
    if not paths:
        return {}
    first_parts = {path.parts[0] for path in paths}
    strip_wrapper = len(first_parts) == 1 and all(len(path.parts) > 1 for path in paths)
    return {
        path: PurePosixPath(*path.parts[1:]) if strip_wrapper else path
        for path in paths
    }


def _require_notice_data(data: bytes, *, source: str) -> bytes:
    if len(data) > MAX_NOTICE_SIZE:
        raise RuntimeError(f"native notice is larger than {MAX_NOTICE_SIZE} bytes: {source}")
    if not data.strip(b"\x00\t\n\r "):
        raise RuntimeError(f"native notice is empty: {source}")
    return data


def _is_logical_notice(path: PurePosixPath) -> bool:
    if len(path.parts) == 1:
        return NOTICE_NAME.fullmatch(path.name) is not None
    return (
        len(path.parts) == 2
        and path.parts[0].casefold() in NOTICE_DIRECTORIES
    )


def _selected_notice_paths(paths: list[PurePosixPath]) -> set[PurePosixPath]:
    primary = {path for path in paths if _is_logical_notice(path)}
    if primary:
        return primary
    fallback = [
        path
        for path in paths
        if len(path.parts) <= 2 and NOTICE_NAME.fullmatch(path.name) is not None
    ]
    if not fallback:
        return set()
    shallowest = min(len(path.parts) for path in fallback)
    return {path for path in fallback if len(path.parts) == shallowest}


def _zip_notices(archive: Path) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise RuntimeError(f"source archive contains duplicate members: {archive}")
        regular: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        for info in infos:
            if info.is_dir():
                continue
            path = _safe_relative_path(
                info.filename, label=f"archive member in {archive.name}"
            )
            unix_type = (info.external_attr >> 16) & 0o170000
            if unix_type == stat.S_IFLNK:
                continue
            if unix_type not in {0, stat.S_IFREG}:
                continue
            regular.append((info, path))
        logical = _logical_archive_paths([path for _, path in regular])
        selected = _selected_notice_paths(list(logical.values()))
        notices: list[tuple[str, bytes]] = []
        for info, source_path in regular:
            logical_path = logical[source_path]
            if logical_path not in selected:
                continue
            if info.flag_bits & 0x1:
                raise RuntimeError(f"native notice is encrypted: {info.filename}")
            if info.file_size > MAX_NOTICE_SIZE:
                raise RuntimeError(f"native notice is too large: {info.filename}")
            data = _require_notice_data(
                source.read(info), source=f"{archive.name}:{info.filename}"
            )
            notices.append((logical_path.as_posix(), data))
    return sorted(notices, key=lambda item: (item[0].casefold(), item[0]))


def _tar_notices(archive: Path) -> list[tuple[str, bytes]]:
    with tarfile.open(archive, mode="r:*") as source:
        members = source.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise RuntimeError(f"source archive contains duplicate members: {archive}")
        regular: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
        for member in members:
            path = _safe_relative_path(
                member.name, label=f"archive member in {archive.name}"
            )
            if member.isfile():
                regular.append((member, path))
        logical = _logical_archive_paths([path for _, path in regular])
        selected = _selected_notice_paths(list(logical.values()))
        notices: list[tuple[str, bytes]] = []
        for member, source_path in regular:
            logical_path = logical[source_path]
            if logical_path not in selected:
                continue
            if member.size > MAX_NOTICE_SIZE:
                raise RuntimeError(f"native notice is too large: {member.name}")
            stream = source.extractfile(member)
            if stream is None:  # pragma: no cover - guarded by member.isfile()
                raise RuntimeError(f"could not read native notice: {member.name}")
            data = _require_notice_data(
                stream.read(), source=f"{archive.name}:{member.name}"
            )
            notices.append((logical_path.as_posix(), data))
    return sorted(notices, key=lambda item: (item[0].casefold(), item[0]))


def archive_notices(archive: Path) -> list[tuple[str, bytes]]:
    """Read every root-level legal-notice-family file from an archive."""

    try:
        if zipfile.is_zipfile(archive):
            notices = _zip_notices(archive)
        else:
            notices = _tar_notices(archive)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"unsupported or invalid source archive: {archive}") from exc
    if not notices:
        raise RuntimeError(
            f"native source archive has no top-level legal notice: {archive.name}"
        )
    return notices


def _vendored_components(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = document.get("vendored_components")
    if not isinstance(value, list) or not value:
        raise RuntimeError("native manifest has no structured vendored_components list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise RuntimeError("vendored component record is not an object")
        name = item.get("name")
        license_value = item.get("license")
        notice_paths = item.get("notice_paths")
        if not isinstance(name, str) or not name or name in seen:
            raise RuntimeError(f"vendored component has an invalid name: {name!r}")
        if not isinstance(license_value, str) or not license_value:
            raise RuntimeError(f"vendored component {name} has no license")
        if not isinstance(notice_paths, list) or not notice_paths:
            raise RuntimeError(f"vendored component {name} has no notice_paths")
        safe_notices = [
            _safe_relative_path(path, label=f"vendored notice for {name}").as_posix()
            for path in notice_paths
        ]
        if len(safe_notices) != len(set(safe_notices)):
            raise RuntimeError(f"vendored component {name} has duplicate notice_paths")
        source_path = item.get("source_path")
        if source_path is not None:
            _safe_relative_path(source_path, label=f"source_path for {name}")
        if "kind" in item or "notices" in item:
            raise RuntimeError(f"vendored component {name} uses a reserved field")
        seen.add(name)
        result.append({**item, "notice_paths": safe_notices})
    return sorted(result, key=lambda item: (item["name"].casefold(), item["name"]))


def _native_component_declarations(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    components = document.get("components")
    if not isinstance(components, list) or not components:
        raise RuntimeError("native manifest contains no components")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in components:
        if not isinstance(item, dict):
            raise RuntimeError("native component record is not an object")
        name = item.get("name")
        version = item.get("version")
        license_value = item.get("license")
        digest = item.get("source_sha256")
        if not isinstance(name, str) or not name or name in seen:
            raise RuntimeError(f"native component has an invalid name: {name!r}")
        if not isinstance(version, str) or not version:
            raise RuntimeError(f"native component {name} has no version")
        if not isinstance(license_value, str) or not license_value:
            raise RuntimeError(f"native component {name} has no license")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise RuntimeError(f"native component {name} has an invalid source digest")
        seen.add(name)
        result.append(
            {
                "name": name,
                "version": version,
                "license": license_value,
                "source_sha256": digest,
            }
        )
    return sorted(result, key=lambda item: (item["name"].casefold(), item["name"]))


def _declarations_match(
    dependency_document: Mapping[str, Any], native_manifest: Mapping[str, Any]
) -> list[dict[str, Any]]:
    generated = [
        {
            "name": record["name"],
            "version": record["version"],
            "license": record["license"],
            "source_sha256": record["source_sha256"],
        }
        for record in native_source_records(dict(dependency_document))
    ]
    generated.sort(key=lambda item: (item["name"].casefold(), item["name"]))
    expected = _native_component_declarations(native_manifest)
    if generated != expected:
        raise RuntimeError(
            "generated dependency sources do not match the reviewed native manifest"
        )
    if _vendored_components(dependency_document) != _vendored_components(native_manifest):
        raise RuntimeError(
            "generated vendored components do not match the reviewed native manifest"
        )
    expected_commit = native_manifest.get("prusaslicer_commit")
    if dependency_document.get("prusa_source_commit") != expected_commit:
        raise RuntimeError("generated dependency sources have the wrong PrusaSlicer commit")
    return generated


def _verify_prusa_commit(prusa_source: Path, expected_commit: object) -> None:
    if not isinstance(expected_commit, str) or GIT_COMMIT.fullmatch(expected_commit) is None:
        raise RuntimeError("native manifest has an invalid PrusaSlicer commit")
    try:
        actual = subprocess.run(
            ["git", "-C", str(prusa_source), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"PrusaSlicer source is not a Git checkout: {prusa_source}") from exc
    if actual != expected_commit:
        raise RuntimeError(
            f"PrusaSlicer checkout is {actual}, expected {expected_commit}"
        )


def _prusa_path(prusa_source: Path, relative: str, *, label: str) -> Path:
    root = prusa_source.resolve()
    candidate = prusa_source / Path(relative)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{label} escapes or is missing from PrusaSlicer: {relative}") from exc
    if candidate.is_symlink():
        raise RuntimeError(f"{label} must not be a symlink: {relative}")
    return candidate


def _notice_record(source_path: str, bundle_path: str, data: bytes) -> dict[str, Any]:
    return {
        "source_path": source_path,
        "bundle_path": bundle_path,
        "sha256": _sha256(data),
        "size": len(data),
    }


def _build_payload(
    dependency_document: dict[str, Any],
    dependency_source_directory: Path,
    prusa_source: Path,
    native_manifest: dict[str, Any],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    declarations = _declarations_match(dependency_document, native_manifest)
    verified_archives = verify_dependency_source_directory(
        dependency_source_directory, dependency_document
    )
    archives = {path.parent.name: path for path in verified_archives}
    files: dict[str, bytes] = {}
    components: list[dict[str, Any]] = []

    for index, item in enumerate(declarations, start=1):
        archive = archives.get(item["source_sha256"])
        if archive is None:
            raise RuntimeError(f"native source archive is missing for {item['name']}")
        notices = archive_notices(archive)
        notice_records = []
        directory = f"native/{index:03d}-{_component_slug(item['name'])}"
        for notice_index, (source_path, data) in enumerate(notices, start=1):
            output_path = (
                f"{directory}/{notice_index:03d}-"
                f"{_safe_output_basename(PurePosixPath(source_path).name)}"
            )
            files[output_path] = data
            notice_records.append(_notice_record(source_path, output_path, data))
        components.append(
            {"kind": "native-source-archive", **item, "notices": notice_records}
        )

    for index, item in enumerate(_vendored_components(native_manifest), start=1):
        name = item["name"]
        source_path = item.get("source_path")
        if isinstance(source_path, str):
            _prusa_path(
                prusa_source, source_path, label=f"vendored source_path for {name}"
            )
        notice_records = []
        directory = f"vendored/{index:03d}-{_component_slug(name)}"
        for notice_index, relative in enumerate(item["notice_paths"], start=1):
            path = _prusa_path(
                prusa_source, relative, label=f"vendored notice for {name}"
            )
            if not path.is_file():
                raise RuntimeError(f"vendored notice is not a file for {name}: {relative}")
            data = _require_notice_data(
                path.read_bytes(), source=f"PrusaSlicer:{relative}"
            )
            output_path = (
                f"{directory}/{notice_index:03d}-"
                f"{_safe_output_basename(path.name)}"
            )
            files[output_path] = data
            notice_records.append(_notice_record(relative, output_path, data))
        declaration = {
            key: value for key, value in item.items() if key != "notice_paths"
        }
        components.append(
            {"kind": "prusaslicer-vendored", **declaration, "notices": notice_records}
        )

    file_records = [
        {"path": path, "sha256": _sha256(data), "size": len(data)}
        for path, data in sorted(files.items())
    ]
    manifest = {
        "schema": BUNDLE_SCHEMA,
        "prusa_source_commit": native_manifest["prusaslicer_commit"],
        "dependency_sources_sha256": _semantic_digest(dependency_document),
        "native_manifest_sha256": _semantic_digest(native_manifest),
        "components": components,
        "files": file_records,
    }
    return files, manifest


def _expected_component_declarations(
    native_manifest: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    native = _native_component_declarations(native_manifest)
    vendored = _vendored_components(native_manifest)
    return native, vendored


def verify_native_license_mapping(
    files: Mapping[str, bytes], native_manifest: Mapping[str, Any]
) -> dict[str, Any]:
    """Verify a complete bundle represented by relative path to bytes."""

    manifest_bytes = files.get(MANIFEST_NAME)
    if manifest_bytes is None:
        raise RuntimeError("native license bundle has no MANIFEST.json")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("native license bundle manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != BUNDLE_SCHEMA:
        raise RuntimeError("native license bundle manifest has the wrong schema")
    expected_manifest_keys = {
        "schema",
        "prusa_source_commit",
        "dependency_sources_sha256",
        "native_manifest_sha256",
        "components",
        "files",
    }
    if set(manifest) != expected_manifest_keys:
        raise RuntimeError("native license bundle manifest has unexpected fields")
    if manifest_bytes != _canonical_json(manifest):
        raise RuntimeError("native license bundle manifest is not canonical JSON")
    if manifest.get("native_manifest_sha256") != _semantic_digest(native_manifest):
        raise RuntimeError("native license bundle does not match the reviewed manifest")
    if manifest.get("prusa_source_commit") != native_manifest.get("prusaslicer_commit"):
        raise RuntimeError("native license bundle has the wrong PrusaSlicer commit")
    dependency_digest = manifest.get("dependency_sources_sha256")
    if not isinstance(dependency_digest, str) or SHA256.fullmatch(dependency_digest) is None:
        raise RuntimeError("native license bundle has no dependency-source digest")

    file_records = manifest.get("files")
    if not isinstance(file_records, list) or not file_records:
        raise RuntimeError("native license bundle manifest contains no files")
    if any(not isinstance(item, dict) for item in file_records):
        raise RuntimeError("native license bundle has a malformed file record")
    if file_records != sorted(file_records, key=lambda item: item.get("path", "")):
        raise RuntimeError("native license bundle file records are not sorted")
    declared_paths: set[str] = set()
    for record in file_records:
        if set(record) != {"path", "sha256", "size"}:
            raise RuntimeError("native license bundle file record has unexpected fields")
        path = _safe_relative_path(
            record.get("path"), label="native license bundle file"
        ).as_posix()
        if not path.startswith(("native/", "vendored/")) or path in declared_paths:
            raise RuntimeError(f"native license bundle has an invalid file path: {path}")
        data = files.get(path)
        if data is None:
            raise RuntimeError(f"native license bundle file is missing: {path}")
        digest = record.get("sha256")
        size = record.get("size")
        if (
            not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or digest != _sha256(data)
            or size != len(data)
        ):
            raise RuntimeError(f"native license bundle digest mismatch: {path}")
        _require_notice_data(data, source=f"bundle:{path}")
        declared_paths.add(path)
    actual_paths = set(files) - {MANIFEST_NAME}
    if actual_paths != declared_paths:
        missing = sorted(declared_paths - actual_paths)
        extra = sorted(actual_paths - declared_paths)
        raise RuntimeError(
            f"native license bundle is not closed: missing={missing}, extra={extra}"
        )

    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        raise RuntimeError("native license bundle manifest contains no components")
    native_expected, vendored_expected = _expected_component_declarations(native_manifest)
    native_actual: list[dict[str, Any]] = []
    vendored_actual: list[dict[str, Any]] = []
    referenced_paths: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            raise RuntimeError("native license bundle has a malformed component")
        notices = component.get("notices")
        if not isinstance(notices, list) or not notices:
            raise RuntimeError(
                f"native license component {component.get('name')} has no notices"
            )
        source_notice_paths = []
        for notice in notices:
            if not isinstance(notice, dict):
                raise RuntimeError("native license bundle has a malformed notice")
            if set(notice) != {"source_path", "bundle_path", "sha256", "size"}:
                raise RuntimeError("native license notice has unexpected fields")
            source_notice_path = _safe_relative_path(
                notice.get("source_path"), label="native license notice source"
            ).as_posix()
            source_notice_paths.append(source_notice_path)
            bundle_path = _safe_relative_path(
                notice.get("bundle_path"), label="native license notice bundle path"
            ).as_posix()
            if bundle_path not in declared_paths:
                raise RuntimeError(f"native notice references an unknown file: {bundle_path}")
            data = files[bundle_path]
            if (
                notice.get("sha256") != _sha256(data)
                or notice.get("size") != len(data)
            ):
                raise RuntimeError(f"native notice digest mismatch: {bundle_path}")
            referenced_paths.append(bundle_path)
        kind = component.get("kind")
        if kind == "native-source-archive":
            if set(component) != {
                "kind",
                "name",
                "version",
                "license",
                "source_sha256",
                "notices",
            }:
                raise RuntimeError("native license component has unexpected fields")
            native_actual.append(
                {
                    key: component.get(key)
                    for key in ("name", "version", "license", "source_sha256")
                }
            )
        elif kind == "prusaslicer-vendored":
            declaration = {
                key: value
                for key, value in component.items()
                if key not in {"kind", "notices"}
            }
            declaration["notice_paths"] = source_notice_paths
            vendored_actual.append(declaration)
        else:
            raise RuntimeError(f"native license component has an invalid kind: {kind!r}")
    native_actual.sort(key=lambda item: (str(item["name"]).casefold(), str(item["name"])))
    vendored_actual.sort(key=lambda item: (str(item["name"]).casefold(), str(item["name"])))
    if native_actual != native_expected or vendored_actual != vendored_expected:
        raise RuntimeError("native license component declarations do not match the manifest")
    if len(referenced_paths) != len(set(referenced_paths)) or set(referenced_paths) != declared_paths:
        raise RuntimeError("native license files are not referenced exactly once")
    return manifest


def verify_native_license_directory(
    directory: Path, native_manifest: Mapping[str, Any]
) -> dict[str, bytes]:
    """Read and verify an exact on-disk native legal-notice bundle."""

    directory = directory.absolute()
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError(f"native license directory does not exist: {directory}")
    files: dict[str, bytes] = {}
    actual_directories: set[str] = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"native license bundle contains a symlink: {path}")
        if path.is_dir():
            actual_directories.add(path.relative_to(directory).as_posix())
            continue
        if not path.is_file():
            raise RuntimeError(f"native license bundle contains a special file: {path}")
        relative = path.relative_to(directory).as_posix()
        _safe_relative_path(relative, label="native license bundle file")
        files[relative] = path.read_bytes()
    expected_directories: set[str] = set()
    for relative in files:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    if actual_directories != expected_directories:
        missing = sorted(expected_directories - actual_directories)
        extra = sorted(actual_directories - expected_directories)
        raise RuntimeError(
            f"native license directory tree is not closed: missing={missing}, extra={extra}"
        )
    verify_native_license_mapping(files, native_manifest)
    return files


def build_native_license_bundle(
    dependency_document: dict[str, Any],
    dependency_source_directory: Path,
    prusa_source: Path,
    native_manifest: dict[str, Any],
    output: Path,
    *,
    verify_prusa_commit: bool = True,
) -> dict[str, bytes]:
    """Create the bundle atomically and return its verified file mapping."""

    if native_manifest.get("schema") != "holderpro.native-dependency-sources/v1":
        raise RuntimeError("native dependency manifest has the wrong schema")
    if verify_prusa_commit:
        _verify_prusa_commit(prusa_source, native_manifest.get("prusaslicer_commit"))
    files, manifest = _build_payload(
        dependency_document,
        dependency_source_directory,
        prusa_source,
        native_manifest,
    )
    files[MANIFEST_NAME] = _canonical_json(manifest)
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"native license output already exists: {output}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for relative, data in sorted(files.items()):
            destination = temporary / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o644)
            os.utime(destination, (0, 0))
        for directory in sorted(
            (path for path in temporary.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            directory.chmod(0o755)
            os.utime(directory, (0, 0))
        temporary.chmod(0o755)
        os.utime(temporary, (0, 0))
        verify_native_license_directory(temporary, native_manifest)
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return verify_native_license_directory(output, native_manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dependency-source-directory", type=Path, required=True)
    parser.add_argument("--prusa-source", type=Path, required=True)
    parser.add_argument(
        "--native-manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "prusaslicer-native-dependency-sources.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        dependency_document = load_manifest(args.manifest)
        native_manifest = json.loads(args.native_manifest.read_text(encoding="utf-8"))
        files = build_native_license_bundle(
            dependency_document,
            args.dependency_source_directory,
            args.prusa_source,
            native_manifest,
            args.output,
        )
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"native legal-notice bundle OK: {len(files) - 1} notices in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
