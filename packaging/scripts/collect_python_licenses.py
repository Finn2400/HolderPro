#!/usr/bin/env python3
"""Collect fail-closed license material for every bundled Python distribution."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


REQUIRED = {
    "holderpro",
    "manifold3d",
    "numpy",
    "pyinstaller",
    "pyside6-essentials",
    "shapely",
    "shiboken6",
    "trimesh",
    "vtk",
}
LICENSE_NAME = re.compile(
    r"(^|/)(licen[cs]e|copying|copyright|notice|authors)([._-].*)?$", re.IGNORECASE
)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qt_source_lock(version: str) -> tuple[dict[str, str], dict[str, str]]:
    path = Path(__file__).resolve().parents[1] / "dependency-binary-source-lock.json"
    components = json.loads(path.read_text(encoding="utf-8"))["components"]
    pyside = next(
        item
        for item in components
        if item["name"] == "PySide6-Essentials" and item["version"] == version
    )
    qt = next(
        item for item in components if item["name"] == "Qt" and item["version"] == version
    )
    return pyside, qt


def fetch_qt_license_material(version: str, temporary: Path) -> tuple[Path, dict[str, str]]:
    pyside, qt = qt_source_lock(version)
    archive_path = temporary / "pyside-source.tar.xz"
    value = hashlib.sha256()
    with urllib.request.urlopen(pyside["source_url"], timeout=120) as response:  # noqa: S310
        with archive_path.open("wb") as stream:
            while block := response.read(1024 * 1024):
                stream.write(block)
                value.update(block)
    if value.hexdigest() != pyside["source_sha256"]:
        raise RuntimeError("downloaded PySide source does not match its reviewed SHA-256")

    extracted = temporary / "qt-license-source"
    extracted.mkdir()
    wanted_suffixes = {
        "LICENSES/Apache-2.0.txt",
        "LICENSES/BSD-3-Clause.txt",
        "LICENSES/GFDL-1.3-no-invariants-only.txt",
        "LICENSES/GPL-2.0-only.txt",
        "LICENSES/GPL-3.0-only.txt",
        "LICENSES/LGPL-3.0-only.txt",
        "LICENSES/Qt-GPL-exception-1.0.txt",
        "sources/pyside6/COPYING",
        "sources/shiboken6/COPYING",
        "sources/shiboken6/COPYING.libshiboken",
    }
    found: set[str] = set()
    with tarfile.open(archive_path, "r:xz") as archive:
        for member in archive.getmembers():
            suffix = next(
                (candidate for candidate in wanted_suffixes if member.name.endswith(candidate)),
                None,
            )
            if suffix is None or not member.isfile():
                continue
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"could not read {member.name} from PySide source")
            target = extracted / suffix
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            found.add(suffix)
    missing = wanted_suffixes - found
    if missing:
        raise RuntimeError("PySide source lacks expected license files: " + ", ".join(missing))
    source_record = {"pyside": pyside, "qt": qt}
    return extracted, source_record


def copy_license(path: Path, destination: Path, copied: list[dict[str, str]]) -> None:
    target = destination / path.name
    if target.exists() and target.read_bytes() != path.read_bytes():
        target = destination / f"{len(copied):02d}-{target.name}"
    shutil.copy2(path, target)
    copied.append({"file": target.name, "sha256": digest(target)})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    installed = {canonical(dist.metadata["Name"]): dist for dist in importlib.metadata.distributions() if dist.metadata.get("Name")}
    missing = sorted(REQUIRED - set(installed))
    if missing:
        raise SystemExit("required bundled distributions are not installed: " + ", ".join(missing))
    reviewed_requirements = json.loads(
        (Path(__file__).resolve().parents[1] / "dependency-source-requirements.json").read_text(
            encoding="utf-8"
        )
    )
    reviewed_licenses = {
        canonical(item["name"]): item["license"]
        for item in reviewed_requirements["components"]
    }

    records = []
    qt_version = installed["pyside6-essentials"].version
    with tempfile.TemporaryDirectory(prefix="holderpro-qt-licenses-") as temporary_name:
        qt_material, qt_sources = fetch_qt_license_material(
            qt_version, Path(temporary_name)
        )
        for name in sorted(REQUIRED):
            dist = installed[name]
            declared_license = (
                dist.metadata.get("License-Expression")
                or dist.metadata.get("License")
                or reviewed_licenses.get(name)
            )
            if not declared_license:
                raise SystemExit(
                    f"{dist.metadata['Name']} {dist.version} has no declared license metadata"
                )
            if name == "holderpro" and "AGPL-3.0-or-later" not in declared_license:
                raise SystemExit(
                    "installed HolderPro metadata is stale or not AGPL-3.0-or-later"
                )
            destination = output / f"{name}-{dist.version}"
            destination.mkdir()
            copied: list[dict[str, str]] = []
            for relative in dist.files or []:
                relative_name = str(relative).replace("\\", "/")
                if not LICENSE_NAME.search(relative_name):
                    continue
                source = Path(str(dist.locate_file(relative)))
                if source.is_file():
                    copy_license(source, destination, copied)
            if name == "holderpro":
                root = Path(__file__).resolve().parents[2]
                for source in (root / "LICENSE", root / "THIRD_PARTY_NOTICES.md"):
                    copy_license(source, destination, copied)
            if name in {"pyside6-essentials", "shiboken6"}:
                for source in sorted((qt_material / "LICENSES").iterdir()):
                    copy_license(source, destination, copied)
                copying = (
                    qt_material / "sources/pyside6/COPYING"
                    if name == "pyside6-essentials"
                    else qt_material / "sources/shiboken6/COPYING"
                )
                copy_license(copying, destination, copied)
                if name == "shiboken6":
                    copy_license(
                        qt_material / "sources/shiboken6/COPYING.libshiboken",
                        destination,
                        copied,
                    )
            if not copied:
                raise SystemExit(
                    f"no license/copyright files found for {name} {dist.version}"
                )
            records.append(
                {
                    "name": dist.metadata["Name"],
                    "version": dist.version,
                    "declared_license": declared_license,
                    "files": copied,
                }
            )

        qt_destination = output / f"qt-{qt_version}"
        qt_destination.mkdir()
        qt_copied: list[dict[str, str]] = []
        for source in sorted((qt_material / "LICENSES").iterdir()):
            copy_license(source, qt_destination, qt_copied)
        notice = qt_destination / "SOURCE-NOTICE.json"
        notice.write_text(
            json.dumps(qt_sources, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        qt_copied.append({"file": notice.name, "sha256": digest(notice)})
        records.append(
            {
                "name": "Qt",
                "version": qt_version,
                "declared_license": qt_sources["qt"]["license"],
                "files": qt_copied,
            }
        )

    python_license = None
    executable = Path(sys.executable).resolve()
    for parent in (Path(sys.base_prefix).resolve(), *executable.parents):
        for name in ("LICENSE.txt", "LICENSE", "license.txt"):
            candidate = parent / name
            if candidate.is_file() and "python" in candidate.read_text(
                encoding="utf-8", errors="ignore"
            ).lower():
                python_license = candidate
                break
        if python_license:
            break
    if python_license is None:
        raise SystemExit("could not locate the CPython license for the frozen interpreter")
    python_folder = output / f"cpython-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_folder.mkdir()
    copied_python_license = python_folder / python_license.name
    shutil.copy2(python_license, copied_python_license)
    records.append(
        {
            "name": "CPython",
            "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "declared_license": "Python-2.0",
            "files": [
                {"file": copied_python_license.name, "sha256": digest(copied_python_license)}
            ],
        }
    )

    pyinstaller_record = next(item for item in records if canonical(item["name"]) == "pyinstaller")
    pyinstaller_folder = output / f"pyinstaller-{pyinstaller_record['version']}"
    pyinstaller_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in pyinstaller_folder.iterdir()
        if path.is_file()
    ).lower()
    if "bootloader" not in pyinstaller_text or "exception" not in pyinstaller_text:
        raise SystemExit("PyInstaller license material lacks the bootloader exception")

    manifest = {"schema": "holderpro.third-party-licenses/v1", "distributions": records}
    (output / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
