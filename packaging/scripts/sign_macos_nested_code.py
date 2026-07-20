#!/usr/bin/env python3
"""Sign every nested Mach-O payload before sealing a macOS application bundle."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


MACH_O_MAGICS = {
    b"\xfe\xed\xfa\xce",  # 32-bit, native endian
    b"\xce\xfa\xed\xfe",  # 32-bit, swapped endian
    b"\xfe\xed\xfa\xcf",  # 64-bit, native endian
    b"\xcf\xfa\xed\xfe",  # 64-bit, swapped endian
    b"\xca\xfe\xba\xbe",  # universal binary
    b"\xbe\xba\xfe\xca",  # universal binary, swapped endian
    b"\xca\xfe\xba\xbf",  # universal binary with 64-bit arch records
    b"\xbf\xba\xfe\xca",  # universal binary with 64-bit arch records, swapped
}
NESTED_CODE_SUFFIXES = {".app", ".appex", ".bundle", ".framework", ".xpc"}


def is_mach_o(path: Path) -> bool:
    """Return whether *path* is a physical Mach-O file."""

    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as stream:
            magic = stream.read(4)
    except OSError:
        return False
    # Java class files share the 32-bit universal-binary magic. They are data,
    # not signable code; the suffix makes that ambiguity safe and explicit.
    return magic in MACH_O_MAGICS and path.suffix.lower() != ".class"


def nested_signing_order(application: Path) -> tuple[list[Path], list[Path]]:
    """Return physical Mach-O files and nested code bundles, deepest first."""

    application = application.resolve()
    if not application.is_dir() or application.suffix.lower() != ".app":
        raise ValueError(f"not a macOS application bundle: {application}")

    files: dict[tuple[int, int], Path] = {}
    bundles: list[Path] = []
    for root, directory_names, file_names in os.walk(application, followlinks=False):
        root_path = Path(root)
        directory_names[:] = [
            name for name in directory_names if not (root_path / name).is_symlink()
        ]
        for name in file_names:
            path = root_path / name
            if not is_mach_o(path):
                continue
            stat = path.stat()
            files.setdefault((stat.st_dev, stat.st_ino), path)
        for name in directory_names:
            path = root_path / name
            if path.suffix.lower() in NESTED_CODE_SUFFIXES:
                bundles.append(path)

    def by_depth(path: Path) -> tuple[int, str]:
        return len(path.parts), path.as_posix()

    return (
        sorted(files.values(), key=by_depth, reverse=True),
        sorted(bundles, key=by_depth, reverse=True),
    )


def sign_nested_code(application: Path, identity: str) -> tuple[int, int]:
    """Sign nested payloads in deterministic inside-out order."""

    files, bundles = nested_signing_order(application)
    if not files:
        raise RuntimeError(f"application contains no Mach-O payloads: {application}")
    common = [
        "codesign",
        "--force",
        "--options",
        "runtime",
        "--timestamp",
        "--sign",
        identity,
    ]
    for path in (*files, *bundles):
        subprocess.run([*common, str(path)], check=True)
    return len(files), len(bundles)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("application", type=Path)
    parser.add_argument("--identity", required=True)
    args = parser.parse_args()
    try:
        files, bundles = sign_nested_code(args.application, args.identity)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"could not sign nested macOS code: {exc}") from exc
    print(f"signed {files} Mach-O files and {bundles} nested code bundles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
