#!/usr/bin/env python3
"""Check release-critical license and attribution material."""

from __future__ import annotations

import json
import re
from pathlib import Path


COMMIT = "b028299c770b8380ee81c921a2867d522f288123"
INTEGRATION = (
    "HolderPro uses the unmodified PrusaSlicer 2.9.6 Organic-support "
    "implementation through a headless adapter."
)
INDEPENDENT = (
    "HolderPro is an independent project; not affiliated with or endorsed by "
    "Prusa Research."
)

APPROVED_LICENSE_EXPRESSIONS = {
    "AGPL-3.0-or-later",
    "AGPL-3.0-or-later AND BSL-1.0",
    "Apache-2.0",
    "BSD-3-Clause",
    "BSD-3-Clause AND IJG",
    "BSD-3-Clause AND MIT",
    "BSL-1.0",
    "GPL-2.0-or-later",
    "GPL-3.0-or-later AND LGPL-3.0-or-later",
    "ISC",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
    "LGPL-3.0-or-later",
    "LGPL-3.0-or-later OR GPL-2.0-or-later",
    "Libpng",
    "MIT",
    "MPL-2.0",
    "OpenSSL",
    "Qhull",
    "SGI-B-2.0",
    "Zlib",
    "curl",
}

RETIRED_PACKAGING_PATHS = (
    "packaging/linux",
    "packaging/macos",
    "packaging/pyinstaller",
    "packaging/windows",
)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    errors: list[str] = []
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    if "GNU AFFERO GENERAL PUBLIC LICENSE" not in license_text:
        errors.append("root LICENSE is not the AGPL text")

    for relative in ["README.md", "docs/licensing.md", "THIRD_PARTY_NOTICES.md"]:
        text = (root / relative).read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        if INTEGRATION not in normalized:
            errors.append(f"{relative} is missing the approved integration wording")
        if INDEPENDENT not in normalized:
            errors.append(f"{relative} is missing the non-affiliation wording")
        if COMMIT not in text:
            errors.append(f"{relative} is missing the pinned commit")

    lock = (root / "upstream/prusaslicer-2.9.6-organic/UPSTREAM.lock").read_text(
        encoding="utf-8"
    )
    if f"commit={COMMIT}" not in lock:
        errors.append("UPSTREAM.lock does not contain the pinned commit")

    governance = " ".join(
        (root / "GOVERNANCE.md").read_text(encoding="utf-8").split()
    )
    if "permanently free software" not in governance:
        errors.append("GOVERNANCE.md lacks the permanent free-software commitment")
    authenticity = (root / "docs/release-authenticity.md").read_text(
        encoding="utf-8"
    )
    for channel in ("GitHub Releases", "PyPI"):
        if channel not in authenticity:
            errors.append(f"release-authenticity policy does not name {channel}")

    manifests = (
        root / "packaging/dependency-source-requirements.json",
        root / "packaging/prusaslicer-native-dependency-sources.json",
    )
    for requirements_path in manifests:
        requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
        if not requirements.get("components"):
            errors.append(f"{requirements_path.name} contains no components")
        for item in requirements.get("components", []):
            license_expression = item.get("license")
            if license_expression not in APPROVED_LICENSE_EXPRESSIONS:
                errors.append(
                    f"dependency {item.get('name', '<unknown>')} has unreviewed "
                    f"license expression {license_expression!r}"
                )
            source_url = item.get("source_url")
            if source_url and not re.match(r"^https://", source_url):
                errors.append(f"dependency archive is not HTTPS: {source_url}")

    native_requirements = json.loads(manifests[1].read_text(encoding="utf-8"))
    vendored = native_requirements.get("vendored_components")
    if not isinstance(vendored, list) or not vendored:
        errors.append("native dependency manifest has no structured vendored components")
    else:
        expected_vendored = {
            "ADMesh",
            "Anti-Grain Geometry",
            "Clipper",
            "Clipper Int128",
            "Mesa GLU libtess",
            "QOI",
            "ankerl::unordered_dense",
            "fast_float",
            "libigl",
            "libnest2d",
            "miniz",
            "semver",
            "tcb::span",
        }
        vendored_names: set[str] = set()
        for item in vendored:
            if not isinstance(item, dict):
                errors.append("native dependency manifest has a non-object vendored component")
                continue
            name = item.get("name", "<unknown>")
            if name in vendored_names:
                errors.append(f"vendored dependency {name} is listed more than once")
            vendored_names.add(name)
            for field in ("name", "version", "snapshot", "license", "source_path"):
                if not item.get(field):
                    errors.append(f"vendored dependency {name} lacks {field}")
            if item.get("snapshot") != COMMIT:
                errors.append(f"vendored dependency {name} is not pinned to {COMMIT}")
            if item.get("license") not in APPROVED_LICENSE_EXPRESSIONS:
                errors.append(
                    f"vendored dependency {name} has unreviewed license expression "
                    f"{item.get('license')!r}"
                )
            for field in ("source_path",):
                candidate = Path(str(item.get(field, "")))
                if candidate.is_absolute() or ".." in candidate.parts:
                    errors.append(f"vendored dependency {name} has unsafe {field}")
            notice_paths = item.get("notice_paths")
            if not isinstance(notice_paths, list) or not notice_paths:
                errors.append(f"vendored dependency {name} has no notice_paths")
            else:
                for notice_path in notice_paths:
                    candidate = Path(str(notice_path))
                    if candidate.is_absolute() or ".." in candidate.parts:
                        errors.append(
                            f"vendored dependency {name} has unsafe notice path "
                            f"{notice_path!r}"
                        )
        missing_vendored = expected_vendored - vendored_names
        if missing_vendored:
            errors.append(
                "native dependency manifest lacks linked vendored components: "
                + ", ".join(sorted(missing_vendored))
            )

    requirements_path = manifests[0]
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    for item in requirements.get("components", []):
        for field in ("name", "requirement", "source_repository", "license", "relationship"):
            if not item.get(field):
                errors.append(f"dependency {item.get('name', '<unknown>')} lacks {field}")
        source = item.get("source_repository", "")
        if source and not re.match(r"^https://", source):
            errors.append(f"dependency source is not HTTPS: {source}")

    for relative in RETIRED_PACKAGING_PATHS:
        path = root / relative
        if path.exists() and any(
            candidate.is_file()
            and candidate.suffix not in {".pyc", ".pyo"}
            and "__pycache__" not in candidate.parts
            for candidate in path.rglob("*")
        ):
            errors.append(f"retired standalone packaging machinery remains: {relative}")

    if errors:
        raise SystemExit("license audit failed:\n- " + "\n- ".join(errors))
    print("license and attribution audit OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
