#!/usr/bin/env python3
from __future__ import annotations

import plistlib
import sys
from pathlib import Path


REQUIRED_RELATIVE_PATHS = (
    "Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle",
    "Contents/Frameworks/Sparkle.framework/Versions/B/Autoupdate",
    "Contents/Frameworks/Sparkle.framework/Versions/B/Updater.app",
    "Contents/Frameworks/Sparkle.framework/Autoupdate",
    "Contents/Frameworks/Sparkle.framework/Updater.app",
)

PRIVATE_KEY_MARKERS = (
    "SUPrivateEDKey",
    "ed25519",
    "PRIVATE KEY",
)

REQUIRED_SPARKLE_KEYS = (
    "SUFeedURL",
    "SUPublicEDKey",
)


def validate_sparkle_info_plist(info_plist: dict) -> list[str]:
    errors: list[str] = []
    for key in REQUIRED_SPARKLE_KEYS:
        value = str(info_plist.get(key) or "").strip()
        if not value:
            errors.append(f"Info.plist missing Sparkle key: {key}")
    if info_plist.get("SUEnableAutomaticChecks") is not False:
        errors.append("Info.plist SUEnableAutomaticChecks must be false")
    if info_plist.get("SUAutomaticallyUpdate") is not False:
        errors.append("Info.plist SUAutomaticallyUpdate must be false")
    if info_plist.get("SUVerifyUpdateBeforeExtraction") is not True:
        errors.append("Info.plist SUVerifyUpdateBeforeExtraction must be true")
    if "SURequireSignedFeed" in info_plist:
        errors.append("Info.plist must not set SURequireSignedFeed in this updater version")
    return errors


def validate_app_bundle(app_path: Path) -> list[str]:
    errors: list[str] = []
    if not app_path.is_dir():
        return [f"App bundle not found: {app_path}"]
    for relative in REQUIRED_RELATIVE_PATHS:
        target = app_path / relative
        if not target.exists():
            errors.append(f"Missing Sparkle bundle path: {relative}")
    sparkle_binary = app_path / "Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle"
    if sparkle_binary.exists() and not sparkle_binary.stat().st_mode & 0o111:
        errors.append("Sparkle.framework/Versions/B/Sparkle is not executable")
    autoupdate = app_path / "Contents/Frameworks/Sparkle.framework/Versions/B/Autoupdate"
    if autoupdate.exists() and not autoupdate.stat().st_mode & 0o111:
        errors.append("Sparkle Autoupdate helper is not executable")
    info_plist_path = app_path / "Contents" / "Info.plist"
    if info_plist_path.exists():
        try:
            with info_plist_path.open("rb") as handle:
                info_plist = plistlib.load(handle)
        except Exception as exc:
            errors.append(f"Info.plist could not be parsed: {exc}")
            info_plist = {}
        if isinstance(info_plist, dict):
            errors.extend(validate_sparkle_info_plist(info_plist))
        text = info_plist_path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.casefold()
        for marker in PRIVATE_KEY_MARKERS:
            if marker.casefold() in lowered:
                errors.append(f"Info.plist appears to embed private update key material: {marker}")
    else:
        errors.append("Info.plist not found")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: validate_sparkle_bundle.py /path/to/Diffasaurus.app", file=sys.stderr)
        return 2
    app_path = Path(argv[1]).resolve()
    errors = validate_app_bundle(app_path)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Sparkle bundle layout OK: {app_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
