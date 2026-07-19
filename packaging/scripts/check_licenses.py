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

    requirements_path = root / "packaging/dependency-source-requirements.json"
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    for item in requirements.get("components", []):
        for field in ("name", "requirement", "source_repository", "license", "relationship"):
            if not item.get(field):
                errors.append(f"dependency {item.get('name', '<unknown>')} lacks {field}")
        source = item.get("source_repository", "")
        if source and not re.match(r"^https://", source):
            errors.append(f"dependency source is not HTTPS: {source}")

    if errors:
        raise SystemExit("license audit failed:\n- " + "\n- ".join(errors))
    print("license and attribution audit OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
