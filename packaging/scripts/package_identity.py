#!/usr/bin/env python3
"""Release identity checks shared by wheel construction and verification."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from verify_native_stage import TARGETS


PROJECT_NAME = "holderpro"


def normalized_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def source_identity(repository: Path) -> tuple[str, str]:
    repository = repository.resolve()
    project = tomllib.loads(
        (repository / "pyproject.toml").read_text(encoding="utf-8")
    ).get("project")
    if not isinstance(project, dict):
        raise RuntimeError("pyproject.toml has no [project] table")
    name = project.get("name")
    version = project.get("version")
    if name != PROJECT_NAME or not isinstance(version, str) or not version:
        raise RuntimeError(
            f"package identity must be {PROJECT_NAME!r} with a nonempty version"
        )
    version_source = (repository / "src/holderpro/version.py").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r'^FALLBACK_VERSION\s*=\s*"([^"]+)"$', version_source, re.MULTILINE
    )
    if match is None:
        raise RuntimeError("src/holderpro/version.py has no FALLBACK_VERSION")
    if match.group(1) != version:
        raise RuntimeError(
            f"version mismatch: pyproject={version!r}, FALLBACK_VERSION={match.group(1)!r}"
        )
    if not re.fullmatch(r"[A-Za-z0-9.]+", version):
        raise RuntimeError(f"release wheel version is not normalized: {version!r}")
    return name, version


def validate_release_identity(
    repository: Path,
    expected_version: str,
    target: str,
    platform_tag: str,
) -> tuple[str, str]:
    if target not in TARGETS:
        raise RuntimeError(f"unknown HolderPro target: {target}")
    expected_tag = TARGETS[target].wheel_tag
    if platform_tag != expected_tag:
        raise RuntimeError(
            f"target {target} requires wheel tag {expected_tag}, got {platform_tag}"
        )
    name, source_version = source_identity(repository)
    if expected_version != source_version:
        raise RuntimeError(
            f"requested version {expected_version!r} does not match source {source_version!r}"
        )
    return name, source_version


def expected_dist_info(name: str, version: str) -> str:
    return f"{normalized_distribution(name)}-{version}.dist-info"
