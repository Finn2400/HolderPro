# HolderPro

HolderPro is a free and open-source desktop and command-line tool for designing
printable, solid Organic support stands. Paint the surfaces that may receive
support, pose the reference model, inspect underside angle and concavity, and
export a connected, watertight support-only STL.

HolderPro uses the unmodified PrusaSlicer 2.9.6 Organic-support implementation
through a headless adapter. The adapter captures the filled support regions
before PrusaSlicer's later perimeter-only support-toolpath policy; HolderPro
then forms those layers into a validated positive-volume solid. It does not
reconstruct supports from G-code and does not use the retired Python
approximation.

> **Project status:** pre-1.0 alpha. Treat exported geometry as manufacturing
> input: inspect and slice-test it before relying on it. HolderPro comes with no
> warranty.

## Highlights

- Strict paint mask: Organic supports contact only green enforcer facets.
- Pose-safe painting: choosing a brush locks the printable pose and camera.
- Underside angle, relative height, concavity, and center-of-mass overlays.
- Single connected organic trunk with a configurable gradual taper.
- Oversized virtual build volume: warn and continue instead of clipping.
- Support preview in the same registered scene after generation.
- Fail-closed STL export with float32-aware repair, atomic replacement, and
  watertight positive-volume reload validation.
- Local operation only: telemetry and automatic updates are disabled.

## Install

HolderPro is distributed only through PyPI and
[GitHub Releases](https://github.com/Finn2400/HolderPro/releases). PyPI is the
normal installation path; GitHub carries the exact same wheels together with
checksums, SBOMs, provenance, and corresponding source. The first public alpha
is gated on native builds and clean-environment generation tests for macOS
arm64, macOS x86_64, Windows x64, and Linux x86_64.

Platform wheels include the matching native engine, so a compiler,
PrusaSlicer, CMake, and Git are not runtime requirements:

```console
python -m pip install holderpro
holderpro doctor
holderpro generate model.stl supports.stl
```

Install the optional desktop interface with:

```console
python -m pip install "holderpro[gui]"
holderpro-gui
```

HolderPro supports Python 3.11–3.14. PyPI releases contain wheels only;
there is no source distribution until the complete native build is
deterministically reproducible from it. There is no npm package because
HolderPro has no JavaScript API.

On Windows, the native engine uses the current Microsoft Visual C++ v14 x64
runtime as an external system prerequisite. HolderPro does not copy that
proprietary runtime into its open-source wheel. Most current Windows systems
already have it; if `holderpro doctor` reports a loader error, install the
[latest supported x64 Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
from Microsoft and retry.

HolderPro does not publish standalone DMGs, Windows installers, AppImages, or
other platform bundles. This avoids paid signing services and duplicate
packaging paths; `holderpro-gui` remains the complete graphical application.

## Desktop workflow

1. Open or drag in an STL/3MF reference model.
2. Use **Pose object** to rotate, translate, and raise or lower the object.
3. Inspect the colored underside and center-of-mass marker.
4. Paint allowed contact areas green. Paint blockers red when needed.
5. Generate, inspect the cyan support preview, and export the support-only STL.
6. Reload the STL in a slicer and choose the desired print infill. A solid STL
   describes a closed volume; it does not itself require 100% printer infill.

The CLI and GUI construct the same public `GenerationJob` and run the same
validation pipeline. See [the user guide](docs/user-guide.md) and
[`holderpro generate --help`](docs/cli.md) for controls and examples.

## Public Python API

HolderPro intentionally exposes a compact API:

```python
from holderpro import GenerationJob, GenerationResult, EngineInfo, generate

result: GenerationResult = generate(
    GenerationJob(input_path="model.stl", output_path="supports.stl")
)
```

Generation, cancellation, validation, and engine failures use documented
exception types. Internal geometry modules are not a compatibility surface.

## Development

Runtime dependencies are deliberately split:

- Core: NumPy, trimesh, manifold3d, and Shapely.
- GUI: PySide6-Essentials and VTK.
- Native: a statically linked, headless HolderPro adapter built against the
  pinned PrusaSlicer source.

Start with [CONTRIBUTING.md](CONTRIBUTING.md), then read the
[build guide](docs/building.md), [architecture](docs/architecture.md), and
[release process](docs/releasing.md). Native builds never install package
managers or mutate a supplied source checkout.

## Free and open source

HolderPro's official source and downloads are available at no charge under
AGPL-3.0-or-later. The project has no proprietary edition, paid feature tier,
license key, account requirement, network activation, telemetry, or delayed
source release. Contributions retain their authors' copyright and require no
copyright assignment or proprietary-relicensing agreement.

The full commitment and succession rules are in
[GOVERNANCE.md](GOVERNANCE.md). Release verification is documented in
[Release authenticity](docs/release-authenticity.md), and removal instructions
are in [Uninstalling HolderPro](docs/uninstalling.md).

## Licensing and PrusaSlicer

HolderPro is licensed under the
[GNU Affero General Public License, version 3 or later](LICENSE).

HolderPro uses the unmodified PrusaSlicer 2.9.6 Organic-support implementation
through a headless adapter. It is pinned to commit
`b028299c770b8380ee81c921a2867d522f288123`. PrusaSlicer is also licensed
under AGPL-3.0-or-later; its copyright and license notices are preserved.

HolderPro is an independent project; not affiliated with or endorsed by Prusa
Research. Prusa and PrusaSlicer names are used only to identify compatibility
and the upstream implementation. HolderPro does not use Prusa logos.

An STL generated by HolderPro is not automatically licensed under the AGPL
merely because HolderPro produced it. Under AGPL section 2, program output is
covered only when the output's content itself constitutes a covered work. You
remain responsible for rights in the input model and resulting geometry.

Every binary release must include a version-matched corresponding-source
archive, SBOM, checksums, provenance, and legal notices. Details are in
[Licensing](docs/licensing.md), [Third-party notices](THIRD_PARTY_NOTICES.md),
and [Corresponding source](docs/corresponding-source.md).

Before stable `1.0`, the project also requires independent license review and a
professional trademark knockout search. Until those gates are complete, no
logo, signing identity, domain strategy, or stable branding is final.
