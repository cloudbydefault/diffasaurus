from pathlib import Path

root = Path(SPECPATH)
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
    icon=str(root / "assets" / "diffasaurus-icon.png"),
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
