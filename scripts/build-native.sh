#!/bin/sh
set -eu

COMMIT=b028299c770b8380ee81c921a2867d522f288123
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
CACHE_ROOT=${HOLDERPRO_NATIVE_CACHE:-"${XDG_CACHE_HOME:-$HOME/.cache}/holderpro/native"}
SOURCE=${PRUSASLICER_SOURCE_DIR:-}
DEPS_PREFIX=${HOLDERPRO_PRUSASLICER_DEPS_PREFIX:-}
DEPS_BUILD=${HOLDERPRO_PRUSASLICER_DEPS_BUILD_DIR:-}
PRESET=
DOWNLOAD_SOURCE=0
SKIP_DEPS=0
RUN_TESTS=1
HOLDERPRO_VERSION_VALUE=${HOLDERPRO_VERSION:-0.1.0a1}
BUILD_ID=${HOLDERPRO_BUILD_ID:-${GITHUB_SHA:-local}}

usage() {
    echo "Usage: scripts/build-native.sh [options]" >&2
    echo "  --preset NAME       macos-arm64, macos-x86_64, linux-x86_64, windows-x86_64" >&2
    echo "  --source PATH       complete pinned PrusaSlicer checkout" >&2
    echo "  --deps-prefix PATH  prebuilt PrusaSlicer dependency prefix" >&2
    echo "  --download-source   explicitly fetch the pinned source into the cache" >&2
    echo "  --skip-deps         reuse a cached or explicitly supplied dependency prefix" >&2
    echo "  --version VERSION   HolderPro version embedded in the engine" >&2
    echo "  --build-id ID       release/build identifier embedded in the engine" >&2
    echo "  --no-test           do not run the native smoke tests" >&2
}

fail() {
    echo "holderpro native build: $*" >&2
    exit 1
}

need() {
    command -v "$1" >/dev/null 2>&1 ||
        fail "missing prerequisite '$1'; install it explicitly, then retry"
}

configure_dependencies() {
    # Strict allowlist: packages not named here stay disabled. Some apparently
    # GUI-oriented headers (GLEW and NanoSVG) remain because the unmodified
    # PrusaSlicer configure/compile graph references them with SLIC3R_GUI=OFF.
    set -- \
        -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        "-DDEP_DOWNLOAD_DIR=$DOWNLOAD_CACHE" \
        -DPrusaSlicer_deps_SELECT_ALL=OFF \
        -DPrusaSlicer_deps_SELECT_Blosc=ON \
        -DPrusaSlicer_deps_SELECT_Boost=ON \
        -DPrusaSlicer_deps_SELECT_CGAL=ON \
        -DPrusaSlicer_deps_SELECT_CURL=ON \
        -DPrusaSlicer_deps_SELECT_Cereal=ON \
        -DPrusaSlicer_deps_SELECT_EXPAT=ON \
        -DPrusaSlicer_deps_SELECT_Eigen=ON \
        -DPrusaSlicer_deps_SELECT_GLEW=ON \
        -DPrusaSlicer_deps_SELECT_GMP=ON \
        -DPrusaSlicer_deps_SELECT_JPEG=ON \
        -DPrusaSlicer_deps_SELECT_LibBGCode=ON \
        -DPrusaSlicer_deps_SELECT_MPFR=ON \
        -DPrusaSlicer_deps_SELECT_NLopt=ON \
        -DPrusaSlicer_deps_SELECT_NanoSVG=ON \
        -DPrusaSlicer_deps_SELECT_OpenEXR=ON \
        -DPrusaSlicer_deps_SELECT_OpenVDB=ON \
        -DPrusaSlicer_deps_SELECT_PNG=ON \
        -DPrusaSlicer_deps_SELECT_Qhull=ON \
        -DPrusaSlicer_deps_SELECT_TBB=ON \
        -DPrusaSlicer_deps_SELECT_ZLIB=ON \
        -DPrusaSlicer_deps_SELECT_heatshrink=ON \
        -DPrusaSlicer_deps_SELECT_json=ON \
        -DPrusaSlicer_deps_SELECT_z3=ON

    case "$PRESET" in
        linux-x86_64) set -- "$@" -DPrusaSlicer_deps_SELECT_OpenSSL=ON ;;
        *) ;;
    esac
    if [ "$PRESET" = windows-x86_64 ]; then
        set -- "$@" -DDEP_DEBUG=OFF
    fi
    case "$PRESET" in
        macos-arm64)
            cmake -S "$SOURCE/deps" -B "$DEPS_BUILD" "$@" \
                -DCMAKE_OSX_ARCHITECTURES=arm64 \
                -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0
            ;;
        macos-x86_64)
            cmake -S "$SOURCE/deps" -B "$DEPS_BUILD" "$@" \
                -DCMAKE_OSX_ARCHITECTURES=x86_64 \
                -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0
            ;;
        *) cmake -S "$SOURCE/deps" -B "$DEPS_BUILD" "$@" ;;
    esac
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --preset) [ "$#" -ge 2 ] || fail "--preset requires a value"; PRESET=$2; shift 2 ;;
        --source) [ "$#" -ge 2 ] || fail "--source requires a value"; SOURCE=$2; shift 2 ;;
        --deps-prefix) [ "$#" -ge 2 ] || fail "--deps-prefix requires a value"; DEPS_PREFIX=$2; shift 2 ;;
        --download-source) DOWNLOAD_SOURCE=1; shift ;;
        --skip-deps) SKIP_DEPS=1; shift ;;
        --version) [ "$#" -ge 2 ] || fail "--version requires a value"; HOLDERPRO_VERSION_VALUE=$2; shift 2 ;;
        --build-id) [ "$#" -ge 2 ] || fail "--build-id requires a value"; BUILD_ID=$2; shift 2 ;;
        --no-test) RUN_TESTS=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; fail "unknown option '$1'" ;;
    esac
done

HOST_OS=$(uname -s)
HOST_ARCH=$(uname -m)
if [ -z "$PRESET" ]; then
    case "$HOST_OS:$HOST_ARCH" in
        Darwin:arm64) PRESET=macos-arm64 ;;
        Darwin:x86_64) PRESET=macos-x86_64 ;;
        Linux:x86_64) PRESET=linux-x86_64 ;;
        MINGW*:x86_64|MSYS*:x86_64) PRESET=windows-x86_64 ;;
        *) fail "no release preset for host $HOST_OS/$HOST_ARCH; pass --preset explicitly" ;;
    esac
fi
case "$PRESET" in
    macos-arm64|macos-x86_64|linux-x86_64|windows-x86_64) ;;
    *) fail "unknown release preset '$PRESET'" ;;
esac
case "$PRESET:$HOST_OS:$HOST_ARCH" in
    macos-arm64:Darwin:*|macos-x86_64:Darwin:*|linux-x86_64:Linux:x86_64|windows-x86_64:MINGW*:x86_64|windows-x86_64:MSYS*:x86_64) ;;
    *) fail "preset $PRESET cannot be built by host $HOST_OS/$HOST_ARCH" ;;
esac
if [ -z "$DEPS_BUILD" ]; then
    DEPS_BUILD="$CACHE_ROOT/deps/$PRESET-$COMMIT"
fi
DOWNLOAD_CACHE="$CACHE_ROOT/downloads"
CACHED_DEPS_PREFIX="$DEPS_BUILD/destdir/usr/local"

need cmake
need ninja

if [ -z "$SOURCE" ]; then
    SOURCE="$CACHE_ROOT/prusaslicer-$COMMIT"
fi

if [ ! -d "$SOURCE" ] && [ "$DOWNLOAD_SOURCE" -eq 1 ]; then
    need git
    mkdir -p "$CACHE_ROOT"
    mkdir -p "$(dirname "$SOURCE")"
    TEMP_SOURCE="$SOURCE.partial.$$"
    [ ! -e "$TEMP_SOURCE" ] || fail "temporary source path already exists: $TEMP_SOURCE"
    trap 'rm -rf "$TEMP_SOURCE"' EXIT HUP INT TERM
    echo "Fetching pinned PrusaSlicer source into $SOURCE" >&2
    git init -q "$TEMP_SOURCE"
    # Keep the fetched worktree byte-identical on every host. In particular,
    # Git for Windows otherwise converts reviewed LF source files to CRLF.
    git -C "$TEMP_SOURCE" config core.autocrlf false
    git -C "$TEMP_SOURCE" config core.eol lf
    git -C "$TEMP_SOURCE" remote add origin https://github.com/prusa3d/PrusaSlicer.git
    git -C "$TEMP_SOURCE" fetch --depth 1 origin "$COMMIT"
    git -C "$TEMP_SOURCE" checkout -q --detach FETCH_HEAD
    mv "$TEMP_SOURCE" "$SOURCE"
    trap - EXIT HUP INT TERM
fi

if [ ! -d "$SOURCE" ]; then
    fail "complete PrusaSlicer 2.9.6 source not found at '$SOURCE'. Pass --source, set PRUSASLICER_SOURCE_DIR, populate $CACHE_ROOT, or explicitly use --download-source"
fi
if [ -d "$SOURCE/.git" ]; then
    need git
    ACTUAL_COMMIT=$(git -C "$SOURCE" rev-parse HEAD 2>/dev/null || true)
    [ "$ACTUAL_COMMIT" = "$COMMIT" ] ||
        fail "PrusaSlicer source is $ACTUAL_COMMIT; expected $COMMIT"
    git -C "$SOURCE" diff --quiet --ignore-submodules -- ||
        fail "pinned PrusaSlicer source has modified tracked files: $SOURCE"
    git -C "$SOURCE" diff --cached --quiet --ignore-submodules -- ||
        fail "pinned PrusaSlicer source has staged tracked changes: $SOURCE"
fi

if [ "$HOST_OS" = Darwin ]; then
    # Some upstream Autotools/Perl steps reject macOS's C.UTF-8 alias.
    export LC_ALL=en_US.UTF-8
    export LANG=en_US.UTF-8
else
    export LC_ALL="${LC_ALL:-C.UTF-8}"
    export LANG="${LANG:-C.UTF-8}"
fi

if [ "$SKIP_DEPS" -eq 1 ] && [ -z "$DEPS_PREFIX" ]; then
    if [ -d "$CACHED_DEPS_PREFIX" ]; then
        DEPS_PREFIX=$CACHED_DEPS_PREFIX
    else
        fail "--skip-deps requires a cached dependency prefix, --deps-prefix, or HOLDERPRO_PRUSASLICER_DEPS_PREFIX"
    fi
fi

if [ -z "$DEPS_PREFIX" ]; then
    need git
    case "$HOST_OS" in
        Darwin|Linux)
            for command in make m4 autoreconf automake makeinfo; do need "$command"; done
            ;;
    esac
    mkdir -p "$DEPS_BUILD" "$DOWNLOAD_CACHE"
    # CMake 4 removed implicit compatibility with dependency projects whose
    # minimum predates 3.5. This user-side policy floor preserves their CMake
    # 3.5 behavior without modifying the pinned PrusaSlicer checkout.
    export CMAKE_POLICY_VERSION_MINIMUM=3.5
    cmake \
        "-DHOLDERPRO_DEP_DOWNLOAD_DIR=$DOWNLOAD_CACHE" \
        -P "$ROOT/scripts/prefetch-native-dependencies.cmake"
    echo "Building pinned PrusaSlicer dependencies in $DEPS_BUILD" >&2
    configure_dependencies
    # Upstream ExternalProject recipes parallelize each package internally and
    # explicitly recommend a serial top-level dependency driver.
    cmake --build "$DEPS_BUILD" --target deps --parallel 1
    DEPS_PREFIX=$CACHED_DEPS_PREFIX
fi
[ -d "$DEPS_PREFIX" ] || fail "PrusaSlicer dependency prefix does not exist: $DEPS_PREFIX"

export PRUSASLICER_SOURCE_DIR="$SOURCE"
export HOLDERPRO_PRUSASLICER_DEPS_PREFIX="$DEPS_PREFIX"
export HOLDERPRO_PRUSASLICER_DEPS_BIN="$DEPS_PREFIX/bin"
if [ "$PRESET" = windows-x86_64 ]; then
    PATH="$DEPS_PREFIX/bin:$PATH"
    export PATH
fi

echo "Configuring $PRESET (source and dependency caches are reused, never installed system-wide)" >&2
(
    cd "$ROOT/native"
    cmake --preset "$PRESET" \
        -DHOLDERPRO_VERSION="$HOLDERPRO_VERSION_VALUE" \
        -DHOLDERPRO_BUILD_ID="$BUILD_ID" \
        -DHOLDERPRO_RUNTIME_DEPENDENCY_DIR="$DEPS_PREFIX/bin"
    cmake --build --preset "$PRESET" --parallel
    if [ "$RUN_TESTS" -eq 1 ]; then
        ctest --preset "$PRESET"
    fi
)

ENGINE="$ROOT/native/build/$PRESET/holderpro-organic-engine"
if [ "$PRESET" = windows-x86_64 ]; then ENGINE="$ENGINE.exe"; fi
[ -x "$ENGINE" ] || [ -f "$ENGINE" ] || fail "expected engine was not produced: $ENGINE"
PROVENANCE=$("$ENGINE" --version-json)
case "$PRESET" in
    macos-arm64) EXPECTED='"os":"macos","architecture":"arm64"' ;;
    macos-x86_64) EXPECTED='"os":"macos","architecture":"x86_64"' ;;
    linux-x86_64) EXPECTED='"os":"linux","architecture":"x86_64"' ;;
    windows-x86_64) EXPECTED='"os":"windows","architecture":"x86_64"' ;;
esac
case "$PROVENANCE" in
    *"$EXPECTED"*) ;;
    *) fail "engine provenance does not match $PRESET: $PROVENANCE" ;;
esac
printf '%s\n' "$PROVENANCE"
echo "Built $ENGINE" >&2
