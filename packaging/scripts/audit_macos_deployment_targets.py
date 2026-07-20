#!/usr/bin/env python3
"""Fail when a macOS desktop bundle contains code newer than its OS floor."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


MACH_O_MAGICS = {
    b"\xfe\xed\xfa\xce",  # 32-bit, big endian
    b"\xce\xfa\xed\xfe",  # 32-bit, little endian
    b"\xfe\xed\xfa\xcf",  # 64-bit, big endian
    b"\xcf\xfa\xed\xfe",  # 64-bit, little endian
    b"\xca\xfe\xba\xbe",  # universal, big endian
    b"\xbe\xba\xfe\xca",  # universal, little endian
    b"\xca\xfe\xba\xbf",  # universal64, big endian
    b"\xbf\xba\xfe\xca",  # universal64, little endian
}


def parse_version(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise RuntimeError(f"invalid macOS version: {value!r}")
    return tuple((int(part) for part in [*parts, "0", "0"][:3]))  # type: ignore[return-value]


def format_version(value: tuple[int, int, int]) -> str:
    return f"{value[0]}.{value[1]}.{value[2]}"


def is_mach_o(path: Path) -> bool:
    # Java class data shares the 32-bit universal Mach-O magic.
    if path.suffix.lower() == ".class":
        return False
    try:
        with path.open("rb") as stream:
            return stream.read(4) in MACH_O_MAGICS
    except OSError:
        return False


def parse_vtool_versions(output: str) -> tuple[tuple[int, int, int], ...]:
    """Read every architecture's deployment target from ``vtool`` output."""

    command: str | None = None
    platform_name: str | None = None
    versions: list[tuple[int, int, int]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("cmd "):
            command = line.split(maxsplit=1)[1]
            platform_name = None
            continue
        if command == "LC_BUILD_VERSION" and line.startswith("platform "):
            platform_name = line.split(maxsplit=1)[1]
            continue
        if command == "LC_BUILD_VERSION" and line.startswith("minos "):
            if platform_name != "MACOS":
                raise RuntimeError(
                    f"Mach-O has non-macOS build platform {platform_name!r}"
                )
            versions.append(parse_version(line.split(maxsplit=1)[1]))
            continue
        if command == "LC_VERSION_MIN_MACOSX" and line.startswith("version "):
            versions.append(parse_version(line.split(maxsplit=1)[1]))
    if not versions:
        raise RuntimeError("Mach-O has no readable macOS deployment target")
    return tuple(versions)


def audit_bundle(
    bundle: Path,
    maximum_version: tuple[int, int, int],
    *,
    vtool: Path,
) -> dict[str, object]:
    bundle = bundle.resolve()
    if not bundle.is_dir():
        raise RuntimeError(f"application bundle does not exist: {bundle}")
    if not vtool.is_file():
        raise RuntimeError(f"vtool does not exist: {vtool}")

    physical_files: set[tuple[int, int]] = set()
    records: list[tuple[Path, tuple[int, int, int]]] = []
    for path in bundle.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(bundle):
            raise RuntimeError(f"application bundle link escapes its root: {path}")
        stat = resolved.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity in physical_files:
            continue
        physical_files.add(identity)
        if not is_mach_o(resolved):
            continue
        completed = subprocess.run(
            [str(vtool), "-show-build", str(resolved)],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"vtool could not inspect {path}: {detail}")
        observed = max(parse_vtool_versions(completed.stdout))
        records.append((path.relative_to(bundle), observed))

    if not records:
        raise RuntimeError("application bundle contains no Mach-O files")
    offenders = [record for record in records if record[1] > maximum_version]
    if offenders:
        details = ", ".join(
            f"{path} ({format_version(version)})"
            for path, version in offenders[:20]
        )
        if len(offenders) > 20:
            details += f", and {len(offenders) - 20} more"
        raise RuntimeError(
            "application bundle contains Mach-O deployment targets newer than "
            f"macOS {format_version(maximum_version)}: {details}"
        )

    maximum_observed = max(version for _, version in records)
    return {
        "maximum_allowed": format_version(maximum_version),
        "maximum_observed": format_version(maximum_observed),
        "physical_macho_files": len(records),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--maximum-version", default="13.0")
    parser.add_argument(
        "--vtool",
        type=Path,
        default=Path(shutil.which("vtool") or "/usr/bin/vtool"),
    )
    args = parser.parse_args()
    try:
        summary = audit_bundle(
            args.bundle,
            parse_version(args.maximum_version),
            vtool=args.vtool,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"macOS deployment-target audit failed: {exc}") from exc
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
