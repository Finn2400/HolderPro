#!/usr/bin/env python3
"""Create or verify a deterministic SHA256SUMS file."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--output", default="SHA256SUMS")
    args = parser.parse_args()
    checksum_path = args.directory / args.output
    if args.verify:
        failures = []
        lines = checksum_path.read_text(encoding="utf-8").splitlines()
        records: dict[str, str] = {}
        for line in lines:
            if "  " not in line:
                raise SystemExit("checksum file contains a malformed line")
            expected, name = line.split("  ", 1)
            if (
                re.fullmatch(r"[0-9a-f]{64}", expected) is None
                or not name
                or Path(name).name != name
                or name == args.output
                or name in records
            ):
                raise SystemExit(f"checksum file contains an invalid record: {line!r}")
            records[name] = expected
            path = args.directory / name
            if not path.is_file() or digest(path) != expected:
                failures.append(name)
        actual = {
            path.name
            for path in args.directory.iterdir()
            if path.is_file() and path.name != args.output
        }
        if set(records) != actual:
            failures.extend(sorted(set(records) ^ actual))
        if failures:
            raise SystemExit(
                "checksum verification failed: " + ", ".join(sorted(set(failures)))
            )
        print("checksums OK")
        return 0

    files = sorted(path for path in args.directory.iterdir() if path.is_file() and path.name != args.output)
    checksum_path.write_text("".join(f"{digest(path)}  {path.name}\n" for path in files), encoding="utf-8")
    print(checksum_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
