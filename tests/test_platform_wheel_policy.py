from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "packaging/scripts"))

from build_platform_wheel import (  # noqa: E402
    _expected_source_entries as build_expected_source_entries,
    _verify_backend_inventory,
    rewrite_wheel,
)
from build_native_license_bundle import _canonical_json, _semantic_digest  # noqa: E402
from package_identity import expected_dist_info  # noqa: E402
from release_version import (  # noqa: E402
    SOURCE_OFFER_NAME,
    identity_from_pep440,
    identity_from_tag,
    source_offer,
)
from verify_platform_wheel import (  # noqa: E402
    _expected_source_entries as verify_expected_source_entries,
    _prohibited_vendored_paths,
    _verify_closed_inventory,
    _verify_corresponding_source_project_url,
)


VERSION = "0.1.0a1"
DIST_INFO = expected_dist_info("holderpro", VERSION)
NATIVE_LICENSE_PREFIX = f"{DIST_INFO}/licenses/native/"


def _native_license_files() -> dict[str, bytes]:
    native_manifest = json.loads(
        (
            PROJECT / "packaging/prusaslicer-native-dependency-sources.json"
        ).read_text(encoding="utf-8")
    )
    files: dict[str, bytes] = {}
    components = []
    for index, item in enumerate(
        sorted(native_manifest["components"], key=lambda value: value["name"].casefold()),
        start=1,
    ):
        path = f"native/{index:03d}/001-LICENSE"
        data = f"license notice for {item['name']}\n".encode()
        files[path] = data
        notice = {
            "source_path": "LICENSE",
            "bundle_path": path,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
        components.append(
            {
                "kind": "native-source-archive",
                "name": item["name"],
                "version": item["version"],
                "license": item["license"],
                "source_sha256": item["source_sha256"],
                "notices": [notice],
            }
        )
    for index, item in enumerate(
        sorted(
            native_manifest["vendored_components"],
            key=lambda value: value["name"].casefold(),
        ),
        start=1,
    ):
        notices = []
        for notice_index, source_path in enumerate(item["notice_paths"], start=1):
            path = f"vendored/{index:03d}/{notice_index:03d}-NOTICE"
            data = f"vendored notice for {item['name']}:{source_path}\n".encode()
            files[path] = data
            notices.append(
                {
                    "source_path": source_path,
                    "bundle_path": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                }
            )
        declaration = {
            key: item[key]
            for key in ("name", "version", "snapshot", "license", "source_path")
            if key in item
        }
        components.append(
            {"kind": "prusaslicer-vendored", **declaration, "notices": notices}
        )
    file_records = [
        {
            "path": path,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
        for path, data in sorted(files.items())
    ]
    bundle_manifest = {
        "schema": "holderpro.native-license-bundle/v1",
        "prusa_source_commit": native_manifest["prusaslicer_commit"],
        "dependency_sources_sha256": "0" * 64,
        "native_manifest_sha256": _semantic_digest(native_manifest),
        "components": components,
        "files": file_records,
    }
    files["MANIFEST.json"] = _canonical_json(bundle_manifest)
    return files


def _metadata_entries(*, include_offer: bool) -> dict[str, bytes]:
    entries = {
        f"{DIST_INFO}/METADATA": (
            "Metadata-Version: 2.4\n"
            "Name: holderpro\n"
            f"Version: {VERSION}\n\n"
        ).encode("utf-8"),
        f"{DIST_INFO}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: holderpro-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode("utf-8"),
        f"{DIST_INFO}/entry_points.txt": (
            "[console_scripts]\n"
            "holderpro = holderpro.cli:main\n"
            "holderpro-gui = holderpro.ui:main\n"
        ).encode("utf-8"),
        f"{DIST_INFO}/top_level.txt": b"holderpro\n",
        f"{DIST_INFO}/RECORD": b"",
        f"{DIST_INFO}/licenses/LICENSE": (PROJECT / "LICENSE").read_bytes(),
        f"{DIST_INFO}/licenses/THIRD_PARTY_NOTICES.md": (
            PROJECT / "THIRD_PARTY_NOTICES.md"
        ).read_bytes(),
        (
            f"{DIST_INFO}/licenses/upstream/"
            "prusaslicer-2.9.6-organic/LICENSE"
        ): (
            PROJECT / "upstream/prusaslicer-2.9.6-organic/LICENSE"
        ).read_bytes(),
    }
    if include_offer:
        entries[f"{DIST_INFO}/{SOURCE_OFFER_NAME}"] = source_offer(VERSION)
        entries[f"{DIST_INFO}/METADATA"] = entries[
            f"{DIST_INFO}/METADATA"
        ].replace(
            b"\n\n",
            (
                b"\nProject-URL: Corresponding Source, "
                b"https://github.com/Finn2400/HolderPro/releases/download/"
                b"v0.1.0-alpha.1/"
                b"holderpro-0.1.0-alpha.1-corresponding-source.tar.zst\n\n"
            ),
            1,
        )
    return entries


def _closed_entries() -> tuple[dict[str, bytes], set[str]]:
    entries = verify_expected_source_entries(PROJECT)
    entries.update(_metadata_entries(include_offer=True))
    native_names = {
        "holderpro/_native/__init__.py",
        "holderpro/_native/MANIFEST.json",
        "holderpro/_native/holderpro-organic-engine",
    }
    entries["holderpro/_native/MANIFEST.json"] = b"{}\n"
    entries["holderpro/_native/holderpro-organic-engine"] = b"native-engine"
    entries.update(
        {
            NATIVE_LICENSE_PREFIX + name: data
            for name, data in _native_license_files().items()
        }
    )
    return entries, native_names


@pytest.mark.parametrize(
    ("pep440", "display", "tag", "prerelease"),
    [
        ("1.2.3", "1.2.3", "v1.2.3", False),
        ("1.2.3a4", "1.2.3-alpha.4", "v1.2.3-alpha.4", True),
        ("1.2.3b5", "1.2.3-beta.5", "v1.2.3-beta.5", True),
        ("1.2.3rc6", "1.2.3-rc.6", "v1.2.3-rc.6", True),
    ],
)
def test_release_identity_round_trip(
    pep440: str, display: str, tag: str, prerelease: bool
) -> None:
    release = identity_from_pep440(pep440)

    assert release.display == display
    assert release.tag == tag
    assert release.prerelease is prerelease
    assert identity_from_tag(tag) == release


@pytest.mark.parametrize(
    "version",
    ["1.2", "01.2.3", "1.02.3", "1.2.03", "1.2.3a0", "1.2.3.dev1"],
)
def test_release_identity_rejects_noncanonical_versions(version: str) -> None:
    with pytest.raises(ValueError, match="normalized"):
        identity_from_pep440(version)


def test_source_offer_names_one_exact_github_release_asset() -> None:
    notice = source_offer(VERSION).decode("utf-8")

    assert "Release: v0.1.0-alpha.1\n" in notice
    assert (
        "Source archive: https://github.com/Finn2400/HolderPro/releases/download/"
        "v0.1.0-alpha.1/"
        "holderpro-0.1.0-alpha.1-corresponding-source.tar.zst\n"
    ) in notice
    assert "/latest/" not in notice
    assert "/main/" not in notice


def test_corresponding_source_project_url_is_exact_and_unique() -> None:
    metadata = BytesParser().parsebytes(
        _metadata_entries(include_offer=True)[f"{DIST_INFO}/METADATA"]
    )

    _verify_corresponding_source_project_url(metadata, VERSION)

    duplicate = BytesParser().parsebytes(
        (
            _metadata_entries(include_offer=True)[f"{DIST_INFO}/METADATA"]
            .replace(
                b"\n\n",
                (
                    b"\nProject-URL: Corresponding Source, "
                    b"https://example.invalid/wrong-source\n\n"
                ),
                1,
            )
        )
    )
    with pytest.raises(RuntimeError, match="exactly one version-matched"):
        _verify_corresponding_source_project_url(duplicate, VERSION)


def test_builder_and_verifier_derive_the_same_project_source_set() -> None:
    assert build_expected_source_entries(PROJECT) == verify_expected_source_entries(
        PROJECT
    )


def test_rewrite_wheel_embeds_source_offer_and_records_it(tmp_path: Path) -> None:
    raw_entries = build_expected_source_entries(PROJECT)
    raw_entries.update(_metadata_entries(include_offer=False))
    source = tmp_path / "raw.whl"
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in raw_entries.items():
            archive.writestr(name, data)
    engine = tmp_path / "holderpro-organic-engine"
    engine.write_bytes(b"native-engine")
    destination = tmp_path / "platform.whl"
    native_manifest = {
        "schema": "holderpro.native-artifact-manifest/v1",
        "target": "macos-arm64",
        "files": [],
    }

    rewrite_wheel(
        source,
        destination,
        "macosx_13_0_arm64",
        [engine],
        native_manifest,
        "holderpro",
        VERSION,
        PROJECT,
        _native_license_files(),
    )

    with zipfile.ZipFile(destination) as archive:
        notice_name = f"{DIST_INFO}/{SOURCE_OFFER_NAME}"
        assert archive.read(notice_name) == source_offer(VERSION)
        assert (archive.getinfo(notice_name).external_attr >> 16) & 0o777 == 0o644
        record = list(
            csv.reader(
                io.StringIO(archive.read(f"{DIST_INFO}/RECORD").decode("utf-8"))
            )
        )
        notice_rows = [row for row in record if row[0] == notice_name]
        assert len(notice_rows) == 1
        assert notice_rows[0][1].startswith("sha256=")
        assert notice_rows[0][2] == str(len(source_offer(VERSION)))
        metadata = archive.read(f"{DIST_INFO}/METADATA").decode("utf-8")
        assert metadata.count("Project-URL: Corresponding Source,") == 1
        assert f"{NATIVE_LICENSE_PREFIX}MANIFEST.json" in archive.namelist()


@pytest.mark.parametrize(
    "path",
    [
        "numpy/__init__.py",
        "trimesh/base.py",
        "shapely.libs/libgeos.dylib",
        "PySide6/QtCore.abi3.so",
        "vtkmodules/vtkCommonCore.so",
        "PyInstaller/__init__.py",
        "holderpro/_native/VCRUNTIME140.dll",
        "holderpro/_native/msvcp140_1.dll",
        "holderpro/_native/ucrtbase.dll",
        "holderpro/_native/api-ms-win-crt-runtime-l1-1-0.dll",
    ],
)
def test_prohibited_vendored_dependency_and_runtime_paths(path: str) -> None:
    assert _prohibited_vendored_paths({path}) == [path]


@pytest.mark.parametrize(
    "path",
    ["numpy/__init__.py", "holderpro/_native/VCRUNTIME140.dll"],
)
def test_closed_inventory_rejects_prohibited_payload(path: str) -> None:
    entries, native_names = _closed_entries()
    entries[path] = b"prohibited"

    with pytest.raises(RuntimeError, match="vendors prohibited"):
        _verify_closed_inventory(entries, PROJECT, DIST_INFO, native_names, VERSION)


def test_backend_inventory_rejects_any_unreviewed_file() -> None:
    entries = build_expected_source_entries(PROJECT)
    entries.update(_metadata_entries(include_offer=False))
    entries["unreviewed_dependency/module.py"] = b""

    with pytest.raises(RuntimeError, match="inventory mismatch"):
        _verify_backend_inventory(entries, PROJECT, DIST_INFO)


def test_closed_inventory_accepts_only_project_sources_and_native_engine() -> None:
    entries, native_names = _closed_entries()

    _verify_closed_inventory(entries, PROJECT, DIST_INFO, native_names, VERSION)


def test_closed_inventory_rejects_wrong_source_offer() -> None:
    entries, native_names = _closed_entries()
    entries[f"{DIST_INFO}/{SOURCE_OFFER_NAME}"] = source_offer("0.1.0a2")

    with pytest.raises(RuntimeError, match="version-mismatched"):
        _verify_closed_inventory(entries, PROJECT, DIST_INFO, native_names, VERSION)


def test_closed_inventory_rejects_unknown_top_level_package() -> None:
    entries, native_names = _closed_entries()
    entries["mystery_dependency/__init__.py"] = b""

    with pytest.raises(RuntimeError, match="inventory is not closed"):
        _verify_closed_inventory(entries, PROJECT, DIST_INFO, native_names, VERSION)
