# Building HolderPro

End users should install a wheel or desktop package. These instructions are for
contributors and release builders.

## Python development

Use Python 3.11 or newer:

```console
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gui,dev]"
pytest
```

On Windows, activate with `.venv\Scripts\Activate.ps1`.

Core installs contain NumPy, trimesh, manifold3d, and Shapely. The `gui` extra
adds PySide6-Essentials 6.7–6.9 and VTK. The Qt for Python 6.10+ macOS wheels
tested for v0.1 declare a deployment target newer than HolderPro's macOS 13
floor, so they remain excluded until upstream restores that contract. HolderPro
intentionally does not install the PySide6
Addons metapackage, SciPy, NetworkX, lxml, PyVista, PyVistaQt, qtpy, Rtree, or
fast-simplification. HolderPro's bounded 3MF Core/Production reader uses only
the standard library and NumPy, avoiding trimesh's NetworkX/lxml import path.

## Native prerequisites

A native build requires:

- CMake and a supported C/C++ toolchain;
- a complete PrusaSlicer checkout at commit
  `b028299c770b8380ee81c921a2867d522f288123`; and
- a PrusaSlicer dependency prefix built for the same target.

Ubuntu/Linux builders also need the platform OpenGL and DBus development
metadata (`libgl1-mesa-dev` and `libdbus-1-dev` on Ubuntu 22.04). DBus is a
configure-time prerequisite in upstream PrusaSlicer; the headless HolderPro
engine does not link or ship it.

Setup helpers verify prerequisites and never install a package manager or alter
a supplied checkout. Downloading the PrusaSlicer checkout occurs only when the
explicit `--download-source` / `-DownloadSource` option is passed. Dependency
source archives are downloaded into the private build cache and verified
against their pinned hashes. Release users never need these tools.

The helpers also prefetch GMP 6.2.1 from hash-verified GNU HTTPS mirrors before
running PrusaSlicer's dependency graph. They set CMake 4's external policy
minimum to 3.5 so older third-party CMake projects retain their documented
compatibility behavior; neither action patches the pinned PrusaSlicer source.

Available configure presets are:

- `macos-arm64`
- `macos-x86_64`
- `linux-x86_64`
- `windows-x86_64`

On Unix-like systems:

```console
./scripts/build-native.sh \
  --preset macos-arm64 \
  --source /src/PrusaSlicer \
  --deps-prefix /src/PrusaSlicer/deps/build/destdir/usr/local \
  --version 0.1.0a1 \
  --build-id local
```

On Windows:

```powershell
./scripts/build-native.ps1 `
  -Preset windows-x86_64 `
  -PrusaSlicerSource C:\src\PrusaSlicer `
  -DepsPrefix C:\src\PrusaSlicer\deps\prefix `
  -Version 0.1.0a1 `
  -BuildId local
```

The engine is written to
`native/build/<preset>/holderpro-organic-engine` (with `.exe` on Windows).
Run its CTest preset and inspect `--version-json` before packaging.

Release tags use readable SemVer prereleases (`v0.1.0-alpha.1`); embedded
Python/native product versions use the equivalent PEP 440 form (`0.1.0a1`).
Release artifacts use the exact Python 3.11.9 interpreter and dependency
versions in `packaging/release-constraints.txt`; the broader project ranges
remain the supported end-user API contract.

## Desktop bundle

Point the PyInstaller specification at the already-tested engine:

```console
python -m pip install -c packaging/release-constraints.txt \
  build setuptools wheel "pyinstaller>=6,<7" pillow
python packaging/scripts/collect_python_licenses.py \
  --output build/third-party-licenses
python packaging/scripts/verify_python_licenses.py build/third-party-licenses
python packaging/scripts/build_icons.py
HOLDERPRO_VERSION=0.1.0a1 \
  HOLDERPRO_BUILD_ID="$(git rev-parse HEAD)" \
  HOLDERPRO_NATIVE_BIN=/absolute/path/to/native/stage/macos-arm64/bin \
  HOLDERPRO_THIRD_PARTY_LICENSES=/absolute/path/to/build/third-party-licenses \
  pyinstaller --clean --noconfirm packaging/pyinstaller/HolderPro.spec
python packaging/scripts/refresh_desktop_native_manifest.py dist/HolderPro.app \
  --expected-version 0.1.0a1 --expected-target macos-arm64 \
  --expected-build-id "$(git rev-parse HEAD)"
python packaging/scripts/verify_macos_bundle.py dist/HolderPro.app \
  --version 0.1.0a1
python packaging/scripts/verify_desktop_bundle.py dist/HolderPro.app
```

PyInstaller receives an explicit Qt/VTK module allow-list. Do not replace it
with a blanket collection of a developer environment. On macOS the result is a
`.app`; other targets produce `dist/HolderPro/`.

## Reproducibility inputs

Each target attaches `HolderPro-<target>-build-environment.json`, recording the
compiler, SDK, CMake, exact Python, constraints digest, runner image, and
platform packager inputs (including the Linux appimagetool URL and hash).
`holderpro-organic-engine --version-json` embeds the exact HolderPro source
commit as its build ID. Reproducibility means rebuilding from the corresponding
source and these recorded inputs—not assuming binaries from different SDKs are
byte-for-byte identical.
