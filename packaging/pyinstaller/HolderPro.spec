# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-directory desktop definition with an explicit GUI allow-list."""

import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]
VERSION = os.environ.get("HOLDERPRO_VERSION")
BUILD_ID = os.environ.get("HOLDERPRO_BUILD_ID")
version_source = (ROOT / "src/holderpro/version.py").read_text(encoding="utf-8")
fallback_match = re.search(r'^FALLBACK_VERSION = "([^"]+)"$', version_source, re.MULTILINE)
if not VERSION or fallback_match is None or VERSION != fallback_match.group(1):
    raise SystemExit("HOLDERPRO_VERSION must exactly match holderpro.version.FALLBACK_VERSION")
if not BUILD_ID:
    raise SystemExit("HOLDERPRO_BUILD_ID must identify the exact HolderPro source commit")
pep_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?", VERSION)
if pep_match is None:
    raise SystemExit("HOLDERPRO_VERSION is not a supported release version")
major, minor, patch = (int(pep_match.group(index)) for index in range(1, 4))
MACOS_SHORT_VERSION = f"{major}.{minor}.{patch}"
stage = pep_match.group(4)
stage_number = int(pep_match.group(5) or 0)
stage_offset = {"a": 0, "b": 30, "rc": 60, None: 99}[stage]
if stage_number > 29:
    raise SystemExit("macOS release stage numbers may not exceed 29")
MACOS_BUILD_VERSION = str(
    major * 1_000_000 + minor * 10_000 + patch * 100 + stage_offset + stage_number
)
NATIVE_BIN = Path(os.environ.get("HOLDERPRO_NATIVE_BIN", "")).resolve()
if not NATIVE_BIN.is_dir():
    raise SystemExit(
        "HOLDERPRO_NATIVE_BIN must name the audited CMake install bin directory"
    )
native_files = sorted(path for path in NATIVE_BIN.iterdir() if path.is_file())
engine_files = [path for path in native_files if path.name in {"holderpro-organic-engine", "holderpro-organic-engine.exe"}]
if len(engine_files) != 1:
    raise SystemExit("HOLDERPRO_NATIVE_BIN must contain exactly one HolderPro engine")
companions = [path for path in native_files if path not in engine_files]
if platform.system() == "Windows":
    if any(path.suffix.lower() != ".dll" for path in companions):
        raise SystemExit("only audited DLL companions may accompany the Windows engine")
elif companions:
    raise SystemExit("the static Unix engine install may not contain companion libraries")
target = {
    ("Darwin", "arm64"): "macos-arm64",
    ("Darwin", "x86_64"): "macos-x86_64",
    ("Linux", "x86_64"): "linux-x86_64",
    ("Windows", "AMD64"): "windows-x86_64",
}.get((platform.system(), platform.machine()))
if target is None:
    raise SystemExit(f"unsupported PyInstaller target: {platform.system()} {platform.machine()}")
native_manifest_directory = Path(tempfile.mkdtemp(prefix="holderpro-native-manifest-"))
native_manifest_path = native_manifest_directory / "MANIFEST.json"
subprocess.run(
    [
        sys.executable,
        str(ROOT / "packaging/scripts/verify_native_stage.py"),
        "--native-bin",
        str(NATIVE_BIN),
        "--expected-version",
        VERSION,
        "--expected-target",
        target,
        "--expected-build-id",
        BUILD_ID,
        "--manifest-out",
        str(native_manifest_path),
    ],
    check=True,
)
LICENSES = Path(os.environ.get("HOLDERPRO_THIRD_PARTY_LICENSES", "")).resolve()
if not (LICENSES / "MANIFEST.json").is_file():
    raise SystemExit(
        "HOLDERPRO_THIRD_PARTY_LICENSES must name a verified collected license directory"
    )

qt_modules = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtSvg",
    "PySide6.QtWidgets",
]
vtk_modules = [
    "vtkmodules.qt.QVTKRenderWindowInteractor",
    "vtkmodules.util.numpy_support",
    "vtkmodules.vtkCommonCore",
    "vtkmodules.vtkCommonDataModel",
    "vtkmodules.vtkFiltersCore",
    "vtkmodules.vtkFiltersSources",
    "vtkmodules.vtkInteractionStyle",
    "vtkmodules.vtkRenderingCore",
    "vtkmodules.vtkRenderingFreeType",
    "vtkmodules.vtkRenderingOpenGL2",
    "vtkmodules.vtkRenderingUI",
]

hidden_imports = qt_modules + vtk_modules

icon_path = None
if platform.system() == "Windows":
    candidate = ROOT / "packaging/assets/HolderPro.ico"
    icon_path = str(candidate) if candidate.is_file() else None
elif platform.system() == "Darwin":
    candidate = ROOT / "packaging/assets/HolderPro.icns"
    icon_path = str(candidate) if candidate.is_file() else None

analysis = Analysis(
    [str(ROOT / "packaging/pyinstaller/launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[(str(path), "holderpro/_native") for path in native_files],
    datas=[
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
        (str(ROOT / "upstream/prusaslicer-2.9.6-organic/LICENSE"), "PRUSASLICER_LICENSE"),
        (str(ROOT / "native/schema"), "holderpro/schema"),
        (str(ROOT / "src/holderpro/assets/holderpro.svg"), "holderpro/assets"),
        (str(native_manifest_path), "holderpro/_native"),
        (str(LICENSES), "THIRD_PARTY_LICENSES"),
    ],
    hiddenimports=hidden_imports,
    excludes=[
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtGraphs",
        "PySide6.QtMultimedia",
        "PySide6.QtNetworkAuth",
        "PySide6.QtPdf",
        "PySide6.QtPositioning",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtSensors",
        "PySide6.QtSpatialAudio",
        "PySide6.QtTextToSpeech",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
        "IPython",
        "PIL",
        "charset_normalizer",
        "fast_simplification",
        "lxml",
        "matplotlib",
        "mpl_toolkits",
        "networkx",
        "pandas",
        "pkg_resources",
        "pyvista",
        "pyvistaqt",
        "pytest",
        "qtpy",
        "rtree",
        "scipy",
        "setuptools",
        "tkinter",
        "vtk",
        "yaml",
    ],
    # Keep pure modules as inspectable .pyc files so the release policy can
    # prove that forbidden dependency trees were not bundled.
    noarchive=True,
)

python_archive = PYZ(analysis.pure)
executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="HolderPro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_path,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="HolderPro",
    contents_directory="_internal",
)

if platform.system() == "Darwin":
    application = BUNDLE(
        collection,
        name="HolderPro.app",
        icon=icon_path,
        bundle_identifier="io.github.finn2400.HolderPro",
        version=MACOS_SHORT_VERSION,
        info_plist={
            "CFBundleDisplayName": "HolderPro",
            "CFBundleShortVersionString": MACOS_SHORT_VERSION,
            "CFBundleVersion": MACOS_BUILD_VERSION,
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
        },
    )
