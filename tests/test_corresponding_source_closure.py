from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "packaging/scripts"))

import fetch_dependency_sources as fetcher  # noqa: E402
from build_dependency_manifest import source_filename  # noqa: E402
from verify_corresponding_source import verify_source_manifest  # noqa: E402


def _document(payload: bytes = b"native source") -> tuple[dict[str, object], str]:
    digest = hashlib.sha256(payload).hexdigest()
    return (
        {
            "schema": "holderpro.dependency-sources/v1",
            "components": [
                {
                    "name": "Fixture",
                    "version": "1",
                    "license": "MIT",
                    "ecosystem": "native-source-archive",
                    "relationship": "native-build-input",
                    "source_url": "https://example.invalid/fixture.tar.gz",
                    "source_filename": "fixture.tar.gz",
                    "source_sha256": digest,
                }
            ],
        },
        digest,
    )


def test_source_filename_is_derived_from_url_path_only() -> None:
    assert (
        source_filename(
            "https://example.invalid/releases/source%20archive.tar.gz?download=1"
        )
        == "source archive.tar.gz"
    )


def test_dependency_source_directory_is_an_exact_hash_verified_set(
    tmp_path: Path,
) -> None:
    payload = b"native source"
    document, digest = _document(payload)
    archive = tmp_path / digest / "fixture.tar.gz"
    archive.parent.mkdir()
    archive.write_bytes(payload)

    assert fetcher.verify_dependency_source_directory(tmp_path, document) == [archive]

    (tmp_path / "unlisted").mkdir()
    with pytest.raises(RuntimeError, match="not closed"):
        fetcher.verify_dependency_source_directory(tmp_path, document)


def test_dependency_source_directory_rejects_hash_mismatch(tmp_path: Path) -> None:
    document, digest = _document()
    archive = tmp_path / digest / "fixture.tar.gz"
    archive.parent.mkdir()
    archive.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        fetcher.verify_dependency_source_directory(tmp_path, document)


def test_fetch_builds_output_atomically_and_refuses_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"native source"
    document, _digest = _document(payload)
    output = tmp_path / "sources"

    monkeypatch.setattr(
        fetcher,
        "_download",
        lambda _record, destination: destination.write_bytes(payload),
    )

    verified = fetcher.fetch_dependency_sources(document, output)
    assert len(verified) == 1
    with pytest.raises(RuntimeError, match="already exists"):
        fetcher.fetch_dependency_sources(document, output)


def test_source_manifest_rejects_unlisted_files_and_directories(tmp_path: Path) -> None:
    payload = tmp_path / "source.txt"
    payload.write_bytes(b"source")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    (tmp_path / "SOURCE-MANIFEST.sha256").write_text(
        f"{digest}  source.txt\n", encoding="utf-8"
    )

    verify_source_manifest(tmp_path)

    extra = tmp_path / "extra"
    extra.mkdir()
    with pytest.raises(RuntimeError, match="closed directory set"):
        verify_source_manifest(tmp_path)
