"""Discovery and provenance inspection for the bundled native engine."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .errors import EngineError, EngineNotFoundError, EngineProvenanceError
from .version import __version__

ENGINE_EXECUTABLE = "holderpro-organic-engine"
LAYER_SCHEMA = "holderpro.organic-support-layers/v1"
PAINT_SCHEMA = "HOLDERPRO_SUPPORT_PAINT_V1"
PINNED_PRUSASLICER_VERSION = "2.9.6"
PINNED_PRUSASLICER_COMMIT = "b028299c770b8380ee81c921a2867d522f288123"


@dataclass(frozen=True, slots=True)
class EngineInfo:
    """Version and build provenance reported by ``--version-json``.

    Instances returned by :func:`inspect_engine` are verified against this
    HolderPro build, its schemas, the target platform, and the pinned
    PrusaSlicer revision. Unversioned or mismatched adapters are rejected.
    """

    path: Path
    holderpro_version: str | None
    adapter_version: str | None
    prusaslicer_version: str | None
    prusaslicer_commit: str | None
    layer_schema: str | None
    paint_schema: str | None
    os: str | None
    architecture: str | None
    build_id: str | None
    verified: bool
    provenance_source: str

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-serializable representation."""

        return {
            "path": str(self.path),
            "holderpro_version": self.holderpro_version,
            "adapter_version": self.adapter_version,
            "prusaslicer_version": self.prusaslicer_version,
            "prusaslicer_commit": self.prusaslicer_commit,
            "layer_schema": self.layer_schema,
            "paint_schema": self.paint_schema,
            "os": self.os,
            "architecture": self.architecture,
            "build_id": self.build_id,
            "verified": self.verified,
            "provenance_source": self.provenance_source,
        }


def project_root() -> Path:
    """Return the repository root when running from a source checkout."""

    return Path(__file__).resolve().parents[2]


def _architecture() -> str:
    machine = platform.machine().lower()
    return {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
    }.get(machine, machine)


def _operating_system() -> str:
    return {
        "darwin": "macos",
        "windows": "windows",
        "linux": "linux",
        "freebsd": "freebsd",
    }.get(platform.system().lower(), platform.system().lower())


def _engine_filename(name: str = ENGINE_EXECUTABLE) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _is_executable(path: Path) -> bool:
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def _packaged_engine_candidates() -> list[Path]:
    """Return engine resources included in an installed platform wheel."""

    try:
        native = resources.files("holderpro").joinpath("_native")
    except (ModuleNotFoundError, TypeError):
        return []

    names = (_engine_filename(),)
    system = platform.system().lower()
    architecture = _architecture()
    directories = (
        native,
        native.joinpath(f"{_operating_system()}-{architecture}"),
        native.joinpath(f"{system}-{architecture}"),
        native.joinpath(f"{sys.platform}-{architecture}"),
        native.joinpath(f"{system}_{architecture}"),
    )
    candidates: list[Path] = []
    for directory in directories:
        for name in names:
            candidate = directory.joinpath(name)
            # Platform wheels are installed unpacked.  Zip imports cannot run a
            # native executable in place and are intentionally unsupported.
            try:
                path = Path(str(candidate))
            except (TypeError, ValueError):
                continue
            candidates.append(path)
    return candidates


def _source_engine_candidates() -> list[Path]:
    root = project_root()
    native = root / "native"
    executable = _engine_filename()
    release_preset = f"{_operating_system()}-{_architecture()}"
    return [
        native / "build" / release_preset / executable,
        native / "build" / executable,
        native / "build" / "Release" / executable,
        native / "build-release" / executable,
        native / "build-release" / "Release" / executable,
        native / "build-default" / executable,
        native / ".prusa-src" / "build-no-occt" / "src" / executable,
        native / ".prusa-src" / "build-default" / "src" / executable,
    ]


def find_engine(explicit: str | Path | None = None) -> Path:
    """Locate the native adapter without falling back to PrusaSlicer itself.

    Search order is an explicit argument, ``HOLDERPRO_ENGINE``, the engine
    bundled in the installed wheel/application, source-checkout build folders,
    and finally ``PATH``.
    """

    if explicit is not None:
        resolved = Path(explicit).expanduser().resolve()
        if _is_executable(resolved):
            return resolved
        raise EngineNotFoundError(
            f"The requested HolderPro Organic engine is not executable: {resolved}"
        )

    candidates: list[Path] = []
    configured_name = "HOLDERPRO_ENGINE"
    configured = os.environ.get(configured_name)
    if configured:
        resolved = Path(configured).expanduser().resolve()
        if _is_executable(resolved):
            return resolved
        raise EngineNotFoundError(
            f"The engine configured by {configured_name} is not executable: "
            f"{resolved}"
        )
    candidates.extend(_packaged_engine_candidates())
    candidates.extend(_source_engine_candidates())
    if on_path := shutil.which(_engine_filename()):
        candidates.append(Path(on_path))

    checked: list[str] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        checked.append(str(resolved))
        if _is_executable(resolved):
            return resolved

    locations = "\n  - ".join(checked) if checked else "(no candidates)"
    raise EngineNotFoundError(
        "The bundled HolderPro Organic engine was not found. Reinstall the wheel "
        "for this platform, pass --engine, or set HOLDERPRO_ENGINE. Developers "
        f"may build it with {project_root() / 'scripts' / 'build-native.sh'}. "
        "Checked:\n  - "
        f"{locations}"
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_engine_info(path: Path, payload: object) -> EngineInfo:
    if not isinstance(payload, Mapping):
        raise EngineProvenanceError("Engine --version-json output is not an object")
    product = _mapping(payload.get("product"))
    adapter = _mapping(payload.get("adapter"))
    prusaslicer = _mapping(payload.get("prusaslicer"))
    schemas = _mapping(payload.get("schemas"))
    info = EngineInfo(
        path=path,
        holderpro_version=_optional_string(product.get("version")),
        adapter_version=_optional_string(adapter.get("version")),
        prusaslicer_version=_optional_string(prusaslicer.get("version")),
        prusaslicer_commit=_optional_string(prusaslicer.get("commit")),
        layer_schema=_optional_string(schemas.get("layers")),
        paint_schema=_optional_string(schemas.get("paint")),
        os=_optional_string(payload.get("os")),
        architecture=_optional_string(payload.get("architecture")),
        build_id=_optional_string(payload.get("build_id")),
        verified=False,
        provenance_source="version-json",
    )
    expected = {
        "product name": (product.get("name"), "HolderPro"),
        "product version": (info.holderpro_version, __version__),
        "adapter name": (adapter.get("name"), ENGINE_EXECUTABLE),
        "PrusaSlicer version": (
            info.prusaslicer_version,
            PINNED_PRUSASLICER_VERSION,
        ),
        "PrusaSlicer commit": (
            info.prusaslicer_commit,
            PINNED_PRUSASLICER_COMMIT,
        ),
        "layer schema": (info.layer_schema, LAYER_SCHEMA),
        "paint schema": (info.paint_schema, PAINT_SCHEMA),
        "operating system": (info.os, _operating_system()),
        "architecture": (info.architecture, _architecture()),
    }
    mismatches = [
        f"{name}={actual!r} (expected {wanted!r})"
        for name, (actual, wanted) in expected.items()
        if actual != wanted
    ]
    if mismatches:
        raise EngineProvenanceError(
            "Native engine provenance does not match this HolderPro build: "
            + "; ".join(mismatches)
        )
    return EngineInfo(
        **{
            **info.to_dict(),
            "path": path,
            "verified": True,
        }
    )


def inspect_engine(
    path: str | Path | None = None,
    *,
    timeout: float = 10.0,
) -> EngineInfo:
    """Query and validate the current engine's complete build provenance."""

    engine = find_engine(path)
    try:
        completed = subprocess.run(
            (str(engine), "--version-json"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = f"Could not query HolderPro engine: {exc}"
        if platform.system() == "Windows" and getattr(exc, "winerror", None) == 126:
            detail += (
                ". Install the current Microsoft Visual C++ v14 x64 "
                "Redistributable, then run holderpro doctor again"
            )
        raise EngineError(detail) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise EngineError(
            "HolderPro engine did not support --version-json"
            + (f": {detail[-1000:]}" if detail else "")
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise EngineProvenanceError(
            "HolderPro engine returned invalid --version-json output"
        ) from exc
    return _parse_engine_info(engine, payload)


__all__ = [
    "ENGINE_EXECUTABLE",
    "EngineError",
    "EngineInfo",
    "EngineNotFoundError",
    "EngineProvenanceError",
    "LAYER_SCHEMA",
    "PAINT_SCHEMA",
    "PINNED_PRUSASLICER_COMMIT",
    "PINNED_PRUSASLICER_VERSION",
    "find_engine",
    "inspect_engine",
    "project_root",
]
