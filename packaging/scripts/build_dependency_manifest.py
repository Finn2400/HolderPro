#!/usr/bin/env python3
"""Resolve direct HolderPro dependencies to auditable source artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path


WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
SHA256 = re.compile(r"[0-9a-f]{64}")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def source_filename(source_url: str) -> str:
    """Derive the stable on-disk archive name from a reviewed source URL."""

    parsed = urllib.parse.urlsplit(source_url)
    filename = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
    reserved = filename.split(".", 1)[0].upper()
    if (
        not filename
        or filename in {".", ".."}
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or filename[-1] in {" ", "."}
        or any(character in '<>:"|?*' or ord(character) < 32 for character in filename)
        or reserved in WINDOWS_RESERVED_NAMES
    ):
        raise RuntimeError(f"source URL has no safe archive filename: {source_url}")
    return filename


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _reviewed_prusa_file(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{label} has no path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != value:
        raise RuntimeError(f"{label} has an unsafe path: {value!r}")
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{label} is missing from the pinned PrusaSlicer tree: {value}")
    return path


def validate_bundled_binary_source(
    prusa_source: Path,
    item: dict[str, object],
    definition: Path,
) -> None:
    """Validate source/version evidence for a Prusa-bundled binary build input."""

    name = str(item.get("name", "<unknown>"))
    evidence = item.get("bundled_binary_source")
    if not isinstance(evidence, dict):
        raise RuntimeError(f"bundled binary source evidence for {name} is not an object")
    if item.get("targets") != ["windows-x86_64"]:
        raise RuntimeError(
            f"bundled binary source {name} must be scoped only to windows-x86_64"
        )

    definition_digest = evidence.get("definition_sha256")
    if (
        not isinstance(definition_digest, str)
        or SHA256.fullmatch(definition_digest) is None
        or _sha256(definition) != definition_digest
    ):
        raise RuntimeError(f"bundled binary source {name} has wrong definition evidence")

    artifacts = evidence.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError(f"bundled binary source {name} has no artifact evidence")
    artifact_paths: set[str] = set()
    roles: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise RuntimeError(f"bundled binary source {name} has malformed evidence")
        role = artifact.get("role")
        relative = artifact.get("path")
        digest = artifact.get("sha256")
        if not isinstance(role, str) or not role:
            raise RuntimeError(f"bundled binary source {name} has evidence without a role")
        if not isinstance(relative, str) or relative in artifact_paths:
            raise RuntimeError(
                f"bundled binary source {name} has duplicate or invalid artifact paths"
            )
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise RuntimeError(
                f"bundled binary source {name} has an invalid artifact SHA-256"
            )
        path = _reviewed_prusa_file(
            prusa_source, relative, label=f"bundled binary artifact for {name}"
        )
        if _sha256(path) != digest:
            raise RuntimeError(
                f"bundled binary artifact for {name} does not match {relative}"
            )
        artifact_paths.add(relative)
        roles.add(role)
    required_roles = {"runtime-dll", "import-library", "header"}
    if not required_roles <= roles:
        raise RuntimeError(
            f"bundled binary source {name} lacks evidence roles: "
            + ", ".join(sorted(required_roles - roles))
        )

    markers = evidence.get("version_markers")
    if not isinstance(markers, list) or not markers:
        raise RuntimeError(f"bundled binary source {name} has no version evidence")
    seen_markers: set[tuple[str, str, str]] = set()
    for marker in markers:
        if not isinstance(marker, dict):
            raise RuntimeError(f"bundled binary source {name} has malformed version evidence")
        relative = marker.get("path")
        encoding = marker.get("encoding")
        text = marker.get("text")
        if (
            not isinstance(relative, str)
            or relative not in artifact_paths
            or encoding not in {"utf-8", "utf-16le"}
            or not isinstance(text, str)
            or not text
        ):
            raise RuntimeError(f"bundled binary source {name} has invalid version evidence")
        identity = (relative, encoding, text)
        if identity in seen_markers:
            raise RuntimeError(f"bundled binary source {name} repeats version evidence")
        seen_markers.add(identity)
        path = _reviewed_prusa_file(
            prusa_source, relative, label=f"version evidence for {name}"
        )
        if text.encode(encoding) not in path.read_bytes():
            raise RuntimeError(
                f"bundled binary source {name} lacks version marker {text!r} in {relative}"
            )


def exact_constraints(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        requirement = line.partition(";")[0].strip()
        if requirement.count("==") != 1:
            raise RuntimeError(f"release constraint is not exact: {line}")
        name, version = requirement.split("==", 1)
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
    sources = [
        item for item in document.get("urls", []) if item.get("packagetype") == "sdist"
    ]
    if len(sources) != 1:
        lock_path = (
            Path(__file__).resolve().parents[1] / "dependency-binary-source-lock.json"
        )
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
            "source_filename": source_filename(item["source_url"]),
            "source_sha256": item["source_sha256"],
        }
    source = sources[0]
    sha256 = source.get("digests", {}).get("sha256")
    if not sha256 or len(sha256) != 64:
        raise RuntimeError(f"PyPI did not provide a SHA-256 for {name} {version}")
    return {
        "source_url": source["url"],
        "source_filename": source["filename"],
        "source_sha256": sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "dependency-source-requirements.json",
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
            components.append(
                {
                    **item,
                    "version": version,
                    "source_filename": source_filename(item["source_url"]),
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
            raise RuntimeError(
                f"required package is not installed: {item['name']}"
            ) from None
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
        (
            Path(__file__).resolve().parents[1] / "dependency-binary-source-lock.json"
        ).read_text(encoding="utf-8")
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
                    "source_filename": source_filename(item["source_url"]),
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
        raise RuntimeError(
            f"PrusaSlicer checkout is {actual_commit}, expected {prusa_commit}"
        )
    for item in native_components:
        definition = args.prusa_source / item["definition"]
        definition_text = definition.read_text(encoding="utf-8")
        if item.get("bundled_binary_source") is not None:
            validate_bundled_binary_source(args.prusa_source, item, definition)
        else:
            if item["source_url"] not in definition_text:
                raise RuntimeError(f"{definition} no longer contains {item['source_url']}")
            if item["source_sha256"].lower() not in definition_text.lower():
                raise RuntimeError(f"{definition} no longer contains the pinned SHA-256")
        components.append(
            {
                **item,
                "ecosystem": "native-source-archive",
                "relationship": "native-build-input",
                "source_filename": source_filename(item["source_url"]),
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
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
