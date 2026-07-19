"""Privacy-conscious diagnostics used by the CLI and desktop application."""

from __future__ import annotations

import ctypes.util
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata, util
from pathlib import Path
from typing import Any, Literal

from .engine import (
    EngineError,
    EngineInfo,
    EngineNotFoundError,
    find_engine,
    inspect_engine,
)
from .version import __version__

CheckStatus = Literal["ok", "warning", "error"]


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One actionable environment check."""

    name: str
    status: CheckStatus
    summary: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": dict(self.details or {}),
        }


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Structured HolderPro environment diagnostics with no model geometry."""

    holderpro_version: str
    generated_at: str
    platform: dict[str, str]
    checks: tuple[DoctorCheck, ...]
    engine_info: EngineInfo | None = None

    @property
    def ok(self) -> bool:
        """Whether command-line generation prerequisites passed."""

        return all(check.status != "error" for check in self.checks)

    @property
    def desktop_ok(self) -> bool:
        """Whether generation and every desktop rendering prerequisite passed."""

        statuses = {check.name: check.status for check in self.checks}
        return self.ok and all(
            statuses.get(name) == "ok"
            for name in ("Qt GUI", "VTK renderer", "OpenGL")
        )

    def to_dict(self, *, redact_paths: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "holderpro.diagnostics/v1",
            "holderpro_version": self.holderpro_version,
            "generated_at": self.generated_at,
            "platform": dict(self.platform),
            "ok": self.ok,
            "generation_ok": self.ok,
            "desktop_ok": self.desktop_ok,
            "checks": [check.to_dict() for check in self.checks],
            "engine": self.engine_info.to_dict() if self.engine_info else None,
            "privacy": {
                "contains_model_geometry": False,
                "paths_redacted": redact_paths,
            },
        }
        return _redact(payload) if redact_paths else payload

    def to_json(self, *, redact_paths: bool = True, indent: int = 2) -> str:
        return json.dumps(
            self.to_dict(redact_paths=redact_paths),
            indent=indent,
            sort_keys=True,
        )

    def to_text(self, *, redact_paths: bool = True) -> str:
        data = self.to_dict(redact_paths=redact_paths)
        lines = [
            f"HolderPro {data['holderpro_version']}",
            f"Platform: {data['platform']['system']} "
            f"{data['platform']['release']} ({data['platform']['architecture']})",
        ]
        for check in data["checks"]:
            lines.append(
                f"[{str(check['status']).upper()}] {check['name']}: "
                f"{check['summary']}"
            )
        return "\n".join(lines)


def _redaction_roots() -> tuple[tuple[str, str], ...]:
    roots: list[tuple[str, str]] = []
    for path, replacement in (
        (Path.home().resolve(), "[HOME]"),
        (Path(tempfile.gettempdir()).resolve(), "[TEMP]"),
        (Path.cwd().resolve(), "[CWD]"),
    ):
        value = str(path)
        if value and value != os.path.sep and all(value != seen for seen, _ in roots):
            roots.append((value, replacement))
    roots.sort(key=lambda item: len(item[0]), reverse=True)
    return tuple(roots)


_PATH_KEYS = frozenset({"directory", "engine", "input", "output", "output_path", "path"})
_POSIX_PATH = re.compile(r"(?<![:/\w\]])/(?![/\s])[^\r\n]*")
_WINDOWS_PATH = re.compile(r"(?i)(?<!\w)(?:[a-z]:[\\/]|\\\\)[^\r\n]*")


def _redact(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: _redact(item, key=item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        for root, replacement in _redaction_roots():
            value = value.replace(root, replacement)
        if key in _PATH_KEYS and not value.startswith(("[HOME]", "[TEMP]", "[CWD]")):
            if Path(value).is_absolute() or re.match(r"(?i)^(?:[a-z]:[\\/]|\\\\)", value):
                return "[PATH]"
        value = _WINDOWS_PATH.sub("[PATH]", value)
        value = _POSIX_PATH.sub("[PATH]", value)
    return value


def _permission_check(name: str, directory: Path) -> DoctorCheck:
    try:
        if not directory.is_dir():
            return DoctorCheck(name, "error", f"Directory does not exist: {directory}")
        with tempfile.NamedTemporaryFile(
            prefix=".holderpro-write-test-", dir=directory, delete=True
        ) as handle:
            handle.write(b"HolderPro permission test\n")
            handle.flush()
        return DoctorCheck(
            name,
            "ok",
            "Temporary file creation succeeded",
            {"directory": str(directory.resolve())},
        )
    except OSError as exc:
        return DoctorCheck(
            name,
            "error",
            f"Cannot create and remove a temporary file: {exc}",
            {"directory": str(directory)},
        )


def _dependency_check(engine: Path, *, provenance_verified: bool) -> DoctorCheck:
    system = platform.system()
    command: tuple[str, ...]
    if system == "Darwin":
        command = ("otool", "-L", str(engine))
    elif system == "Linux":
        command = ("ldd", str(engine))
    elif system == "Windows":
        # Windows has no guaranteed built-in equivalent to ldd.  Starting the
        # executable for --version-json already asks the loader to resolve all
        # of its imports, which is the reliable end-user check.
        return DoctorCheck(
            "Engine dependencies",
            "ok" if provenance_verified else "error",
            "Windows loaded and executed the verified engine successfully"
            if provenance_verified
            else "Native engine provenance was not verified",
        )
    else:
        return DoctorCheck(
            "Engine dependencies",
            "warning",
            f"No dependency audit is implemented for {system}",
        )
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return DoctorCheck(
            "Engine dependencies",
            "warning",
            f"Could not run the platform dependency audit: {exc}",
        )
    output = completed.stdout.strip()
    missing = [line.strip() for line in output.splitlines() if "not found" in line]
    if completed.returncode != 0 or missing:
        return DoctorCheck(
            "Engine dependencies",
            "error",
            "One or more native dependencies could not be resolved",
            {"missing": missing, "audit_exit_code": completed.returncode},
        )
    dependencies = [line.strip() for line in output.splitlines()[1:] if line.strip()]
    return DoctorCheck(
        "Engine dependencies",
        "ok",
        f"Resolved {len(dependencies)} native dependency entries",
        {"dependency_count": len(dependencies)},
    )


def _module_available(module: str) -> bool:
    try:
        return util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _package_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


_OPENGL_PROBE = r"""
import json
from vtkmodules.vtkRenderingCore import vtkRenderWindow, vtkRenderer
import vtkmodules.vtkRenderingOpenGL2  # register the OpenGL render-window factory

window = vtkRenderWindow()
renderer = vtkRenderer()
window.AddRenderer(renderer)
window.SetSize(1, 1)
window.SetOffScreenRendering(1)
window.Render()
capabilities = window.ReportCapabilities() or ""
details = {}
for line in capabilities.splitlines():
    lowered = line.lower().strip()
    for label, key in (
        ("opengl vendor string:", "vendor"),
        ("opengl renderer string:", "renderer"),
        ("opengl version string:", "version"),
    ):
        if lowered.startswith(label):
            details[key] = line.split(":", 1)[1].strip()
details["supports_opengl"] = bool(window.SupportsOpenGL())
window.Finalize()
print(json.dumps(details, sort_keys=True))
"""


def opengl_probe_main() -> int:
    """Run the VTK graphics probe for a frozen launcher's private child mode."""

    from vtkmodules.vtkRenderingCore import vtkRenderer, vtkRenderWindow

    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401 - registers backend

    window = vtkRenderWindow()
    renderer = vtkRenderer()
    window.AddRenderer(renderer)
    window.SetSize(1, 1)
    window.SetOffScreenRendering(1)
    window.Render()
    capabilities = window.ReportCapabilities() or ""
    details: dict[str, Any] = {}
    for line in capabilities.splitlines():
        lowered = line.lower().strip()
        for label, key in (
            ("opengl vendor string:", "vendor"),
            ("opengl renderer string:", "renderer"),
            ("opengl version string:", "version"),
        ):
            if lowered.startswith(label):
                details[key] = line.split(":", 1)[1].strip()
    details["supports_opengl"] = bool(window.SupportsOpenGL())
    window.Finalize()
    print(json.dumps(details, sort_keys=True))
    return 0


def _opengl_check(vtk_available: bool) -> DoctorCheck:
    libraries = tuple(
        library
        for library in (
            ctypes.util.find_library("OpenGL"),
            ctypes.util.find_library("GL"),
            ctypes.util.find_library("EGL"),
        )
        if library
    )
    if not vtk_available:
        return DoctorCheck(
            "OpenGL",
            "warning",
            "A graphics-context probe was skipped because VTK is not installed",
            {"runtime_libraries": sorted(set(libraries)), "probe": "vtk-unavailable"},
        )
    try:
        command = (
            (sys.executable, "--holderpro-opengl-probe")
            if getattr(sys, "frozen", False)
            else (sys.executable, "-I", "-c", _OPENGL_PROBE)
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return DoctorCheck(
            "OpenGL",
            "warning",
            f"The isolated graphics-context probe could not run: {exc}",
            {"runtime_libraries": sorted(set(libraries)), "probe": "launch-failed"},
        )
    if completed.returncode != 0:
        reason = (completed.stderr or completed.stdout).strip()[-1200:]
        return DoctorCheck(
            "OpenGL",
            "warning",
            "VTK could not create an OpenGL context in the isolated probe",
            {
                "runtime_libraries": sorted(set(libraries)),
                "probe": "context-failed",
                "exit_code": completed.returncode,
                "reason": reason or "No diagnostic text was returned",
            },
        )
    try:
        details = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return DoctorCheck(
            "OpenGL",
            "warning",
            "The isolated OpenGL probe returned an unreadable response",
            {
                "runtime_libraries": sorted(set(libraries)),
                "probe": "invalid-response",
            },
        )
    if not isinstance(details, dict):
        details = {}
    if not details.get("supports_opengl"):
        return DoctorCheck(
            "OpenGL",
            "warning",
            "VTK created a context but reported that OpenGL is unsupported",
            {"runtime_libraries": sorted(set(libraries)), **details},
        )
    vendor = str(details.get("vendor") or "unknown vendor")
    renderer = str(details.get("renderer") or "unknown renderer")
    version = str(details.get("version") or "unknown version")
    return DoctorCheck(
        "OpenGL",
        "ok",
        f"{vendor} / {renderer} / OpenGL {version}",
        {
            "vendor": vendor,
            "renderer": renderer,
            "version": version,
            "runtime_libraries": sorted(set(libraries)),
            "probe": "isolated-vtk-context",
        },
    )


def _gui_checks() -> tuple[DoctorCheck, DoctorCheck, DoctorCheck]:
    qt_available = _module_available("PySide6") and _module_available(
        "PySide6.QtWidgets"
    )
    vtk_available = _module_available("vtkmodules")
    qt = DoctorCheck(
        "Qt GUI",
        "ok" if qt_available else "warning",
        (
            f"PySide6 {_package_version('PySide6-Essentials') or 'available'}"
            if qt_available
            else "Not installed; install holderpro[gui] to use holderpro-gui"
        ),
    )
    vtk = DoctorCheck(
        "VTK renderer",
        "ok" if vtk_available else "warning",
        (
            f"VTK {_package_version('vtk') or 'available'}"
            if vtk_available
            else "Not installed; install holderpro[gui] to use the 3D preview"
        ),
    )
    opengl = _opengl_check(vtk_available)
    return qt, vtk, opengl


def run_doctor(
    output_dir: Path | None = None,
    engine_path: Path | None = None,
) -> DoctorReport:
    """Collect generation, provenance, permission, GUI, and OpenGL checks.

    No input model is opened, copied, hashed, or included in the report.
    """

    checks: list[DoctorCheck] = []
    engine_info: EngineInfo | None = None
    try:
        engine = find_engine(engine_path)
        engine_info = inspect_engine(engine)
        checks.append(
            DoctorCheck(
                "Engine provenance",
                "ok",
                "Pinned PrusaSlicer engine provenance verified",
                {"engine": str(engine), "build_id": engine_info.build_id},
            )
        )
        checks.append(
            _dependency_check(engine, provenance_verified=engine_info.verified)
        )
    except EngineNotFoundError as exc:
        checks.append(DoctorCheck("Engine provenance", "error", str(exc)))
    except EngineError as exc:
        checks.append(DoctorCheck("Engine provenance", "error", str(exc)))

    checks.append(_permission_check("Temporary directory", Path(tempfile.gettempdir())))
    checks.append(_permission_check("Output directory", output_dir or Path.cwd()))
    checks.extend(_gui_checks())
    return DoctorReport(
        holderpro_version=__version__,
        generated_at=datetime.now(UTC).isoformat(),
        platform={
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
        },
        checks=tuple(checks),
        engine_info=engine_info,
    )


def collect_diagnostics(output_dir: Path | None = None) -> DoctorReport:
    """GUI-friendly alias for :func:`run_doctor`."""

    return run_doctor(output_dir=output_dir)


def export_diagnostics(
    destination: Path,
    report: DoctorReport | None = None,
    redact_paths: bool = True,
) -> Path:
    """Write a redacted JSON diagnostic bundle without any model geometry."""

    path = Path(destination).expanduser()
    if path.exists() and path.is_dir():
        path = path / "holderpro-diagnostics.json"
    elif not path.suffix:
        path = path.with_suffix(".json")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (report or run_doctor(output_dir=path.parent)).to_json(
        redact_paths=redact_paths
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


__all__ = [
    "DoctorCheck",
    "DoctorReport",
    "collect_diagnostics",
    "export_diagnostics",
    "opengl_probe_main",
    "run_doctor",
]
