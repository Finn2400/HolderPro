from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "packaging/scripts"))

from verify_native_stage import PRUSA_COMMIT  # noqa: E402
from verify_release_provenance import (  # noqa: E402
    require_native_license_binding,
    semantic_digest,
)


def _wheel_with_native_legal_manifest(
    path: Path, dependency_sources_digest: str
) -> zipfile.ZipFile:
    manifest = {
        "schema": "holderpro.native-license-bundle/v1",
        "prusa_source_commit": PRUSA_COMMIT,
        "dependency_sources_sha256": dependency_sources_digest,
    }
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(
            "holderpro-0.1.0a1.dist-info/licenses/native/MANIFEST.json",
            json.dumps(manifest),
        )
    return zipfile.ZipFile(path)


def test_release_provenance_binds_wheel_notices_to_dependency_sources(
    tmp_path: Path,
) -> None:
    dependencies = {"schema": "holderpro.dependency-sources/v1", "sources": []}
    digest = semantic_digest(dependencies)

    with _wheel_with_native_legal_manifest(tmp_path / "holderpro.whl", digest) as package:
        require_native_license_binding(package, "macos-arm64", digest)


def test_release_provenance_rejects_wheel_notices_for_other_sources(
    tmp_path: Path,
) -> None:
    expected = semantic_digest({"sources": ["expected"]})
    foreign = semantic_digest({"sources": ["foreign"]})

    with _wheel_with_native_legal_manifest(tmp_path / "holderpro.whl", foreign) as package:
        with pytest.raises(RuntimeError, match="does not match the release sources"):
            require_native_license_binding(package, "windows-x86_64", expected)
