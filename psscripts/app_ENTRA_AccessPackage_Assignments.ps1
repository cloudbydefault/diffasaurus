#Requires -Version 7

<#
.COCO_GRAPH_SCOPES
EntitlementManagement.Read.All
Directory.Read.All
User.Read.All
.COCO_GRAPH_SCOPES_END
#>

param(
    [string]$OutputPath = "",
    [switch]$OnlyActiveAssignments
)

$ErrorActionPreference = "Stop"

if (Get-Variable -Name PSStyle -Scope Global -ErrorAction SilentlyContinue) {
    $PSStyle.OutputRendering = "PlainText"
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
        if ($null -eq $Object) {
            return ""
        }

        $value = $Object.$PropertyName

        if ($null -eq $value) {
            return ""
        }

        return [string]$value
    }
    catch {
        return ""
    }
}

function Invoke-GraphGetAll {
    param(
        [Parameter(Mandatory)]
        [string]$Uri
    )

    $all = [System.Collections.Generic.List[object]]::new()
    $next = $Uri

    while ($next) {
        Write-Info "GET $next"

        $response = Invoke-MgGraphRequest `
            -Uri $next `
            -Method GET `
            -OutputType PSObject `
            -ErrorAction Stop

        foreach ($item in @($response.value)) {
            $all.Add($item) | Out-Null
        }

        $next = $response.'@odata.nextLink'
    }

    return $all
}

# ==========================================================
# OUTPUT
# ==========================================================

if (-not $OutputPath) {
    $reportsDir = $env:REPORTS_DIR
    if (-not $reportsDir) {
        $projectRoot = Split-Path -Parent $PSScriptRoot
        $reportsDir = Join-Path $projectRoot "reports"
    }

    New-Item -Path $reportsDir -ItemType Directory -Force | Out-Null

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $reportsDir "Entra_AccessPackage_User_Assignments_$timestamp.csv"
}
else {
    $dir = Split-Path $OutputPath -Parent

    if ($dir) {
        New-Item -Path $dir -ItemType Directory -Force | Out-Null
    }
}

# ==========================================================
# CONNECT
# ==========================================================

Write-Info "Connecting to Microsoft Graph..."

$scopes = @(
    "EntitlementManagement.Read.All",
    "Directory.Read.All",
    "User.Read.All"
)

$ctx = Get-MgContext -ErrorAction SilentlyContinue

if (-not $ctx) {
    Connect-MgGraph -Scopes $scopes -NoWelcome | Out-Null
}

# ==========================================================
# RETRIEVE ASSIGNMENTS
# ==========================================================

Write-Info "Retrieving Access Package assignments..."

$baseUri = "https://graph.microsoft.com/v1.0/identityGovernance/entitlementManagement/assignments?`$expand=target,accessPackage,assignmentPolicy&`$top=999"

$assignments = Invoke-GraphGetAll -Uri $baseUri

Write-Info "Assignments found: $($assignments.Count)"

$result = foreach ($a in $assignments) {

    $state = (Safe-Get $a "state").ToLowerInvariant()

    if ($OnlyActiveAssignments -and $state -and $state -ne "delivered") {
        continue
    }

    $target = $a.target
    $accessPackage = $a.accessPackage
    $policy = $a.assignmentPolicy

    [PSCustomObject]@{
        UserDisplayName          = Safe-Get $target "displayName"
        UserPrincipalName        = Safe-Get $target "userPrincipalName"
        UserEmail                = Safe-Get $target "email"
        UserId                   = Safe-Get $target "id"

        AccessPackageName        = Safe-Get $accessPackage "displayName"
        AccessPackageId          = Safe-Get $accessPackage "id"
        AccessPackageDescription = Safe-Get $accessPackage "description"

        PolicyName               = Safe-Get $policy "displayName"
        PolicyId                 = Safe-Get $policy "id"

        AssignmentId             = Safe-Get $a "id"
        AssignmentState          = Safe-Get $a "state"
        AssignmentStatus         = Safe-Get $a "status"
        AssignmentSchedule       = Safe-Get $a "schedule"
        CreatedDateTime          = Safe-Get $a "createdDateTime"
        ModifiedDateTime         = Safe-Get $a "modifiedDateTime"
        ExpiredDateTime          = Safe-Get $a "expiredDateTime"
    }
}

# ==========================================================
# EXPORT
# ==========================================================

if (-not $result -or $result.Count -eq 0) {
    Write-Host "No access package assignments found." -ForegroundColor Yellow

    $result = @(
        [PSCustomObject]@{
            UserDisplayName          = ""
            UserPrincipalName        = ""
            UserEmail                = ""
            UserId                   = ""
            AccessPackageName        = ""
            AccessPackageId          = ""
            AccessPackageDescription = ""
            PolicyName               = ""
            PolicyId                 = ""
            AssignmentId             = ""
            AssignmentState          = ""
            AssignmentStatus         = ""
            AssignmentSchedule       = ""
            CreatedDateTime          = ""
            ModifiedDateTime         = ""
            ExpiredDateTime          = ""
        }
    )
}

Write-Info "Saving CSV to: $OutputPath"

$result |
    Sort-Object UserPrincipalName, AccessPackageName |
    Export-Csv `
        -Path $OutputPath `
        -NoTypeInformation `
        -Encoding UTF8BOM `
        -Delimiter ";"

Write-Host ""
Write-Host "Done!" -ForegroundColor Green
Write-Host "CSV exported to: $OutputPath"
Write-Host "Rows exported: $($result.Count)"
