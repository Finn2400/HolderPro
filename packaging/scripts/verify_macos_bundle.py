#!/usr/bin/env python3
"""Verify Apple-compatible bundle versions derived from HolderPro's PEP 440 version."""

from __future__ import annotations

import argparse
import plistlib
import re
from pathlib import Path


def apple_versions(version: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?", version)
    if match is None:
        raise RuntimeError(f"unsupported HolderPro release version: {version!r}")
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    stage = match.group(4)
    stage_number = int(match.group(5) or 0)
    if stage_number > 29:
        raise RuntimeError("release stage number exceeds the macOS build-number range")
    offset = {"a": 0, "b": 30, "rc": 60, None: 99}[stage]
    return (
        f"{major}.{minor}.{patch}",
        str(major * 1_000_000 + minor * 10_000 + patch * 100 + offset + stage_number),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    plist = args.bundle / "Contents/Info.plist"
    try:
        document = plistlib.loads(plist.read_bytes())
        expected_short, expected_build = apple_versions(args.version)
        short = document.get("CFBundleShortVersionString")
        build = document.get("CFBundleVersion")
        if short != expected_short or re.fullmatch(r"\d+(?:\.\d+){0,2}", str(short)) is None:
            raise RuntimeError(
                f"CFBundleShortVersionString is {short!r}, expected {expected_short!r}"
            )
        if build != expected_build or re.fullmatch(r"\d+(?:\.\d+){0,2}", str(build)) is None:
            raise RuntimeError(f"CFBundleVersion is {build!r}, expected {expected_build!r}")
    except (OSError, plistlib.InvalidFileException, RuntimeError) as exc:
        raise SystemExit(f"macOS bundle version verification failed: {exc}") from exc
    print(f"macOS bundle versions OK: short={short}, build={build}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
