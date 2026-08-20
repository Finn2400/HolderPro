#!/usr/bin/env python3
"""Validate HolderPro release tags and emit their PEP 440 product version."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass


TAG = re.compile(
    r"^v(?P<base>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<kind>alpha|beta|rc)\.(?P<number>[1-9]\d*))?$"
)
SUFFIX = {"alpha": "a", "beta": "b", "rc": "rc"}
PEP440 = re.compile(
    r"^(?P<base>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))"
    r"(?:(?P<kind>a|b|rc)(?P<number>[1-9]\d*))?$"
)
PEP440_KIND = {"a": "alpha", "b": "beta", "rc": "rc"}
RELEASE_BASE_URL = "https://github.com/Finn2400/HolderPro/releases/download"
SOURCE_OFFER_NAME = "SOURCE-OFFER.txt"


@dataclass(frozen=True)
class ReleaseIdentity:
    """Canonical names derived from one normalized PEP 440 version."""

    pep440: str
    display: str
    tag: str
    prerelease: bool

    @property
    def corresponding_source_name(self) -> str:
        return f"holderpro-{self.display}-corresponding-source.tar.zst"

    @property
    def corresponding_source_url(self) -> str:
        return (
            f"{RELEASE_BASE_URL}/{self.tag}/"
            f"{self.corresponding_source_name}"
        )


def identity_from_pep440(version: str) -> ReleaseIdentity:
    """Map the project's canonical PEP 440 spelling to release asset names."""

    match = PEP440.fullmatch(version)
    if match is None:
        raise ValueError(
            "version must be normalized MAJOR.MINOR.PATCH or "
            "MAJOR.MINOR.PATCH(a|b|rc)N"
        )
    kind = match["kind"]
    display = match["base"]
    if kind is not None:
        display += f"-{PEP440_KIND[kind]}.{match['number']}"
    return ReleaseIdentity(
        pep440=version,
        display=display,
        tag=f"v{display}",
        prerelease=kind is not None,
    )


def source_offer(version: str) -> bytes:
    """Return the exact corresponding-source notice embedded in a wheel."""

    release = identity_from_pep440(version)
    return (
        "HolderPro corresponding source\n"
        f"Release: {release.tag}\n"
        f"Source archive: {release.corresponding_source_url}\n"
        "\n"
        "This wheel is licensed under AGPL-3.0-or-later. The archive at the URL "
        "above is the complete corresponding source for this exact release.\n"
    ).encode("utf-8")


def identity_from_tag(tag: str) -> ReleaseIdentity:
    """Validate a release tag and return its canonical PEP 440 identity."""

    match = TAG.fullmatch(tag)
    if match is None:
        raise ValueError(
            "release tag must be vMAJOR.MINOR.PATCH or "
            "vMAJOR.MINOR.PATCH-(alpha|beta|rc).N"
        )
    version = f"{match['base']}.{match['minor']}.{match['patch']}"
    if match["kind"] is not None:
        version += SUFFIX[match["kind"]] + match["number"]
    identity = identity_from_pep440(version)
    if identity.tag != tag:  # pragma: no cover - both regexes are intentionally strict
        raise ValueError("release tag is not canonical")
    return identity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args()
    try:
        release = identity_from_tag(args.tag)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.github_output:
        print(f"pep440={release.pep440}")
        print(f"prerelease={'true' if release.prerelease else 'false'}")
        print(f"display={release.display}")
    else:
        print(release.pep440)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
