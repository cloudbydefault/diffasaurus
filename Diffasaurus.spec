import os
import sys
from pathlib import Path

from diffasaurus import __release_label__, __version__

root = Path(SPECPATH)
version = __version__.split("rc", 1)[0]
build_version = (
    f"{version}.{__version__.split('rc', 1)[1]}"
    if "rc" in __version__
    else version
)
macos_icon = root / "assets" / "diffasaurus-icon.icns"
windows_icon = root / "assets" / "diffasaurus-icon.ico"
default_icon = root / "assets" / "diffasaurus-icon.png"
icon_path = (
    macos_icon
    if sys.platform == "darwin"
    else windows_icon
    if sys.platform == "win32"
    else default_icon
)
signing_identity = os.environ.get("DIFFASAURUS_SIGN_IDENTITY")
entitlements = root / "packaging" / "macos" / "entitlements.plist"
preview_feed_url = os.environ.get(
    "DIFFASAURUS_SPARKLE_FEED_URL",
    "https://cloudbydefault.github.io/diffasaurus/appcast-preview.xml",
)
sparkle_public_ed_key = os.environ.get("DIFFASAURUS_SPARKLE_PUBLIC_ED_KEY", "").strip()
release_build = os.environ.get("DIFFASAURUS_RELEASE_BUILD", "").strip() == "1"
if release_build and not sparkle_public_ed_key:
    raise SystemExit(
        "Release build requires DIFFASAURUS_SPARKLE_PUBLIC_ED_KEY for Sparkle EdDSA verification."
    )
hiddenimports = []
if sys.platform == "darwin":
    hiddenimports.extend(
        [
            "objc",
            "Foundation",
        ]
    )
datas = [
    (str(root / "psscripts"), "psscripts"),
    (str(root / "assets"), "assets"),
]

analysis = Analysis(
    ["run.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Diffasaurus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(icon_path),
    codesign_identity=signing_identity,
    entitlements_file=str(entitlements) if signing_identity else None,
    contents_directory=".",
)
bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="Diffasaurus",
)

if sys.platform == "darwin":
    macos_app = BUNDLE(
        bundle,
        name="Diffasaurus.app",
        icon=str(macos_icon),
        bundle_identifier="com.cloudbydefault.diffasaurus",
        info_plist={
            "CFBundleDisplayName": "Diffasaurus",
            "CFBundleName": "Diffasaurus",
            "CFBundleShortVersionString": version,
            "CFBundleVersion": build_version,
            "DiffasaurusReleaseLabel": __release_label__,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
            "SUFeedURL": preview_feed_url,
            "SUPublicEDKey": sparkle_public_ed_key,
            "SUEnableAutomaticChecks": False,
            "SUAutomaticallyUpdate": False,
            "SUVerifyUpdateBeforeExtraction": True,
        },
    )
