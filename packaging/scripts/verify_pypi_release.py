#!/usr/bin/env python3
"""Verify that PyPI has only HolderPro's exact tested release wheels."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from release_version import identity_from_pep440
from verify_native_stage import TARGETS


PYPI_PROJECT = "holderpro"
PYPI_JSON_BASE = "https://pypi.org/pypi"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
POST_UPLOAD_ATTEMPTS = 7
POST_UPLOAD_DELAY_SECONDS = 5.0


class IncompleteReleaseError(RuntimeError):
    """PyPI has not exposed every expected wheel yet."""


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def expected_wheel_names(version: str) -> set[str]:
    identity_from_pep440(version)
    return {
        f"holderpro-{version}-py3-none-{target.wheel_tag}.whl"
        for target in TARGETS.values()
    }


def local_wheel_records(directory: Path, version: str) -> dict[str, dict[str, Any]]:
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError(f"wheel directory does not exist: {directory}")
    expected = expected_wheel_names(version)
    entries = list(directory.iterdir())
    actual = {path.name for path in entries}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(
            f"local PyPI wheel set is not closed: missing={missing}, extra={extra}"
        )
    records: dict[str, dict[str, Any]] = {}
    for path in entries:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"local PyPI payload is not a non-empty regular file: {path}")
        records[path.name] = {
            "sha256": sha256(path),
            "size": path.stat().st_size,
        }
    return records


def verify_pypi_document(
    local: dict[str, dict[str, Any]],
    version: str,
    document: object,
    *,
    require_complete: bool,
) -> int:
    if not isinstance(document, dict):
        raise RuntimeError("PyPI returned a non-object JSON document")
    urls = document.get("urls")
    if not isinstance(urls, list):
        raise RuntimeError("PyPI JSON has no release file list")
    info = document.get("info")
    if urls and (not isinstance(info, dict) or info.get("version") != version):
        raise RuntimeError("PyPI returned release metadata for another version")

    remote_names: set[str] = set()
    for item in urls:
        if not isinstance(item, dict):
            raise RuntimeError("PyPI returned a malformed release-file record")
        name = item.get("filename")
        if not isinstance(name, str) or name in remote_names:
            raise RuntimeError("PyPI returned a duplicate or invalid filename")
        expected = local.get(name)
        if expected is None:
            raise RuntimeError(f"PyPI contains an unexpected file for {version}: {name}")
        digests = item.get("digests")
        remote_sha256 = digests.get("sha256") if isinstance(digests, dict) else None
        if (
            item.get("packagetype") != "bdist_wheel"
            or remote_sha256 != expected["sha256"]
            or item.get("size") != expected["size"]
        ):
            raise RuntimeError(f"PyPI file does not match the tested wheel: {name}")
        remote_names.add(name)

    if require_complete and remote_names != set(local):
        missing = sorted(set(local) - remote_names)
        raise IncompleteReleaseError(
            f"PyPI release is incomplete; missing exact wheels: {missing}"
        )
    return len(remote_names)


def fetch_pypi_document(version: str) -> dict[str, Any]:
    identity_from_pep440(version)
    url = f"{PYPI_JSON_BASE}/{PYPI_PROJECT}/{version}/json"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "HolderPro release verifier",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"info": {"version": version}, "urls": []}
        raise RuntimeError(f"PyPI JSON request failed with HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"PyPI JSON request failed: {exc}") from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RuntimeError("PyPI JSON response exceeds the safety limit")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("PyPI returned invalid JSON") from exc
    if not isinstance(document, dict):
        raise RuntimeError("PyPI returned a non-object JSON document")
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        local = local_wheel_records(args.directory, args.version)
        attempts = POST_UPLOAD_ATTEMPTS if args.require_complete else 1
        for attempt in range(1, attempts + 1):
            try:
                count = verify_pypi_document(
                    local,
                    args.version,
                    fetch_pypi_document(args.version),
                    require_complete=args.require_complete,
                )
                break
            except IncompleteReleaseError:
                if attempt == attempts:
                    raise
                time.sleep(POST_UPLOAD_DELAY_SECONDS)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"PyPI release verification failed: {exc}") from exc
    state = "complete" if args.require_complete else "safe to resume"
    print(f"PyPI {args.version} is {state}: {count}/{len(local)} exact wheels present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
