#!/usr/bin/env python3
"""Reject release-source omissions and accidental user/build artifacts."""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
import tomllib
from pathlib import Path


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=False,
        stdout=subprocess.PIPE,
    )
    if result.returncode == 0 and result.stdout:
        return sorted(
            item.decode("utf-8", errors="surrogateescape")
            for item in result.stdout.split(b"\0")
            if item
        )
    fallback = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    return sorted(
        item.decode("utf-8", errors="surrogateescape")
        for item in fallback.split(b"\0")
        if item
    )


def matches(path: str, pattern: str) -> bool:
    return Path(path).match(pattern) or fnmatch.fnmatchcase(path, pattern)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument(
        "--manifest", type=Path, default=Path("packaging/source-manifest.toml")
    )
    args = parser.parse_args()
    root = args.repository.resolve()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))

    errors: list[str] = []
    files = tracked_files(root)
    file_set = set(files)
    fixture_roots = [
        item.rstrip("/") for item in manifest.get("synthetic_fixture_roots", [])
    ]

    def approved_fixture(path: str) -> bool:
        return any(path == item or path.startswith(item + "/") for item in fixture_roots)

    for required in manifest["required_paths"]:
        if required not in file_set and not (root / required).is_file():
            errors.append(f"required source path is missing: {required}")
    for path in files:
        for pattern in manifest["forbidden_globs"]:
            if matches(path, pattern) and not approved_fixture(path):
                errors.append(f"forbidden release-source path is tracked: {path}")
                break
    for path in files:
        suffix = Path(path).suffix.lower()
        if suffix in {".stl", ".3mf", ".obj", ".npz"}:
            if not approved_fixture(path):
                errors.append(f"model-derived file is outside an approved fixture root: {path}")

    if errors:
        print("source manifest verification failed:", file=sys.stderr)
        for error in sorted(set(errors)):
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"source manifest OK: {len(files)} source files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
