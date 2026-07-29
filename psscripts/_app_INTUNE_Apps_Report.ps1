#Requires -Version 7.0

param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

if (Get-Variable -Name PSStyle -Scope Global -ErrorAction SilentlyContinue) {
    $PSStyle.OutputRendering = 'PlainText'
}

# ---------------------------------------------------------
# Resolve output path
# ---------------------------------------------------------
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
    $OutputPath = Join-Path $reportsDir "Intune_Apps_Full_$timestamp.csv"
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

function Invoke-GraphGetAllPages {
    param(
        [Parameter(Mandatory)]
        [string]$Uri
    )

    $all = @()
    $next = $Uri

    while ($next) {
        $resp = Invoke-MgGraphRequest -Method GET -Uri $next -OutputType PSObject
        if ($resp.value) {
            $all += @($resp.value)
        }
        else {
            $all += @($resp)
        }
        $next = $resp.'@odata.nextLink'
    }

    return $all
}

function Invoke-GraphBatch {
    param(
        [Parameter(Mandatory)]
        [array]$Requests
    )

    $body = @{ requests = $Requests } | ConvertTo-Json -Depth 20

    Invoke-MgGraphRequest `
        -Method POST `
        -Uri "https://graph.microsoft.com/v1.0/`$batch" `
        -Body $body `
        -ContentType "application/json" `
        -OutputType PSObject
}

function Split-IntoChunks {
    param(
        [Parameter(Mandatory)]
        [array]$Items,
        [int]$ChunkSize = 20
    )

    $chunks = @()
    for ($i = 0; $i -lt $Items.Count; $i += $ChunkSize) {
        $end = [Math]::Min($i + $ChunkSize - 1, $Items.Count - 1)
        $chunks += ,($Items[$i..$end])
    }
    return $chunks
}

function Invoke-BatchWithRetry {
    param(
        [Parameter(Mandatory)]
        [array]$Requests,
        [int]$MaxRetry = 3
    )

    $pending = @($Requests)
    $finalResponses = @()

    for ($attempt = 1; $attempt -le $MaxRetry -and $pending.Count -gt 0; $attempt++) {
        $batchResponse = Invoke-GraphBatch -Requests $pending
        $retryList = @()

        foreach ($resp in $batchResponse.responses) {
            if ($resp.status -eq 200) {
                $finalResponses += $resp
            }
            elseif ($resp.status -in 429, 503, 504) {
                $originalRequest = $pending | Where-Object { $_.id -eq $resp.id }
                if ($originalRequest) {
                    $retryList += $originalRequest
                }
            }
            else {
                $finalResponses += $resp
            }
        }

        if ($retryList.Count -gt 0 -and $attempt -lt $MaxRetry) {
            Start-Sleep -Seconds (3 * $attempt)
        }

        $pending = @($retryList)
    }

    foreach ($left in $pending) {
        $finalResponses += [pscustomobject]@{
            id     = $left.id
            status = 999
            body   = @{
                error = @{
                    message = "Request still failed after retries."
                }
            }
        }
    }

    return $finalResponses
}

function Get-PlatformFromODataType {
    param([string]$ODataType)

    switch -Regex ($ODataType) {
        'win32|windows' { 'Windows' }
        'macOS|macos'   { 'macOS' }
        'ios|ipad'      { 'iOS/iPadOS' }
        'android'       { 'Android' }
        default         { 'Other / Unknown' }
    }
}

function Get-AppTypeShort {
    param([string]$ODataType)

    if ([string]::IsNullOrWhiteSpace($ODataType)) { return "" }
    return ($ODataType -replace '^#microsoft\.graph\.', '')
}

function Get-AssignmentTargetType {
    param([object]$Assignment)

    $targetType = $Assignment.target.'@odata.type'

    switch ($targetType) {
        '#microsoft.graph.allLicensedUsersAssignmentTarget' { 'All licensed users' }
        '#microsoft.graph.allDevicesAssignmentTarget'       { 'All devices' }
        '#microsoft.graph.groupAssignmentTarget'            { 'Included group' }
        '#microsoft.graph.exclusionGroupAssignmentTarget'   { 'Excluded group' }
        default                                             { ($targetType -replace '^#microsoft\.graph\.', '') }
    }
}

function Resolve-GroupNamesBulk {
    param(
        [string[]]$GroupIds
    )

    $map = @{}
    $uniqueIds = $GroupIds | Where-Object { $_ } | Sort-Object -Unique

    if (-not $uniqueIds -or $uniqueIds.Count -eq 0) {
        return $map
    }

    $chunks = Split-IntoChunks -Items $uniqueIds -ChunkSize 1000

    foreach ($chunk in $chunks) {
        $body = @{
            ids   = @($chunk)
            types = @("group")
        } | ConvertTo-Json -Depth 5

        $resp = Invoke-MgGraphRequest `
            -Method POST `
            -Uri "https://graph.microsoft.com/v1.0/directoryObjects/getByIds" `
            -Body $body `
            -ContentType "application/json" `
            -OutputType PSObject

        foreach ($obj in $resp.value) {
            $map[$obj.id] = $obj.displayName
        }
    }

    return $map
}

function Join-UniqueValues {
    param(
        [array]$Values,
        [string]$Separator = "; "
    )

    return (($Values | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Sort-Object -Unique) -join $Separator)
}

Connect-MgGraph -Scopes "DeviceManagementApps.Read.All,Group.Read.All" -NoWelcome | Out-Null

Write-Host "Retrieving Intune apps..." -ForegroundColor Cyan
$apps = @(Invoke-GraphGetAllPages -Uri "https://graph.microsoft.com/v1.0/deviceAppManagement/mobileApps")

if (-not $apps -or $apps.Count -eq 0) {
    Write-Warning "No Intune apps found."
    @() | Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8
    return
}

Write-Host "Apps found: $($apps.Count)" -ForegroundColor Green
Write-Host "Retrieving assignments in batch..." -ForegroundColor Cyan

$assignmentRequests = foreach ($app in $apps) {
    @{
        id     = $app.id
        method = "GET"
        url    = "/deviceAppManagement/mobileApps/$($app.id)/assignments"
    }
}

$chunks = Split-IntoChunks -Items $assignmentRequests -ChunkSize 20
$assignmentMap = @{}

$batchIndex = 0
foreach ($chunk in $chunks) {
    $batchIndex++
    Write-Host "Processing batch $batchIndex / $($chunks.Count) ..." -ForegroundColor Yellow

    $responses = Invoke-BatchWithRetry -Requests $chunk

    foreach ($resp in $responses) {
        if ($resp.status -eq 200) {
            $assignmentMap[$resp.id] = @($resp.body.value)
        }
        else {
            $assignmentMap[$resp.id] = @()
        }
    }
}

Write-Host "Resolving group names..." -ForegroundColor Cyan

$allGroupIds = foreach ($app in $apps) {
    if ($assignmentMap.ContainsKey($app.id)) {
        foreach ($assignment in $assignmentMap[$app.id]) {
            if ($assignment.target.groupId) {
                $assignment.target.groupId
            }
        }
    }
}

$groupNameMap = Resolve-GroupNamesBulk -GroupIds $allGroupIds

Write-Host "Building final report..." -ForegroundColor Cyan

$report = foreach ($app in $apps) {
    $assignments = if ($assignmentMap.ContainsKey($app.id)) { @($assignmentMap[$app.id]) } else { @() }

    $requiredGroups = @()
    $availableGroups = @()
    $uninstallGroups = @()
    $excludedGroups = @()
    $allGroupIds = @()
    $allGroupNames = @()
    $assignmentSummary = @()
    $assignmentDetails = @()
    $assignmentTargets = @()
    $assignmentIntents = @()

    foreach ($assignment in $assignments) {
        $intent = [string]$assignment.intent
        $targetType = Get-AssignmentTargetType -Assignment $assignment
        $groupId = $assignment.target.groupId
        $groupName = ""

        if ($groupId) {
            $allGroupIds += $groupId
            if ($groupNameMap.ContainsKey($groupId)) {
                $groupName = $groupNameMap[$groupId]
                $allGroupNames += $groupName
            }
            else {
                $groupName = $groupId
            }
        }

        if ($intent) { $assignmentIntents += $intent }
        if ($targetType) { $assignmentTargets += $targetType }

        $label = if ($groupName) { $groupName } else { $targetType }
        if ($intent -and $label) {
            $assignmentSummary += "$intent $label"
        }

        switch ($intent.ToLower()) {
            "required" {
                if ($groupName) { $requiredGroups += $groupName }
            }
            "available" {
                if ($groupName) { $availableGroups += $groupName }
            }
            "uninstall" {
                if ($groupName) { $uninstallGroups += $groupName }
            }
        }

        if ($targetType -eq "Excluded group" -and $groupName) {
            $excludedGroups += $groupName
        }

        $assignmentDetails += [pscustomobject]@{
            AssignmentId       = $assignment.id
            Intent             = $intent
            TargetType         = $targetType
            GroupId            = $groupId
            GroupName          = $groupName
            RawTargetODataType = $assignment.target.'@odata.type'
            Settings           = if ($assignment.settings) { $assignment.settings | ConvertTo-Json -Depth 20 -Compress } else { "" }
        }
    }

    [pscustomobject]@{
        AppName                 = $app.displayName
        Publisher               = $app.publisher
        PlatformGuess           = Get-PlatformFromODataType -ODataType $app.'@odata.type'
        AppGraphType            = Get-AppTypeShort -ODataType $app.'@odata.type'
        PublishingState         = $app.publishingState
        IsFeatured              = $app.isFeatured
        CreatedDateTime         = Format-DateValue $app.createdDateTime
        LastModifiedDateTime    = Format-DateValue $app.lastModifiedDateTime
        AppId                   = $app.id

        IsAssigned              = ($assignments.Count -gt 0)
        AssignmentCount         = $assignments.Count
        AssignmentIntents       = Join-UniqueValues -Values $assignmentIntents
        AssignmentTargets       = Join-UniqueValues -Values $assignmentTargets
        RequiredGroups          = Join-UniqueValues -Values $requiredGroups
        AvailableGroups         = Join-UniqueValues -Values $availableGroups
        UninstallGroups         = Join-UniqueValues -Values $uninstallGroups
        ExcludedGroups          = Join-UniqueValues -Values $excludedGroups
        AllAssignmentGroupIds   = Join-UniqueValues -Values $allGroupIds
        AllAssignmentGroupNames = Join-UniqueValues -Values $allGroupNames
        AssignmentSummary       = Join-UniqueValues -Values $assignmentSummary
        AssignmentDetailsJson   = if ($assignmentDetails.Count -gt 0) { $assignmentDetails | ConvertTo-Json -Depth 20 -Compress } else { "" }
    }
}

$report |
    Sort-Object PlatformGuess, AppName |
    Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8

Disconnect-MgGraph | Out-Null

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "CSV: $OutputPath" -ForegroundColor Green
Write-Host "Rows: $($report.Count)" -ForegroundColor Green
