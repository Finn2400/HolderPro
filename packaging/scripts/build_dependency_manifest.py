#!/usr/bin/env python3
"""Resolve direct HolderPro dependencies to auditable source artifacts."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import re
import subprocess
import urllib.request
from pathlib import Path


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def exact_constraints(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            raise RuntimeError(f"release constraint is not exact: {line}")
        name, version = line.split("==", 1)
        key = canonical_name(name.strip())
        if not key or not version or key in result:
            raise RuntimeError(f"invalid duplicate release constraint: {line}")
        result[key] = version.strip()
    if not result:
        raise RuntimeError("release constraints are empty")
    return result


def pypi_source(name: str, version: str) -> dict[str, str]:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed HTTPS host
        document = json.load(response)
    sources = [item for item in document.get("urls", []) if item.get("packagetype") == "sdist"]
    if len(sources) != 1:
        lock_path = Path(__file__).resolve().parents[1] / "dependency-binary-source-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        matching = [
            item
            for item in lock["components"]
            if item["name"].lower() == name.lower() and item["version"] == version
        ]
        if len(matching) != 1:
            raise RuntimeError(
                f"{name} {version} has no sdist and no exact reviewed source lock"
            )
        item = matching[0]
        return {
            "source_url": item["source_url"],
            "source_filename": Path(item["source_url"]).name,
            "source_sha256": item["source_sha256"],
        }
    source = sources[0]
    sha256 = source.get("digests", {}).get("sha256")
    if not sha256 or len(sha256) != 64:
        raise RuntimeError(f"PyPI did not provide a SHA-256 for {name} {version}")
    return {"source_url": source["url"], "source_filename": source["filename"], "source_sha256": sha256}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "dependency-source-requirements.json",
    )
    parser.add_argument("--prusa-source", type=Path, required=True)
    parser.add_argument("--holderpro-build-id", required=True)
    parser.add_argument(
        "--constraints",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "release-constraints.txt",
    )
    parser.add_argument(
        "--native-manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "prusaslicer-native-dependency-sources.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    required = json.loads(args.requirements.read_text(encoding="utf-8"))["components"]
    constraints = exact_constraints(args.constraints)
    components: list[dict[str, object]] = []

    prusa_commit = "b028299c770b8380ee81c921a2867d522f288123"
    components.append(
        {
            **next(item for item in required if item["name"] == "PrusaSlicer"),
            "version": "2.9.6",
            "source_commit": prusa_commit,
            "source_in_corresponding_archive": "source/prusaslicer",
        }
    )

    for item in required:
        if item["ecosystem"] == "source-archive":
            version = str(item["requirement"]).removeprefix("==")
            if item["name"] == "CPython" and platform.python_version() != version:
                raise RuntimeError(
                    f"release interpreter is {platform.python_version()}, expected {version}"
                )
            components.append(
                {
                    **item,
                    "version": version,
                    "source_filename": Path(item["source_url"]).name,
                }
            )
            continue
        if item["ecosystem"] != "pypi":
            continue
        try:
            resolved = importlib.metadata.version(item["name"])
        except importlib.metadata.PackageNotFoundError:
            if item["relationship"].startswith("optional-"):
                continue
            raise RuntimeError(f"required package is not installed: {item['name']}") from None
        pinned = constraints.get(canonical_name(item["name"]))
        if pinned is None:
            raise RuntimeError(f"release constraints do not pin {item['name']}")
        if resolved != pinned:
            raise RuntimeError(
                f"installed {item['name']} is {resolved}, release constraint is {pinned}"
            )
        components.append(
            {
                **item,
                "version": resolved,
                "release_constraint": f"=={pinned}",
                **pypi_source(item["name"], resolved),
            }
        )

    binary_lock = json.loads(
        (Path(__file__).resolve().parents[1] / "dependency-binary-source-lock.json").read_text(
            encoding="utf-8"
        )
    )
    pyside_versions = {
        item["version"] for item in components if item["name"] == "PySide6-Essentials"
    }
    for item in binary_lock["components"]:
        if item["name"] == "Qt" and item["version"] in pyside_versions:
            components.append(
                {
                    **item,
                    "ecosystem": "source-archive",
                    "relationship": "runtime-dynamic",
                    "source_filename": Path(item["source_url"]).name,
                }
            )

    native = json.loads(args.native_manifest.read_text(encoding="utf-8"))
    if native.get("schema") != "holderpro.native-dependency-sources/v1":
        raise RuntimeError("native dependency manifest has the wrong schema")
    native_components = native.get("components")
    if not isinstance(native_components, list) or not native_components:
        raise RuntimeError("native dependency manifest contains no components")
    actual_commit = subprocess.run(
        ["git", "-C", str(args.prusa_source), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if actual_commit != prusa_commit:
        raise RuntimeError(f"PrusaSlicer checkout is {actual_commit}, expected {prusa_commit}")
    for item in native_components:
        definition = args.prusa_source / item["definition"]
        definition_text = definition.read_text(encoding="utf-8")
        if item["source_url"] not in definition_text:
            raise RuntimeError(f"{definition} no longer contains {item['source_url']}")
        if item["source_sha256"].lower() not in definition_text.lower():
            raise RuntimeError(f"{definition} no longer contains the pinned SHA-256")
        components.append(
            {
                **item,
                "ecosystem": "native-source-archive",
                "relationship": "native-build-input",
            }
        )

    result = {
        "schema": "holderpro.dependency-sources/v1",
        "holderpro_build_id": args.holderpro_build_id,
        "prusa_source_commit": prusa_commit,
        "components": components,
        "system_components": native.get("system_components", {}),
        "vendored_components": native.get("vendored_components"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
