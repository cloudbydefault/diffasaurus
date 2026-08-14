param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

if (Get-Variable -Name PSStyle -Scope Global -ErrorAction SilentlyContinue) {
    $PSStyle.OutputRendering = 'PlainText'
}

# ------------------------------------------------
# Default output path
# ------------------------------------------------
if (-not $OutputPath) {
    $reportsDir = $env:REPORTS_DIR
    if (-not $reportsDir) {
        $projectRoot = Split-Path -Parent $PSScriptRoot
        $reportsDir = Join-Path $projectRoot "reports"
    }

    if (-not (Test-Path $reportsDir)) {
        New-Item -ItemType Directory -Path $reportsDir -Force | Out-Null
    }

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $reportsDir "Entra_Role_Assignments_$timestamp.csv"
}

# ------------------------------------------------
# Connect Graph
# ------------------------------------------------
Connect-MgGraph -Scopes `
"RoleManagement.Read.Directory", `
"Directory.Read.All", `
"Group.Read.All", `
"User.Read.All" `
-NoWelcome

$ctx = Get-MgContext
Write-Host "Connected to tenant: $($ctx.TenantId)" -ForegroundColor Cyan

# ------------------------------------------------
# Helper Functions
# ------------------------------------------------
function Invoke-GraphGetAll {
    param($uri)

    $results = @()
    $next = $uri

    while ($next) {
        $resp = Invoke-MgGraphRequest -Method GET -Uri $next
        $results += $resp.value
        $next = $resp.'@odata.nextLink'
    }

    return $results
}

function Split-Array {
    param([object[]]$Array,[int]$Size)

    for ($i=0; $i -lt $Array.Count; $i+=$Size) {
        $end = [Math]::Min($i+$Size-1,$Array.Count-1)
        ,@($Array[$i..$end])
    }
}

# ------------------------------------------------
# Role Definitions
# ------------------------------------------------
Write-Host "Retrieving role definitions..."

$roleDefs = Invoke-GraphGetAll "/v1.0/roleManagement/directory/roleDefinitions?`$select=id,displayName"

$roleMap = @{}
foreach($r in $roleDefs){
    $roleMap[$r.id] = $r.displayName
}

# ------------------------------------------------
# Role Schedules
# ------------------------------------------------
Write-Host "Retrieving active schedules..."

$activeSchedules = Invoke-GraphGetAll "/v1.0/roleManagement/directory/roleAssignmentSchedules"

Write-Host "Retrieving eligible schedules..."

$eligibleSchedules = Invoke-GraphGetAll "/v1.0/roleManagement/directory/roleEligibilitySchedules"

Write-Host "Active schedules: $($activeSchedules.Count)"
Write-Host "Eligible schedules: $($eligibleSchedules.Count)"

# ------------------------------------------------
# Collect principals
# ------------------------------------------------
$principalIds = @(
    $activeSchedules.principalId
    $eligibleSchedules.principalId
) | Where-Object { $_ } | Sort-Object -Unique

# ------------------------------------------------
# Resolve principals (bulk)
# ------------------------------------------------
Write-Host "Resolving principal details..."

$userMap = @{}
$groupMap = @{}

foreach($chunk in (Split-Array $principalIds 999)) {

    $body = @{
        ids   = @($chunk)
        types = @("user","group")
    } | ConvertTo-Json -Depth 5

    $resp = Invoke-MgGraphRequest `
        -Method POST `
        -Uri "/v1.0/directoryObjects/getByIds" `
        -Body $body `
        -ContentType "application/json"

    foreach($obj in $resp.value) {
        $type = $obj.'@odata.type'

        switch($type) {
            "#microsoft.graph.user"  { $userMap[$obj.id]  = $obj }
            "#microsoft.graph.group" { $groupMap[$obj.id] = $obj }
        }
    }
}

Write-Host "Users loaded: $($userMap.Count)"
Write-Host "Groups loaded: $($groupMap.Count)"

# ------------------------------------------------
# Prepare result container
# ------------------------------------------------
$results = New-Object System.Collections.Generic.List[object]

function AddRow {
    param(
        $User,
        [string]$RoleName,
        [string]$RoleDefinitionId,
        [string]$State,
        [string]$Source,
        [string]$Group,
        [string]$GroupId,
        $Schedule
    )

    $results.Add([pscustomobject]@{
        UserPrincipalName    = $User.userPrincipalName
        DisplayName          = $User.displayName
        Mail                 = $User.mail
        AccountEnabled       = $User.accountEnabled
        RoleName             = $RoleName
        RoleState            = $State
        AssignmentSource     = $Source
        SourceGroup          = $Group
        UserId               = $User.id
        RoleDefinitionId     = $RoleDefinitionId
        AssignmentScheduleId = $Schedule.id
        SourcePrincipalId    = $Schedule.principalId
        SourceGroupId        = $GroupId
        DirectoryScopeId     = $Schedule.directoryScopeId
        AppScopeId           = $Schedule.appScopeId
    })
}

# ------------------------------------------------
# Direct roles
# ------------------------------------------------
Write-Host "Processing direct roles..."

foreach($s in $activeSchedules) {
    if($userMap.ContainsKey($s.principalId)) {
        $user = $userMap[$s.principalId]
        AddRow -User $user -RoleName $roleMap[$s.roleDefinitionId] -RoleDefinitionId $s.roleDefinitionId -State "Active" -Source "Direct" -Group "" -GroupId "" -Schedule $s
    }
}

foreach($s in $eligibleSchedules) {
    if($userMap.ContainsKey($s.principalId)) {
        $user = $userMap[$s.principalId]
        AddRow -User $user -RoleName $roleMap[$s.roleDefinitionId] -RoleDefinitionId $s.roleDefinitionId -State "Eligible" -Source "Direct" -Group "" -GroupId "" -Schedule $s
    }
}

# ------------------------------------------------
# Group roles
# ------------------------------------------------
Write-Host "Expanding role groups..."

foreach($s in $activeSchedules) {
    if($groupMap.ContainsKey($s.principalId)) {
        $group = $groupMap[$s.principalId]

        $members = Invoke-GraphGetAll "/v1.0/groups/$($group.id)/members/microsoft.graph.user?`$select=id,displayName,userPrincipalName,mail,accountEnabled"

        foreach($m in $members) {
            AddRow -User $m -RoleName $roleMap[$s.roleDefinitionId] -RoleDefinitionId $s.roleDefinitionId -State "Active" -Source "Group" -Group $group.displayName -GroupId $group.id -Schedule $s
        }
    }
}

foreach($s in $eligibleSchedules) {
    if($groupMap.ContainsKey($s.principalId)) {
        $group = $groupMap[$s.principalId]

        $members = Invoke-GraphGetAll "/v1.0/groups/$($group.id)/members/microsoft.graph.user?`$select=id,displayName,userPrincipalName,mail,accountEnabled"

        foreach($m in $members) {
            AddRow -User $m -RoleName $roleMap[$s.roleDefinitionId] -RoleDefinitionId $s.roleDefinitionId -State "Eligible" -Source "Group" -Group $group.displayName -GroupId $group.id -Schedule $s
        }
    }
}

# ------------------------------------------------
# Export
# ------------------------------------------------
$results |
    Sort-Object UserPrincipalName, RoleName |
    Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8

Write-Host ""
Write-Host "Report complete"
Write-Host "Rows exported: $($results.Count)"
Write-Host "File: $OutputPath"
