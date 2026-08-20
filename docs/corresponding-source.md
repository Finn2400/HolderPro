# Corresponding-source releases

Every HolderPro binary release must attach a single version-matched source
archive. This is separate from GitHub's automatically generated repository
snapshot.

## Required contents

The archive contains:

1. HolderPro application source at the release commit.
2. The native headless adapter and layer/paint schemas.
3. All patches, CMake presets, setup helpers, build scripts, wheel-packaging
   configuration, and release automation.
4. Complete PrusaSlicer source pinned to
   `b028299c770b8380ee81c921a2867d522f288123`, including its history-independent
   generated/build inputs required by the build.
5. A dependency source manifest containing exact resolved versions, source
   URLs, archive hashes, licenses, and relationships for native dependencies
   and separately installed Python dependencies.
6. Every exact hash-verified source archive in the conservative union of native
   dependencies incorporated into or used to link a released engine. This
   includes the target-specific GMP 5.0.1 and MPFR 3.0.0 sources corresponding
   to PrusaSlicer's pinned Windows headers/import libraries/DLLs, as well as the
   newer GMP/MPFR sources used by Unix builds. These archives are stored under
   `dependency-source-archives/<sha256>/` and are independently matched to the
   manifest.
7. License texts, copyright notices, build-input metadata, and an archive
   manifest with a SHA-256 for every file.

The same source inputs also produce a closed `MANIFEST.json`-bound native
legal-notice directory. Every platform wheel embeds that directory under its
`.dist-info/licenses/native/` tree.

The four target-specific build-environment manifests accompany this common
archive in the release. They bind each wheel to compiler/SDK/runner details and
the exact release-constraints digest.

Build output, `.git`, caches, virtual environments, unreviewed downloaded
binaries, user models, and diagnostic geometry are forbidden. Binary build
inputs already tracked in the exact PrusaSlicer commit remain in that upstream
tree only when the dependency manifest binds them to their corresponding
source, definition, artifact hashes, and version evidence.

## Build the archive

After the exact release engine has been built, run:

```console
python packaging/scripts/build_corresponding_source.py \
  --repository . \
  --prusa-source /src/PrusaSlicer \
  --dependency-manifest build/dependency-sources.json \
  --dependency-source-directory build/dependency-source-archives \
  --version 0.1.0-alpha.1 \
  --output dist/holderpro-0.1.0-alpha.1-corresponding-source.tar.zst
```

Create the dependency directory first with
`packaging/scripts/fetch_dependency_sources.py`, then build the wheel notice
directory with `packaging/scripts/build_native_license_bundle.py`. The builder verifies the Prusa
commit, validates dependency records, rejects missing, extra, or digest-mismatched
native source archives, rejects forbidden file types, normalizes
timestamps/ownership, and writes an internal `SOURCE-MANIFEST.sha256`. It fails
rather than emitting a partial archive.

## Verify independently

Extract into an empty directory and run:

```console
python holderpro/packaging/scripts/verify_corresponding_source.py ARCHIVE
```

The release job verifies the archive on a clean runner before it can create a
draft release. A release manager should also rebuild one target from the
archive, not from the development checkout.

## Availability

Keep corresponding source available for at least as long as the associated
binary is offered, and for any longer period required by the applicable
license. Never delete a source archive while its binary remains downloadable.
