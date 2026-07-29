#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This release script must run on macOS." >&2
    exit 1
fi

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

version=${DIFFASAURUS_VERSION:-0.1.0}
architecture=$(uname -m)
release_dir="$project_dir/release"
app_path="$project_dir/dist/Diffasaurus.app"
dmg_path="$release_dir/Diffasaurus-${version}-macOS-${architecture}.dmg"

mkdir -p "$release_dir"
"$project_dir/.venv/bin/python" -m PyInstaller --clean --noconfirm Diffasaurus.spec

if [ ! -d "$app_path" ]; then
    echo "Build failed: $app_path was not created." >&2
    exit 1
fi

codesign --verify --deep --strict --verbose=2 "$app_path"
if [ -z "${DIFFASAURUS_SIGN_IDENTITY:-}" ]; then
    echo "WARNING: no Developer ID identity supplied; this is an ad-hoc signed preview."
    echo "Gatekeeper will not accept it as an identified and notarized developer build."
fi

rm -f "$dmg_path"
hdiutil create \
    -volname "Diffasaurus" \
    -srcfolder "$app_path" \
    -ov \
    -format UDZO \
    "$dmg_path"

if [ -n "${DIFFASAURUS_SIGN_IDENTITY:-}" ]; then
    codesign \
        --force \
        --options runtime \
        --timestamp \
        --sign "$DIFFASAURUS_SIGN_IDENTITY" \
        "$dmg_path"
    codesign --verify --strict --verbose=2 "$dmg_path"
fi

if [ -n "${DIFFASAURUS_NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit \
        "$dmg_path" \
        --keychain-profile "$DIFFASAURUS_NOTARY_PROFILE" \
        --wait
    xcrun stapler staple "$dmg_path"
    xcrun stapler validate "$dmg_path"
fi

shasum -a 256 "$dmg_path"
echo "Created $dmg_path"
