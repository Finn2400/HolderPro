#!/usr/bin/env python3
"""Validate HolderPro release tags and emit their PEP 440 product version."""

from __future__ import annotations

import argparse
import re


TAG = re.compile(
    r"^v(?P<base>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<kind>alpha|beta|rc)\.(?P<number>[1-9]\d*))?$"
)
SUFFIX = {"alpha": "a", "beta": "b", "rc": "rc"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args()
    match = TAG.fullmatch(args.tag)
    if not match:
        raise SystemExit(
            "release tag must be vMAJOR.MINOR.PATCH or "
            "vMAJOR.MINOR.PATCH-(alpha|beta|rc).N"
        )
    version = f"{match['base']}.{match['minor']}.{match['patch']}"
    prerelease = match["kind"] is not None
    if prerelease:
        version += SUFFIX[match["kind"]] + match["number"]
    if args.github_output:
        print(f"pep440={version}")
        print(f"prerelease={'true' if prerelease else 'false'}")
        print(f"display={args.tag.removeprefix('v')}")
    else:
        print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
