# Force embedded modules ONLY (pass EMBEDDED_PSMODULES from caller)
if ($env:EMBEDDED_PSMODULES) {
    $env:PSModulePath = $env:EMBEDDED_PSMODULES
}

Write-Host "PWSh:" $PSVersionTable.PSVersion
Write-Host "PSModulePath:" $env:PSModulePath

# Import by explicit path (most deterministic)
$moduleRoot = Join-Path $env:PSModulePath "Microsoft.Graph.Authentication"

$best = Get-ChildItem -Path $moduleRoot -Directory |
    Where-Object { $_.Name -as [version] } |
    Sort-Object { [version]$_.Name } -Descending |
    Select-Object -First 1

if (-not $best) { throw "No version folder found under: $moduleRoot" }

$psd1Path = Join-Path $best.FullName "Microsoft.Graph.Authentication.psd1"
if (-not (Test-Path $psd1Path)) { throw "Manifest not found: $psd1Path" }

Import-Module $psd1Path -Force -ErrorAction Stop

Write-Host "Imported Microsoft.Graph.Authentication from:"
(Get-Module Microsoft.Graph.Authentication).ModuleBase