#!/usr/bin/env python3
"""Validate HolderPro's checked-in JSON schemas without external packages."""

from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    schema_path = root / "native/schema/support-layers-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    expected = "holderpro.organic-support-layers/v1"
    serialized = json.dumps(schema, sort_keys=True)
    if expected not in serialized:
        raise SystemExit(f"{schema_path} does not declare {expected}")
    if "$schema" not in schema:
        raise SystemExit(f"{schema_path} has no $schema declaration")

    paint_path = root / "native/schema/support-paint-v1.txt"
    paint = paint_path.read_text(encoding="utf-8")
    if "paint" not in paint.lower() and "facet" not in paint.lower():
        raise SystemExit(f"{paint_path} does not describe painted facets")
    print("schema identity and syntax OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
