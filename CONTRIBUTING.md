# Contributing to HolderPro

Thank you for improving HolderPro. Contributions are accepted under the
project's AGPL-3.0-or-later license.

## Before opening a change

- Use an issue for behavior changes, new dependencies, schema changes, or
  native-engine modifications.
- Keep the public Python API small. Do not import private modules across the
  CLI/GUI boundary.
- Do not replace or approximate the Organic algorithm. The normal
  PrusaSlicer `Print::process()` and Organic source path are intentional.
- Never commit real user models, diagnostic geometry, or paths from diagnostic
  bundles. Tests must use small, redistribution-safe synthetic fixtures.
- Do not add telemetry, silent network access, automatic updating, or an
  app-store target without an approved design and license review.

## Local checks

Create a Python 3.11+ environment and install development tools:

```console
python -m pip install -e ".[gui,dev]"
ruff check .
mypy
pytest
python packaging/scripts/validate_schema.py
python packaging/scripts/verify_source_manifest.py
python packaging/scripts/check_licenses.py
```

Native changes also require the pinned upstream checkout and:

```console
./scripts/build-native.sh --preset linux-x86_64 \
  --source /path/to/PrusaSlicer \
  --deps-prefix /path/to/prusa/deps/prefix
ctest --preset linux-x86_64
```

Use the matching PowerShell helper on Windows. See [docs/building.md](docs/building.md).

## Pull requests

A pull request should:

- explain the user-visible problem and result;
- include focused tests and update schemas/docs when applicable;
- preserve painting-to-pose registration and support-mask strictness;
- reload every exported STL and prove watertight positive volume;
- list new runtime/build dependencies and their licenses;
- avoid unrelated formatting or generated artifacts; and
- update `CHANGELOG.md` for user-visible changes.

Native geometry changes require a before/after regression report across the
synthetic model corpus. Changes that remove upstream features from the headless
build must be published as patches and demonstrate geometry equivalence.

## Reporting conduct or security concerns

Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Security vulnerabilities belong
in a private GitHub Security Advisory, as described in [SECURITY.md](SECURITY.md),
not a public issue.
