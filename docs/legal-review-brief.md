# Legal review brief

This document packages the technical facts and preliminary brand screening a
qualified reviewer needs. It is not legal advice, a trademark clearance, or a
substitute for written independent review. Personal identity documents,
addresses, certificates, and the reviewer's opinion belong in private release
records, not this public repository.

## Binary-license review scope

HolderPro is an AGPL-3.0-or-later desktop application, Python package, and
headless native adapter. The native executable statically links the unmodified
PrusaSlicer 2.9.6 Organic-support implementation pinned to commit
`b028299c770b8380ee81c921a2867d522f288123`. PySide6/Qt is dynamically bundled
in PyInstaller one-directory applications; VTK and the Python core dependencies
are bundled runtime components.

Initial distribution is limited to direct GitHub Releases and platform-specific
PyPI wheels. Every binary release includes a version-matched archive containing
HolderPro, the complete pinned PrusaSlicer source, adapter, schemas, dependency
source manifest, patches, and all build and packaging scripts. Store channels
remain prohibited pending a separate terms and AGPL compatibility review.

Ask independent counsel to review at least:

- whether the repository license, notices, UI disclosure, and corresponding
  source delivery satisfy the obligations of the combined native work;
- whether the bundled Qt layout, relinking information, source links, and
  notices satisfy the selected Qt/PySide license path;
- the accuracy of the generated-output explanation and Prusa non-affiliation
  statement;
- the resolved dependency license manifest and SBOM for the exact release;
- signing, installer, website, and download notices; and
- any proposed store, SaaS, hosted-generation, or commercial-service terms.

The review evidence is:

- [`LICENSE`](../LICENSE) and [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md);
- [`licensing.md`](licensing.md) and
  [`corresponding-source.md`](corresponding-source.md);
- the upstream lock and notices under `upstream/prusaslicer-2.9.6-organic`;
- exact release constraints and dependency source locks under `packaging`;
- the generated corresponding-source archive, dependency-source manifest,
  third-party license directory, SBOM, checksums, and attestations; and
- `.github/workflows/release-build.yml` and `release-publish.yml`.

## Preliminary HolderPro name screening

The July 19, 2026 screening did **not** clear the name. Treat `HolderPro` as
elevated risk and do not finalize a logo, domain strategy, public signing
identity, stable `1.0` branding, or trademark filing until professional counsel
completes the relevant jurisdictions and classes.

An exact search found no live or pending `HOLDERPRO` record in the U.S. federal
register. However, [holderpro.com](https://holderpro.com/) is an established
IT/software/hosting business and states that HOLDERPRO is a registered trademark
of Company Network, LLC. A [Ukrainian business-data record](https://opendatabot.ua/c/37609345)
identifies an LLC HOLDERPRO founded in 2011 with software, programming, hosting,
and data-processing activities. The site claim could not be tied to a U.S.
record and may concern a different jurisdiction; counsel must resolve that
ambiguity.

Live neighboring U.S. records identified for professional comparison include:

- [`HOLDERPROF`](https://tsdr.uspto.gov/#caseNumber=90617787&caseSearchType=US_APPLICATION&caseType=DEFAULT&searchType=statusSearch),
  U.S. application 90617787 / registration 6670695, including
  downloadable operating programs and device stands in class 009;
- [`HOLDERPROF`](https://tsdr.uspto.gov/#caseNumber=98055238&caseSearchType=US_APPLICATION&caseType=DEFAULT&searchType=statusSearch),
  application 98055238 / registration 7433696;
- [`PROHOLDER`](https://tsdr.uspto.gov/#caseNumber=88912752&caseSearchType=US_APPLICATION&caseType=DEFAULT&searchType=statusSearch),
  application 88912752 / registration 6332050, including phone
  holders and stands in class 009; and
- pending [`PROHOLDER`](https://tsdr.uspto.gov/#caseNumber=99203328&caseSearchType=US_APPLICATION&caseType=DEFAULT&searchType=statusSearch),
  application 99203328, including a 3D body-part support platform in class 009.

Use the [USPTO search system](https://tmsearch.uspto.gov/search/s) and official
[knockout-search guidance](https://www.uspto.gov/trademarks/search/federal-trademark-searching)
to reproduce and expand the federal screen. WIPO, EUIPO/TMview, UK IPO, Ukraine,
state registers, company names, domains, stores, and common-law uses still need
manual professional review. Exact package-name availability on PyPI or npm is
not trademark clearance.

## Owner intake needed for counsel

The project owner must provide privately:

- exact applicant legal name, entity type, formation jurisdiction, and address;
- launch countries now and over the next three to five years;
- first public use and first U.S. interstate-commerce use, with evidence;
- all names and signs to clear, including `HOLDER PRO`, CLI/engine names, logo,
  and slogans;
- planned downloadable software, hosted services, custom printing, physical
  stands, training, marketplace sales, and merchandise;
- free/open-source and paid revenue models, buyers, and sales channels;
- current users, revenue, releases, domains, handles, and any third-party contact;
- rebrand tolerance, alternative names, deadline, and search/filing budget; and
- logo variants if a logo is ultimately pursued.

Counsel should assess at least downloadable software (often class 009), software
services (often class 042), and any custom-printing or physical-product classes
actually planned. Variants should include spacing, phonetic equivalents,
`HoldPro`, `HoldrPro`, `ProHolder`, `HolderProf`, and relevant transliterations.

## Recording approval

Keep the dated written opinion, reviewer identity and jurisdiction, exact release
scope, conditions, and disposition privately. Only after that record exists may
the release manager change `HOLDERPRO_LICENSE_REVIEW_APPROVED` or
`HOLDERPRO_TRADEMARK_REVIEW_APPROVED` to `true` in the protected GitHub
`release` environment. The CI variable is an enforcement signal, not the review.
