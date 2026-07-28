#Requires -Version 7

param(
    [Parameter(Mandatory = $false)]
    [string]$OutputFolder = $env:REPORTS_DIR
)

$ErrorActionPreference = "Stop"

if (-not $OutputFolder) {
    throw "REPORTS_DIR environment variable is not set and OutputFolder was not provided."
}

New-Item -ItemType Directory -Path $OutputFolder -Force | Out-Null

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$OutputCsv = Join-Path $OutputFolder "Intune_iOS_Devices_$Timestamp.csv"

Import-Module Microsoft.Graph.Authentication -ErrorAction Stop

try {
    Import-Module Microsoft.Graph.DeviceManagement -ErrorAction Stop
}
catch {
    # Not all environments have this module split loaded.
    # Invoke-MgGraphRequest only needs Authentication context.
}

Connect-MgGraph -Scopes "DeviceManagementManagedDevices.Read.All" -NoWelcome

function Invoke-GraphGetAll {
    param(
        [Parameter(Mandatory)]
        [string]$Uri
    )

    $results = @()
    $next = $Uri

    while ($next) {
        $response = Invoke-MgGraphRequest -Method GET -Uri $next
        $results += @($response.value)
        $next = $response.'@odata.nextLink'
    }

    return $results
}

function Invoke-GraphBatch {
    param(
        [Parameter(Mandatory)]
        [array]$Requests
    )

    $body = @{
        requests = $Requests
    } | ConvertTo-Json -Depth 10

    Invoke-MgGraphRequest `
        -Method POST `
        -Uri "https://graph.microsoft.com/v1.0/`$batch" `
        -Body $body `
        -ContentType "application/json"
}

$selectList = @(
    "id",
    "deviceName",
    "managedDeviceName",
    "serialNumber",
    "imei",
    "meid",
    "manufacturer",
    "model",
    "operatingSystem",
    "osVersion",
    "userPrincipalName",
    "userDisplayName",
    "emailAddress",
    "phoneNumber",
    "subscriberCarrier",
    "wiFiMacAddress",
    "ethernetMacAddress",
    "iccid",
    "udid",
    "easActivated",
    "easDeviceId",
    "easActivationDateTime",
    "azureADRegistered",
    "azureADDeviceId",
    "complianceState",
    "managementAgent",
    "managementState",
    "managedDeviceOwnerType",
    "deviceEnrollmentType",
    "enrollmentProfileName",
    "enrolledDateTime",
    "lastSyncDateTime",
    "jailBroken",
    "isEncrypted",
    "isSupervised",
    "totalStorageSpaceInBytes",
    "freeStorageSpaceInBytes",
    "activationLockBypassCode"
) -join ","

$devicesUri = "https://graph.microsoft.com/v1.0/deviceManagement/managedDevices?`$filter=(operatingSystem eq 'iOS' or operatingSystem eq 'iPadOS')&`$select=id,deviceName"

Write-Host "Retrieving iOS / iPadOS devices..."
$devices = Invoke-GraphGetAll -Uri $devicesUri

if (-not $devices) {
    Write-Warning "No iOS / iPadOS devices found."

    @() | Export-Csv `
        -Path $OutputCsv `
        -NoTypeInformation `
        -Encoding UTF8BOM `
        -Delimiter ";"

    Write-Host "CSV exported to: $OutputCsv"
    return
}

$detailsById = @{}
$chunkSize = 20
$totalDevices = $devices.Count
$currentDevice = 0

Write-Host "Retrieving detailed hardware data with Graph batch..."
Write-Host "Total devices to process: $totalDevices"

for ($i = 0; $i -lt $devices.Count; $i += $chunkSize) {
    $endIndex = [Math]::Min($i + $chunkSize - 1, $devices.Count - 1)
    $deviceChunk = $devices[$i..$endIndex]

    $batchRequests = @()
    $index = 1

    foreach ($device in $deviceChunk) {
        $batchRequests += @{
            id     = "$index"
            method = "GET"
            url    = "/deviceManagement/managedDevices/$($device.id)?`$select=$selectList"
        }
        $index++
    }

    $currentDevice += $deviceChunk.Count
    Write-Host "Processing devices $currentDevice / $totalDevices..."

    $batchResponse = Invoke-GraphBatch -Requests $batchRequests

    foreach ($response in $batchResponse.responses) {
        if ($response.status -ge 200 -and $response.status -lt 300) {
            $detailsById[$response.body.id] = $response.body
        }
        else {
            Write-Warning "Batch request ID $($response.id) failed with status $($response.status)"
        }
    }

    Start-Sleep -Milliseconds 300
}

$now = Get-Date

$report = foreach ($device in $devices) {
    $d = $detailsById[$device.id]

    if (-not $d) {
        $d = $device
    }

    $lastSync = $null
    if ($d.lastSyncDateTime) {
        try { $lastSync = [datetime]$d.lastSyncDateTime } catch {}
    }

    $daysSinceLastSync = ""
    $activityStatus = ""

    if ($lastSync) {
        $daysSinceLastSync = [math]::Floor(($now - $lastSync).TotalDays)

        if ($daysSinceLastSync -le 30) {
            $activityStatus = "Active <=30d"
        }
        elseif ($daysSinceLastSync -le 90) {
            $activityStatus = "Stale 31-90d"
        }
        else {
            $activityStatus = "Inactive >90d"
        }
    }
    else {
        $activityStatus = "Never synced"
    }

    [PSCustomObject]@{
        DeviceName                = $d.deviceName
        ManagementName            = $d.managedDeviceName
        IntuneDeviceId            = $d.id
        EntraDeviceId             = $d.azureADDeviceId
        UDID                      = $d.udid
        SerialNumber              = $d.serialNumber
        IMEI                      = $d.imei
        MEID                      = $d.meid
        Manufacturer              = $d.manufacturer
        Model                     = $d.model
        OperatingSystem           = $d.operatingSystem
        OSVersion                 = $d.osVersion

        UserDisplayName           = $d.userDisplayName
        UserPrincipalName         = $d.userPrincipalName
        EmailAddress              = $d.emailAddress
        PhoneNumber               = $d.phoneNumber

        OwnerType                 = $d.managedDeviceOwnerType
        ManagementAgent           = $d.managementAgent
        ManagementState           = $d.managementState
        DeviceEnrollmentType      = $d.deviceEnrollmentType
        EnrollmentProfileName     = $d.enrollmentProfileName
        EnrolledDateTime          = $d.enrolledDateTime
        LastSyncDateTime          = $d.lastSyncDateTime
        DaysSinceLastSync         = $daysSinceLastSync
        DeviceActivityStatus      = $activityStatus

        ComplianceState           = $d.complianceState
        AzureADRegistered         = $d.azureADRegistered
        IsSupervised              = $d.isSupervised
        IsEncrypted               = $d.isEncrypted
        JailBroken                = $d.jailBroken
        EASActivated              = $d.easActivated
        EASActivationId           = $d.easActivationId
        EASActivationDateTime     = $d.easActivationDateTime

        SubscriberCarrier         = $d.subscriberCarrier
        CellularTechnology        = ""
        WiFiMacAddress            = $d.wiFiMacAddress
        EthernetMacAddress        = $d.ethernetMacAddress
        ICCID                     = $d.iccid

        TotalStorageGB            = if ($d.totalStorageSpaceInBytes) { [math]::Round($d.totalStorageSpaceInBytes / 1GB, 2) } else { "" }
        FreeStorageGB             = if ($d.freeStorageSpaceInBytes) { [math]::Round($d.freeStorageSpaceInBytes / 1GB, 2) } else { "" }

        ActivationLockBypassCode  = $d.activationLockBypassCode
        HasActivationBypassCode   = if ($d.activationLockBypassCode) { "Yes" } else { "No" }
    }
}

$report |
    Sort-Object DeviceName |
    Export-Csv -Path $OutputCsv -NoTypeInformation -Encoding UTF8BOM -Delimiter ";"

Write-Host ""
Write-Host "Done."
Write-Host "CSV exported to: $OutputCsv"
Write-Host "Devices exported: $($report.Count)"