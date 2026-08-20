from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "packaging/scripts"))

from verify_pypi_release import (  # noqa: E402
    expected_wheel_names,
    local_wheel_records,
    verify_pypi_document,
)


VERSION = "0.1.0a1"


def _local_wheels(directory: Path) -> dict[str, dict[str, object]]:
    directory.mkdir()
    for index, name in enumerate(sorted(expected_wheel_names(VERSION)), start=1):
        (directory / name).write_bytes(f"wheel-{index}".encode())
    return local_wheel_records(directory, VERSION)


def _remote_record(name: str, record: dict[str, object]) -> dict[str, object]:
    return {
        "filename": name,
        "packagetype": "bdist_wheel",
        "digests": {"sha256": record["sha256"]},
        "size": record["size"],
    }


def test_pypi_preflight_allows_no_files_or_an_exact_partial_upload(
    tmp_path: Path,
) -> None:
    local = _local_wheels(tmp_path / "wheels")
    first_name = sorted(local)[0]
    empty = {"info": {"version": VERSION}, "urls": []}
    partial = {
        "info": {"version": VERSION},
        "urls": [_remote_record(first_name, local[first_name])],
    }

    assert verify_pypi_document(local, VERSION, empty, require_complete=False) == 0
    assert verify_pypi_document(local, VERSION, partial, require_complete=False) == 1


def test_pypi_postflight_requires_all_four_exact_wheels(tmp_path: Path) -> None:
    local = _local_wheels(tmp_path / "wheels")
    document = {
        "info": {"version": VERSION},
        "urls": [_remote_record(name, record) for name, record in local.items()],
    }

    assert verify_pypi_document(local, VERSION, document, require_complete=True) == 4
    document["urls"].pop()
    with pytest.raises(RuntimeError, match="release is incomplete"):
        verify_pypi_document(local, VERSION, document, require_complete=True)


@pytest.mark.parametrize("mutation", ["digest", "size", "unexpected", "sdist"])
def test_pypi_recovery_rejects_any_foreign_or_changed_file(
    tmp_path: Path,
    mutation: str,
) -> None:
    local = _local_wheels(tmp_path / "wheels")
    name = sorted(local)[0]
    record = _remote_record(name, local[name])
    if mutation == "digest":
        record["digests"] = {"sha256": "0" * 64}
    elif mutation == "size":
        record["size"] = int(record["size"]) + 1
    elif mutation == "unexpected":
        record["filename"] = "holderpro-foreign.tar.gz"
    else:
        record["packagetype"] = "sdist"
    document = {"info": {"version": VERSION}, "urls": [record]}

    with pytest.raises(RuntimeError, match="unexpected file|does not match"):
        verify_pypi_document(local, VERSION, document, require_complete=False)


def test_local_pypi_payload_must_be_the_closed_platform_wheel_set(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "wheels"
    _local_wheels(directory)
    (directory / "holderpro-0.1.0a1.tar.gz").write_bytes(b"source")

    with pytest.raises(RuntimeError, match="wheel set is not closed"):
        local_wheel_records(directory, VERSION)
