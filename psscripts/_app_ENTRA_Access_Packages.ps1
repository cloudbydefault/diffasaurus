param(
    [string]$OutputPath = "",
    [switch]$SkipDisabledPolicies
)

$ErrorActionPreference = "Stop"

if (Get-Variable -Name PSStyle -Scope Global -ErrorAction SilentlyContinue) {
    $PSStyle.OutputRendering = 'PlainText'
}

function Write-Info {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Cyan
}

function Safe-Get {
    param(
        $Object,
        [string]$PropertyName
    )

    try {
        $value = $Object.$PropertyName
        if ($null -eq $value) { return "" }
        return [string]$value
    }
    catch {
        return ""
    }
}

function Resolve-PolicyEnabled {
    param($Policy)

    if ($null -ne $Policy.isEnabled) {
        return [bool]$Policy.isEnabled
    }

    if ($null -ne $Policy.accessPackageAssignmentPolicyStatus) {
        return ($Policy.accessPackageAssignmentPolicyStatus -eq "enabled")
    }

    if ($Policy.state -eq "enabled" -or $Policy.status -eq "enabled" -or $Policy.status -eq "Enabled") {
        return $true
    }

    # Fallback: assume enabled if Graph does not expose a clear status
    return $true
}

# ---------------------------------------------------------
# Resolve default output path
# ---------------------------------------------------------
if (-not $OutputPath) {
    $reportsDir = $env:REPORTS_DIR
    if (-not $reportsDir) {
        $projectRoot = Split-Path -Parent $PSScriptRoot
        $reportsDir = Join-Path $projectRoot "reports"
    }

    if (-not (Test-Path $reportsDir)) {
        New-Item -Path $reportsDir -ItemType Directory -Force | Out-Null
    }

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $reportsDir "Entra_Access_Packages_$timestamp.csv"
}

$dir = Split-Path $OutputPath -Parent
if (-not (Test-Path $dir)) {
    New-Item -Path $dir -ItemType Directory -Force | Out-Null
}

# ---------------------------------------------------------
# Connect Graph
# ---------------------------------------------------------
Write-Info "Connecting to Microsoft Graph..."

try {
    $ctx = Get-MgContext -ErrorAction SilentlyContinue
    if (-not $ctx) {
        Connect-MgGraph -Scopes @(
            "EntitlementManagement.Read.All",
            "EntitlementManagement.ReadWrite.All",
            "Directory.Read.All",
            "User.Read.All"
        ) -NoWelcome | Out-Null
    }
}
catch {
    Connect-MgGraph -Scopes @(
        "EntitlementManagement.Read.All",
        "EntitlementManagement.ReadWrite.All",
        "Directory.Read.All",
        "User.Read.All"
    ) -NoWelcome | Out-Null
}

# ---------------------------------------------------------
# Retrieve Access Packages
# ---------------------------------------------------------
Write-Info "Retrieving Access Packages..."

$packagesResponse = Invoke-MgGraphRequest `
    -Uri "https://graph.microsoft.com/beta/identityGovernance/entitlementManagement/accessPackages" `
    -Method GET `
    -OutputType PSObject

$packages = @($packagesResponse.value)

if (-not $packages -or $packages.Count -eq 0) {
    Write-Host "No access packages found in tenant." -ForegroundColor Yellow

    @(
        [PSCustomObject]@{
            AccessPackageName        = ""
            AccessPackageId          = ""
            AccessPackageDescription = ""
            CatalogId                = ""
            CreatedDateTime          = ""
            ModifiedDateTime         = ""
            PolicyName               = ""
            PolicyId                 = ""
            PolicyDescription        = ""
            PolicyStatus             = ""
        }
    ) | Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8

    Write-Host "Empty placeholder CSV created at: $OutputPath" -ForegroundColor Green
    exit 0
}

$result = @()

foreach ($pkg in $packages) {
    $pkgName = Safe-Get $pkg "displayName"
    $pkgId   = Safe-Get $pkg "id"

    Write-Info "→ Access Package: $pkgName"

    $pkgDescription = Safe-Get $pkg "description"
    $catalogId      = Safe-Get $pkg "catalogId"
    $created        = Safe-Get $pkg "createdDateTime"
    $modified       = Safe-Get $pkg "modifiedDateTime"

    $uri = "https://graph.microsoft.com/beta/identityGovernance/entitlementManagement/accessPackageAssignmentPolicies?`$filter=accessPackageId eq '$pkgId'&`$expand=customExtensionHandlers"

    try {
        $response = Invoke-MgGraphRequest -Uri $uri -Method GET -OutputType PSObject -ErrorAction Stop
        $policies = @($response.value)
    }
    catch {
        Write-Host "Failed to retrieve policies for $pkgName : $($_.Exception.Message)" -ForegroundColor Yellow
        $policies = @()
    }

    if (-not $policies -or $policies.Count -eq 0) {
        $result += [PSCustomObject]@{
            AccessPackageName        = $pkgName
            AccessPackageId          = $pkgId
            AccessPackageDescription = $pkgDescription
            CatalogId                = $catalogId
            CreatedDateTime          = $created
            ModifiedDateTime         = $modified
            PolicyName               = ""
            PolicyId                 = ""
            PolicyDescription        = ""
            PolicyStatus             = ""
        }
        continue
    }

    foreach ($policy in $policies) {
        $isEnabled = Resolve-PolicyEnabled -Policy $policy

        if ($SkipDisabledPolicies -and -not $isEnabled) {
            continue
        }

        $result += [PSCustomObject]@{
            AccessPackageName        = $pkgName
            AccessPackageId          = $pkgId
            AccessPackageDescription = $pkgDescription
            CatalogId                = $catalogId
            CreatedDateTime          = $created
            ModifiedDateTime         = $modified
            PolicyName               = (Safe-Get $policy "displayName")
            PolicyId                 = (Safe-Get $policy "id")
            PolicyDescription        = (Safe-Get $policy "description")
            PolicyStatus             = $(if ($isEnabled) { "Enabled" } else { "Disabled" })
        }
    }
}

if (-not $result -or $result.Count -eq 0) {
    $result = @(
        [PSCustomObject]@{
            AccessPackageName        = ""
            AccessPackageId          = ""
            AccessPackageDescription = ""
            CatalogId                = ""
            CreatedDateTime          = ""
            ModifiedDateTime         = ""
            PolicyName               = ""
            PolicyId                 = ""
            PolicyDescription        = ""
            PolicyStatus             = ""
        }
    )
}

Write-Info "Saving CSV to: $OutputPath"
$result | Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8

Write-Host "Done! Exported $($result.Count) row(s)." -ForegroundColor Green
