#Requires -Version 7

<#
.SYNOPSIS
    Export Intune configuration policies as a portable Policy Snapshot Bundle (Phase 0).

.DESCRIPTION
    Exports raw configuration policy data from Microsoft Graph into a deterministic
    bundle suitable for schema analysis and future Diffasaurus normalization.

    Sources:
      - Modern configurationPolicies (+ settings, direct configurationSettings definitions, assignments)
      - Classic deviceConfigurations (+ assignments)
      - Windows groupPolicyConfigurations / ADMX (+ definitionValues, presentationValues)

    Output contract (Phase 0):
      <OutputFolder>/Intune_ConfigurationPolicies_<timestamp>.csv          (anchor)
      <OutputFolder>/Intune_ConfigurationPolicies_<timestamp>/              (bundle)
        snapshot_manifest.json, inventory.csv, assignment_filters.json,
        retrieval_diagnostics.json, platform/source JSON files

.NOTES
    Requires Microsoft.Graph.Authentication and delegated scope:
      DeviceManagementConfiguration.Read.All
#>

[CmdletBinding()]
param(
    [string]$OutputFolder = $env:REPORTS_DIR,
    [switch]$SkipClassic,
    [switch]$SkipAdministrativeTemplates
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($OutputFolder)) {
    throw "REPORTS_DIR environment variable is not set and OutputFolder was not provided."
}

# --- Snapshot identity (single immutable capture) ---
$snapshotSchemaVersion = 1
$policyExportSchemaVersion = 4
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
$snapshotId = "Intune_ConfigurationPolicies_$timestamp"
$bundleName = $snapshotId
$requiredGraphScope = "DeviceManagementConfiguration.Read.All"

$bundleRoot = Join-Path $OutputFolder $bundleName
$anchorCsvPath = Join-Path $OutputFolder "$snapshotId.csv"

$folderMap = @{
    WindowsModern              = "Windows/Modern"
    WindowsClassic             = "Windows/Classic"
    WindowsADMX                = "Windows/AdministrativeTemplates"
    macOSModern                = "macOS/Modern"
    macOSClassic               = "macOS/Classic"
    iOSModern                  = "iOS-iPadOS/Modern"
    iOSClassic                 = "iOS-iPadOS/Classic"
    AndroidModern              = "Android/Modern"
    AndroidClassic             = "Android/Classic"
    OtherModern                = "Other/Modern"
    OtherClassic               = "Other/Classic"
}

foreach ($relative in $folderMap.Values) {
    New-Item -ItemType Directory -Path (Join-Path $bundleRoot $relative) -Force | Out-Null
}

if (-not (Get-Module -ListAvailable -Name Microsoft.Graph.Authentication)) {
    throw "Microsoft.Graph.Authentication is not installed."
}

Import-Module Microsoft.Graph.Authentication

$exportStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$script:GraphRequestCount = 0
$script:BatchHttpRequestCount = 0
$script:BatchItemCount = 0
$script:BatchRequestCount = 0
$script:SettingDefinitionRequestCount = 0
$script:SettingDefinitionsFound = 0
$script:SettingDefinitionsMissing = 0
$script:PresentationValueRequestCount = 0
$script:DefinitionCache = @{}
$script:DefinitionFailedIds = [System.Collections.Generic.HashSet[string]]::new()
$script:Diagnostics = [System.Collections.Generic.List[object]]::new()
$script:Inventory = [System.Collections.Generic.List[object]]::new()
$script:ExportIntegrityErrors = [System.Collections.Generic.List[string]]::new()
$script:PolicyLocalDefinitionReferences = 0
$script:UniqueDefinitionIdsRequired = [System.Collections.Generic.HashSet[string]]::new()
$script:PolicyLocalDefinitionsResolved = 0
$script:PolicyLocalDefinitionsMissing = 0

$script:InventoryColumnOrder = @(
    "SnapshotId",
    "CapturedAtUtc",
    "Platform",
    "PolicyType",
    "Source",
    "PolicyName",
    "Description",
    "PolicyId",
    "ODataType",
    "PlatformsRaw",
    "Technologies",
    "TemplateFamily",
    "TemplateDisplayName",
    "TemplateDisplayVersion",
    "SettingCount",
    "RetrievedSettingCount",
    "AssignmentCount",
    "AssignmentTargets",
    "IsAssigned",
    "RoleScopeTagIds",
    "CreatedDateTime",
    "LastModifiedDateTime",
    "Version",
    "JsonRelativePath",
    "RetrievalStatus",
    "SettingsRetrievalStatus",
    "AssignmentsRetrievalStatus",
    "DefinitionsRetrievalStatus"
)

function Add-GraphRequestCount {
    param([int]$Count = 1)
    $script:GraphRequestCount += $Count
}

function New-RetrievalComponent {
    param(
        [Parameter(Mandatory)]
        [ValidateSet("success", "partial", "error", "not_applicable", "skipped")]
        [string]$Status,

        [int]$Count = 0,
        [string]$ErrorMessage = $null
    )

    return [ordered]@{
        status = $Status
        count  = $Count
        error  = $ErrorMessage
    }
}

function Get-SanitizedGraphError {
    param($Exception)

    if ($null -eq $Exception) {
        return "Unknown Graph error"
    }

    $message = [string]$Exception.Message
    if ($message.Length -gt 240) {
        $message = $message.Substring(0, 240)
    }

    return ($message -replace '(?i)(bearer|token|authorization)[^\s]*', '[redacted]')
}

function Add-RetrievalDiagnostic {
    param(
        [string]$Source,
        [string]$PolicyId,
        [string]$Component,
        [string]$Status,
        [string]$ErrorMessage = "",
        [string]$HttpCategory = ""
    )

    $maskedId = if ($PolicyId) { "$($PolicyId.Substring(0, [Math]::Min(8, $PolicyId.Length)))..." } else { "" }

    $script:Diagnostics.Add([ordered]@{
        source       = $Source
        policyId     = $maskedId
        component    = $Component
        status       = $Status
        httpCategory = $HttpCategory
        error        = $ErrorMessage
    })
}

function Invoke-GraphPagedGet {
    param(
        [Parameter(Mandatory)]
        [string]$Uri
    )

    $results = @()
    $next = $Uri

    while ($next) {
        Add-GraphRequestCount
        $response = Invoke-MgGraphRequest -Method GET -Uri $next -OutputType PSObject

        if ($null -ne $response.value) {
            $results += @($response.value)
            $next = $response.'@odata.nextLink'
        }
        else {
            return @($response)
        }
    }

    return $results
}

function Invoke-GraphBatchGet {
    param(
        [Parameter(Mandatory)]
        [array]$RelativeUrls,

        [ValidateSet("v1.0", "beta")]
        [string]$ApiVersion = "v1.0"
    )

    $results = @{}
    $chunkSize = 20

    for ($offset = 0; $offset -lt $RelativeUrls.Count; $offset += $chunkSize) {
        $chunk = $RelativeUrls[$offset..([Math]::Min($offset + $chunkSize - 1, $RelativeUrls.Count - 1))]
        $requests = @()
        $index = 1

        foreach ($item in $chunk) {
            $requests += @{
                id     = "$index"
                method = "GET"
                url    = $item.url
            }
            $index++
        }

        $body = @{ requests = $requests } | ConvertTo-Json -Depth 8
        Add-GraphRequestCount
        $script:BatchHttpRequestCount++
        $script:BatchItemCount += $chunk.Count
        $script:BatchRequestCount += $chunk.Count

        $batchResponse = Invoke-MgGraphRequest `
            -Method POST `
            -Uri "https://graph.microsoft.com/$ApiVersion/`$batch" `
            -Body $body `
            -ContentType "application/json"

        foreach ($response in $batchResponse.responses) {
            $key = $chunk[[int]$response.id - 1].key
            $results[$key] = [ordered]@{
                status = $response.status
                body   = $response.body
            }
        }

        Start-Sleep -Milliseconds 200
    }

    return $results
}

function Get-SafeFileName {
    param([Parameter(Mandatory)][string]$Name)

    $safe = $Name
    foreach ($c in [System.IO.Path]::GetInvalidFileNameChars()) {
        $safe = $safe.Replace([string]$c, "_")
    }

    $safe = $safe -replace '[:/\\?*"<>|]', '_'
    $safe = $safe.Trim()

    if ([string]::IsNullOrWhiteSpace($safe)) {
        return "UnnamedPolicy"
    }

    if ($safe.Length -gt 120) {
        $safe = $safe.Substring(0, 120)
    }

    return $safe
}

function Get-ModernPlatform {
    param($Policy)

    $platform = [string]$Policy.platforms

    if ($platform -match '(?i)windows10|windows10X') { return "Windows" }
    if ($platform -match '(?i)macOS') { return "macOS" }
    if ($platform -match '(?i)(^|[,; ])iOS($|[,; ])') { return "iOS/iPadOS" }
    if ($platform -match '(?i)androidEnterprise|aosp|android') { return "Android" }
    return "Other"
}

function Get-ClassicPlatform {
    param($Policy)

    $odataType = [string]$Policy.'@odata.type'
    if ($odataType -match '(?i)ios|ipad') { return "iOS/iPadOS" }
    if ($odataType -match '(?i)macos') { return "macOS" }
    if ($odataType -match '(?i)android|aosp') { return "Android" }
    if ($odataType -match '(?i)windows|editionUpgrade|sharedPC|deliveryOptimization|networkBoundary') { return "Windows" }
    return "Other"
}

function Get-ModernPolicyType {
    param($Policy)

    $templateDisplayName = Get-TemplateReferenceValue -Policy $Policy -PropertyName "templateDisplayName"
    $templateFamily = Get-TemplateReferenceValue -Policy $Policy -PropertyName "templateFamily"
    $technology = [string]$Policy.technologies

    if (-not [string]::IsNullOrWhiteSpace($templateDisplayName)) {
        return $templateDisplayName
    }

    if ($templateFamily -match '(?i)settingsCatalog') {
        return "Settings catalog"
    }

    if ($technology -match '(?i)mdm|appleRemoteManagement|android') {
        return "Settings catalog / Modern"
    }

    return "Modern configuration policy"
}

function Get-ClassicPolicyType {
    param($Policy)

    $odataType = [string]$Policy.'@odata.type'

    switch -Regex ($odataType) {
        '(?i)wifi' { return "Wi-Fi" }
        '(?i)eas.*email|email.*profile|email' { return "Email" }
        '(?i)vpn' { return "VPN" }
        '(?i)devicefeatures' { return "Device features" }
        '(?i)generaldeviceconfiguration|generalconfiguration' { return "Device restrictions" }
        '(?i)customconfiguration|custom' { return "Custom" }
        '(?i)trustedrootcertificate' { return "Trusted certificate" }
        '(?i)pkcs' { return "PKCS certificate" }
        '(?i)scep' { return "SCEP certificate" }
        '(?i)derivedcredential' { return "Derived credential" }
        '(?i)certificate' { return "Certificate" }
        '(?i)editionupgrade' { return "Edition upgrade" }
        '(?i)sharedpc' { return "Shared PC" }
        '(?i)deliveryoptimization' { return "Delivery Optimization" }
        default {
            $clean = $odataType -replace '^#microsoft\.graph\.', ''
            if ([string]::IsNullOrWhiteSpace($clean)) {
                return "Classic device configuration"
            }
            return $clean
        }
    }
}

function Get-PolicyFolderRelative {
    param(
        [Parameter(Mandatory)][string]$Platform,
        [Parameter(Mandatory)][ValidateSet("Modern", "Classic", "AdministrativeTemplate")]
        [string]$Source
    )

    if ($Source -eq "AdministrativeTemplate") {
        return $folderMap.WindowsADMX
    }

    $platformKey = switch ($Platform) {
        "Windows" { if ($Source -eq "Modern") { "WindowsModern" } else { "WindowsClassic" } }
        "macOS" { if ($Source -eq "Modern") { "macOSModern" } else { "macOSClassic" } }
        "iOS/iPadOS" { if ($Source -eq "Modern") { "iOSModern" } else { "iOSClassic" } }
        "Android" { if ($Source -eq "Modern") { "AndroidModern" } else { "AndroidClassic" } }
        default { if ($Source -eq "Modern") { "OtherModern" } else { "OtherClassic" } }
    }

    return $folderMap[$platformKey]
}

function Save-PolicyJson {
    param(
        [Parameter(Mandatory)]$Object,
        [Parameter(Mandatory)][string]$RelativeFolder,
        [Parameter(Mandatory)][string]$PolicyName,
        [Parameter(Mandatory)][string]$PolicyId
    )

    $safeName = Get-SafeFileName -Name $PolicyName
    $fileName = "$safeName`__$PolicyId.json"
    $relativePath = "$RelativeFolder/$fileName"
    $fullPath = Join-Path $bundleRoot $relativePath

    $Object | ConvertTo-Json -Depth 100 | Set-Content -Path $fullPath -Encoding utf8
    return $relativePath
}

function Get-AssignmentTargetSummary {
    param([array]$Assignments)

    if (-not $Assignments -or $Assignments.Count -eq 0) {
        return ""
    }

    $targets = foreach ($assignment in $Assignments) {
        $target = $assignment.target
        if ($null -ne $target) {
            $type = [string]$target.'@odata.type'
            $groupId = [string]$target.groupId
            $filterType = [string]$target.deviceAndAppManagementAssignmentFilterType
            $filterId = [string]$target.deviceAndAppManagementAssignmentFilterId

            $summary = $type -replace '^#microsoft\.graph\.', ''
            if ($groupId) { $summary += ":$groupId" }
            if ($filterId) { $summary += " [Filter=$filterType/$filterId]" }
            $summary
        }
    }

    return ($targets -join "; ")
}

function Get-AggregateRetrievalStatus {
    param([array]$Components)

    $statuses = @($Components | ForEach-Object { $_.status } | Where-Object { $_ -and $_ -ne "not_applicable" })

    if ($statuses.Count -eq 0) { return "success" }
    if ($statuses -contains "error" -and ($statuses -contains "success" -or $statuses -contains "partial")) { return "partial" }
    if ($statuses -contains "error") { return "error" }
    if ($statuses -contains "partial") { return "partial" }
    return "success"
}

function Invoke-RetrievedPagedCollection {
    param(
        [Parameter(Mandatory)][string]$Uri,
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$PolicyId,
        [Parameter(Mandatory)][string]$Component
    )

    try {
        $data = @(Invoke-GraphPagedGet -Uri $Uri)
        return @{
            data      = $data
            retrieval = (New-RetrievalComponent -Status "success" -Count $data.Count)
        }
    }
    catch {
        $errorMessage = Get-SanitizedGraphError -Exception $_.Exception
        Add-RetrievalDiagnostic -Source $Source -PolicyId $PolicyId -Component $Component -Status "error" -ErrorMessage $errorMessage
        return @{
            data      = @()
            retrieval = (New-RetrievalComponent -Status "error" -Count 0 -ErrorMessage $errorMessage)
        }
    }
}

function Get-GraphNodeProperty {
    param(
        [object]$Node,
        [Parameter(Mandatory)][string]$Name
    )

    if ($null -eq $Node) {
        return $null
    }

    if ($Node -is [System.Collections.IDictionary]) {
        if ($Node.Contains($Name)) {
            return $Node[$Name]
        }
        return $null
    }

    return $Node.$Name
}

function Test-IsSettingInstanceNode {
    param([object]$Node)

    $odataType = [string](Get-GraphNodeProperty -Node $Node -Name '@odata.type')
    return ($odataType -like '*deviceManagementConfiguration*' -and $odataType.EndsWith('Instance'))
}

function Test-IsWalkableGraphNode {
    param([object]$Node)

    if ($null -eq $Node) {
        return $false
    }

    if ($Node -is [string] -or $Node -is [char]) {
        return $false
    }

    if ($Node.GetType().IsValueType) {
        return $false
    }

    if ($Node -is [System.Array]) {
        return $true
    }

    if ($Node -is [System.Collections.IDictionary] -or $Node -is [PSCustomObject]) {
        return $true
    }

    if ($Node -is [System.Collections.IEnumerable]) {
        return $true
    }

    return $false
}

function Invoke-ModernSettingGraphWalk {
    param(
        [Parameter(Mandatory)][array]$Settings,
        [Parameter(Mandatory)][scriptblock]$OnInstanceNode
    )

    $visited = @{}

    function Walk-GraphNode {
        param([object]$Node)

        if (-not (Test-IsWalkableGraphNode -Node $Node)) {
            return
        }

        if ($Node -is [System.Array]) {
            foreach ($item in $Node) {
                Walk-GraphNode -Node $item
            }
            return
        }

        if ($Node -is [System.Collections.IDictionary] -or $Node -is [PSCustomObject]) {
            if ($visited.ContainsKey($Node)) {
                return
            }
            $visited[$Node] = $true

            if (Test-IsSettingInstanceNode -Node $Node) {
                & $OnInstanceNode $Node
            }

            if ($Node -is [System.Collections.IDictionary]) {
                foreach ($entry in $Node.GetEnumerator()) {
                    Walk-GraphNode -Node $entry.Value
                }
            }
            else {
                foreach ($prop in $Node.PSObject.Properties) {
                    Walk-GraphNode -Node $prop.Value
                }
            }
            return
        }

        if ($Node -is [System.Collections.IEnumerable] -and $Node -isnot [string]) {
            foreach ($item in @($Node)) {
                Walk-GraphNode -Node $item
            }
        }
    }

    foreach ($setting in @($Settings)) {
        Walk-GraphNode -Node $setting
    }
}

function Get-RecursiveSettingDefinitionIds {
    param(
        [Parameter(Mandatory)]
        [array]$Settings
    )

    $definitionIdSet = [System.Collections.Generic.HashSet[string]]::new()

    Invoke-ModernSettingGraphWalk -Settings $Settings -OnInstanceNode {
        param([object]$InstanceNode)

        $definitionId = [string](Get-GraphNodeProperty -Node $InstanceNode -Name 'settingDefinitionId')
        if (-not [string]::IsNullOrWhiteSpace($definitionId)) {
            [void]$definitionIdSet.Add($definitionId)
        }
    }

    return [string[]]@($definitionIdSet)
}

function Get-ModernSettingStructuralMetrics {
    param(
        [Parameter(Mandatory)][array]$Settings,
        [AllowNull()][System.Collections.IDictionary]$DefinitionMap
    )

    $state = @{
        instanceReferences   = 0
        resolvedReferences   = 0
        missingReferences    = 0
    }
    $uniqueIds = [System.Collections.Generic.HashSet[string]]::new()
    $definitionKeys = @{}
    if ($null -ne $DefinitionMap) {
        foreach ($key in @($DefinitionMap.Keys)) {
            $definitionKeys[[string]$key] = $true
        }
    }

    Invoke-ModernSettingGraphWalk -Settings $Settings -OnInstanceNode {
        param([object]$InstanceNode)

        $state.instanceReferences++
        $definitionId = [string](Get-GraphNodeProperty -Node $InstanceNode -Name 'settingDefinitionId')
        if ([string]::IsNullOrWhiteSpace($definitionId)) {
            return
        }

        [void]$uniqueIds.Add($definitionId)
        if ($definitionKeys.ContainsKey($definitionId)) {
            $state.resolvedReferences++
        }
        else {
            $state.missingReferences++
        }
    }

    return [ordered]@{
        policyLocalDefinitionReferences   = $state.instanceReferences
        uniqueDefinitionIdsRequired       = $uniqueIds.Count
        policyLocalDefinitionsResolved    = $state.resolvedReferences
        policyLocalDefinitionsMissing     = $state.missingReferences
        uniqueDefinitionIds               = @($uniqueIds)
    }
}

function New-SettingDefinitionsRetrieval {
    param(
        [Parameter(Mandatory)][string]$Status,
        [int]$Count = 0,
        [string]$ErrorMessage = $null,
        [int]$RequestedCount = 0,
        [int]$FoundCount = 0,
        [int]$MissingCount = 0
    )

    return [ordered]@{
        status         = $Status
        count          = $Count
        error          = $ErrorMessage
        requestedCount = $RequestedCount
        foundCount     = $FoundCount
        missingCount   = $MissingCount
    }
}

function Add-SourceExportAccountingCheck {
    param(
        [Parameter(Mandatory)][string]$SourceName,
        [int]$ListedCount,
        [int]$ExportedCount,
        [int]$ProcessingErrors
    )

    if ($ListedCount -le 0) {
        return
    }

    if (($ExportedCount + $ProcessingErrors) -ne $ListedCount) {
        [void]$script:ExportIntegrityErrors.Add("${SourceName}_source_accounting_mismatch")
    }
}

function Get-TemplateReferenceValue {
    param(
        [object]$Policy,
        [Parameter(Mandatory)][string]$PropertyName
    )

    $templateReference = Get-GraphNodeProperty -Node $Policy -Name 'templateReference'
    if ($null -eq $templateReference) {
        return ""
    }

    return [string](Get-GraphNodeProperty -Node $templateReference -Name $PropertyName)
}

function Copy-DefinitionToPolicyMap {
    param(
        [Parameter(Mandatory)]
        [System.Collections.Specialized.OrderedDictionary]$PolicyDefinitions,

        [Parameter(Mandatory)]
        [string]$DefinitionId
    )

    if (-not $script:DefinitionCache.ContainsKey($DefinitionId)) {
        return $false
    }

    $cached = $script:DefinitionCache[$DefinitionId]
    if ($null -eq $cached) {
        return $false
    }

    try {
        $PolicyDefinitions[$DefinitionId] = ($cached | ConvertTo-Json -Depth 100 | ConvertFrom-Json)
        return $true
    }
    catch {
        return $false
    }
}

function Resolve-ConfigurationSettingDefinitions {
    param(
        [Parameter(Mandatory)][string[]]$DefinitionIds,
        [Parameter(Mandatory)][string]$PolicyId,
        [Parameter(Mandatory)][string]$Source
    )

    $definitionIdList = @(
        @($DefinitionIds) |
            ForEach-Object { [string]$_ } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )

    $toFetch = [System.Collections.Generic.List[string]]::new()
    foreach ($definitionId in $definitionIdList) {
        if ($script:DefinitionCache.ContainsKey($definitionId)) {
            # Cache hit still counts toward this policy's definition map.
        }
        elseif ($script:DefinitionFailedIds.Contains($definitionId)) {
            # Previously failed lookup; leave missing for this policy.
        }
        else {
            $toFetch.Add($definitionId) | Out-Null
        }
    }

    if ($toFetch.Count -gt 0) {
        $requests = @()
        foreach ($definitionId in $toFetch) {
            $encodedId = [System.Uri]::EscapeDataString($definitionId)
            $requests += @{
                key = $definitionId
                url = "/deviceManagement/configurationSettings/$encodedId"
            }
            $script:SettingDefinitionRequestCount++
        }

        $batchResults = Invoke-GraphBatchGet -RelativeUrls $requests -ApiVersion beta

        foreach ($definitionId in $toFetch) {
            if (-not $batchResults.ContainsKey($definitionId)) {
                [void]$script:DefinitionFailedIds.Add($definitionId)
                $script:SettingDefinitionsMissing++
                Add-RetrievalDiagnostic `
                    -Source "configurationSettings" `
                    -PolicyId $PolicyId `
                    -Component "settingDefinitions" `
                    -Status "error" `
                    -ErrorMessage "Batch response missing definition lookup" `
                    -HttpCategory "missing"
            }
            else {
                $result = $batchResults[$definitionId]
                if ($result.status -ge 200 -and $result.status -lt 300 -and $null -ne $result.body) {
                    $script:DefinitionCache[$definitionId] = $result.body
                    $script:SettingDefinitionsFound++
                }
                else {
                    $errorMessage = "HTTP $($result.status)"
                    if ($result.body.error.message) {
                        $errorMessage = Get-SanitizedGraphError -Exception ([System.Exception]::new([string]$result.body.error.message))
                    }

                    [void]$script:DefinitionFailedIds.Add($definitionId)
                    $script:SettingDefinitionsMissing++
                    Add-RetrievalDiagnostic `
                        -Source "configurationSettings" `
                        -PolicyId $PolicyId `
                        -Component "settingDefinitions" `
                        -Status "error" `
                        -ErrorMessage $errorMessage `
                        -HttpCategory ([string]$result.status)
                }
            }
        }
    }

    $policyDefinitions = [ordered]@{}
    $foundCount = 0
    $missingCount = 0

    foreach ($definitionId in $definitionIdList) {
        if (Copy-DefinitionToPolicyMap -PolicyDefinitions $policyDefinitions -DefinitionId $definitionId) {
            $foundCount++
        }
        else {
            $missingCount++
        }
    }

    $requestedCount = $definitionIdList.Count
    $status = "success"
    if ($missingCount -gt 0 -and $foundCount -gt 0) { $status = "partial" }
    elseif ($missingCount -gt 0) { $status = "error" }

    return @{
        definitions = $policyDefinitions
        retrieval   = (New-SettingDefinitionsRetrieval `
            -Status $status `
            -Count $foundCount `
            -ErrorMessage $(if ($missingCount -gt 0) { "$missingCount setting definition lookups failed" } else { $null }) `
            -RequestedCount $requestedCount `
            -FoundCount $foundCount `
            -MissingCount $missingCount)
    }
}

function Get-ModernPolicySettingDefinitions {
    param(
        [Parameter(Mandatory)][array]$Settings,
        [Parameter(Mandatory)][string]$PolicyId,
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$SettingsRetrievalStatus
    )

    if ($SettingsRetrievalStatus -eq "error") {
        return @{
            definitions = [ordered]@{}
            retrieval   = (New-SettingDefinitionsRetrieval -Status "error" -Count 0 -ErrorMessage "Settings retrieval failed")
        }
    }

    $definitionIds = @(
        Get-RecursiveSettingDefinitionIds -Settings @($Settings)
    )

    if ($definitionIds.Count -eq 0) {
        return @{
            definitions = [ordered]@{}
            retrieval   = (New-SettingDefinitionsRetrieval -Status "success" -Count 0)
        }
    }

    return Resolve-ConfigurationSettingDefinitions `
        -DefinitionIds ([string[]]$definitionIds) `
        -PolicyId $PolicyId `
        -Source $Source
}

function Get-AdmxDefinitionValuesWithPresentations {
    param(
        [Parameter(Mandatory)][string]$PolicyId,
        [Parameter(Mandatory)][array]$DefinitionValues,
        [Parameter(Mandatory)][string]$Source
    )

    if (-not $DefinitionValues -or $DefinitionValues.Count -eq 0) {
        return @{
            values    = @()
            retrieval = (New-RetrievalComponent -Status "success" -Count 0)
        }
    }

    $requests = @()
    foreach ($definitionValue in $DefinitionValues) {
        $definitionValueId = [string]$definitionValue.id
        if ([string]::IsNullOrWhiteSpace($definitionValueId)) { continue }
        $requests += @{
            key = $definitionValueId
            url = "/deviceManagement/groupPolicyConfigurations/$PolicyId/definitionValues/$definitionValueId/presentationValues"
        }
        $script:PresentationValueRequestCount++
    }

    $batchResults = @{}
    if ($requests.Count -gt 0) {
        $batchResults = Invoke-GraphBatchGet -RelativeUrls $requests -ApiVersion beta
    }

    $enriched = @()
    $errorCount = 0
    $successCount = 0
    $presentationTotal = 0

    foreach ($definitionValue in $DefinitionValues) {
        $definitionValueId = [string]$definitionValue.id
        $presentationValues = @()
        $presentationRetrieval = New-RetrievalComponent -Status "success" -Count 0

        if ($definitionValueId -and $batchResults.ContainsKey($definitionValueId)) {
            $result = $batchResults[$definitionValueId]
            if ($result.status -ge 200 -and $result.status -lt 300) {
                if ($null -ne $result.body.value) {
                    $presentationValues = @($result.body.value)
                }
                elseif ($result.body) {
                    $presentationValues = @($result.body)
                }

                $presentationRetrieval = New-RetrievalComponent -Status "success" -Count $presentationValues.Count
                $presentationTotal += $presentationValues.Count
                $successCount++
            }
            else {
                $errorMessage = "HTTP $($result.status)"
                $presentationRetrieval = New-RetrievalComponent -Status "error" -Count 0 -ErrorMessage $errorMessage
                $errorCount++
                Add-RetrievalDiagnostic -Source $Source -PolicyId $PolicyId -Component "presentationValues" -Status "error" -ErrorMessage $errorMessage -HttpCategory ([string]$result.status)
            }
        }

        $item = [ordered]@{}
        foreach ($prop in $definitionValue.PSObject.Properties) {
            $item[$prop.Name] = $prop.Value
        }
        $item.presentationValues = $presentationValues
        $item.presentationRetrieval = $presentationRetrieval
        $enriched += $item
    }

    $status = "success"
    if ($errorCount -gt 0 -and $successCount -gt 0) { $status = "partial" }
    elseif ($errorCount -gt 0) { $status = "error" }

    return @{
        values    = $enriched
        retrieval = (New-RetrievalComponent -Status $status -Count $presentationTotal -ErrorMessage $(if ($errorCount -gt 0) { "$errorCount presentation value requests failed" } else { $null }))
    }
}

function Add-InventoryRow {
    param(
        [Parameter(Mandatory)]$Row
    )

    $script:Inventory.Add($Row)
}

function New-InventoryRow {
    param(
        [string]$Platform,
        [string]$PolicyType,
        [string]$Source,
        [string]$PolicyName,
        [string]$Description,
        [string]$PolicyId,
        [string]$ODataType,
        [string]$PlatformsRaw,
        [string]$Technologies,
        [string]$TemplateFamily,
        [string]$TemplateDisplayName,
        [string]$TemplateDisplayVersion,
        $SettingCount,
        $RetrievedSettingCount,
        [int]$AssignmentCount,
        [string]$AssignmentTargets,
        $IsAssigned,
        [string]$RoleScopeTagIds,
        [string]$CreatedDateTime,
        [string]$LastModifiedDateTime,
        [string]$Version,
        [string]$JsonRelativePath,
        [string]$RetrievalStatus,
        [string]$SettingsRetrievalStatus,
        [string]$AssignmentsRetrievalStatus,
        [string]$DefinitionsRetrievalStatus
    )

    return [ordered]@{
        SnapshotId                   = $snapshotId
        CapturedAtUtc                = $capturedAtUtc
        Platform                     = $Platform
        PolicyType                   = $PolicyType
        Source                       = $Source
        PolicyName                   = $PolicyName
        Description                  = $Description
        PolicyId                     = $PolicyId
        ODataType                    = $ODataType
        PlatformsRaw                 = $PlatformsRaw
        Technologies                 = $Technologies
        TemplateFamily               = $TemplateFamily
        TemplateDisplayName          = $TemplateDisplayName
        TemplateDisplayVersion       = $TemplateDisplayVersion
        SettingCount                 = $SettingCount
        RetrievedSettingCount        = $RetrievedSettingCount
        AssignmentCount              = $AssignmentCount
        AssignmentTargets            = $AssignmentTargets
        IsAssigned                   = $IsAssigned
        RoleScopeTagIds              = $RoleScopeTagIds
        CreatedDateTime              = $CreatedDateTime
        LastModifiedDateTime         = $LastModifiedDateTime
        Version                      = $Version
        JsonRelativePath             = $JsonRelativePath
        RetrievalStatus              = $RetrievalStatus
        SettingsRetrievalStatus      = $SettingsRetrievalStatus
        AssignmentsRetrievalStatus   = $AssignmentsRetrievalStatus
        DefinitionsRetrievalStatus   = $DefinitionsRetrievalStatus
    }
}

function ConvertTo-InventoryRecord {
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Row
    )

    $props = [ordered]@{}
    foreach ($column in $script:InventoryColumnOrder) {
        $value = $Row[$column]
        if ($null -eq $value) {
            $props[$column] = ""
        }
        else {
            $props[$column] = [string]$value
        }
    }

    return [pscustomobject]$props
}

function Export-InventoryCsv {
    param(
        [Parameter(Mandatory)][string]$Path,
        [AllowNull()][array]$Rows
    )

    $directory = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    if ($null -eq $Rows) {
        $Rows = @()
    }

    $records = [System.Collections.Generic.List[object]]::new()
    foreach ($row in $Rows) {
        if ($null -eq $row) {
            continue
        }

        if ($row -is [System.Collections.IDictionary]) {
            $records.Add((ConvertTo-InventoryRecord -Row $row)) | Out-Null
        }
        else {
            $records.Add($row) | Out-Null
        }
    }

    if ($records.Count -eq 0) {
        $headerLine = ($script:InventoryColumnOrder -join ",") + "`r`n"
        [System.IO.File]::WriteAllText($Path, $headerLine, [System.Text.UTF8Encoding]::new($false))
        return
    }

    $records | Export-Csv -Path $Path -NoTypeInformation -Encoding utf8
}

# --- Connect ---
Write-Host "Connecting to Microsoft Graph..." -ForegroundColor Cyan
Connect-MgGraph -Scopes $requiredGraphScope -NoWelcome
Add-GraphRequestCount

$context = Get-MgContext
if (-not $context) {
    throw "Microsoft Graph connection could not be established."
}

Write-Host "Connected." -ForegroundColor Green

$sourceCoverage = [ordered]@{}
$retrievalSummary = [ordered]@{
    policiesComplete           = 0
    policiesPartial            = 0
    policiesError              = 0
    assignmentRetrievalErrors  = 0
    settingsRetrievalErrors    = 0
    definitionRetrievalErrors  = 0
    presentationRetrievalErrors = 0
}

# --- Assignment filters (once per bundle) ---
Write-Host ""
Write-Host "=== Assignment Filters ===" -ForegroundColor Cyan

$assignmentFiltersArtifact = [ordered]@{
    snapshotSchemaVersion     = $snapshotSchemaVersion
    policyExportSchemaVersion = $policyExportSchemaVersion
    snapshotId                = $snapshotId
    capturedAtUtc             = $capturedAtUtc
    retrieval                 = (New-RetrievalComponent -Status "success" -Count 0)
    assignmentFilters         = @()
}

try {
    $filters = @(Invoke-GraphPagedGet -Uri "https://graph.microsoft.com/beta/deviceManagement/assignmentFilters")
    $assignmentFiltersArtifact.retrieval = New-RetrievalComponent -Status "success" -Count $filters.Count
    $assignmentFiltersArtifact.assignmentFilters = $filters
    $sourceCoverage.assignmentFilters = [ordered]@{ status = "success"; count = $filters.Count; error = $null }
}
catch {
    $errorMessage = Get-SanitizedGraphError -Exception $_.Exception
    $assignmentFiltersArtifact.retrieval = New-RetrievalComponent -Status "error" -Count 0 -ErrorMessage $errorMessage
    $sourceCoverage.assignmentFilters = [ordered]@{ status = "error"; count = 0; error = $errorMessage }
    Add-RetrievalDiagnostic -Source "assignmentFilters" -PolicyId "" -Component "assignmentFilters" -Status "error" -ErrorMessage $errorMessage
}

$assignmentFiltersPath = Join-Path $bundleRoot "assignment_filters.json"
$assignmentFiltersArtifact | ConvertTo-Json -Depth 100 | Set-Content -Path $assignmentFiltersPath -Encoding utf8

# --- Modern policies ---
Write-Host ""
Write-Host "=== Modern Configuration Policies ===" -ForegroundColor Cyan

$modernCoverage = [ordered]@{
    status           = "success"
    count            = 0
    exportedCount    = 0
    processingErrors = 0
    error            = $null
}
$modernPolicies = @()

try {
    $modernPolicies = @(Invoke-GraphPagedGet -Uri "https://graph.microsoft.com/beta/deviceManagement/configurationPolicies")
    $modernCoverage.count = $modernPolicies.Count
    Write-Host "Found $($modernPolicies.Count) modern policies."
}
catch {
    $errorMessage = Get-SanitizedGraphError -Exception $_.Exception
    $modernCoverage.status = "error"
    $modernCoverage.error = $errorMessage
    Add-RetrievalDiagnostic -Source "configurationPolicies" -PolicyId "" -Component "list" -Status "error" -ErrorMessage $errorMessage
}

$modernIndex = 0
foreach ($policySummary in $modernPolicies) {
    $modernIndex++
    $policyId = [string]$policySummary.id

    try {
        $policyDetailRetrieval = New-RetrievalComponent -Status "success" -Count 1
        $policy = $policySummary

        try {
            Add-GraphRequestCount
            $policy = Invoke-MgGraphRequest `
                -Method GET `
                -Uri "https://graph.microsoft.com/beta/deviceManagement/configurationPolicies/$policyId" `
                -OutputType PSObject
        }
        catch {
            $errorMessage = Get-SanitizedGraphError -Exception $_.Exception
            $policyDetailRetrieval = New-RetrievalComponent -Status "error" -Count 0 -ErrorMessage $errorMessage
            Add-RetrievalDiagnostic -Source "configurationPolicies" -PolicyId $policyId -Component "policyDetail" -Status "error" -ErrorMessage $errorMessage
        }

        $platform = Get-ModernPlatform -Policy $policy
        $policyType = Get-ModernPolicyType -Policy $policy
        Write-Host "[$modernIndex/$($modernPolicies.Count)] $platform | $policyType"

        $settingsResult = Invoke-RetrievedPagedCollection `
            -Uri "https://graph.microsoft.com/beta/deviceManagement/configurationPolicies/$policyId/settings" `
            -Source "configurationPolicies" `
            -PolicyId $policyId `
            -Component "settings"

        $assignmentsResult = Invoke-RetrievedPagedCollection `
            -Uri "https://graph.microsoft.com/beta/deviceManagement/configurationPolicies/$policyId/assignments" `
            -Source "configurationPolicies" `
            -PolicyId $policyId `
            -Component "assignments"

        $definitionsResult = Get-ModernPolicySettingDefinitions `
            -Settings $settingsResult.data `
            -PolicyId $policyId `
            -Source "configurationPolicies" `
            -SettingsRetrievalStatus $settingsResult.retrieval.status

        $structuralMetrics = Get-ModernSettingStructuralMetrics `
            -Settings @($settingsResult.data) `
            -DefinitionMap $definitionsResult.definitions
        $script:PolicyLocalDefinitionReferences += $structuralMetrics.policyLocalDefinitionReferences
        $script:PolicyLocalDefinitionsResolved += $structuralMetrics.policyLocalDefinitionsResolved
        $script:PolicyLocalDefinitionsMissing += $structuralMetrics.policyLocalDefinitionsMissing
        foreach ($requiredDefinitionId in $structuralMetrics.uniqueDefinitionIds) {
            [void]$script:UniqueDefinitionIdsRequired.Add([string]$requiredDefinitionId)
        }

        $retrieval = [ordered]@{
            policyDetail       = $policyDetailRetrieval
            settings           = $settingsResult.retrieval
            assignments        = $assignmentsResult.retrieval
            settingDefinitions = $definitionsResult.retrieval
        }

        $overallStatus = Get-AggregateRetrievalStatus -Components @(
            $policyDetailRetrieval,
            $settingsResult.retrieval,
            $assignmentsResult.retrieval,
            $definitionsResult.retrieval
        )

        switch ($overallStatus) {
            "success" { $retrievalSummary.policiesComplete++ }
            "partial" { $retrievalSummary.policiesPartial++ }
            "error"   { $retrievalSummary.policiesError++ }
        }

        if ($settingsResult.retrieval.status -eq "error") { $retrievalSummary.settingsRetrievalErrors++ }
        if ($assignmentsResult.retrieval.status -eq "error") { $retrievalSummary.assignmentRetrievalErrors++ }
        if ($definitionsResult.retrieval.status -in @("error", "partial")) { $retrievalSummary.definitionRetrievalErrors++ }

        $exportObject = [ordered]@{
            policyExportSchemaVersion = $policyExportSchemaVersion
            snapshotId                = $snapshotId
            capturedAtUtc             = $capturedAtUtc
            exportSource              = "configurationPolicies"
            platform                  = $platform
            policyType                = $policyType
            retrieval                 = $retrieval
            policy                    = $policy
            settings                  = $settingsResult.data
            settingDefinitions        = $definitionsResult.definitions
            assignments               = $assignmentsResult.data
        }

        $relativeFolder = Get-PolicyFolderRelative -Platform $platform -Source "Modern"
        $policyName = [string]$policy.name
        if ([string]::IsNullOrWhiteSpace($policyName)) { $policyName = "UnnamedPolicy" }

        $jsonRelativePath = Save-PolicyJson `
            -Object $exportObject `
            -RelativeFolder $relativeFolder `
            -PolicyName $policyName `
            -PolicyId $policyId

        Add-InventoryRow -Row (New-InventoryRow `
            -Platform $platform `
            -PolicyType $policyType `
            -Source "Modern" `
            -PolicyName $policyName `
            -Description ([string]$policy.description) `
            -PolicyId $policyId `
            -ODataType ([string]$policy.'@odata.type') `
            -PlatformsRaw ([string]$policy.platforms) `
            -Technologies ([string]$policy.technologies) `
            -TemplateFamily (Get-TemplateReferenceValue -Policy $policy -PropertyName "templateFamily") `
            -TemplateDisplayName (Get-TemplateReferenceValue -Policy $policy -PropertyName "templateDisplayName") `
            -TemplateDisplayVersion (Get-TemplateReferenceValue -Policy $policy -PropertyName "templateDisplayVersion") `
            -SettingCount $policy.settingCount `
            -RetrievedSettingCount $settingsResult.data.Count `
            -AssignmentCount $assignmentsResult.data.Count `
            -AssignmentTargets (Get-AssignmentTargetSummary -Assignments $assignmentsResult.data) `
            -IsAssigned $policy.isAssigned `
            -RoleScopeTagIds (($policy.roleScopeTagIds -join ";")) `
            -CreatedDateTime ([string]$policy.createdDateTime) `
            -LastModifiedDateTime ([string]$policy.lastModifiedDateTime) `
            -Version "" `
            -JsonRelativePath $jsonRelativePath `
            -RetrievalStatus $overallStatus `
            -SettingsRetrievalStatus $settingsResult.retrieval.status `
            -AssignmentsRetrievalStatus $assignmentsResult.retrieval.status `
            -DefinitionsRetrievalStatus $definitionsResult.retrieval.status)

        $modernCoverage.exportedCount++
    }
    catch {
        $modernCoverage.processingErrors++
        $errorMessage = Get-SanitizedGraphError -Exception $_.Exception
        Add-RetrievalDiagnostic -Source "configurationPolicies" -PolicyId $policyId -Component "policyProcessing" -Status "error" -ErrorMessage $errorMessage
        Write-Host "  Policy processing failed." -ForegroundColor Yellow
    }
}

$sourceCoverage.modern = $modernCoverage

# --- Classic policies ---
if (-not $SkipClassic) {
    Write-Host ""
    Write-Host "=== Classic Device Configurations ===" -ForegroundColor Cyan

    $classicCoverage = [ordered]@{
        status           = "success"
        count            = 0
        exportedCount    = 0
        processingErrors = 0
        error            = $null
    }
    $classicPolicies = @()

    try {
        $classicPolicies = @(Invoke-GraphPagedGet -Uri "https://graph.microsoft.com/beta/deviceManagement/deviceConfigurations")
        $classicCoverage.count = $classicPolicies.Count
        Write-Host "Found $($classicPolicies.Count) classic policies."
    }
    catch {
        $errorMessage = Get-SanitizedGraphError -Exception $_.Exception
        $classicCoverage.status = "error"
        $classicCoverage.error = $errorMessage
        Add-RetrievalDiagnostic -Source "deviceConfigurations" -PolicyId "" -Component "list" -Status "error" -ErrorMessage $errorMessage
    }

    $classicIndex = 0
    foreach ($policySummary in $classicPolicies) {
        $classicIndex++
        $policyId = [string]$policySummary.id

        try {
            $policy = $policySummary
            $policyDetailRetrieval = New-RetrievalComponent -Status "success" -Count 1

            try {
                Add-GraphRequestCount
                $policy = Invoke-MgGraphRequest `
                    -Method GET `
                    -Uri "https://graph.microsoft.com/beta/deviceManagement/deviceConfigurations/$policyId" `
                    -OutputType PSObject
            }
            catch {
                $errorMessage = Get-SanitizedGraphError -Exception $_.Exception
                $policyDetailRetrieval = New-RetrievalComponent -Status "error" -Count 0 -ErrorMessage $errorMessage
                Add-RetrievalDiagnostic -Source "deviceConfigurations" -PolicyId $policyId -Component "policyDetail" -Status "error" -ErrorMessage $errorMessage
            }

            $platform = Get-ClassicPlatform -Policy $policy
            $policyType = Get-ClassicPolicyType -Policy $policy
            Write-Host "[$classicIndex/$($classicPolicies.Count)] $platform | $policyType"

            $assignmentsResult = Invoke-RetrievedPagedCollection `
                -Uri "https://graph.microsoft.com/beta/deviceManagement/deviceConfigurations/$policyId/assignments" `
                -Source "deviceConfigurations" `
                -PolicyId $policyId `
                -Component "assignments"

            $retrieval = [ordered]@{
                policyDetail = $policyDetailRetrieval
                settings     = (New-RetrievalComponent -Status "not_applicable" -Count 0)
                assignments  = $assignmentsResult.retrieval
                settingDefinitions = (New-RetrievalComponent -Status "not_applicable" -Count 0)
            }

            $overallStatus = Get-AggregateRetrievalStatus -Components @(
                $policyDetailRetrieval,
                $assignmentsResult.retrieval
            )

            switch ($overallStatus) {
                "success" { $retrievalSummary.policiesComplete++ }
                "partial" { $retrievalSummary.policiesPartial++ }
                "error"   { $retrievalSummary.policiesError++ }
            }

            if ($assignmentsResult.retrieval.status -eq "error") { $retrievalSummary.assignmentRetrievalErrors++ }

            $exportObject = [ordered]@{
                policyExportSchemaVersion = $policyExportSchemaVersion
                snapshotId                = $snapshotId
                capturedAtUtc             = $capturedAtUtc
                exportSource              = "deviceConfigurations"
                platform                  = $platform
                policyType                = $policyType
                retrieval                 = $retrieval
                policy                    = $policy
                assignments               = $assignmentsResult.data
            }

            $relativeFolder = Get-PolicyFolderRelative -Platform $platform -Source "Classic"
            $policyName = [string]$policy.displayName
            if ([string]::IsNullOrWhiteSpace($policyName)) { $policyName = "UnnamedPolicy" }

            $jsonRelativePath = Save-PolicyJson `
                -Object $exportObject `
                -RelativeFolder $relativeFolder `
                -PolicyName $policyName `
                -PolicyId $policyId

            Add-InventoryRow -Row (New-InventoryRow `
                -Platform $platform `
                -PolicyType $policyType `
                -Source "Classic" `
                -PolicyName $policyName `
                -Description ([string]$policy.description) `
                -PolicyId $policyId `
                -ODataType ([string]$policy.'@odata.type') `
                -PlatformsRaw "" `
                -Technologies "" `
                -TemplateFamily "" `
                -TemplateDisplayName "" `
                -TemplateDisplayVersion "" `
                -SettingCount "" `
                -RetrievedSettingCount "" `
                -AssignmentCount $assignmentsResult.data.Count `
                -AssignmentTargets (Get-AssignmentTargetSummary -Assignments $assignmentsResult.data) `
                -IsAssigned ($assignmentsResult.data.Count -gt 0) `
                -RoleScopeTagIds (($policy.roleScopeTagIds -join ";")) `
                -CreatedDateTime ([string]$policy.createdDateTime) `
                -LastModifiedDateTime ([string]$policy.lastModifiedDateTime) `
                -Version ([string]$policy.version) `
                -JsonRelativePath $jsonRelativePath `
                -RetrievalStatus $overallStatus `
                -SettingsRetrievalStatus "not_applicable" `
                -AssignmentsRetrievalStatus $assignmentsResult.retrieval.status `
                -DefinitionsRetrievalStatus "not_applicable")

            $classicCoverage.exportedCount++
        }
        catch {
            $classicCoverage.processingErrors++
            $errorMessage = Get-SanitizedGraphError -Exception $_.Exception
            Add-RetrievalDiagnostic -Source "deviceConfigurations" -PolicyId $policyId -Component "policyProcessing" -Status "error" -ErrorMessage $errorMessage
            Write-Host "  Policy processing failed." -ForegroundColor Yellow
        }
    }

    $sourceCoverage.classic = $classicCoverage
}
else {
    $sourceCoverage.classic = [ordered]@{
        status           = "skipped_by_option"
        count            = 0
        exportedCount    = 0
        processingErrors = 0
        error            = $null
    }
    Write-Host ""
    Write-Host "=== Classic Device Configurations skipped ===" -ForegroundColor DarkGray
}

# --- ADMX ---
if (-not $SkipAdministrativeTemplates) {
    Write-Host ""
    Write-Host "=== Administrative Templates / ADMX ===" -ForegroundColor Cyan

    $admxCoverage = [ordered]@{
        status           = "success"
        count            = 0
        exportedCount    = 0
        processingErrors = 0
        error            = $null
    }
    $admxPolicies = @()

    try {
        $admxPolicies = @(Invoke-GraphPagedGet -Uri "https://graph.microsoft.com/beta/deviceManagement/groupPolicyConfigurations")
        $admxCoverage.count = $admxPolicies.Count
        Write-Host "Found $($admxPolicies.Count) ADMX policies."
    }
    catch {
        $errorMessage = Get-SanitizedGraphError -Exception $_.Exception
        $admxCoverage.status = "error"
        $admxCoverage.error = $errorMessage
        Add-RetrievalDiagnostic -Source "groupPolicyConfigurations" -PolicyId "" -Component "list" -Status "error" -ErrorMessage $errorMessage
    }

    $admxIndex = 0
    foreach ($policy in $admxPolicies) {
        $admxIndex++
        $policyId = [string]$policy.id

        try {
            Write-Host "[$admxIndex/$($admxPolicies.Count)] Windows | ADMX"

            $definitionValuesResult = Invoke-RetrievedPagedCollection `
                -Uri "https://graph.microsoft.com/beta/deviceManagement/groupPolicyConfigurations/$policyId/definitionValues?`$expand=definition" `
                -Source "groupPolicyConfigurations" `
                -PolicyId $policyId `
                -Component "definitionValues"

            $presentationResult = Get-AdmxDefinitionValuesWithPresentations `
                -PolicyId $policyId `
                -DefinitionValues $definitionValuesResult.data `
                -Source "groupPolicyConfigurations"

            $assignmentsResult = Invoke-RetrievedPagedCollection `
                -Uri "https://graph.microsoft.com/beta/deviceManagement/groupPolicyConfigurations/$policyId/assignments" `
                -Source "groupPolicyConfigurations" `
                -PolicyId $policyId `
                -Component "assignments"

            $retrieval = [ordered]@{
                policyDetail       = (New-RetrievalComponent -Status "success" -Count 1)
                definitionValues   = $definitionValuesResult.retrieval
                presentationValues = $presentationResult.retrieval
                assignments        = $assignmentsResult.retrieval
                settings           = (New-RetrievalComponent -Status "not_applicable" -Count 0)
                settingDefinitions = (New-RetrievalComponent -Status "not_applicable" -Count 0)
            }

            $overallStatus = Get-AggregateRetrievalStatus -Components @(
                $definitionValuesResult.retrieval,
                $presentationResult.retrieval,
                $assignmentsResult.retrieval
            )

            switch ($overallStatus) {
                "success" { $retrievalSummary.policiesComplete++ }
                "partial" { $retrievalSummary.policiesPartial++ }
                "error"   { $retrievalSummary.policiesError++ }
            }

            if ($assignmentsResult.retrieval.status -eq "error") { $retrievalSummary.assignmentRetrievalErrors++ }
            if ($presentationResult.retrieval.status -in @("error", "partial")) { $retrievalSummary.presentationRetrievalErrors++ }

            $exportObject = [ordered]@{
                policyExportSchemaVersion = $policyExportSchemaVersion
                snapshotId                = $snapshotId
                capturedAtUtc             = $capturedAtUtc
                exportSource              = "groupPolicyConfigurations"
                platform                  = "Windows"
                policyType                = "Administrative Templates / ADMX"
                retrieval                 = $retrieval
                policy                    = $policy
                definitionValues          = $presentationResult.values
                assignments               = $assignmentsResult.data
            }

            $policyName = [string]$policy.displayName
            if ([string]::IsNullOrWhiteSpace($policyName)) { $policyName = "UnnamedPolicy" }

            $jsonRelativePath = Save-PolicyJson `
                -Object $exportObject `
                -RelativeFolder (Get-PolicyFolderRelative -Platform "Windows" -Source "AdministrativeTemplate") `
                -PolicyName $policyName `
                -PolicyId $policyId

            Add-InventoryRow -Row (New-InventoryRow `
                -Platform "Windows" `
                -PolicyType "Administrative Templates / ADMX" `
                -Source "AdministrativeTemplate" `
                -PolicyName $policyName `
                -Description ([string]$policy.description) `
                -PolicyId $policyId `
                -ODataType ([string]$policy.'@odata.type') `
                -PlatformsRaw "" `
                -Technologies "" `
                -TemplateFamily "" `
                -TemplateDisplayName "" `
                -TemplateDisplayVersion "" `
                -SettingCount $definitionValuesResult.data.Count `
                -RetrievedSettingCount $definitionValuesResult.data.Count `
                -AssignmentCount $assignmentsResult.data.Count `
                -AssignmentTargets (Get-AssignmentTargetSummary -Assignments $assignmentsResult.data) `
                -IsAssigned ($assignmentsResult.data.Count -gt 0) `
                -RoleScopeTagIds (($policy.roleScopeTagIds -join ";")) `
                -CreatedDateTime ([string]$policy.createdDateTime) `
                -LastModifiedDateTime ([string]$policy.lastModifiedDateTime) `
                -Version "" `
                -JsonRelativePath $jsonRelativePath `
                -RetrievalStatus $overallStatus `
                -SettingsRetrievalStatus "not_applicable" `
                -AssignmentsRetrievalStatus $assignmentsResult.retrieval.status `
                -DefinitionsRetrievalStatus $presentationResult.retrieval.status)

            $admxCoverage.exportedCount++
        }
        catch {
            $admxCoverage.processingErrors++
            $errorMessage = Get-SanitizedGraphError -Exception $_.Exception
            Add-RetrievalDiagnostic -Source "groupPolicyConfigurations" -PolicyId $policyId -Component "policyProcessing" -Status "error" -ErrorMessage $errorMessage
            Write-Host "  Policy processing failed." -ForegroundColor Yellow
        }
    }

    $sourceCoverage.administrativeTemplates = $admxCoverage
}
else {
    $sourceCoverage.administrativeTemplates = [ordered]@{
        status           = "skipped_by_option"
        count            = 0
        exportedCount    = 0
        processingErrors = 0
        error            = $null
    }
    Write-Host ""
    Write-Host "=== Administrative Templates skipped ===" -ForegroundColor DarkGray
}

# --- Inventory + manifest ---
$sortedInventory = @(
    $script:Inventory |
        Sort-Object Platform, PolicyType, PolicyName, PolicyId
)

$inventoryRelativePath = "inventory.csv"
$inventoryPath = Join-Path $bundleRoot $inventoryRelativePath
Export-InventoryCsv -Path $inventoryPath -Rows $sortedInventory
Export-InventoryCsv -Path $anchorCsvPath -Rows $sortedInventory

$platformCounts = @{}
$sourceCounts = @{}
$policyTypeCounts = @{}

foreach ($row in $sortedInventory) {
    if (-not $platformCounts.ContainsKey($row.Platform)) { $platformCounts[$row.Platform] = 0 }
    $platformCounts[$row.Platform]++

    if (-not $sourceCounts.ContainsKey($row.Source)) { $sourceCounts[$row.Source] = 0 }
    $sourceCounts[$row.Source]++

    $typeKey = "$($row.Platform)|$($row.PolicyType)"
    if (-not $policyTypeCounts.ContainsKey($typeKey)) { $policyTypeCounts[$typeKey] = 0 }
    $policyTypeCounts[$typeKey]++
}

# --- Source export accounting ---
$modernListed = [int]$sourceCoverage.modern.count
$modernExported = [int]$sourceCoverage.modern.exportedCount
$modernProcessingErrors = [int]$sourceCoverage.modern.processingErrors
Add-SourceExportAccountingCheck `
    -SourceName "modern" `
    -ListedCount $modernListed `
    -ExportedCount $modernExported `
    -ProcessingErrors $modernProcessingErrors

if ($sourceCoverage.classic.status -ne "skipped_by_option") {
    $classicListed = [int]$sourceCoverage.classic.count
    $classicExported = [int]$sourceCoverage.classic.exportedCount
    $classicProcessingErrors = [int]$sourceCoverage.classic.processingErrors
    Add-SourceExportAccountingCheck `
        -SourceName "classic" `
        -ListedCount $classicListed `
        -ExportedCount $classicExported `
        -ProcessingErrors $classicProcessingErrors
}

if ($sourceCoverage.administrativeTemplates.status -ne "skipped_by_option") {
    $admxListed = [int]$sourceCoverage.administrativeTemplates.count
    $admxExported = [int]$sourceCoverage.administrativeTemplates.exportedCount
    $admxProcessingErrors = [int]$sourceCoverage.administrativeTemplates.processingErrors
    Add-SourceExportAccountingCheck `
        -SourceName "administrativeTemplates" `
        -ListedCount $admxListed `
        -ExportedCount $admxExported `
        -ProcessingErrors $admxProcessingErrors
}

$exportStatus = "complete"
if ($script:ExportIntegrityErrors.Count -gt 0) {
    $exportStatus = "integrity_error"
}
elseif (
    $modernProcessingErrors -gt 0 -or
    $script:PolicyLocalDefinitionsMissing -gt 0 -or
    (($sourceCoverage.classic.status -ne "skipped_by_option") -and $classicProcessingErrors -gt 0) -or
    (($sourceCoverage.administrativeTemplates.status -ne "skipped_by_option") -and $admxProcessingErrors -gt 0) -or
    $sourceCoverage.modern.status -eq "error" -or
    ($sourceCoverage.classic.status -eq "error") -or
    ($sourceCoverage.administrativeTemplates.status -eq "error")
) {
    $exportStatus = "incomplete"
}

$exportStopwatch.Stop()

$diagnosticsRelativePath = "retrieval_diagnostics.json"
$diagnosticsArtifact = [ordered]@{
    snapshotId                = $snapshotId
    capturedAtUtc             = $capturedAtUtc
    retrievalSummary          = $retrievalSummary
    graphRequestCount         = $script:GraphRequestCount
    batchHttpRequestCount     = $script:BatchHttpRequestCount
    batchItemCount            = $script:BatchItemCount
    batchRequestCount         = $script:BatchItemCount
    settingDefinitionRequests  = $script:SettingDefinitionRequestCount
    settingDefinitionsFound    = $script:SettingDefinitionsFound
    settingDefinitionsMissing  = $script:SettingDefinitionsMissing
    definitionRetrievalErrors  = $retrievalSummary.definitionRetrievalErrors
    presentationValueRequests  = $script:PresentationValueRequestCount
    policyLocalDefinitionReferences = $script:PolicyLocalDefinitionReferences
    uniqueDefinitionIdsRequired   = $script:UniqueDefinitionIdsRequired.Count
    policyLocalDefinitionsResolved = $script:PolicyLocalDefinitionsResolved
    policyLocalDefinitionsMissing  = $script:PolicyLocalDefinitionsMissing
    exportDurationSeconds     = [Math]::Round($exportStopwatch.Elapsed.TotalSeconds, 2)
    entries                   = @($script:Diagnostics)
}

$diagnosticsPath = Join-Path $bundleRoot $diagnosticsRelativePath
$diagnosticsArtifact | ConvertTo-Json -Depth 30 | Set-Content -Path $diagnosticsPath -Encoding utf8

$manifest = [ordered]@{
    snapshotSchemaVersion     = $snapshotSchemaVersion
    policyExportSchemaVersion = $policyExportSchemaVersion
    snapshotId                = $snapshotId
    capturedAtUtc             = $capturedAtUtc
    timestamp                 = $timestamp
    requiredGraphScope        = $requiredGraphScope
    bundleName                = $bundleName
    exportStatus              = $exportStatus
    exportIntegrityErrors     = @($script:ExportIntegrityErrors)
    sourceCoverage            = $sourceCoverage
    policyCount               = $sortedInventory.Count
    platformCounts            = $platformCounts
    sourceCounts              = $sourceCounts
    policyTypeCounts          = $policyTypeCounts
    inventoryRelativePath     = $inventoryRelativePath
    anchorRelativePath        = (Split-Path -Leaf $anchorCsvPath)
    assignmentFiltersRelativePath = "assignment_filters.json"
    diagnosticsRelativePath   = $diagnosticsRelativePath
    retrievalSummary          = $retrievalSummary
    exportDurationSeconds     = [Math]::Round($exportStopwatch.Elapsed.TotalSeconds, 2)
    graphRequestCount         = $script:GraphRequestCount
    batchHttpRequestCount     = $script:BatchHttpRequestCount
    batchItemCount            = $script:BatchItemCount
    batchRequestCount         = $script:BatchItemCount
    settingDefinitionRequests  = $script:SettingDefinitionRequestCount
    settingDefinitionsFound    = $script:SettingDefinitionsFound
    settingDefinitionsMissing  = $script:SettingDefinitionsMissing
    definitionRetrievalErrors  = $retrievalSummary.definitionRetrievalErrors
    presentationValueRequests  = $script:PresentationValueRequestCount
    policyLocalDefinitionReferences = $script:PolicyLocalDefinitionReferences
    uniqueDefinitionIdsRequired   = $script:UniqueDefinitionIdsRequired.Count
    policyLocalDefinitionsResolved = $script:PolicyLocalDefinitionsResolved
    policyLocalDefinitionsMissing  = $script:PolicyLocalDefinitionsMissing
}

$manifestPath = Join-Path $bundleRoot "snapshot_manifest.json"
$manifest | ConvertTo-Json -Depth 30 | Set-Content -Path $manifestPath -Encoding utf8

Write-Host ""
if ($exportStatus -eq "complete") {
    Write-Host "Export complete." -ForegroundColor Green
}
else {
    Write-Host "Export finished with status: $exportStatus" -ForegroundColor Yellow
}
Write-Host "Bundle root : $bundleRoot"
Write-Host "Anchor CSV  : $anchorCsvPath"
Write-Host "Policies    : $($sortedInventory.Count)"
Write-Host "Duration    : $([Math]::Round($exportStopwatch.Elapsed.TotalSeconds, 2))s"
Write-Host "Graph calls : $($script:GraphRequestCount) (batch HTTP: $($script:BatchHttpRequestCount), batch items: $($script:BatchItemCount))"
Write-Host "Done." -ForegroundColor Green
