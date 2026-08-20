from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "packaging/scripts"))

from build_dependency_manifest import validate_bundled_binary_source  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, object], Path]:
    root = tmp_path / "PrusaSlicer"
    definition = root / "deps/+Example/Example.cmake"
    runtime = root / "deps/+Example/example/lib/win64/example.dll"
    import_library = root / "deps/+Example/example/lib/win64/example.lib"
    header = root / "deps/+Example/example/include/example.h"
    for path in (definition, runtime, import_library, header):
        path.parent.mkdir(parents=True, exist_ok=True)
    definition.write_text("copy bundled Windows files\n", encoding="utf-8")
    runtime.write_bytes("ProductVersion 1.2.3.0".encode("utf-16le"))
    import_library.write_bytes(b"reviewed import library")
    header.write_text("#define EXAMPLE_VERSION \"1.2.3\"\n", encoding="utf-8")
    item: dict[str, object] = {
        "name": "Example Windows bundled build input",
        "version": "1.2.3",
        "targets": ["windows-x86_64"],
        "bundled_binary_source": {
            "definition_sha256": _sha256(definition),
            "artifacts": [
                {
                    "role": "runtime-dll",
                    "path": runtime.relative_to(root).as_posix(),
                    "sha256": _sha256(runtime),
                },
                {
                    "role": "import-library",
                    "path": import_library.relative_to(root).as_posix(),
                    "sha256": _sha256(import_library),
                },
                {
                    "role": "header",
                    "path": header.relative_to(root).as_posix(),
                    "sha256": _sha256(header),
                },
            ],
            "version_markers": [
                {
                    "path": runtime.relative_to(root).as_posix(),
                    "encoding": "utf-16le",
                    "text": "1.2.3.0",
                },
                {
                    "path": header.relative_to(root).as_posix(),
                    "encoding": "utf-8",
                    "text": '#define EXAMPLE_VERSION "1.2.3"',
                },
            ],
        },
    }
    return root, item, definition


def test_bundled_binary_source_requires_exact_artifacts_and_versions(
    tmp_path: Path,
) -> None:
    root, item, definition = _fixture(tmp_path)

    validate_bundled_binary_source(root, item, definition)

    runtime = root / "deps/+Example/example/lib/win64/example.dll"
    runtime.write_bytes(runtime.read_bytes() + b"tampered")
    with pytest.raises(RuntimeError, match="does not match"):
        validate_bundled_binary_source(root, item, definition)


def test_bundled_binary_source_does_not_require_archive_url_in_definition(
    tmp_path: Path,
) -> None:
    root, item, definition = _fixture(tmp_path)
    item["source_url"] = "https://example.invalid/example-1.2.3.tar.gz"

    assert "example.invalid" not in definition.read_text(encoding="utf-8")
    validate_bundled_binary_source(root, item, definition)


def test_bundled_binary_source_rejects_false_version_marker(tmp_path: Path) -> None:
    root, item, definition = _fixture(tmp_path)
    evidence = item["bundled_binary_source"]
    assert isinstance(evidence, dict)
    markers = evidence["version_markers"]
    assert isinstance(markers, list)
    assert isinstance(markers[0], dict)
    markers[0]["text"] = "9.9.9.0"

    with pytest.raises(RuntimeError, match="lacks version marker"):
        validate_bundled_binary_source(root, item, definition)
