# Release process

HolderPro releases are build-once, test-exactly-those-files promotions. The
official project uses two no-charge distribution channels only:

- GitHub Releases for source, four platform wheels, checksums, SBOMs,
  attestations, and provenance; and
- PyPI for those exact four wheels.

There are no standalone DMGs, Windows installers, portable application
archives, AppImages, app-store listings, or paid platform-signing services.
The GUI is installed with `pip install "holderpro[gui]"` and launched with
`holderpro-gui`.

## One-time GitHub configuration

Enable GitHub Actions and artifact attestations, protect `main`, and create two
environments with required reviewers:

- `release`: allow deployments only from `main`;
- `pypi`: allow deployments only from `main`.

Prefer a second trusted reviewer. A sole reviewer who may approve their own
deployment is an intentional bootstrap configuration, not two-person control.
Accounts with repository, environment, or PyPI publication authority must use
multi-factor authentication.

No Apple, Azure, certificate-authority, app-store, or package-manager account is
part of the release path. Never place passwords, private keys, identity records,
or long-lived PyPI tokens in the repository.

## PyPI Trusted Publishing

While signed into PyPI, add a pending Trusted Publisher with these exact fields:

```text
PyPI project name: holderpro
GitHub owner: Finn2400
GitHub repository: HolderPro
Workflow filename: release-publish.yml
Environment name: pypi
```

No PyPI API token or GitHub secret is used. The OIDC-enabled `pypi` job contains
only artifact download and the pinned official publish action; all shell and
corresponding-source checks happen in a predecessor without an identity token.
A pending publisher does not reserve the package name, so configure it before
the first release.

## Legal release flags

Keep both `release` environment variables false until the respective written,
independent reviews are actually complete:

```text
HOLDERPRO_LICENSE_REVIEW_APPROVED=false
HOLDERPRO_TRADEMARK_REVIEW_APPROVED=false
```

Only after recording the review privately should a release manager change the
corresponding value to `true`. Never use the variables as substitutes for the
underlying reports. These variables block public promotion, not creation of a
private draft; maintainers can therefore build and test exact artifacts while
the independent review is still in progress.

No first public binary may be published until an independent license review is
recorded. Paid counsel is not required: a qualified pro-bono lawyer or
supervised university clinic may satisfy the gate. No stable `1.0`, final logo,
or domain decision may be published until a professional trademark knockout
search is recorded; qualified pro-bono review may satisfy that gate as well.
Start the reviewer handoff with [the legal review brief](legal-review-brief.md).

## Prepare

1. Update `CHANGELOG.md` and all versioned compatibility statements.
2. Confirm the Prusa tag, commit, source snapshot hashes, and third-party
   notices.
3. Run Python, schema, native, license, and source-manifest checks.
4. Run the real-engine synthetic regression suite on all four targets.
5. Create and push an annotated prerelease tag such as `v0.1.0-alpha.1`.

## Build the draft

From the protected `main` branch, manually run
`.github/workflows/release-build.yml` and supply that tag. The workflow first
uses its protected-main checkout to validate the tag spelling, annotated-tag
type, and ancestry; it checks out the validated commit only after those gates.
Tag pushes do not execute release code. The source job then:

- checks out the exact HolderPro tag and complete pinned PrusaSlicer source;
- resolves every dependency source to an exact version, URL, and SHA-256;
- downloads and verifies the complete native dependency source-archive set;
- builds and independently verifies the version-matched corresponding-source
  archive; and
- attests the source assets.

The platform matrix builds natively on macOS arm64, macOS Intel, Windows x64,
and Ubuntu 22.04 x64. Each target:

- builds one engine and verifies `--version-json` provenance;
- audits native runtime dependencies and the macOS deployment floor where
  applicable;
- runs native CTest and real-engine Python tests;
- builds one platform-specific `py3-none-<platform>` wheel with that engine;
- proves the wheel contains no copied Qt, VTK, core dependency, installer, or
  source-distribution payload;
- installs and tests the wheel in a clean Python environment;
- installs the wheel's `gui` extra and verifies Qt/VTK availability;
- creates a wheel SBOM, target build-environment record, and digest-bound asset
  manifest; and
- attests every target asset.

A fresh package-test job sees only each finished wheel artifact. It installs
the CLI and GUI dependencies, exercises generation with a synthetic model, and
checks GUI imports without a PrusaSlicer checkout, CMake, Git, or compiler.
The Windows test also verifies the declared external Visual C++ v14 x64 system
runtime before exercising the engine; that proprietary prerequisite is never
copied into a HolderPro wheel.

The aggregation job verifies the closed artifact inventory, checksums, wheel
tags, source completeness, dependency-source closure, provenance, and GitHub
attestations before creating a draft GitHub release. It never substitutes a
generic wheel or source distribution for a missing platform artifact.

## Promote without rebuilding

After testing the exact draft assets, manually run
`.github/workflows/release-publish.yml` with the tag. The workflow:

1. downloads and re-verifies the release's exact inventory, whether it is
   still a draft or an identical prior attempt already made it public;
2. proves that every wheel already present for this version on PyPI has the
   exact expected filename, size, and SHA-256, then safely resumes missing
   uploads only;
3. makes the source-bearing GitHub release public idempotently;
4. confirms that the version-matched corresponding source is publicly
   downloadable; and
5. publishes the same four wheels through PyPI Trusted Publishing and then
   requires PyPI to report the complete exact four-wheel set, allowing a short
   bounded retry window for normal package-index propagation.

This recovery path handles a stopped or partially completed PyPI upload without
accepting a changed pre-existing file; PyPI filenames are immutable, so any
hash or inventory disagreement fails closed for manual investigation. No
artifact is rebuilt during promotion. Release notes link
[Release authenticity](release-authenticity.md), corresponding source, SBOMs,
checksums, and the privacy policy.

## Alpha completion criteria

The first public alpha is complete only when all four platform wheels install
in clean supported-Python environments and each one:

- reports verified engine provenance;
- loads and paints a synthetic reference model through the shared code path;
- preserves pose-to-paint registration;
- generates only under painted facets;
- warns and continues for an oversized model;
- creates a connected single-trunk base;
- reloads a watertight positive-volume STL; and
- imports the optional GUI's Qt/VTK renderer successfully.

GitHub Releases and PyPI remain the only official channels unless the public
governance and AGPL review process explicitly changes this policy.
