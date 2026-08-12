#Requires -Version 7

<#
.SYNOPSIS
    Exports Microsoft Intune managed Android devices to CSV.

.DESCRIPTION
    Retrieves Android managed devices from Microsoft Graph and exports useful
    inventory, ownership, user, compliance, security and cellular identifiers.

    The script performs a light initial query to obtain the Android device IDs,
    then uses Microsoft Graph JSON batching (20 requests per batch) to retrieve
    full details for each device.

    Important Android notes:
      - IMEI, MEID, ICCID and phone number are returned only when Intune/Android
        is able to collect them for that enrollment type/device/SIM.
      - PhoneNumber can legitimately be blank even when an IMEI is available.
      - This script uses the documented managedDevice v1.0 API. The newer Intune
        Device Inventory "SimInfo" table shown in the portal is a separate Intune
        Data Platform inventory source and can contain per-SIM/per-slot data that
        isn't exposed as a SimInfo collection on the v1.0 managedDevice resource.

.PARAMETER OutputFolder
    Folder where the CSV report is written.
    Defaults to the REPORTS_DIR environment variable.

.EXAMPLE
    .\Intune_Android_Devices_Report.ps1 -OutputFolder "C:\Reports"
#>

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
$OutputCsv = Join-Path $OutputFolder "Intune_Android_Devices_$Timestamp.csv"

Import-Module Microsoft.Graph.Authentication -ErrorAction Stop

try {
    Import-Module Microsoft.Graph.DeviceManagement -ErrorAction Stop
}
catch {
    # Invoke-MgGraphRequest only requires the Authentication module/context.
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

# Properties documented on the Microsoft Graph v1.0 managedDevice resource.
# ICCID is explicitly selected because Microsoft documents that a per-device GET
# with $select is required to retrieve its actual value.
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
    "androidSecurityPatchLevel",
    "userPrincipalName",
    "userDisplayName",
    "emailAddress",
    "phoneNumber",
    "subscriberCarrier",
    "wiFiMacAddress",
    "iccid",
    "easActivated",
    "easDeviceId",
    "easActivationDateTime",
    "azureADRegistered",
    "azureADDeviceId",
    "deviceRegistrationState",
    "complianceState",
    "complianceGracePeriodExpirationDateTime",
    "managementAgent",
    "managedDeviceOwnerType",
    "deviceEnrollmentType",
    "enrollmentProfileName",
    "enrolledDateTime",
    "lastSyncDateTime",
    "jailBroken",
    "isEncrypted",
    "partnerReportedThreatState",
    "managementCertificateExpirationDate",
    "totalStorageSpaceInBytes",
    "freeStorageSpaceInBytes"
) -join ","

$devicesUri = "https://graph.microsoft.com/v1.0/deviceManagement/managedDevices?`$filter=operatingSystem eq 'Android'&`$select=id,deviceName"

Write-Host "Retrieving Android devices..."
$devices = Invoke-GraphGetAll -Uri $devicesUri

if (-not $devices) {
    Write-Warning "No Android devices found."

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

Write-Host "Retrieving detailed Android device data with Microsoft Graph batch..."
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
            $errorMessage = $response.body.error.message
            if (-not $errorMessage) {
                $errorMessage = "Unknown Graph error"
            }

            Write-Warning "Batch request ID $($response.id) failed with HTTP $($response.status): $errorMessage"
        }
    }

    # Small pause to remain friendly to Graph throttling.
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
        try {
            $lastSync = [datetime]$d.lastSyncDateTime
        }
        catch {
            $lastSync = $null
        }
    }

    $daysSinceLastSync = ""
    $activityStatus = "Never synced"

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

    [PSCustomObject]@{
        # Device identity
        DeviceName                       = $d.deviceName
        ManagementName                   = $d.managedDeviceName
        IntuneDeviceId                   = $d.id
        EntraDeviceId                    = $d.azureADDeviceId
        SerialNumber                     = $d.serialNumber
        Manufacturer                     = $d.manufacturer
        Model                            = $d.model
        OperatingSystem                  = $d.operatingSystem
        OSVersion                        = $d.osVersion
        AndroidSecurityPatchLevel        = $d.androidSecurityPatchLevel

        # User
        UserDisplayName                  = $d.userDisplayName
        UserPrincipalName                = $d.userPrincipalName
        EmailAddress                     = $d.emailAddress

        # Cellular / SIM
        # PhoneNumber may be empty depending on Android Enterprise enrollment type
        # and whether the SIM/carrier exposes the number to Android/Intune.
        PhoneNumber                      = $d.phoneNumber
        IMEI                             = $d.imei
        MEID                             = $d.meid
        ICCID                            = $d.iccid
        SubscriberCarrier                = $d.subscriberCarrier
        WiFiMacAddress                   = $d.wiFiMacAddress

        # Enrollment / management
        OwnerType                        = $d.managedDeviceOwnerType
        ManagementAgent                  = $d.managementAgent
        DeviceEnrollmentType             = $d.deviceEnrollmentType
        EnrollmentProfileName            = $d.enrollmentProfileName
        DeviceRegistrationState          = $d.deviceRegistrationState
        EnrolledDateTime                 = $d.enrolledDateTime
        ManagementCertificateExpiration  = $d.managementCertificateExpirationDate

        # Activity
        LastSyncDateTime                 = $d.lastSyncDateTime
        DaysSinceLastSync                = $daysSinceLastSync
        DeviceActivityStatus             = $activityStatus

        # Compliance / security
        ComplianceState                  = $d.complianceState
        ComplianceGracePeriodExpiration  = $d.complianceGracePeriodExpirationDateTime
        AzureADRegistered                = $d.azureADRegistered
        IsEncrypted                      = $d.isEncrypted
        Rooted                           = $d.jailBroken
        PartnerReportedThreatState       = $d.partnerReportedThreatState

        # Exchange ActiveSync
        EASActivated                     = $d.easActivated
        EASDeviceId                      = $d.easDeviceId
        EASActivationDateTime            = $d.easActivationDateTime

        # Storage
        TotalStorageGB                   = if ($d.totalStorageSpaceInBytes) {
            [math]::Round($d.totalStorageSpaceInBytes / 1GB, 2)
        }
        else {
            ""
        }

        FreeStorageGB                    = if ($d.freeStorageSpaceInBytes) {
            [math]::Round($d.freeStorageSpaceInBytes / 1GB, 2)
        }
        else {
            ""
        }
    }
}

$report |
    Sort-Object DeviceName |
    Export-Csv `
        -Path $OutputCsv `
        -NoTypeInformation `
        -Encoding UTF8BOM `
        -Delimiter ";"

Write-Host ""
Write-Host "Done."
Write-Host "CSV exported to: $OutputCsv"
Write-Host "Android devices exported: $($report.Count)"