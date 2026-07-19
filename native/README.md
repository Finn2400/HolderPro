# HolderPro native Organic-support engine

`holderpro-organic-engine` is a thin, headless adapter around the unmodified
PrusaSlicer 2.9.6 `libslic3r` Organic-support implementation. It loads one STL,
OBJ, or 3MF object, invokes the normal `Print::process()` path, and exports the
filled `SupportLayer::support_islands` polygons as versioned JSON. It does not
infer similar branches, reconstruct supports from G-code, or replace the
upstream Organic algorithm.

HolderPro pins PrusaSlicer commit
`b028299c770b8380ee81c921a2867d522f288123`. The headless configuration turns
off PrusaSlicer's GUI, tests, sandboxes, and optional STEP importer. The adapter
still calls the upstream STL, OBJ, and 3MF loaders and the normal Organic source
path. On macOS, the empty `OCCTWrapper` interface target represents the
intentionally absent STEP backend; it does not patch Organic-support geometry.

The adapter does **not** arrange, center, rotate, scale, or move a model onto
the bed. Input geometry and its intentional Z elevation are preserved. Its only
coordinate conversion is PrusaSlicer's fixed-point XY representation back to
millimetres; the print-instance XY shift is reapplied so exported polygons stay
in the input model's world coordinate system.

Prebuilt HolderPro wheels and desktop applications include this engine. End
users do not need PrusaSlicer, CMake, Git, a compiler, or the dependencies below.

## Reproducible native build

Builds require:

- CMake 3.24 or newer and Ninja;
- a platform C++17 toolchain (Apple Clang, GCC/Clang, or Visual Studio 2022);
- the complete pinned PrusaSlicer source tree; and
- PrusaSlicer's native dependency prefix built for the same OS and architecture.

Building PrusaSlicer's dependencies on Unix also needs the upstream Autotools
prerequisites (`m4`, `autoreconf`, `automake`, and `makeinfo`). Windows builds
must run in a Visual Studio 2022 x64 Developer PowerShell. Refer to PrusaSlicer's
build documentation for any additional platform SDK requirements.

The HolderPro helpers validate prerequisites and stop with an actionable error.
They never invoke Homebrew, apt, Chocolatey, winget, or another package manager,
and never modify a system installation. They do not download source unless the
explicit `--download-source` / `-DownloadSource` option is supplied.

The source is resolved in this order:

1. `--source PATH` / `-PrusaSlicerSource PATH`;
2. `PRUSASLICER_SOURCE_DIR`;
3. `prusaslicer-<commit>` in `HOLDERPRO_NATIVE_CACHE` (or the normal per-user
   cache directory).

A prebuilt dependency prefix can similarly be passed with `--deps-prefix`,
`-DepsPrefix`, or `HOLDERPRO_PRUSASLICER_DEPS_PREFIX`. This is the recommended
release and CI path because the substantial upstream dependency build can be
cached independently. Set `HOLDERPRO_PRUSASLICER_DEPS_BUILD_DIR` to place that
build cache explicitly; otherwise it lives under `HOLDERPRO_NATIVE_CACHE`.

```sh
./scripts/build-native.sh \
  --preset macos-arm64 \
  --source /source-cache/PrusaSlicer-2.9.6 \
  --deps-prefix /dependency-cache/macos-arm64/usr/local \
  --skip-deps \
  --version 0.1.0a1 \
  --build-id "$GITHUB_SHA"
```

For an isolated first developer build, explicitly populate and reuse the local
download cache. The helper fetches only the pinned commit, then builds upstream
dependencies in a separate per-platform cache, leaving the pinned source tree
clean:

```sh
HOLDERPRO_NATIVE_CACHE="$HOME/.cache/holderpro/native" \
  ./scripts/build-native.sh --download-source
```

On Windows:

```powershell
.\scripts\build-native.ps1 `
  -PrusaSlicerSource C:\source-cache\PrusaSlicer-2.9.6 `
  -DepsPrefix C:\dependency-cache\windows-x86_64\usr\local `
  -SkipDeps `
  -Version 0.1.0a1 `
  -BuildId $env:GITHUB_SHA
```

Both helpers configure, build, run CTest, and finally execute `--version-json`.
The engine is left at
`native/build/<preset>/holderpro-organic-engine[.exe]`.

### CMake presets

| Preset | Native build host | Deployment baseline |
|---|---|---|
| `macos-arm64` | macOS arm64 | macOS 13, arm64 |
| `macos-x86_64` | macOS Intel | macOS 13, x86_64 |
| `windows-x86_64` | Windows x64 | Windows 10/11, x64 |
| `linux-x86_64` | Linux x64 | CI uses Ubuntu 22.04-compatible glibc |

All presets use Release mode, static `libslic3r`, supported interprocedural
optimization, function/data sections, and platform dead-code elimination.
CTest covers help and provenance, input/output alias rejection, and secure
private-output staging. Release jobs should strip an installed staging
copy—not the debuggable build-tree executable—before runtime dependency
auditing and code signing:

```sh
cmake --install native/build/macos-arm64 \
  --prefix staging/macos-arm64 --strip
```

On Windows the install step also resolves and copies non-system runtime DLLs
from the pinned dependency prefix (including GMP) beside the executable. Wheel
and installer staging must copy the complete installed `bin/` directory, then
audit it; copying only the `.exe` does not produce a self-contained package.

To configure without a helper, set `PRUSASLICER_SOURCE_DIR` and
`HOLDERPRO_PRUSASLICER_DEPS_PREFIX`, then run the desired configure and build
presets from `native/`.

The configure step refuses a Git checkout at any revision other than the
pinned commit. Corresponding-source archives without Git metadata are checked
against SHA-256 values for the adapter's pinned upstream pipeline files.

When the helper builds native dependencies itself, it disables upstream's
`SELECT_ALL` behavior and enables a strict, reviewed package allowlist. The list
retains GLEW and NanoSVG because the unmodified PrusaSlicer configure/compile
graph still references them with `SLIC3R_GUI=OFF`; it omits OCCT, OpenCSG,
wxWidgets, and Catch2. Linux retains OpenSSL for CURL; macOS uses its system
CURL, and Windows CURL uses SChannel, so neither builds OpenSSL. Release CI
rebuilds and exercises the allowlist on every native target; changing it
requires the same four-platform gate. The upstream dependency sources and their
pinned hashes remain unchanged.

## Provenance contract

`--version-json` prints one JSON object and exits without loading PrusaSlicer
configuration or model geometry:

```json
{"product":{"name":"HolderPro","version":"0.1.0a1"},"adapter":{"name":"holderpro-organic-engine","version":"1"},"prusaslicer":{"version":"2.9.6","commit":"b028299c770b8380ee81c921a2867d522f288123"},"schemas":{"layers":"holderpro.organic-support-layers/v1","paint":"HOLDERPRO_SUPPORT_PAINT_V1"},"os":"macos","architecture":"arm64","build_id":"local"}
```

Release builds inject the HolderPro version and immutable build identifier with
`HOLDERPRO_VERSION` and `HOLDERPRO_BUILD_ID` CMake cache values. The adapter,
PrusaSlicer, and schema identities are compiled in and covered by CTest.

## Generate support layers

```sh
holderpro-organic-engine \
  --input transformed-model.stl \
  --output support-layers.json \
  --layer-height 0.2 \
  --branch-diameter 2.0 \
  --tip-diameter 0.8 \
  --branch-angle 40 \
  --branch-angle-slow 25 \
  --contact-distance 0.0
```

An exported PrusaSlicer INI can be supplied with `--config`. Any existing Prusa
option can be set with repeatable `--set KEY=VALUE`; the adapter always forces
`support_material=1`, `support_material_style=organic`, and `raft_layers=0`.
Automatic overhang detection is enabled unless `--enforcers-only` is passed.

Progress is written to stderr. JSON is first written inside a unique,
atomically owner-only temporary directory beside the destination; its file is
reserved with exclusive creation that refuses a pre-existing path before
writing and renamed only after it is complete. POSIX builds also use
`O_NOFOLLOW` when available and enforce `0700`/`0600` modes even under a
permissive umask, while Windows uses a protected owner-only DACL. Invalid
arguments return status 2; loading, configuration, slicing, and output failures
return status 1.

## Output contract

The v1 JSON schema is
[`schema/support-layers-v1.schema.json`](schema/support-layers-v1.schema.json).
The top-level shape is:

```json
{
  "schema": "holderpro.organic-support-layers/v1",
  "version": 1,
  "engine": {
    "name": "PrusaSlicer Organic",
    "version": "2.9.6",
    "commit": "b028299c770b8380ee81c921a2867d522f288123"
  },
  "units": "mm",
  "input": "transformed-model.stl",
  "layers": [
    {
      "print_z": 0.2,
      "height": 0.2,
      "bottom_z": 0.0,
      "polygons": [
        {"contour": [[10.0, 20.0], [11.0, 20.0], [10.0, 21.0]], "holes": []}
      ]
    }
  ],
  "summary": {
    "layer_count": 1,
    "nonempty_layer_count": 1,
    "polygon_count": 1,
    "point_count": 3
  }
}
```

Support polygons remain filled 2D regions, including their holes. HolderPro's
solidifier turns each `[bottom_z, print_z]` slab into a printable solid without
using PrusaSlicer's intentional single-sheath Organic toolpath policy.

Before writing JSON, the adapter recursively inspects every path in each
nonempty `SupportLayer::support_fills` collection. Every observed extrusion
height must be finite, positive, and equal to that layer's slab height within
`0.00001 mm`. A layer with no paths, an unknown extrusion entity, or mixed
heights is rejected instead of being flattened into a lossy solid.

## Scope, attribution, and license

Version 1 accepts one model object with one instance. A 3MF object may contain
multiple positive volumes, modifiers, support enforcers, and blockers, but
separate objects/copies must be merged before invoking the engine.

HolderPro uses the unmodified PrusaSlicer 2.9.6 Organic-support implementation
through this headless adapter. The adapter and linked PrusaSlicer code are
licensed under GNU AGPL-3.0-or-later. Preserve the pinned upstream copyrights,
license notices, exact source, build scripts, and dependency source manifest in
every corresponding-source release.

HolderPro is an independent project; it is not affiliated with or endorsed by
Prusa Research.
