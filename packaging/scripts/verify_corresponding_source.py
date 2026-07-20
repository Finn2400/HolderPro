#!/usr/bin/env python3
"""Safely extract and verify a HolderPro corresponding-source archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from build_corresponding_source import PRUSA_COMMIT, validate_dependency_manifest
from fetch_dependency_sources import verify_dependency_source_directory


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    names: set[str] = set()
    for member in members:
        archive_path = PurePosixPath(member.name)
        name = archive_path.as_posix()
        if (
            archive_path.is_absolute()
            or ".." in archive_path.parts
            or "\\" in member.name
            or name in {"", "."}
            or name != member.name
        ):
            raise RuntimeError(f"unsafe archive path: {member.name}")
        if name in names:
            raise RuntimeError(f"duplicate archive path: {member.name}")
        names.add(name)
        if member.issym() or member.islnk():
            raise RuntimeError(f"source archive must not contain links: {member.name}")
        if not member.isfile() and not member.isdir():
            raise RuntimeError(f"source archive contains a special file: {member.name}")
    return members


def verify_source_manifest(root: Path) -> None:
    manifest = root / "SOURCE-MANIFEST.sha256"
    if manifest.is_symlink() or not manifest.is_file():
        raise RuntimeError("source archive has no SOURCE-MANIFEST.sha256")

    expected: dict[str, str] = {}
    for line_number, line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise RuntimeError(f"malformed source manifest line {line_number}")
        expected_digest, relative = match.groups()
        manifest_path = PurePosixPath(relative)
        if (
            manifest_path.is_absolute()
            or ".." in manifest_path.parts
            or "\\" in relative
            or relative == "SOURCE-MANIFEST.sha256"
        ):
            raise RuntimeError(f"unsafe source manifest path: {relative}")
        canonical = manifest_path.as_posix()
        if canonical != relative or relative in expected:
            raise RuntimeError(
                f"duplicate or non-canonical source manifest path: {relative}"
            )
        expected[relative] = expected_digest

    actual_files: dict[str, Path] = {}
    actual_directories: set[str] = set()
    for filesystem_path in root.rglob("*"):
        relative = filesystem_path.relative_to(root).as_posix()
        if relative == "SOURCE-MANIFEST.sha256":
            continue
        if filesystem_path.is_symlink():
            raise RuntimeError(f"source archive contains a link: {relative}")
        if filesystem_path.is_dir():
            actual_directories.add(relative)
        elif filesystem_path.is_file():
            actual_files[relative] = filesystem_path
        else:
            raise RuntimeError(f"source archive contains a special file: {relative}")

    if set(actual_files) != set(expected):
        missing = sorted(set(expected) - set(actual_files))
        extra = sorted(set(actual_files) - set(expected))
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unlisted " + ", ".join(extra))
        raise RuntimeError(
            "source manifest is not a closed file set: " + "; ".join(details)
        )

    expected_directories: set[str] = set()
    for relative in expected:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    if actual_directories != expected_directories:
        missing = sorted(expected_directories - actual_directories)
        extra = sorted(actual_directories - expected_directories)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unlisted " + ", ".join(extra))
        raise RuntimeError(
            "source manifest is not a closed directory set: " + "; ".join(details)
        )

    failures = [
        relative
        for relative, expected_digest in expected.items()
        if digest(actual_files[relative]) != expected_digest
    ]
    if failures:
        raise RuntimeError(
            "source manifest verification failed: " + ", ".join(failures)
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="holderpro-verify-source-") as temporary:
        directory = Path(temporary)
        tar_path = args.archive
        if args.archive.name.endswith(".tar.zst"):
            tar_path = directory / "source.tar"
            with tar_path.open("wb") as stream:
                subprocess.run(
                    ["zstd", "-q", "-d", "-c", str(args.archive)],
                    check=True,
                    stdout=stream,
                )
        with tarfile.open(tar_path, "r:*") as archive:
            members = safe_members(archive)
            archive.extractall(directory, members=members, filter="data")
        entries = [path for path in directory.iterdir() if path != tar_path]
        if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
            raise SystemExit("archive must contain exactly one root directory")
        root = entries[0]
        required = [
            root / "source/holderpro/LICENSE",
            root / "source/holderpro/native/src/main.cpp",
            root / "source/prusaslicer/src/libslic3r/Support/OrganicSupport.cpp",
            root / "dependency-sources.json",
            root / "dependency-source-archives",
            root / "BUILD-INPUTS.json",
            root / "SOURCE-MANIFEST.sha256",
        ]
        missing = [
            str(path.relative_to(root))
            for path in required
            if not path.exists() or path.is_symlink()
        ]
        if missing:
            raise SystemExit("source archive is incomplete: " + ", ".join(missing))
        verify_source_manifest(root)

        build_inputs = json.loads(
            (root / "BUILD-INPUTS.json").read_text(encoding="utf-8")
        )
        if (
            not isinstance(build_inputs, dict)
            or build_inputs.get("schema") != "holderpro.build-inputs/v1"
            or build_inputs.get("prusa_commit") != PRUSA_COMMIT
            or not isinstance(build_inputs.get("holderpro_commit"), str)
        ):
            raise SystemExit("source archive has invalid build inputs")
        dependency_manifest = root / "dependency-sources.json"
        dependency_document = validate_dependency_manifest(
            dependency_manifest,
            root / "source/holderpro/packaging/dependency-source-requirements.json",
            build_inputs["holderpro_commit"],
        )
        verify_dependency_source_directory(
            root / "dependency-source-archives",
            dependency_document,
        )
    print("corresponding-source archive OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
