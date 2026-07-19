#!/bin/sh
set -eu

if [ "$#" -ne 3 ]; then
    echo "usage: package.sh APP_DIRECTORY OUTPUT_DIRECTORY VERSION" >&2
    exit 2
fi
: "${APPIMAGETOOL:?APPIMAGETOOL must point to a pinned appimagetool executable}"

APP=$1
OUTPUT=$2
VERSION=$3
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
mkdir -p "$OUTPUT"
OUTPUT=$(CDPATH= cd -- "$OUTPUT" && pwd)

if [ ! -x "$APP/HolderPro" ]; then
    echo "PyInstaller application is missing HolderPro: $APP" >&2
    exit 1
fi
if ! command -v zstd >/dev/null 2>&1; then
    echo "zstd is required for the portable Linux archive" >&2
    exit 1
fi

tar --sort=name --mtime="@${SOURCE_DATE_EPOCH:-0}" --owner=0 --group=0 --numeric-owner \
    -C "$(dirname -- "$APP")" -cf - "$(basename -- "$APP")" | \
    zstd -q -19 -T0 -o "$OUTPUT/HolderPro-$VERSION-linux-x86_64.tar.zst"

APPDIR=$(mktemp -d "${TMPDIR:-/tmp}/holderpro-appdir.XXXXXX")
trap 'rm -rf "$APPDIR"' EXIT INT TERM
mkdir -p "$APPDIR/usr/bin"
cp -R "$APP"/. "$APPDIR/usr/bin/"
cp "$ROOT/packaging/linux/AppRun" "$APPDIR/AppRun"
cp "$ROOT/packaging/linux/holderpro.desktop" "$APPDIR/holderpro.desktop"
cp "$ROOT/src/holderpro/assets/holderpro.svg" "$APPDIR/holderpro.svg"
chmod +x "$APPDIR/AppRun"
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$OUTPUT/HolderPro-$VERSION-linux-x86_64.AppImage"
