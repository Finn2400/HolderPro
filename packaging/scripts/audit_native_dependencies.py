#!/usr/bin/env python3
"""Fail when the engine links to a non-allowlisted runtime dependency."""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path


def output(command: list[str]) -> str:
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout


def dependencies(binary: Path, system: str) -> list[str]:
    if system == "darwin":
        return [line.strip().split(" ", 1)[0] for line in output(["otool", "-L", str(binary)]).splitlines()[1:] if line.strip()]
    if system == "linux":
        result: list[str] = []
        for line in output(["ldd", str(binary)]).splitlines():
            line = line.strip()
            if not line:
                continue
            result.append(line.split(" => ", 1)[0].split(" ", 1)[0])
        return result
    if dumpbin := shutil.which("dumpbin"):
        dump = output([dumpbin, "/DEPENDENTS", str(binary)])
        return re.findall(
            r"^\s+([A-Za-z0-9_.-]+\.dll)\s*$",
            dump,
            re.MULTILINE | re.IGNORECASE,
        )
    try:
        import pefile  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("Windows audit requires dumpbin or the pefile package") from error
    image = pefile.PE(str(binary))
    return [entry.dll.decode("ascii") for entry in image.DIRECTORY_ENTRY_IMPORT]


def allowed_dependency(dependency: str, system: str, allowlist: list[str]) -> bool:
    if system == "darwin":
        return Path(dependency).is_absolute() and any(
            dependency.startswith(prefix) for prefix in allowlist
        )
    basename = Path(dependency).name.lower()
    if system == "windows":
        exact = {name.lower() for name in allowlist if not name.endswith("-")}
        prefixes = [name.lower() for name in allowlist if name.endswith("-")]
        return basename in exact or any(
            basename.startswith(prefix) for prefix in prefixes
        )
    exact = {name.lower() for name in allowlist if not name.endswith(".")}
    prefixes = [name.lower() for name in allowlist if name.endswith(".")]
    return basename in exact or any(basename.startswith(prefix) for prefix in prefixes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument(
        "--companion-dir",
        type=Path,
        help="directory whose exact DLL basenames may satisfy private dependencies",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "native-runtime-allowlist.json",
    )
    args = parser.parse_args()
    if not args.binary.is_file():
        raise SystemExit(f"native engine does not exist: {args.binary}")
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    if system is None:
        raise SystemExit(f"unsupported audit host: {platform.system()}")
    allow = json.loads(args.allowlist.read_text(encoding="utf-8"))[system]
    found = dependencies(args.binary, system)
    companions = (
        {path.name.lower() for path in args.companion_dir.iterdir() if path.is_file()}
        if args.companion_dir
        else set()
    )
    rejected = [
        dep
        for dep in found
        if not allowed_dependency(dep, system, allow)
        and Path(dep).name.lower() not in companions
    ]
    if rejected:
        raise SystemExit("unexpected native runtime dependencies:\n- " + "\n- ".join(rejected))
    print(json.dumps({"binary": str(args.binary), "system": system, "dependencies": found}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
