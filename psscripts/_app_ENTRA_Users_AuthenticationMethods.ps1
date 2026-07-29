#Requires -Version 7

param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

if (Get-Variable -Name PSStyle -Scope Global -ErrorAction SilentlyContinue) {
    $PSStyle.OutputRendering = 'PlainText'
}

# ==========================================================
# DEFAULT OUTPUT
# ==========================================================

if (-not $OutputPath) {
    $reportsDir = $env:REPORTS_DIR
    if (-not $reportsDir) {
        $projectRoot = Split-Path -Parent $PSScriptRoot
        $reportsDir = Join-Path $projectRoot "reports"
    }

    if (-not (Test-Path $reportsDir)) {
        New-Item -ItemType Directory -Path $reportsDir -Force | Out-Null
    }

    $timestamp  = Get-Date -Format "yyyyMMdd-HHmmss"

    # Keep old COCO report name for compatibility
    $OutputPath = Join-Path $reportsDir "Entra_Users_AuthenticationMethods_$timestamp.csv"
}

# ==========================================================
# CONNECT
# ==========================================================

Connect-MgGraph -Scopes `
    "AuditLog.Read.All", `
    "User.Read.All" `
    -ContextScope CurrentUser `
    -NoWelcome

# ==========================================================
# HELPERS
# ==========================================================

function Join-ArrayValue {
    param($Value)

    if ($null -eq $Value) {
        return ""
    }

    if ($Value -is [array]) {
        return (($Value | Where-Object { $_ }) -join " ; ")
    }

    return [string]$Value
}

function Invoke-GraphGetAll {
    param(
        [Parameter(Mandatory)] [string] $Uri
    )

    $results = [System.Collections.Generic.List[object]]::new()
    $next = $Uri

    while ($next) {
        $response = Invoke-MgGraphRequest `
            -Method GET `
            -Uri $next `
            -ErrorAction Stop

        foreach ($item in @($response.value)) {
            $results.Add($item) | Out-Null
        }

        if ($response.'@odata.nextLink') {
            $next = [string]$response.'@odata.nextLink'
        }
        else {
            $next = $null
        }
    }

    return $results.ToArray()
}

# ==========================================================
# GET MICROSOFT AUTHENTICATION REGISTRATION REPORT
# ==========================================================

Write-Host "Retrieving Microsoft Authentication Registration report..." -ForegroundColor Cyan

$authUri = "https://graph.microsoft.com/v1.0/reports/authenticationMethods/userRegistrationDetails?`$top=999"
$authDetails = Invoke-GraphGetAll -Uri $authUri

Write-Host "Authentication registration rows loaded: $($authDetails.Count)" -ForegroundColor Green

# ==========================================================
# GET USERS FOR COCO ENRICHMENT
# ==========================================================

Write-Host "Retrieving Entra users for COCO enrichment..." -ForegroundColor Cyan

$userProps = @(
    "id",
    "displayName",
    "userPrincipalName",
    "userType",
    "accountEnabled",
    "jobTitle",
    "companyName",
    "department",
    "country",
    "city"
)

$users = Get-MgUser -All -Property $userProps

$userMap = @{}

foreach ($u in $users) {
    if ($u.UserPrincipalName) {
        $userMap[$u.UserPrincipalName.ToLower()] = $u
    }
}

Write-Host "Users loaded: $($users.Count)" -ForegroundColor Green

# ==========================================================
# BUILD HYBRID COCO REPORT
# ==========================================================

Write-Host "Building COCO authentication report..." -ForegroundColor Cyan

$report = foreach ($d in $authDetails) {

    $upnKey = ""

    if ($d.userPrincipalName) {
        $upnKey = $d.userPrincipalName.ToLower()
    }

    $u = $null

    if ($upnKey -and $userMap.ContainsKey($upnKey)) {
        $u = $userMap[$upnKey]
    }

    $methodsRegistered = Join-ArrayValue $d.methodsRegistered
    $systemPreferredMethods = Join-ArrayValue $d.systemPreferredAuthenticationMethods

    [pscustomobject]@{
        # ==================================================
        # Original COCO columns
        # ==================================================

        DisplayName                                      = if ($u) { $u.DisplayName } else { $d.userDisplayName }
        UPN                                              = $d.userPrincipalName
        UserType                                         = if ($u) { $u.UserType } else { $d.userType }
        AccountEnabled                                   = if ($u) { $u.AccountEnabled } else { $null }

        JobTitle                                         = if ($u) { $u.JobTitle } else { "" }
        CompanyName                                      = if ($u) { $u.CompanyName } else { "" }
        Department                                       = if ($u) { $u.Department } else { "" }
        Country                                          = if ($u) { $u.Country } else { "" }
        City                                             = if ($u) { $u.City } else { "" }

        IsSystemPreferredAuthenticationMethodEnabled     = $d.isSystemPreferredAuthenticationMethodEnabled
        UserPreferredMethodForSecondaryAuthentication    = $d.userPreferredMethodForSecondaryAuthentication
        SystemPreferredAuthenticationMethod              = $systemPreferredMethods

        AuthenticationMethods                            = $methodsRegistered

        # ==================================================
        # Compatibility columns for old dashboard
        # These no longer represent per-user query success.
        # They are kept to avoid breaking existing UI logic.
        # ==================================================

        PrefsStatus                                      = 200
        PrefsError                                       = ""
        MethodsStatus                                    = 200
        MethodsError                                     = ""

        # ==================================================
        # New Microsoft report columns
        # ==================================================

        MicrosoftReportId                                = $d.id
        IsAdmin                                          = $d.isAdmin

        IsMfaRegistered                                  = $d.isMfaRegistered
        IsMfaCapable                                     = $d.isMfaCapable
        IsPasswordlessCapable                            = $d.isPasswordlessCapable

        IsSsprRegistered                                 = $d.isSsprRegistered
        IsSsprEnabled                                    = $d.isSsprEnabled
        IsSsprCapable                                    = $d.isSsprCapable

        DefaultMfaMethod                                 = $d.defaultMfaMethod
        MethodsRegistered                                = $methodsRegistered
        SystemPreferredAuthenticationMethods             = $systemPreferredMethods

        LastUpdatedDateTime                              = $d.lastUpdatedDateTime
        ReportSource                                     = "Microsoft authenticationMethods/userRegistrationDetails"
    }
}

# ==========================================================
# EXPORT
# ==========================================================

Write-Host "Writing CSV to: $OutputPath" -ForegroundColor Green

$report |
    Sort-Object UPN |
    Export-Csv `
        -Path $OutputPath `
        -NoTypeInformation `
        -Delimiter ';' `
        -Encoding UTF8

Write-Host "Done." -ForegroundColor Green
Write-Host "Rows exported: $($report.Count)" -ForegroundColor Green
Write-Host "CSV: $OutputPath" -ForegroundColor Green
