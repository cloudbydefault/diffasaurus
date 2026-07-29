#Requires -Version 7

param(
    [string]$OutputPath = "",
    [int]$LimitUsers = 0,
    [bool]$MembersOnly = $false
)

$ErrorActionPreference = "Stop"

if (Get-Variable -Name PSStyle -Scope Global -ErrorAction SilentlyContinue) {
    $PSStyle.OutputRendering = 'PlainText'
}

function Format-DateValue {
    param($Value)

    if ($null -eq $Value) { return "" }

    try {
        return ([datetime]$Value).ToString("yyyy-MM-dd HH:mm:ss")
    }
    catch {
        return ""
    }
}

# ---------------------------
# DEFAULT OUTPUT
# ---------------------------
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
    $OutputPath = Join-Path $reportsDir "Entra_Users_Activity_$timestamp.csv"
}

# ---------------------------
# CONNECT
# ---------------------------
Connect-MgGraph -Scopes `
    "User.Read.All", `
    "AuditLog.Read.All" `
    -ContextScope CurrentUser `
    -NoWelcome

# ---------------------------
# GET USERS (v1.0)
# ---------------------------
$props = @(
    "id",
    "displayName",
    "userPrincipalName",
    "mail",
    "userType",
    "accountEnabled",
    "jobTitle",
    "companyName",
    "department",
    "country",
    "city",
    "createdDateTime",
    "lastPasswordChangeDateTime",
    "onPremisesSyncEnabled",
    "signInActivity"
)

Write-Host "Retrieving users from v1.0 with signInActivity..." -ForegroundColor Cyan

$users = Get-MgUser -All -Property $props

if ($MembersOnly) {
    $users = $users | Where-Object { $_.UserType -eq "Member" }
}

if ($LimitUsers -and $LimitUsers -gt 0) {
    $users = $users | Select-Object -First $LimitUsers
}

Write-Host ("Users loaded: {0}" -f $users.Count) -ForegroundColor Cyan

# ---------------------------
# BUILD REPORT
# ---------------------------
Write-Host "Building report objects..." -ForegroundColor Cyan

$report = foreach ($u in $users) {

    $mailValue = if ($u.Mail) { $u.Mail } else { $u.UserPrincipalName }

    [pscustomobject]@{
        DisplayName    = $u.DisplayName
        UPN            = $u.UserPrincipalName
        Mail           = $mailValue
        UserId         = $u.Id
        UserType       = $u.UserType
        AccountEnabled = $u.AccountEnabled

        JobTitle       = $u.JobTitle
        CompanyName    = $u.CompanyName
        Department     = $u.Department
        Country        = $u.Country
        City           = $u.City

        CreatedDateTime                = Format-DateValue $u.CreatedDateTime
        LastPasswordChangeDateTime     = Format-DateValue $u.LastPasswordChangeDateTime
        OnPremisesSyncEnabled          = $u.OnPremisesSyncEnabled

        LastInteractiveSignInDateTime    = Format-DateValue $u.SignInActivity.LastSignInDateTime
        LastNonInteractiveSignInDateTime = Format-DateValue $u.SignInActivity.LastNonInteractiveSignInDateTime
        LastSuccessfulSignInDateTime     = Format-DateValue $u.SignInActivity.LastSuccessfulSignInDateTime
    }
}

# ---------------------------
# EXPORT
# ---------------------------
Write-Host "Writing CSV: $OutputPath" -ForegroundColor Green
$report |
    Sort-Object DisplayName, UPN |
    Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8

Disconnect-MgGraph | Out-Null

Write-Host "Done." -ForegroundColor Green
