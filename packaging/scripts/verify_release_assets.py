#!/usr/bin/env python3
"""Require the exact, closed HolderPro release-asset inventory."""

from __future__ import annotations

import argparse
from pathlib import Path

from verify_native_stage import TARGETS


def expected_assets(display: str, pep440: str, include_checksums: bool) -> set[str]:
    assets = {
        f"holderpro-{pep440}-py3-none-{target.wheel_tag}.whl"
        for target in TARGETS.values()
    }
    assets.update(
        {
            f"holderpro-{display}-corresponding-source.tar.zst",
            "dependency-sources.json",
        }
    )
    for target in TARGETS:
        assets.update(
            {
                f"HolderPro-{target}.cdx.json",
                f"HolderPro-{target}-native-manifest.json",
                f"HolderPro-{target}-build-environment.json",
                f"HolderPro-{target}-asset-manifest.json",
            }
        )
    if include_checksums:
        assets.add("SHA256SUMS")
    return assets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--display-version", required=True)
    parser.add_argument("--pep440-version", required=True)
    parser.add_argument("--include-checksums", action="store_true")
    args = parser.parse_args()
    if not args.directory.is_dir():
        raise SystemExit(f"release asset directory does not exist: {args.directory}")
    entries = list(args.directory.iterdir())
    invalid = sorted(path.name for path in entries if not path.is_file() or path.is_symlink())
    if invalid:
        raise SystemExit("release assets contain non-files or links: " + ", ".join(invalid))
    actual = {path.name for path in entries}
    expected = expected_assets(
        args.display_version, args.pep440_version, args.include_checksums
    )
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise SystemExit(f"release asset inventory mismatch: missing={missing}, extra={extra}")
    print(f"release asset inventory OK: {len(actual)} exact files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
