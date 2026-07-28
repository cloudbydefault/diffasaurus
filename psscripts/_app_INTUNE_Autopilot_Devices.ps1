#Requires -Version 7.0

$ErrorActionPreference = "Stop"

if (Get-Variable -Name PSStyle -Scope Global -ErrorAction SilentlyContinue) {
    $PSStyle.OutputRendering = 'PlainText'
}

# Use app Reports folder
if (-not $env:REPORTS_DIR) {
    Write-Error "REPORTS_DIR not set"
    exit 1
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$OutputCsv = Join-Path $env:REPORTS_DIR "Intune_Devices_Autopilot_$Timestamp.csv"

Connect-MgGraph -Scopes "DeviceManagementServiceConfig.Read.All" -ContextScope CurrentUser -NoWelcome | Out-Null

Write-Host "Retrieving Autopilot devices..."

$devices = Get-MgDeviceManagementWindowsAutopilotDeviceIdentity -All

$report = foreach ($Device in $devices) {

    if (-not [string]::IsNullOrWhiteSpace($Device.UserPrincipalName)) {
        $assignedUser = $Device.UserPrincipalName
    }
    elseif (-not [string]::IsNullOrWhiteSpace($Device.AddressableUserName)) {
        $assignedUser = $Device.AddressableUserName
    }
    else {
        $assignedUser = "Unassigned"
    }

    $assignmentStatus = if ($assignedUser -eq "Unassigned") {
        "NotAssigned"
    }
    else {
        "Assigned"
    }

    if ($assignmentStatus -eq "Assigned") {
        if ($Device.EnrollmentState -eq "enrolled") {
            $recommendedAction = "Review"
        }
        else {
            $recommendedAction = "ReadyToUnassign"
        }
    }
    else {
        $recommendedAction = "ReadyToAssign"
    }

    [PSCustomObject]@{
        DisplayName                  = $Device.DisplayName
        SerialNumber                 = $Device.SerialNumber
        Manufacturer                 = $Device.Manufacturer
        Model                        = $Device.Model
        GroupTag                     = $Device.GroupTag
        PurchaseOrderIdentifier      = $Device.PurchaseOrderIdentifier
        EnrollmentState              = $Device.EnrollmentState
        LastContactedDateTime        = $Device.LastContactedDateTime
        UserPrincipalName            = $Device.UserPrincipalName
        AddressableUserName          = $Device.AddressableUserName
        ResourceName                 = $Device.ResourceName
        SkuNumber                    = $Device.SkuNumber
        SystemFamily                 = $Device.SystemFamily
        AzureADDeviceId              = $Device.AzureActiveDirectoryDeviceId
        ManagedDeviceId              = $Device.ManagedDeviceId
        AutopilotObjectId            = $Device.Id
        AssignedUser                 = $assignedUser
        AssignmentStatus             = $assignmentStatus
        RecommendedAction            = $recommendedAction
    }
}

$report |
    Sort-Object Manufacturer, Model, SerialNumber |
    Export-Csv -Path $OutputCsv -NoTypeInformation -Encoding UTF8

Write-Host "OK: Autopilot report generated"
Write-Host "Total Autopilot devices: $($report.Count)"
Write-Host $OutputCsv

exit 0