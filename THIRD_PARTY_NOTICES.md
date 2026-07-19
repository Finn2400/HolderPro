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
URLs, hashes, and license files in each release's SBOM and generated dependency
source manifest. A binary release must fail closed if that inventory cannot be
produced.

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

Desktop packages keep Qt dynamically replaceable and include the applicable Qt
license text, notices, and source offer/link required for the resolved version.
The release license audit verifies the packaged notices before publication.

## Packaging tools

PyInstaller is used to create desktop packages under its GPL license with the
PyInstaller bootloader exception. Packaging tools are not imported by the
installed HolderPro library.

No third-party trademark grants affiliation or endorsement. Third-party names
are used only for accurate identification and attribution.
