# Corresponding-source releases

Every HolderPro binary release must attach a single version-matched source
archive. This is separate from GitHub's automatically generated repository
snapshot.

## Required contents

The archive contains:

1. HolderPro application source at the release commit.
2. The native headless adapter and layer/paint schemas.
3. All patches, CMake presets, setup helpers, build scripts, PyInstaller
   configuration, installer configuration, and release automation.
4. Complete PrusaSlicer source pinned to
   `b028299c770b8380ee81c921a2867d522f288123`, including its history-independent
   generated/build inputs required by the build.
5. A dependency source manifest containing exact resolved versions, source
   URLs, archive hashes, licenses, and relationships for native, runtime,
   interpreter, and packaging dependencies.
6. License texts, copyright notices, build-input metadata, and an archive
   manifest with a SHA-256 for every file.

The four target-specific build-environment manifests accompany this common
archive in the release. They bind each binary to compiler/SDK/runner details,
the exact release-constraints digest, and target packager inputs.

Build output, `.git`, caches, virtual environments, downloaded binaries, user
models, and diagnostic geometry are forbidden.

## Build the archive

After the exact release engine has been built, run:

```console
python packaging/scripts/build_corresponding_source.py \
  --repository . \
  --prusa-source /src/PrusaSlicer \
  --dependency-manifest build/dependency-sources.json \
  --version 0.1.0-alpha.1 \
  --output dist/holderpro-0.1.0-alpha.1-corresponding-source.tar.zst
```

The command verifies the Prusa commit, validates dependency records, rejects
forbidden file types, normalizes timestamps/ownership, and writes an internal
`SOURCE-MANIFEST.sha256`. It fails rather than emitting a partial archive.

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
