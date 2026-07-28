#Requires -Version 7

<#
Intune / Entra - Managed Devices Compliance Report (API-call friendly + Retry-After aware)

Goal (per device):
- Which user has which device (Primary User UPN / DisplayName)
- Compliance status
- Last time the device contacted Intune (lastSyncDateTime)
- Useful device inventory fields (OS, model, serial, ownership, join ids, etc.)
- Derived fields:
  - DaysSinceLastSync
  - DeviceActivityStatus (NeverSynced / Active<=30d / Stale31-90d / Inactive>90d)

API strategy (throttle-friendly):
- 1 paged GET call to: /v1.0/deviceManagement/managedDevices?$select=...&$top=999
- Retry-After aware handling for 429/503/504 for each page request
No per-user calls, no per-device enrichment calls.

Permissions (delegated scopes):
- DeviceManagementManagedDevices.Read.All
Optionally:
- DeviceManagementManagedDevices.ReadWrite.All (NOT required for read)
#>

# ---------------------------
# APP CONFIG
# ---------------------------

$OutDir = $env:REPORTS_DIR
if (-not $OutDir) { $OutDir = Join-Path $PSScriptRoot "..\reports" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# ---------------------------
# CONFIG
# ---------------------------

$maxAttemptsPerPage    = 6
$retryAfterBufferSec   = 2
$timestamp             = Get-Date -Format "yyyyMMdd-HHmmss"

# Output name (naming convention) -> ALWAYS into app reports folder
$outFile = Join-Path $OutDir "Intune_ManagedDevices_Compliance_${timestamp}.csv"

# ---------------------------
# CONNECT
# ---------------------------
Connect-MgGraph -Scopes `
    "DeviceManagementManagedDevices.Read.All" `
    -ContextScope CurrentUser -NoWelcome

# ---------------------------
# HELPERS
# ---------------------------
function Get-RetryAfterSeconds {
    param($headers)
    if ($null -eq $headers) { return $null }

    foreach ($k in @("Retry-After","retry-after")) {
        try {
            if ($headers.ContainsKey($k) -and $headers[$k]) {
                $val = [string]$headers[$k]
                $n = 0
                if ([int]::TryParse($val, [ref]$n)) { return $n }
            }
        } catch {}
    }
    return $null
}

function Invoke-GraphGetWithRetry {
    param(
        [Parameter(Mandatory)] [string] $Uri,
        [int] $MaxAttempts = 6,
        [int] $RetryAfterBufferSec = 2
    )

    $attempt = 0
    while ($true) {
        $attempt++

        try {
            return Invoke-MgGraphRequest -Method GET -Uri $Uri -ErrorAction Stop
        }
        catch {
            # Try to detect transient errors
            $statusCode = $null
            $headers    = $null
            $msg        = $_.Exception.Message

            try { $statusCode = [int]$_.Exception.Response.StatusCode } catch {}
            try { $headers    = $_.Exception.Response.Headers } catch {}

            $isTransient = $false
            if ($statusCode -in 429,503,504) { $isTransient = $true }

            if (-not $isTransient -or $attempt -ge $MaxAttempts) {
                throw
            }

            $ra = $null
            try { $ra = Get-RetryAfterSeconds -headers $headers } catch {}
            $sleepSec = if ($ra -and $ra -gt 0) { $ra + $RetryAfterBufferSec } else { 5 }

            Write-Host ("Transient GET failure (HTTP {0}) attempt {1}/{2}. Sleeping {3}s... [{4}]" -f $statusCode, $attempt, $MaxAttempts, $sleepSec, $msg) -ForegroundColor Yellow
            Start-Sleep -Seconds $sleepSec
        }
    }
}

function Normalize-ComplianceState {
    param([string]$state)
    if ([string]::IsNullOrWhiteSpace($state)) { return $null }

    # Intune returns values like: compliant, noncompliant, unknown, notApplicable, error, conflict
    switch ($state.ToLowerInvariant()) {
        "compliant"      { "Compliant" ; break }
        "noncompliant"   { "NonCompliant" ; break }
        "unknown"        { "Unknown" ; break }
        "notapplicable"  { "NotApplicable" ; break }
        "error"          { "Error" ; break }
        "conflict"       { "Conflict" ; break }
        default          { $state }
    }
}

# ---------------------------
# GET MANAGED DEVICES (paged)
# ---------------------------
Write-Host "Retrieving Intune managed devices (paged)..." -ForegroundColor Cyan

$select = @(
    # Identity
    "id","deviceName","azureADDeviceId","userId",
    "userPrincipalName","userDisplayName",

    # Platform / inventory
    "operatingSystem","osVersion","model","manufacturer","serialNumber",

    # Management & enrollment
    "managementAgent","enrolledDateTime","lastSyncDateTime",

    # Status
    "complianceState","jailBroken",

    # Ownership / classification
    "managedDeviceOwnerType",

    # Useful extra keys (lightweight)
    "emailAddress","phoneNumber"
) -join ","

$uri = "https://graph.microsoft.com/v1.0/deviceManagement/managedDevices?`$select=$select&`$top=999"

$allDevices = [System.Collections.Generic.List[object]]::new()

while ($uri) {
    $resp = Invoke-GraphGetWithRetry -Uri $uri -MaxAttempts $maxAttemptsPerPage -RetryAfterBufferSec $retryAfterBufferSec

    foreach ($d in @($resp.value)) {
        $allDevices.Add($d) | Out-Null
    }

    if ($resp.'@odata.nextLink') {
        $uri = [string]$resp.'@odata.nextLink'
    } else {
        $uri = $null
    }
}

Write-Host ("Managed devices loaded: {0}" -f $allDevices.Count) -ForegroundColor Green

# ---------------------------
# BUILD REPORT
# ---------------------------
Write-Host "Building report objects..." -ForegroundColor Cyan

$now = Get-Date

$report = foreach ($d in $allDevices) {

    $lastSync = $d.lastSyncDateTime
    $daysSinceLastSync = if ($lastSync) { (New-TimeSpan -Start ([datetime]$lastSync) -End $now).Days } else { $null }

    $deviceActivityStatus = if (-not $lastSync) {
        "NeverSynced"
    } elseif ($daysSinceLastSync -le 30) {
        "Active<=30d"
    } elseif ($daysSinceLastSync -le 90) {
        "Stale31-90d"
    } else {
        "Inactive>90d"
    }

    [pscustomobject]@{
        # User mapping
        UserPrincipalName  = $d.userPrincipalName
        UserDisplayName    = $d.userDisplayName
        UserId             = $d.userId

        # Device identity
        DeviceName         = $d.deviceName
        ManagedDeviceId    = $d.id
        AzureADDeviceId    = $d.azureADDeviceId
        SerialNumber       = $d.serialNumber
        Manufacturer       = $d.manufacturer
        Model              = $d.model

        # Platform
        OperatingSystem    = $d.operatingSystem
        OSVersion          = $d.osVersion

        # Management
        ManagementAgent    = $d.managementAgent
        EnrolledDateTime   = $d.enrolledDateTime
        LastSyncDateTime   = $d.lastSyncDateTime

        # Compliance & security flags
        ComplianceState    = Normalize-ComplianceState $d.complianceState
        JailBroken         = $d.jailBroken

        # Ownership
        OwnerType          = $d.managedDeviceOwnerType

        # Derived (no extra API calls)
        DaysSinceLastSync      = $daysSinceLastSync
        DeviceActivityStatus   = $deviceActivityStatus

        # Optional contact-ish fields some orgs like
        EmailAddress       = $d.emailAddress
        PhoneNumber        = $d.phoneNumber
    }
}

Disconnect-MgGraph

# ---------------------------
# EXPORT
# ---------------------------
Write-Host "Writing CSV to: $outFile" -ForegroundColor Green
$report | Export-Csv -Path $outFile -NoTypeInformation -Delimiter ';' -Encoding UTF8
Write-Host "Done." -ForegroundColor Green