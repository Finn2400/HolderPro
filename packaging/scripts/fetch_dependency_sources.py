#!/usr/bin/env python3
"""Fetch and verify native build-input source archives for corresponding source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO


CHUNK_SIZE = 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def sha256_stream(stream: BinaryIO) -> str:
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
        value.update(block)
    return value.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256_stream(stream)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"could not read dependency source manifest: {path}"
        ) from exc
    if not isinstance(document, dict):
        raise RuntimeError("dependency source manifest must be a JSON object")
    if document.get("schema") != "holderpro.dependency-sources/v1":
        raise RuntimeError("dependency source manifest has the wrong schema")
    return document


def _safe_filename(value: object, *, component: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise RuntimeError(f"native dependency {component} has no source_filename")
    reserved = value.split(".", 1)[0].upper()
    if (
        Path(value).name != value
        or "/" in value
        or "\\" in value
        or value[-1] in {" ", "."}
        or any(character in '<>:"|?*' or ord(character) < 32 for character in value)
        or reserved in WINDOWS_RESERVED_NAMES
    ):
        raise RuntimeError(
            f"native dependency {component} has an unsafe source_filename: {value!r}"
        )
    return value


def _https_urls(item: dict[str, Any], *, component: str) -> tuple[str, ...]:
    primary = item.get("source_url")
    mirrors = item.get("verified_mirror_urls", [])
    if not isinstance(primary, str) or not primary.startswith("https://"):
        raise RuntimeError(f"native dependency {component} has no HTTPS source_url")
    if not isinstance(mirrors, list) or any(
        not isinstance(url, str) for url in mirrors
    ):
        raise RuntimeError(f"native dependency {component} has invalid mirror URLs")
    urls = (primary, *mirrors)
    if any(not url.startswith("https://") for url in urls):
        raise RuntimeError(f"native dependency {component} has a non-HTTPS source URL")
    return urls


def native_source_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the closed, digest-deduplicated native archive set."""

    if document.get("schema") != "holderpro.dependency-sources/v1":
        raise RuntimeError("dependency source manifest has the wrong schema")
    components = document.get("components")
    if not isinstance(components, list):
        raise RuntimeError("dependency source manifest has no components list")

    records: dict[str, dict[str, Any]] = {}
    for item in components:
        if not isinstance(item, dict):
            raise RuntimeError(
                "dependency source manifest contains a malformed component"
            )
        if item.get("ecosystem") != "native-source-archive":
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise RuntimeError("native dependency source has no name")
        version = item.get("version")
        license_expression = item.get("license")
        if not isinstance(version, str) or not version:
            raise RuntimeError(f"native dependency {name} has no version")
        if not isinstance(license_expression, str) or not license_expression:
            raise RuntimeError(f"native dependency {name} has no license expression")
        if item.get("relationship") != "native-build-input":
            raise RuntimeError(f"native dependency {name} has the wrong relationship")
        digest = item.get("source_sha256")
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
            raise RuntimeError(
                f"native dependency {name} has an invalid source SHA-256"
            )
        filename = _safe_filename(item.get("source_filename"), component=name)
        urls = _https_urls(item, component=name)
        record = {
            "name": name,
            "version": version,
            "license": license_expression,
            "source_filename": filename,
            "source_sha256": digest,
            "urls": urls,
        }
        previous = records.get(digest)
        if previous is not None:
            if previous["source_filename"] != filename:
                raise RuntimeError(
                    "native dependency source digest is assigned conflicting filenames: "
                    f"{previous['source_filename']!r} and {filename!r}"
                )
            previous["urls"] = tuple(dict.fromkeys((*previous["urls"], *urls)))
            continue
        records[digest] = record

    if not records:
        raise RuntimeError("dependency source manifest contains no native build inputs")
    return [records[digest] for digest in sorted(records)]


def verify_dependency_source_directory(
    directory: Path, document: dict[str, Any]
) -> list[Path]:
    """Verify that *directory* contains exactly the manifest's native archives."""

    directory = directory.absolute()
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError(f"dependency source directory does not exist: {directory}")

    records = native_source_records(document)
    expected_directories = {record["source_sha256"] for record in records}
    actual_directories: set[str] = set()
    verified: list[Path] = []

    for entry in directory.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            raise RuntimeError(
                f"unexpected dependency source entry: {entry.relative_to(directory)}"
            )
        actual_directories.add(entry.name)
    if actual_directories != expected_directories:
        missing = sorted(expected_directories - actual_directories)
        extra = sorted(actual_directories - expected_directories)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise RuntimeError(
            "dependency source directory is not closed: " + "; ".join(details)
        )

    for record in records:
        digest = record["source_sha256"]
        source_directory = directory / digest
        entries = list(source_directory.iterdir())
        expected = source_directory / record["source_filename"]
        if len(entries) != 1 or entries[0] != expected:
            raise RuntimeError(
                f"dependency source {digest} must contain exactly {record['source_filename']}"
            )
        if expected.is_symlink() or not expected.is_file():
            raise RuntimeError(f"dependency source is not a regular file: {expected}")
        actual = sha256_file(expected)
        if actual != digest:
            raise RuntimeError(
                f"dependency source SHA-256 mismatch for {record['name']}: "
                f"expected {digest}, got {actual}"
            )
        verified.append(expected)
    return verified


def _download(record: dict[str, Any], destination: Path) -> None:
    errors: list[str] = []
    for url in record["urls"]:
        temporary = destination.with_suffix(destination.suffix + ".partial")
        temporary.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "HolderPro corresponding-source fetcher"},
            )
            with (
                urllib.request.urlopen(request, timeout=120) as response,
                temporary.open("wb") as stream,
            ):  # noqa: S310 - manifest URLs are validated HTTPS inputs
                shutil.copyfileobj(response, stream, CHUNK_SIZE)
            actual = sha256_file(temporary)
            if actual != record["source_sha256"]:
                raise RuntimeError(
                    f"SHA-256 mismatch: expected {record['source_sha256']}, got {actual}"
                )
            os.replace(temporary, destination)
            return
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            temporary.unlink(missing_ok=True)
            errors.append(f"{url}: {exc}")
    raise RuntimeError(
        f"could not fetch native dependency source {record['name']}: "
        + " | ".join(errors)
    )


def fetch_dependency_sources(document: dict[str, Any], output: Path) -> list[Path]:
    """Populate *output* atomically with the manifest's exact native archive set."""

    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"dependency source output already exists: {output}")
    records = native_source_records(document)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for record in records:
            directory = temporary / record["source_sha256"]
            directory.mkdir()
            _download(record, directory / record["source_filename"])
        verify_dependency_source_directory(temporary, document)
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return verify_dependency_source_directory(output, document)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        verified = fetch_dependency_sources(load_manifest(args.manifest), args.output)
    except RuntimeError as exc:
        parser.error(str(exc))
    print(
        f"verified {len(verified)} native dependency source archives in {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
