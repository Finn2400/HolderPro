# Release process

HolderPro releases are build-once, test-exactly-those-files promotions.

## One-time configuration

Choose the public legal publisher before buying signing services. Apple and
Windows show that verified individual or company identity to users, and a later
change creates a different publisher identity. Do not put passwords,
certificates, private keys, or identity-review documents in the repository.

Enable GitHub Actions and artifact attestations, protect `main`, and create two
environments with required reviewers:

- `release`: allow deployments only from `main` and tags matching `v*`;
- `pypi`: allow deployments only from `main`.

Prefer a second trusted reviewer. A sole reviewer who may approve their own
deployment is an intentional bootstrap configuration, not two-person control.

### Apple Developer ID and notarization

Enroll the selected individual or legal organization in the
[Apple Developer Program](https://developer.apple.com/programs/enroll/). The
Account Holder creates a **Developer ID Application** certificate and exports
the certificate plus its private key as a password-protected `.p12`. HolderPro
does not need a Developer ID Installer certificate because it ships a DMG, not
a PKG. Create a separate Apple app-specific password for notarization.

Configure these values on the `release` environment:

| Kind | Name | Value |
|---|---|---|
| Secret | `APPLE_CERTIFICATE_P12` | Base64 text of the exported `.p12` |
| Secret | `APPLE_CERTIFICATE_PASSWORD` | `.p12` export password |
| Variable | `HOLDERPRO_APPLE_SIGNING_IDENTITY` | `Developer ID Application: LEGAL NAME (TEAMID)` |
| Secret | `APPLE_NOTARY_ID` | Apple Account email |
| Secret | `APPLE_NOTARY_TEAM_ID` | 10-character Apple Team ID |
| Secret | `APPLE_NOTARY_PASSWORD` | App-specific password |

The workflow imports the certificate into a temporary keychain, signs nested
Mach-O payloads inside-out, applies hardened runtime and secure timestamps,
notarizes and staples the DMG, and asks Gatekeeper to assess both the DMG and
the mounted application. Enter secrets directly in GitHub. For example, this
keeps the binary certificate out of shell arguments:

```console
base64 < DeveloperIDApplication.p12 | gh secret set APPLE_CERTIFICATE_P12 --env release
gh secret set APPLE_CERTIFICATE_PASSWORD --env release
gh secret set APPLE_NOTARY_ID --env release
gh secret set APPLE_NOTARY_TEAM_ID --env release
gh secret set APPLE_NOTARY_PASSWORD --env release
gh variable set HOLDERPRO_APPLE_SIGNING_IDENTITY --env release
```

### Windows Artifact Signing

Create an Azure subscription and Microsoft Entra tenant for the same public
publisher. Register `Microsoft.CodeSigning`, create an Artifact Signing account,
complete public identity validation, and create a real `PublicTrust` certificate
profile. `PublicTrustTest` is not acceptable for a release. Give a dedicated
Entra application the **Artifact Signing Certificate Profile Signer** role at
the certificate-profile scope.

Create a federated credential for GitHub Actions with exactly these claims:

```text
Issuer:   https://token.actions.githubusercontent.com
Audience: api://AzureADTokenExchange
Subject:  repo:Finn2400@66322386/HolderPro@1306001188:environment:release
```

The numeric owner and repository IDs are required by GitHub's immutable OIDC
identity format for this repository. Do not substitute the legacy mutable
`repo:Finn2400/HolderPro:environment:release` subject.

Configure these values on the `release` environment:

| Kind | Name |
|---|---|
| Secret | `AZURE_CLIENT_ID` |
| Secret | `AZURE_TENANT_ID` |
| Secret | `AZURE_SUBSCRIPTION_ID` |
| Variable | `AZURE_ARTIFACT_SIGNING_ENDPOINT` |
| Variable | `AZURE_ARTIFACT_SIGNING_ACCOUNT` |
| Variable | `AZURE_ARTIFACT_SIGNING_PROFILE` |

### PyPI Trusted Publishing

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

### Legal release flags

Keep both `release` environment variables false until the respective written,
independent reviews are actually complete:

```text
HOLDERPRO_LICENSE_REVIEW_APPROVED=false
HOLDERPRO_TRADEMARK_REVIEW_APPROVED=false
```

Only after recording the review privately should a release manager change the
corresponding value to `true`. Never use the variables as substitutes for the
underlying reports.

## Human release gates

No first public binary may be published until an independent license review is
recorded. No stable `1.0`, final logo, signing identity, or domain decision may
be published until a professional trademark knockout search is recorded. These
are human approvals; CI checks an explicit environment approval but cannot
perform legal work. Start the reviewer handoff with the
[legal review brief](legal-review-brief.md).

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
