#Requires -Version 7
param(
    [Parameter(Mandatory)][string]$Depth3SettingJsonPath,
    [Parameter(Mandatory)][string]$RealShapeSettingJsonPath,
    [Parameter(Mandatory)][string]$MixedSettingsJsonPath
)

$ErrorActionPreference = "Stop"

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
$script:TestBatchPrepared = $false
$script:TestBatchRequestCount = 0

$exporterScript = Join-Path $PSScriptRoot ".." ".." "psscripts" "app_INTUNE_ConfigurationPolicy.ps1"
$exporterText = Get-Content -Path $exporterScript -Raw
$functionBlock = ($exporterText -split "# --- Connect ---", 2)[0]
$paramEnd = $functionBlock.IndexOf("function Add-GraphRequestCount")
if ($paramEnd -lt 0) {
    throw "Could not locate exporter function block."
}
$functionBlock = $functionBlock.Substring($paramEnd)
Invoke-Expression $functionBlock

function Invoke-GraphBatchGet {
    param(
        [Parameter(Mandatory)][array]$RelativeUrls,
        [ValidateSet("v1.0", "beta")]
        [string]$ApiVersion = "v1.0"
    )

    $script:TestBatchPrepared = $true
    $script:TestBatchRequestCount = $RelativeUrls.Count
    $results = @{}
    foreach ($item in $RelativeUrls) {
        $definitionId = [string]$item.key
        $results[$definitionId] = [ordered]@{
            status = 200
            body   = [ordered]@{
                id = $definitionId
                '@odata.type' = '#microsoft.graph.deviceManagementConfigurationSimpleSettingDefinition'
            }
        }
        $script:DefinitionCache[$definitionId] = $results[$definitionId].body
        $script:SettingDefinitionsFound++
    }
    return $results
}

function Assert-Count {
    param(
        [string]$Label,
        [int]$Expected,
        [array]$Actual
    )

    if ($Actual.Count -ne $Expected) {
        throw "${Label}: expected count $Expected, got $($Actual.Count) (type=$($Actual.GetType().FullName))"
    }
}

function Assert-Equal {
    param(
        [string]$Label,
        [int]$Expected,
        [int]$Actual
    )

    if ($Actual -ne $Expected) {
        throw "${Label}: expected $Expected, got $Actual"
    }
}

function Assert-ContainsAllIds {
    param(
        [string]$Label,
        [array]$Actual,
        [string[]]$Expected
    )

    $actualSet = [System.Collections.Generic.HashSet[string]]::new([string[]]$Actual)
    foreach ($expectedId in $Expected) {
        if (-not $actualSet.Contains($expectedId)) {
            throw "${Label}: missing expected definition id ${expectedId}"
        }
    }
}

function Get-SettingInstanceCount {
    param([array]$Settings)

    $script:TestInstanceCount = 0
    Invoke-ModernSettingGraphWalk -Settings $Settings -OnInstanceNode {
        param([object]$InstanceNode)
        $script:TestInstanceCount++
    }
    return $script:TestInstanceCount
}

# A. zero recursive settingDefinitionIds (no settingInstance nodes)
$zero = @(Get-RecursiveSettingDefinitionIds -Settings @(
    [pscustomobject]@{
        id              = "setting-empty"
        settingInstance = $null
    }
))
Assert-Count -Label "zero-settings" -Expected 0 -Actual $zero

# B. exactly one recursive settingDefinitionId (scalar-collapse path)
$oneSetting = @(
    [pscustomobject]@{
        id              = "setting-one"
        settingInstance = [pscustomobject]@{
            '@odata.type'         = '#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance'
            settingDefinitionId   = 'def-one'
        }
    }
)
$oneRaw = Get-RecursiveSettingDefinitionIds -Settings $oneSetting
$oneIds = @($oneRaw)
Assert-Count -Label "one-setting-raw" -Expected 1 -Actual $oneIds
$oneAssigned = [string[]]@($oneRaw)
Assert-Count -Label "one-setting-assigned" -Expected 1 -Actual $oneAssigned
if ($oneAssigned[0] -ne "def-one") {
    throw "one-setting-assigned: expected def-one, got $($oneAssigned[0])"
}

# C. multiple recursive settingDefinitionIds
$manySetting = @(
    [pscustomobject]@{
        id              = "setting-a"
        settingInstance = [pscustomobject]@{
            '@odata.type'       = '#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance'
            settingDefinitionId = 'def-a'
        }
    },
    [pscustomobject]@{
        id              = "setting-b"
        settingInstance = [pscustomobject]@{
            '@odata.type'       = '#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance'
            settingDefinitionId = 'def-b'
        }
    }
)
$manyIds = @(
    Get-RecursiveSettingDefinitionIds -Settings $manySetting
)
Assert-Count -Label "many-settings" -Expected 2 -Actual $manyIds

# D. depth-3 nested fixture: exactly 8 instance nodes and 8 definition IDs
$depth3ExpectedIds = @(
    'def-group-0',
    'def-group-1',
    'def-group-2',
    'def-choice-1',
    'def-simple-1',
    'def-simple-2',
    'def-choice-2',
    'def-choice-3'
)
$depth3Setting = Get-Content -Path $Depth3SettingJsonPath -Raw | ConvertFrom-Json
$depth3Ids = @(Get-RecursiveSettingDefinitionIds -Settings @($depth3Setting))
$depth3InstanceCount = Get-SettingInstanceCount -Settings @($depth3Setting)
Assert-Equal -Label "depth3-instance-nodes" -Expected 8 -Actual $depth3InstanceCount
Assert-Count -Label "depth3-definition-ids" -Expected 8 -Actual $depth3Ids
Assert-ContainsAllIds -Label "depth3-definition-ids" -Actual $depth3Ids -Expected $depth3ExpectedIds

# E. real-shape children fixture: inline instances under groupSettingCollectionValue.children
$realShapeExpectedIds = @(
    'def-root',
    'def-g1',
    'def-g2',
    'def-c1',
    'def-s1',
    'def-s2',
    'def-c2',
    'def-c3'
)
$realShapeSetting = Get-Content -Path $RealShapeSettingJsonPath -Raw | ConvertFrom-Json
$realShapeIds = @(Get-RecursiveSettingDefinitionIds -Settings @($realShapeSetting))
$realShapeInstanceCount = Get-SettingInstanceCount -Settings @($realShapeSetting)
Assert-Equal -Label "real-shape-instance-nodes" -Expected 8 -Actual $realShapeInstanceCount
Assert-Count -Label "real-shape-definition-ids" -Expected 8 -Actual $realShapeIds
Assert-ContainsAllIds -Label "real-shape-definition-ids" -Actual $realShapeIds -Expected $realShapeExpectedIds

# F. mixed tree fixture
$mixedSettings = Get-Content -Path $MixedSettingsJsonPath -Raw | ConvertFrom-Json
$mixedExpectedIds = @(
    'def-mixed-simple',
    'def-mixed-choice',
    'def-mixed-choice-child',
    'def-mixed-group',
    'def-mixed-group-child'
)
$mixedIds = @(Get-RecursiveSettingDefinitionIds -Settings @($mixedSettings))
$mixedInstanceCount = Get-SettingInstanceCount -Settings @($mixedSettings)
Assert-Equal -Label "mixed-instance-nodes" -Expected 5 -Actual $mixedInstanceCount
Assert-Count -Label "mixed-definition-ids" -Expected 5 -Actual $mixedIds
Assert-ContainsAllIds -Label "mixed-definition-ids" -Actual $mixedIds -Expected $mixedExpectedIds

$script:TestBatchPrepared = $false
$script:TestBatchRequestCount = 0
$script:SettingDefinitionRequestCount = 0
$script:SettingDefinitionsFound = 0
$script:SettingDefinitionsMissing = 0
$script:DefinitionCache = @{}
$script:DefinitionFailedIds = [System.Collections.Generic.HashSet[string]]::new()

$mixedDefinitionResult = Get-ModernPolicySettingDefinitions `
    -Settings @($mixedSettings) `
    -PolicyId "policy-mixed-001" `
    -Source "configurationPolicies" `
    -SettingsRetrievalStatus "success"

if (-not $script:TestBatchPrepared) {
    throw "mixed-policy: definition batch preparation was not reached"
}
Assert-Equal -Label "mixed-batch-requests" -Expected 5 -Actual $script:TestBatchRequestCount
if ($mixedDefinitionResult.retrieval.requestedCount -ne 5) {
    throw "mixed-policy: expected requestedCount 5, got $($mixedDefinitionResult.retrieval.requestedCount)"
}
if ($mixedDefinitionResult.retrieval.foundCount -ne 5) {
    throw "mixed-policy: expected foundCount 5, got $($mixedDefinitionResult.retrieval.foundCount)"
}
if ($mixedDefinitionResult.retrieval.missingCount -ne 0) {
    throw "mixed-policy: expected missingCount 0, got $($mixedDefinitionResult.retrieval.missingCount)"
}
foreach ($expectedId in $mixedExpectedIds) {
    if (-not $mixedDefinitionResult.definitions.Contains($expectedId)) {
        throw "mixed-policy: definitions map missing ${expectedId}"
    }
}

Write-Output "DEPTH3_COUNT=$($depth3Ids.Count)"
Write-Output "REAL_SHAPE_COUNT=$($realShapeIds.Count)"
Write-Output "MIXED_COUNT=$($mixedIds.Count)"
Write-Output "OK"
