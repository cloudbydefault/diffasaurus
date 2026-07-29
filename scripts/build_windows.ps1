$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Version = (
    python -c "from diffasaurus import __release_label__; print(__release_label__)"
).Trim()
if ($env:DIFFASAURUS_VERSION) {
    $Version = $env:DIFFASAURUS_VERSION
}
$ReleaseDirectory = Join-Path $ProjectRoot "release"
$Archive = Join-Path $ReleaseDirectory "Diffasaurus-$Version-Windows-x64.zip"

Set-Location $ProjectRoot
python -m pip install -r requirements-build.txt
python -m PyInstaller --clean Diffasaurus.spec

New-Item -ItemType Directory -Force -Path $ReleaseDirectory | Out-Null
if (Test-Path $Archive) {
    Remove-Item -Force $Archive
}
Compress-Archive -Path (Join-Path $ProjectRoot "dist\Diffasaurus\*") -DestinationPath $Archive

Write-Host "Windows portable build created at $Archive"
