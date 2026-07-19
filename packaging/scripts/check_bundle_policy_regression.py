#!/usr/bin/env python3
"""Prove the desktop policy accepts its closure and detects forbidden modules."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from verify_desktop_bundle import verify_bundle


def main() -> int:
    policy_path = (
        Path(__file__).resolve().parents[1] / "pyinstaller/bundle-policy.json"
    )
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="holderpro-bundle-policy-") as temporary:
        bundle = Path(temporary) / "HolderPro"
        for fragment in policy["required_path_fragments"]:
            relative = fragment
            if fragment in {"vtkmodules", "PySide6"}:
                relative = f"_internal/{fragment}/__init__.pyc"
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"holderpro-policy-fixture")
        verify_bundle(bundle, policy)

        forbidden = bundle / "_internal/networkx/__init__.pyc"
        forbidden.parent.mkdir(parents=True)
        forbidden.write_bytes(b"forbidden")
        try:
            verify_bundle(bundle, policy)
        except RuntimeError as exc:
            if "forbidden bundled module networkx" not in str(exc):
                raise
        else:
            raise RuntimeError("desktop policy failed to detect a forbidden module")
    print("desktop bundle policy regression OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
