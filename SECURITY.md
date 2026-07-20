# Security policy

## Supported versions

Before `1.0`, only the newest prerelease receives security fixes. After a stable
release, this table will identify supported lines explicitly.

| Version | Supported |
|---|---:|
| Latest prerelease | Yes |
| Older prereleases | No |

## Report a vulnerability privately

Use **Security → Report a vulnerability** in the
[HolderPro GitHub repository](https://github.com/Finn2400/HolderPro/security/advisories/new).
Do not attach user models, API tokens, signing material, or unredacted
diagnostic bundles. Include the HolderPro version, platform, reproduction using
a synthetic model when possible, and the security impact.

The project will acknowledge a report within seven days, assess severity, and
coordinate disclosure and remediation. This is a response target, not a paid
bug-bounty commitment.

## Release security

Official binaries are published only on GitHub Releases and PyPI. Verify the
release checksum and GitHub build attestation. A release is blocked if its
SBOM, provenance attestation, corresponding-source archive, dependency-source
closure, or native dependency audit is missing. HolderPro does not claim Apple
notarization or Windows Authenticode signing; see
[Release authenticity](docs/release-authenticity.md).
