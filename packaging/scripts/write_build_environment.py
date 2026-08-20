#!/usr/bin/env python3
"""Record non-secret, target-specific inputs used to build a release artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


def command_version(*candidates: list[str]) -> str | None:
    for command in candidates:
        if shutil.which(command[0]) is None:
            continue
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        value = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        if value:
            return value[:4000]
    return None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def constraints(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--constraints",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "release-constraints.txt",
    )
    args = parser.parse_args()
    runner_keys = (
        "ImageOS",
        "ImageVersion",
        "RUNNER_OS",
        "RUNNER_ARCH",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "WindowsSDKVersion",
        "VCToolsVersion",
        "MACOSX_DEPLOYMENT_TARGET",
    )
    document = {
        "schema": "holderpro.build-environment/v1",
        "target": args.target,
        "version": args.version,
        "holderpro_build_id": args.build_id,
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "runner": {key: os.environ[key] for key in runner_keys if os.environ.get(key)},
        "constraints": {
            "filename": args.constraints.name,
            "sha256": sha256(args.constraints),
            "entries": constraints(args.constraints),
        },
        "tools": {
            "cmake": command_version(["cmake", "--version"]),
            "compiler": command_version(
                ["cl"],
                [os.environ.get("CXX", "c++"), "--version"],
                ["clang++", "--version"],
            ),
            "xcode": command_version(["xcodebuild", "-version"]),
            "macos_sdk": command_version(["xcrun", "--sdk", "macosx", "--show-sdk-version"]),
            "glibc": command_version(["ldd", "--version"]),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
