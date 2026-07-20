# HolderPro governance

## Free software commitment

HolderPro is a community project committed to remaining permanently free
software. Every official release and its complete corresponding source are
available at no charge under
AGPL-3.0-or-later. The official project will not publish a proprietary edition,
use commercial dual licensing, require a license key, account, subscription, or
network activation, delay source access for paying users, or reserve features
for a paid tier.

This is project policy, not a noncommercial restriction. The AGPL protects the
freedom to use HolderPro for any purpose, including commercial use, and permits
third parties to charge for copies or services while preserving the license's
source and freedom requirements.

Official binaries must be buildable from complete publicly available
corresponding source and may contain only free/open-source components plus
ordinary operating-system system libraries. Platform SDKs and public build
services are external tools, not secret HolderPro source or runtime services.

## Maintainer and release roles

HolderPro is currently in single-maintainer bootstrap governance:

- Maintainer and committer: [@Finn2400](https://github.com/Finn2400)
- Pull-request reviewer: [@Finn2400](https://github.com/Finn2400)
- Release approver: [@Finn2400](https://github.com/Finn2400)

External contributions require maintainer review. Changes to licensing,
release provenance, native dependencies, or corresponding-source production
receive the same review and automated gates as application code. A second
maintainer should be added before stable `1.0`; at that point release approval
should require someone other than the author of the release change.

## Decision making and review

User-visible changes begin with a public issue or pull request. Maintainers aim
for consensus, but the current maintainer makes the final project decision when
consensus is not available. Decisions that change a public interface, schema,
dependency, license obligation, privacy behavior, or release channel must be
documented in the pull request and relevant project documentation.

The following are non-negotiable release invariants:

- the complete shipping HolderPro source remains AGPL-3.0-or-later;
- every redistributed dependency follows a free/open-source license path;
- each binary is bound to a public source commit and complete corresponding
  source;
- generated support geometry remains the user's output as described in
  `docs/licensing.md`;
- telemetry, automatic uploads, and silent network access remain absent; and
- GitHub Releases and PyPI are the only official distribution channels.

## Contributions and copyright

Contributors retain copyright in their work. By submitting a contribution,
they agree to license it under AGPL-3.0-or-later and represent that they have
the right to do so. HolderPro requires no copyright assignment and no
contributor agreement granting proprietary-relicensing rights. Existing AGPL
grants cannot be withdrawn from copies already distributed.

## Project asset stewardship

GitHub, PyPI, release approvals, and any future project accounts are held in
trust for the public project. An educational institution, nonprofit, fiscal
sponsor, or other steward may help administer them, but that role does not by
itself transfer contributor copyrights or permit closing HolderPro. Any
transfer of repository, package-index, domain, or trademark control must be
recorded publicly and preserve uninterrupted access to the AGPL source.

## Funding and conflicts

Donations, grants, sponsorships, academic support, and paid services may fund
development, but they do not purchase exclusive source, delayed publication,
proprietary licensing rights, or control over community releases. Maintainers
must disclose a material conflict when it could affect a project decision.

## Succession and policy changes

If a maintainer steps down, control should pass to an active contributor who
accepts this governance policy. If no successor is available, the public
repository and published releases remain available so the community can fork
and continue the project.

Changes to this document require a public pull request, an explanation of their
effect on user freedoms, and maintainer approval. No governance change can
retroactively remove rights already granted under the AGPL.
