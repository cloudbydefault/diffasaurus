#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

python_bin="$project_dir/.venv/bin/python"
if [ ! -x "$python_bin" ]; then
    python_bin=$(command -v python3)
fi

pin_file="$project_dir/packaging/macos/sparkle.pin.json"
vendor_dir="$project_dir/packaging/macos/vendor"
cache_dir="$vendor_dir/cache"
framework_dir="$vendor_dir/Sparkle.framework"

if [ ! -f "$pin_file" ]; then
    echo "Missing Sparkle pin file: $pin_file" >&2
    exit 1
fi

version=$("$python_bin" -c 'import json, pathlib; pin=json.loads(pathlib.Path("packaging/macos/sparkle.pin.json").read_text()); print(pin["version"])')
url=$("$python_bin" -c 'import json, pathlib; pin=json.loads(pathlib.Path("packaging/macos/sparkle.pin.json").read_text()); print(pin["distribution_url"])')
expected_sha=$("$python_bin" -c 'import json, pathlib; pin=json.loads(pathlib.Path("packaging/macos/sparkle.pin.json").read_text()); print(pin["sha256"])')

mkdir -p "$cache_dir"
archive_path="$cache_dir/Sparkle-${version}.tar.xz"

if [ ! -f "$archive_path" ]; then
    echo "Downloading Sparkle ${version}…"
    curl -fsSL "$url" -o "$archive_path"
fi

actual_sha=$(shasum -a 256 "$archive_path" | awk '{print $1}')
if [ "$actual_sha" != "$expected_sha" ]; then
    echo "Sparkle archive checksum mismatch." >&2
    echo "Expected: $expected_sha" >&2
    echo "Actual:   $actual_sha" >&2
    exit 1
fi

extract_dir="$cache_dir/Sparkle-${version}"
rm -rf "$extract_dir"
mkdir -p "$extract_dir"
tar -xJf "$archive_path" -C "$extract_dir" --strip-components=1

if [ ! -d "$extract_dir/Sparkle.framework" ]; then
    echo "Sparkle.framework not found in extracted archive." >&2
    exit 1
fi

rm -rf "$framework_dir"
cp -R "$extract_dir/Sparkle.framework" "$framework_dir"
chmod -R u+rwX,go+rX "$framework_dir"

required_paths="
Sparkle.framework/Versions/B/Sparkle
Sparkle.framework/Versions/B/Autoupdate
Sparkle.framework/Versions/B/Updater.app
Sparkle.framework/Autoupdate
Sparkle.framework/Updater.app
"
for relative_path in $required_paths; do
    if [ ! -e "$vendor_dir/$relative_path" ]; then
        echo "Missing required Sparkle path: $relative_path" >&2
        exit 1
    fi
done

echo "Sparkle ${version} ready at $framework_dir"
