import os
import sys
from pathlib import Path

root = Path(SPECPATH)
version = "0.1.0"
macos_icon = root / "assets" / "diffasaurus-icon.icns"
default_icon = root / "assets" / "diffasaurus-icon.png"
icon_path = macos_icon if sys.platform == "darwin" else default_icon
signing_identity = os.environ.get("DIFFASAURUS_SIGN_IDENTITY")
entitlements = root / "packaging" / "macos" / "entitlements.plist"
datas = [
    (str(root / "psscripts"), "psscripts"),
    (str(root / "assets"), "assets"),
]

analysis = Analysis(
    ["run.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
            "CFBundleVersion": version,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
        },
    )
