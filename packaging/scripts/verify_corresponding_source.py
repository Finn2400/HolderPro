#!/usr/bin/env python3
"""Safely extract and verify a HolderPro corresponding-source archive."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import tarfile
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk():
            target = Path(member.linkname)
            if target.is_absolute() or ".." in target.parts:
                raise RuntimeError(f"unsafe archive link: {member.name} -> {member.linkname}")
    return members


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
                subprocess.run(["zstd", "-q", "-d", "-c", str(args.archive)], check=True, stdout=stream)
        with tarfile.open(tar_path, "r:*") as archive:
            members = safe_members(archive)
            archive.extractall(directory, members=members, filter="data")
        roots = [path for path in directory.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise SystemExit("archive must contain exactly one root directory")
        root = roots[0]
        manifest = root / "SOURCE-MANIFEST.sha256"
        required = [
            root / "source/holderpro/LICENSE",
            root / "source/holderpro/native/src/main.cpp",
            root / "source/prusaslicer/src/libslic3r/Support/OrganicSupport.cpp",
            root / "dependency-sources.json",
            root / "BUILD-INPUTS.json",
        ]
        missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
        if missing:
            raise SystemExit("source archive is incomplete: " + ", ".join(missing))
        failures = []
        for line in manifest.read_text(encoding="utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            path = root / relative
            if not path.is_file() or digest(path) != expected:
                failures.append(relative)
        if failures:
            raise SystemExit("source manifest verification failed: " + ", ".join(failures))
    print("corresponding-source archive OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
