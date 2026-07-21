from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "packaging/scripts"))

from build_native_license_bundle import (  # noqa: E402
    archive_notices,
    build_native_license_bundle,
    verify_native_license_directory,
    verify_native_license_mapping,
)


def _zip_archive(path: Path, *, with_notice: bool = True) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("alpha-source/README.md", "alpha source\n")
        archive.writestr(
            "alpha-source/src/vendor/deep/LICENSE", "nested, not root\n"
        )
        if with_notice:
            archive.writestr("alpha-source/LICENSE.txt", "Alpha license\n")
            archive.writestr("alpha-source/AUTHORS", "Alpha authors\n")


def _tar_archive(path: Path) -> None:
    with tarfile.open(path, "w:bz2") as archive:
        for name, data in {
            "beta-source/README": b"beta source\n",
            "beta-source/COPYING.LESSER": b"Beta copying terms\n",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))


def _fixture(
    tmp_path: Path, *, first_has_notice: bool = True
) -> tuple[dict[str, object], Path, Path, dict[str, object]]:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    alpha = inputs / "alpha.zip"
    beta = inputs / "beta.tar.bz2"
    _zip_archive(alpha, with_notice=first_has_notice)
    _tar_archive(beta)
    alpha_digest = hashlib.sha256(alpha.read_bytes()).hexdigest()
    beta_digest = hashlib.sha256(beta.read_bytes()).hexdigest()
    source_directory = tmp_path / "dependency-sources"
    for archive, digest in ((alpha, alpha_digest), (beta, beta_digest)):
        destination = source_directory / digest / archive.name
        destination.parent.mkdir(parents=True)
        destination.write_bytes(archive.read_bytes())

    commit = "a" * 40
    vendored = [
        {
            "name": "Vendored Example",
            "version": "snapshot-1",
            "snapshot": commit,
            "license": "MIT",
            "source_path": "vendor/example",
            "notice_paths": [
                "vendor/example/LICENSE",
                "vendor/example/example.h",
            ],
        }
    ]
    prusa_source = tmp_path / "PrusaSlicer"
    (prusa_source / "vendor/example").mkdir(parents=True)
    (prusa_source / "vendor/example/LICENSE").write_text(
        "Vendored license\n", encoding="utf-8"
    )
    (prusa_source / "vendor/example/example.h").write_text(
        "/* MIT notice */\n", encoding="utf-8"
    )
    components = [
        {
            "name": "Alpha",
            "version": "1.0",
            "license": "MIT",
            "source_url": "https://example.invalid/alpha.zip",
            "source_sha256": alpha_digest,
            "definition": "deps/alpha.cmake",
        },
        {
            "name": "Beta",
            "version": "2.0",
            "license": "LGPL-3.0-or-later",
            "source_url": "https://example.invalid/beta.tar.bz2",
            "source_sha256": beta_digest,
            "definition": "deps/beta.cmake",
        },
    ]
    native_manifest: dict[str, object] = {
        "schema": "holderpro.native-dependency-sources/v1",
        "prusaslicer_commit": commit,
        "components": components,
        "system_components": {},
        "vendored_components": vendored,
    }
    dependency_document: dict[str, object] = {
        "schema": "holderpro.dependency-sources/v1",
        "holderpro_build_id": "test",
        "prusa_source_commit": commit,
        "components": [
            {
                **item,
                "ecosystem": "native-source-archive",
                "relationship": "native-build-input",
                "source_filename": Path(str(item["source_url"])).name,
            }
            for item in components
        ],
        "system_components": {},
        "vendored_components": vendored,
    }
    return dependency_document, source_directory, prusa_source, native_manifest


def test_bundle_is_deterministic_complete_and_verifiable(tmp_path: Path) -> None:
    document, sources, prusa, native_manifest = _fixture(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_files = build_native_license_bundle(
        document,
        sources,
        prusa,
        native_manifest,
        first,
        verify_prusa_commit=False,
    )
    second_files = build_native_license_bundle(
        document,
        sources,
        prusa,
        native_manifest,
        second,
        verify_prusa_commit=False,
    )

    assert first_files == second_files
    assert verify_native_license_directory(first, native_manifest) == first_files
    manifest = verify_native_license_mapping(first_files, native_manifest)
    assert len(manifest["components"]) == 3
    native_notices = [
        notice["source_path"]
        for component in manifest["components"]
        if component["kind"] == "native-source-archive"
        for notice in component["notices"]
    ]
    assert native_notices == ["AUTHORS", "LICENSE.txt", "COPYING.LESSER"]
    assert "src/vendor/deep/LICENSE" not in native_notices
    assert all(path.stat().st_mode & 0o777 == 0o644 for path in first.rglob("*") if path.is_file())


def test_source_archive_without_root_notice_fails_closed(tmp_path: Path) -> None:
    document, sources, prusa, native_manifest = _fixture(
        tmp_path, first_has_notice=False
    )

    with pytest.raises(RuntimeError, match="no top-level legal notice"):
        build_native_license_bundle(
            document,
            sources,
            prusa,
            native_manifest,
            tmp_path / "output",
            verify_prusa_commit=False,
        )


def test_missing_declared_vendored_notice_fails_closed(tmp_path: Path) -> None:
    document, sources, prusa, native_manifest = _fixture(tmp_path)
    (prusa / "vendor/example/example.h").unlink()

    with pytest.raises(RuntimeError, match="escapes or is missing"):
        build_native_license_bundle(
            document,
            sources,
            prusa,
            native_manifest,
            tmp_path / "output",
            verify_prusa_commit=False,
        )


def test_bundle_digest_tampering_and_extra_paths_are_rejected(tmp_path: Path) -> None:
    document, sources, prusa, native_manifest = _fixture(tmp_path)
    output = tmp_path / "output"
    files = build_native_license_bundle(
        document,
        sources,
        prusa,
        native_manifest,
        output,
        verify_prusa_commit=False,
    )
    notice = next(path for path in files if path != "MANIFEST.json")
    tampered = dict(files)
    tampered[notice] += b"tampered"

    with pytest.raises(RuntimeError, match="digest mismatch"):
        verify_native_license_mapping(tampered, native_manifest)

    (output / "unexpected-empty-directory").mkdir()
    with pytest.raises(RuntimeError, match="directory tree is not closed"):
        verify_native_license_directory(output, native_manifest)


def test_archive_reader_rejects_nested_notice_as_a_substitute(tmp_path: Path) -> None:
    archive = tmp_path / "nested-only.zip"
    _zip_archive(archive, with_notice=False)

    with pytest.raises(RuntimeError, match="no top-level legal notice"):
        archive_notices(archive)


def test_archive_reader_includes_root_licenses_directory(tmp_path: Path) -> None:
    archive = tmp_path / "licenses-directory.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("component/LICENSES/MIT.txt", "MIT terms\n")
        source.writestr("component/LICENSES/BSD.txt", "BSD terms\n")
        source.writestr("component/src/LICENSE", "nested source license\n")

    assert [name for name, _ in archive_notices(archive)] == [
        "LICENSES/BSD.txt",
        "LICENSES/MIT.txt",
    ]


def test_archive_reader_uses_only_shallowest_fallback_notices(tmp_path: Path) -> None:
    archive = tmp_path / "expat-layout.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("component/expat/AUTHORS", "authors\n")
        source.writestr("component/expat/COPYING", "copying\n")
        source.writestr("component/vendor/deep/LICENSE", "nested vendor\n")

    assert [name for name, _ in archive_notices(archive)] == [
        "expat/AUTHORS",
        "expat/COPYING",
    ]
