#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This release script must run on macOS." >&2
    exit 1
fi

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

python_bin="$project_dir/.venv/bin/python"
if [ ! -x "$python_bin" ]; then
    python_bin=$(command -v python3)
fi

version=$(
    "$python_bin" -c \
        'from diffasaurus import __release_label__; print(__release_label__)'
)
version=${DIFFASAURUS_VERSION:-$version}
architecture=$(uname -m)
release_dir="$project_dir/release"
app_path="$project_dir/dist/Diffasaurus.app"
dmg_path="$release_dir/Diffasaurus-${version}-macOS-${architecture}.dmg"
sparkle_framework="$project_dir/packaging/macos/vendor/Sparkle.framework"
preview_feed_url=${DIFFASAURUS_SPARKLE_FEED_URL:-https://cloudbydefault.github.io/diffasaurus/appcast-preview.xml}
release_build=${DIFFASAURUS_RELEASE_BUILD:-1}

echo "Fetching pinned Sparkle framework…"
sh "$project_dir/scripts/fetch_sparkle_framework.sh"

if [ "$release_build" = "1" ]; then
    if [ -z "${DIFFASAURUS_SPARKLE_PUBLIC_ED_KEY:-}" ]; then
        echo "Release build requires DIFFASAURUS_SPARKLE_PUBLIC_ED_KEY." >&2
        exit 1
    fi
fi

export DIFFASAURUS_SPARKLE_FEED_URL="$preview_feed_url"
export DIFFASAURUS_SPARKLE_PUBLIC_ED_KEY="${DIFFASAURUS_SPARKLE_PUBLIC_ED_KEY:-}"
export DIFFASAURUS_RELEASE_BUILD="$release_build"

mkdir -p "$release_dir"
"$python_bin" -m PyInstaller --clean --noconfirm Diffasaurus.spec

if [ ! -d "$app_path" ]; then
    echo "Build failed: $app_path was not created." >&2
    exit 1
fi

frameworks_dir="$app_path/Contents/Frameworks"
mkdir -p "$frameworks_dir"
rm -rf "$frameworks_dir/Sparkle.framework"
ditto "$sparkle_framework" "$frameworks_dir/Sparkle.framework"

"$python_bin" "$project_dir/tools/validate_sparkle_bundle.py" "$app_path"

sign_identity=${DIFFASAURUS_SIGN_IDENTITY:-}
if [ -n "$sign_identity" ]; then
    find "$frameworks_dir/Sparkle.framework" -type f -perm +111 -print0 \
        | while IFS= read -r -d '' helper; do
            codesign --force --sign "$sign_identity" "$helper"
        done
    codesign --force --deep --sign "$sign_identity" "$frameworks_dir/Sparkle.framework"
    codesign --force --deep --sign "$sign_identity" "$app_path"
else
    echo "Ad-hoc signing Sparkle helpers and app bundle…"
    find "$frameworks_dir/Sparkle.framework" -type f -perm +111 -print0 \
        | while IFS= read -r -d '' helper; do
            codesign --force -s - "$helper"
        done
    codesign --force --deep -s - "$frameworks_dir/Sparkle.framework"
    codesign --force --deep -s - "$app_path"
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
