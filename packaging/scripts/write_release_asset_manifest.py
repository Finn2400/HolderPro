#!/usr/bin/env python3
"""Digest-bind one target's final release payloads to its HolderPro commit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from verify_native_stage import TARGETS


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def expected_names(target_name: str, display: str, version: str) -> set[str]:
    target = TARGETS[target_name]
    result = {
        f"holderpro-{version}-py3-none-{target.wheel_tag}.whl",
        f"HolderPro-{target_name}.cdx.json",
        f"HolderPro-{target_name}-native-manifest.json",
        f"HolderPro-{target_name}-build-environment.json",
        (
            f"THIRD_PARTY_LICENSES-{target_name}.zip"
            if target_name == "windows-x86_64"
            else f"THIRD_PARTY_LICENSES-{target_name}.tar.gz"
        ),
    }
    if target_name.startswith("macos-"):
        result.add(f"HolderPro-{display}-{target_name}.dmg")
    elif target_name == "windows-x86_64":
        result.update(
            {
                f"HolderPro-{display}-windows-x86_64-setup.exe",
                f"HolderPro-{display}-windows-x86_64-portable.zip",
            }
        )
    elif target_name == "linux-x86_64":
        result.update(
            {
                f"HolderPro-{display}-linux-x86_64.AppImage",
                f"HolderPro-{display}-linux-x86_64.tar.zst",
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--display-version", required=True)
    parser.add_argument("--pep440-version", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected = expected_names(
        args.target, args.display_version, args.pep440_version
    )
    actual = {path.name for path in args.directory.iterdir() if path.is_file()}
    if actual != expected:
        raise SystemExit(
            "target asset set mismatch before manifest creation: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    document = {
        "schema": "holderpro.release-asset-manifest/v1",
        "target": args.target,
        "version": args.pep440_version,
        "display_version": args.display_version,
        "holderpro_build_id": args.build_id,
        "files": [
            {
                "name": name,
                "sha256": sha256(args.directory / name),
                "size": (args.directory / name).stat().st_size,
            }
            for name in sorted(expected)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
