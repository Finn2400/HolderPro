# Release process

HolderPro releases are build-once, test-exactly-those-files promotions.

## One-time configuration

- Enable GitHub Actions and artifact attestations.
- Configure the `release` environment for draft creation.
- Configure the `pypi` environment and PyPI Trusted Publisher.
- Add Apple Developer ID signing/notarization secrets documented in the release
  workflow.
- Configure Azure Artifact Signing OIDC and repository variables for the
  signing account and certificate profile.
- Protect `main` and both environments with required reviewers.

## Human release gates

No first public binary may be published until an independent license review is
recorded. No stable `1.0`, final logo, signing identity, or domain decision may
be published until a professional trademark knockout search is recorded. These
are human approvals; CI checks an explicit environment approval but cannot
perform legal work.

Restricted app stores remain out of scope pending separate AGPL analysis.

## Prepare

1. Update `CHANGELOG.md` and all versioned compatibility statements.
2. Confirm the Prusa tag, commit, source snapshot hashes, and third-party
   notices.
3. Run Python, schema, native, license, and source-manifest checks.
4. Run the real-engine synthetic regression suite on all four targets.
5. Create an annotated prerelease tag such as `v0.1.0-alpha.1`.

## Build draft

Pushing the tag starts `.github/workflows/release-build.yml`. It builds natively
on macOS arm64, macOS Intel, Windows x64, and Ubuntu 22.04 x64. Each target:

- builds one engine and verifies `--version-json` provenance;
- runs native CTest and real-engine Python tests;
- builds a platform-specific `py3-none-<platform>` wheel with that engine;
- installs and tests the wheel without a development checkout;
- builds the PyInstaller one-directory app from the same engine;
- signs/notarizes platform deliverables;
- audits native runtime dependencies; and
- produces a final signed-desktop-payload SBOM, target build-environment
  manifest, and provenance subjects.

The platform jobs verify signatures and notarization before uploading their
artifacts. The aggregation job then verifies checksums, wheel tags, absence of
sdists, source completeness, provenance, and the exact artifact set before
creating a **draft** GitHub release. Missing signing inputs fail clearly; an
unsigned release is never silently substituted.

A separate fresh runner downloads only each finished desktop artifact—without
a repository checkout or Python/toolchain setup—then installs or extracts it,
scrubs `PATH`, uses a long temporary path and a read-only payload, and runs the
frozen generation/render self-test.

## Promote

After clean-machine testing the exact draft assets, manually run
`.github/workflows/release-publish.yml` with the tag. The workflow downloads the
draft assets, verifies the exact inventory, checksum file, attestations, and
legal gates, and rejects sdists. It first makes the source-bearing GitHub release
public, confirms that the version-matched source is downloadable, and only then
publishes the same wheels through PyPI Trusted Publishing. It does not rebuild
anything.

Automation generates a painted synthetic model through the bundled engine,
reloads the connected positive-volume STL, feeds it into the frozen VTK support
actor, and renders offscreen. Before promotion, a release manager must also
open each exact draft desktop package on real display hardware and confirm the
interactive model, paint overlay, and generated support preview are visibly
correct; offscreen automation cannot replace that visual acceptance check.

## Alpha completion criteria

The first alpha is complete only when every desktop package on all four native
targets works in a clean environment without Python, PrusaSlicer, Git, CMake,
or a compiler, and each wheel works in a clean supported-Python environment
without those external build/native tools. Each package must:

- load and paint a synthetic reference model;
- preserve pose-to-paint registration;
- generate only under painted facets;
- warn and continue for an oversized model;
- create a connected single-trunk base;
- reload a watertight positive-volume STL; and
- display the generated support in the viewer.

Homebrew Cask, WinGet, and Flatpak are considered only after the signed alpha
artifacts complete a full test cycle.
