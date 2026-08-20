# Building HolderPro

End users should install a wheel from PyPI or GitHub Releases. These
instructions are for contributors and release builders.

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
adds PySide6-Essentials and VTK. On macOS, PySide6-Essentials is limited to
6.7–6.9. The Qt for Python 6.10+ macOS wheels
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

Unix helpers also prefetch GMP 6.2.1 from hash-verified GNU HTTPS mirrors before
running PrusaSlicer's dependency graph. PrusaSlicer's Windows recipe instead
uses its pinned GMP 5.0.1 and MPFR 3.0.0 headers/import libraries/DLLs as build
inputs. HolderPro verifies those exact upstream bytes and version markers and
includes the matching authoritative source archives in corresponding source;
the newer 6.2.1/4.2.1 source records remain for the Unix targets. The helpers
also set CMake 4's external policy minimum to 3.5 so older third-party CMake
projects retain their documented compatibility behavior. None of these checks
patches the pinned PrusaSlicer source.

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

## Platform wheel

Install the tested engine into a clean staging directory, then build the wheel
with the target's explicit platform tag:

```console
cmake --install native/build/macos-arm64 \
  --prefix native/stage/macos-arm64 --strip
python packaging/scripts/build_platform_wheel.py \
  --repository . \
  --native-bin native/stage/macos-arm64/bin \
  --version 0.1.0a1 \
  --build-id "$(git rev-parse HEAD)" \
  --target macos-arm64 \
  --platform-tag macosx_13_0_arm64 \
  --native-license-directory build/native-license-bundle \
  --output dist
python packaging/scripts/verify_platform_wheel.py \
  dist/holderpro-0.1.0a1-py3-none-macosx_13_0_arm64.whl \
  --platform-tag macosx_13_0_arm64 \
  --version 0.1.0a1 \
  --build-id "$(git rev-parse HEAD)" \
  --target macos-arm64
```

The wheel contains HolderPro's Python source, the matching native engine and
any required FOSS companion DLLs, and a digest-bound legal-notice bundle for
the complete reviewed native dependency set. Core and GUI dependencies remain
separately installed packages. Do not copy a development environment, Qt, VTK,
proprietary system runtime DLLs, or platform installer payloads into the wheel.

## Reproducibility inputs

Each target attaches `HolderPro-<target>-build-environment.json`, recording the
compiler, SDK, CMake, exact Python, constraints digest, and runner image.
`holderpro-organic-engine --version-json` embeds the exact HolderPro source
commit as its build ID. Reproducibility means rebuilding from the corresponding
source and these recorded inputs—not assuming binaries from different SDKs are
byte-for-byte identical.
