#!/bin/sh
set -eu

if [ "$#" -ne 3 ]; then
    echo "usage: package_dmg.sh APP_PATH OUTPUT_DMG VERSION" >&2
    exit 2
fi

APP=$1
OUTPUT=$2
VERSION=$3
: "${HOLDERPRO_APPLE_SIGNING_IDENTITY:?HOLDERPRO_APPLE_SIGNING_IDENTITY is required}"
: "${HOLDERPRO_NOTARY_PROFILE:?HOLDERPRO_NOTARY_PROFILE is required}"
: "${HOLDERPRO_VERSION:?HOLDERPRO_VERSION is required}"
: "${HOLDERPRO_TARGET:?HOLDERPRO_TARGET is required}"
: "${HOLDERPRO_BUILD_ID:?HOLDERPRO_BUILD_ID is required}"

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
if [ ! -d "$APP" ]; then
    echo "application bundle does not exist: $APP" >&2
    exit 1
fi

python "$ROOT/packaging/scripts/verify_macos_bundle.py" "$APP" \
    --version "$HOLDERPRO_VERSION"

codesign --force --deep --options runtime --timestamp \
    --entitlements "$ROOT/packaging/macos/entitlements.plist" \
    --sign "$HOLDERPRO_APPLE_SIGNING_IDENTITY" "$APP"
# Deep signing mutates nested Mach-O files. Refresh the digest manifest, then
# seal that final manifest with a non-deep outer application signature.
python "$ROOT/packaging/scripts/refresh_desktop_native_manifest.py" "$APP" \
    --expected-version "$HOLDERPRO_VERSION" \
    --expected-target "$HOLDERPRO_TARGET" \
    --expected-build-id "$HOLDERPRO_BUILD_ID"
codesign --force --options runtime --timestamp \
    --entitlements "$ROOT/packaging/macos/entitlements.plist" \
    --sign "$HOLDERPRO_APPLE_SIGNING_IDENTITY" "$APP"
python "$ROOT/packaging/scripts/verify_desktop_bundle.py" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/holderpro-dmg.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT INT TERM
cp -R "$APP" "$STAGE/HolderPro.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -fs HFS+ -volname "HolderPro $VERSION" \
    -srcfolder "$STAGE" -format UDZO "$OUTPUT"
codesign --force --timestamp --sign "$HOLDERPRO_APPLE_SIGNING_IDENTITY" "$OUTPUT"
xcrun notarytool submit "$OUTPUT" --keychain-profile "$HOLDERPRO_NOTARY_PROFILE" --wait
xcrun stapler staple "$OUTPUT"
xcrun stapler validate "$OUTPUT"
spctl --assess --type open --context context:primary-signature --verbose=2 "$OUTPUT"
