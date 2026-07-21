# Third-party notices

HolderPro is distributed under AGPL-3.0-or-later. It includes or depends on
third-party software whose copyright remains with its respective authors. The
resolved release SBOM and corresponding-source dependency manifest are the
authoritative version-specific inventory; this file documents the direct
integration boundary.

## Native engine

### PrusaSlicer 2.9.6

- Copyright: Prusa Research and PrusaSlicer contributors
- License: GNU AGPL v3 or later
- Repository: <https://github.com/prusa3d/PrusaSlicer>
- Tag: `version_2.9.6`
- Commit: `b028299c770b8380ee81c921a2867d522f288123`
- Included notice: `upstream/prusaslicer-2.9.6-organic/LICENSE`

HolderPro uses the unmodified PrusaSlicer 2.9.6 Organic-support implementation
through a headless adapter. Complete pinned PrusaSlicer source is included in
each binary release's corresponding-source archive.

HolderPro is an independent project; not affiliated with or endorsed by Prusa
Research.

PrusaSlicer's native dependency graph is recorded with exact versions, source
URLs, hashes, and license identifiers in each release's SBOM and generated
dependency-source manifest. The corresponding-source archive also contains the
exact hash-verified source archives used for native static dependencies, whose
license and copyright files are authoritative. A binary release must fail
closed if that source closure cannot be produced and independently verified.
Every wheel also embeds the extracted license/copyright notices and a closed
digest manifest under its `.dist-info/licenses/native/` directory.

PrusaSlicer's MSVC dependency recipe carries older Windows build inputs for
GMP 5.0.1 and MPFR 3.0.0 (headers, import libraries, and DLLs), while the Unix
recipe builds GMP 6.2.1 and MPFR 4.2.1 from source. HolderPro conservatively
records all four source releases. The two Windows-only records include exact
hashes and version markers for the pinned PrusaSlicer definition, headers,
import libraries, and DLLs, even when dead-code elimination means those DLLs
are not runtime companions of a particular engine build. Their authoritative
GNU/MPFR source archives are included in corresponding source and their LGPL
notices are included in each wheel's native notice bundle.

The pinned PrusaSlicer tree also contains these third-party components linked
directly into `libslic3r` or its `libslic3r_cgal` companion target:

| Component | Version or snapshot | License |
|---|---|---|
| Clipper | 6.4.2-derived | BSL-1.0 |
| ADMesh | PrusaSlicer-modified snapshot | GPL-2.0-or-later |
| miniz | 2.1.0-derived | MIT |
| libigl | PrusaSlicer snapshot | MPL-2.0 |
| semver | 0.2.0-derived | MIT |
| libnest2d | PrusaSlicer snapshot | LGPL-3.0-only |
| Mesa GLU libtess | Mesa commit `0bf42e41`-derived | SGI-B-2.0 |
| QOI | upstream commit `6c0831f9` | MIT |
| fast_float | PrusaSlicer snapshot | MIT |
| Clipper Int128 | 6.2.9-derived with Prusa modifications | AGPL-3.0-or-later AND BSL-1.0 |
| tcb::span | upstream commit `836dc6a0` | BSL-1.0 |
| Anti-Grain Geometry | 2.4 | BSD-3-Clause |
| ankerl::unordered_dense | 3.1.1 | MIT |

Their exact source directories and in-tree notice paths are recorded under
`vendored_components` in
`packaging/prusaslicer-native-dependency-sources.json`; those files travel in
the complete pinned PrusaSlicer source included with each release. Prusa-authored
helper targets in the same source tree remain covered by PrusaSlicer's AGPL
notice above.

## Python runtime

| Component | Purpose | License |
|---|---|---|
| NumPy | Mesh arrays and transforms | BSD-3-Clause |
| trimesh | Mesh loading and validation | MIT |
| manifold3d | Solidification and watertight repair | Apache-2.0 |
| Shapely | Connected-trunk polygon operations | BSD-3-Clause |

## Optional desktop runtime

| Component | Purpose | License |
|---|---|---|
| PySide6-Essentials / Qt | Desktop interface | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| VTK | Rendering, picking, locators, and face colors | BSD-3-Clause |

The optional GUI dependencies are installed as separate PyPI distributions;
HolderPro's wheels do not redistribute Qt or VTK. Qt therefore remains
dynamically replaceable in the user's Python environment. The applicable
license texts, notices, and source information travel with those distributions.

## Build and platform boundary

HolderPro publishes wheels rather than frozen desktop bundles. Setuptools,
wheel, and the PyPA build frontend are free/open-source build tools and are not
imported by the installed HolderPro library. Windows and macOS builds use their
platform SDKs and ordinary system libraries; those operating-system components
are not redistributed as HolderPro code.

No third-party trademark grants affiliation or endorsement. Third-party names
are used only for accurate identification and attribution.
