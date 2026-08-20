# Release authenticity

HolderPro publishes through two official channels only:

- [GitHub Releases](https://github.com/Finn2400/HolderPro/releases), containing
  the four platform wheels, checksums, SBOMs, provenance records, dependency
  source records, and matching corresponding source; and
- [PyPI](https://pypi.org/project/holderpro/), containing the exact same four
  platform wheels after the GitHub release becomes public.

HolderPro does not require paid Apple or Windows signing services and does not
publish standalone installers. A platform wheel contains HolderPro's Python
source, the matching native Organic engine, and a digest-bound native legal-
notice bundle. Runtime Python dependencies are installed as separately
licensed packages by `pip`.

## Integrity and provenance

Every release provides:

- `SHA256SUMS` covering the complete closed asset set;
- GitHub artifact attestations binding each asset to the public release
  workflow and source revision;
- one native provenance manifest and one CycloneDX SBOM per target;
- a target build-environment record;
- a digest-bound asset manifest; and
- a version-matched corresponding-source archive.

PyPI publication uses Trusted Publishing with short-lived GitHub OIDC
credentials. No PyPI API token is stored in the repository or release
environment. Before a resumed publication, HolderPro compares every file
already present for that version on PyPI with the tested wheel's filename,
size, and SHA-256; after upload it requires the complete four-wheel set.
Checksums and attestations prove content integrity and build origin; they are
not Apple notarization or Windows Authenticode signatures.

Example verification after downloading a release asset:

```console
sha256sum --check SHA256SUMS
gh attestation verify holderpro-*.whl --repo Finn2400/HolderPro
```

Use `shasum -a 256 -c SHA256SUMS` on macOS when `sha256sum` is unavailable.

## Release authority

During the single-maintainer bootstrap period, [@Finn2400](https://github.com/Finn2400)
is the committer, reviewer, and release approver. Every release is built from
an annotated tag by a manually dispatched protected-main workflow, requires
manual environment approval, and promotes the already-tested draft artifacts
without rebuilding. Tag pushes alone cannot execute release jobs. Multi-factor
authentication is required for accounts with GitHub or PyPI publication
authority.

See [the privacy policy](privacy.md) for HolderPro's local-only behavior and
[the release process](releasing.md) for the complete gates.
