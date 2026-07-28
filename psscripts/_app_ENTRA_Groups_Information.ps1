#Requires -Version 7

<#
Entra - Groups Dependencies Report (API-call friendly + Retry-After aware)

What it gives you (per group):
- Core group properties (type, mail/security, dynamic/unified, created, hybrid flag)
- Friendly group type and membership type
- Teams detection
- Owners: OwnersCount + Owners (names/UPNs joined)          [batched]
- MembersCount                                             [batched]
- Conditional Access dependencies:
  - UsedInConditionalAccess (Yes/No)
  - CA_PoliciesInclude (policy names)
  - CA_PoliciesExclude (policy names)
- Entra role assignments:
  - AssignedRoles
- Enterprise Application dependencies:
  - ReferencedInAppRoles
- Access Package dependencies:
  - ReferencedInAccessPackages

API strategy (throttle-friendly):
1) GET /v1.0/groups?$select=... (paged)
2) GET /v1.0/groups?$filter=resourceProvisioningOptions/Any(x:x eq 'Team') (paged)
3) GET /v1.0/identity/conditionalAccess/policies (paged)
4) GET /v1.0/roleManagement/directory/roleDefinitions + roleAssignments (paged)
5) GET /beta/identityGovernance/entitlementManagement/accessPackages (paged)
6) POST /beta/$batch for:
   - /groups/{id}/owners?$select=displayName,userPrincipalName
   - /groups/{id}/members/$count
   - /groups/{id}/appRoleAssignments?$select=resourceDisplayName,appRoleId,principalDisplayName
   - /identityGovernance/entitlementManagement/accessPackages/{id}?$expand=accessPackageResourceRoleScopes($expand=accessPackageResourceScope)

Permissions (delegated scopes):
- Group.Read.All
- Directory.Read.All
- Policy.Read.All
- RoleManagement.Read.Directory
- Application.Read.All
- EntitlementManagement.Read.All
#>

# ---------------------------
# APP CONFIG
# ---------------------------

$OutDir = $env:REPORTS_DIR

if (-not $OutDir -or $OutDir.Trim() -eq "") {
    throw "REPORTS_DIR environment variable is not set. This script is designed to be launched by the Python app."
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# ---------------------------
# CONFIG
# ---------------------------
$groupsPerBatch        = 10
$maxAttemptsPerRequest = 6
$maxAttemptsPerPage    = 6
$retryAfterBufferSec   = 2
$timestamp             = Get-Date -Format "yyyyMMdd-HHmmss"

# Feature toggles
$includeTeamsInfo         = $true
$includeOwners            = $true
$includeMemberCounts      = $true
$includeCARefs            = $true
$includeRoleAssignments   = $true
$includeAppRoleRefs       = $true
$includeAccessPackageRefs = $true

# Output name -> ALWAYS into app reports folder
$outFile = Join-Path $OutDir "Entra_Groups_Dependencies_${timestamp}.csv"

# ---------------------------
# CONNECT
# ---------------------------
Connect-MgGraph -Scopes `
    "Group.Read.All", `
    "Directory.Read.All", `
    "Policy.Read.All", `
    "RoleManagement.Read.Directory", `
    "Application.Read.All", `
    "EntitlementManagement.Read.All" `
    -ContextScope CurrentUser -NoWelcome

# ---------------------------
# HELPERS
# ---------------------------
function Get-ErrorMessageFromBody {
    param($body)
    try {
        if ($null -ne $body -and $null -ne $body.error -and $null -ne $body.error.message) {
            return [string]$body.error.message
        }
    } catch {}
    return $null
}

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
            $statusCode = $null
            $headers    = $null
            try { $statusCode = [int]$_.Exception.Response.StatusCode } catch {}
            try { $headers    = $_.Exception.Response.Headers } catch {}

            $isTransient = $false
            if ($statusCode -in 429,503,504) { $isTransient = $true }

            if (-not $isTransient -or $attempt -ge $MaxAttempts) { throw }

            $ra = $null
            try { $ra = Get-RetryAfterSeconds -headers $headers } catch {}
            $sleepSec = if ($ra -and $ra -gt 0) { $ra + $RetryAfterBufferSec } else { 5 }

            Write-Host ("Transient GET failure (HTTP {0}) attempt {1}/{2}. Sleeping {3}s..." -f $statusCode, $attempt, $MaxAttempts, $sleepSec) -ForegroundColor Yellow
            Start-Sleep -Seconds $sleepSec
        }
    }
}

function Split-IntoBatches {
    param(
        [Parameter(Mandatory)] [array] $Items,
        [Parameter(Mandatory)] [int] $BatchSize
    )
    $out = [System.Collections.Generic.List[object]]::new()
    for ($i = 0; $i -lt $Items.Count; $i += $BatchSize) {
        $end = [Math]::Min($i + $BatchSize - 1, $Items.Count - 1)
        $out.Add($Items[$i..$end])
    }
    return $out
}

function Join-Arr {
    param($arr, [string]$sep = " ; ")
    if ($null -eq $arr) { return $null }
    $vals = @($arr | Where-Object { $_ -and $_.ToString().Trim() -ne "" })
    if ($vals.Count -eq 0) { return $null }
    return ($vals -join $sep)
}

function Add-ToHashSetMap {
    param(
        [Parameter(Mandatory)] [hashtable] $Map,
        [Parameter(Mandatory)] [string] $Key,
        [Parameter(Mandatory)] [string] $Value
    )

    if (-not $Key -or -not $Value) { return }

    if (-not $Map.ContainsKey($Key)) {
        $Map[$Key] = [System.Collections.Generic.HashSet[string]]::new()
    }
    [void]$Map[$Key].Add($Value)
}

function Get-HashSetValues {
    param(
        [Parameter(Mandatory)] [hashtable] $Map,
        [Parameter(Mandatory)] [string] $Key
    )

    if ($Map.ContainsKey($Key)) { return @($Map[$Key]) }
    return @()
}

# ---------------------------
# GET GROUPS (paged)
# ---------------------------
Write-Host "Retrieving groups (paged)..." -ForegroundColor Cyan

$groupSelect = @(
    "id","displayName","description",
    "createdDateTime",
    "mailEnabled","securityEnabled",
    "groupTypes","mail","mailNickname","visibility",
    "onPremisesSyncEnabled",
    "membershipRule","membershipRuleProcessingState",
    "resourceProvisioningOptions"
) -join ","

$groupsUri = "https://graph.microsoft.com/v1.0/groups?`$select=$groupSelect&`$top=999"

$groups = [System.Collections.Generic.List[object]]::new()

while ($groupsUri) {
    $resp = Invoke-GraphGetWithRetry -Uri $groupsUri -MaxAttempts $maxAttemptsPerPage -RetryAfterBufferSec $retryAfterBufferSec
    foreach ($g in @($resp.value)) { $groups.Add($g) | Out-Null }

    if ($resp.'@odata.nextLink') { $groupsUri = [string]$resp.'@odata.nextLink' } else { $groupsUri = $null }
}

Write-Host ("Groups loaded: {0}" -f $groups.Count) -ForegroundColor Green

# ---------------------------
# TEAMS MAPPING
# ---------------------------
$teamsGroupIds = [System.Collections.Generic.HashSet[string]]::new()

if ($includeTeamsInfo) {
    Write-Host "Retrieving Teams-enabled groups..." -ForegroundColor Cyan

    $teamsUri = "https://graph.microsoft.com/v1.0/groups?`$filter=resourceProvisioningOptions/Any(x:x eq 'Team')&`$select=id&`$top=999"

    while ($teamsUri) {
        $resp = Invoke-GraphGetWithRetry -Uri $teamsUri -MaxAttempts $maxAttemptsPerPage -RetryAfterBufferSec $retryAfterBufferSec
        foreach ($tg in @($resp.value)) {
            if ($tg.id) { [void]$teamsGroupIds.Add([string]$tg.id) }
        }

        if ($resp.'@odata.nextLink') { $teamsUri = [string]$resp.'@odata.nextLink' } else { $teamsUri = $null }
    }

    Write-Host ("Teams-enabled groups loaded: {0}" -f $teamsGroupIds.Count) -ForegroundColor Green
}

# ---------------------------
# CONDITIONAL ACCESS POLICY MAPPING
# ---------------------------
$caIncludeByGroup = @{}
$caExcludeByGroup = @{}

if ($includeCARefs) {
    Write-Host "Retrieving Conditional Access policies (paged)..." -ForegroundColor Cyan

    $caUri = "https://graph.microsoft.com/v1.0/identity/conditionalAccess/policies?`$top=100"
    $policiesCount = 0

    while ($caUri) {
        $resp = Invoke-GraphGetWithRetry -Uri $caUri -MaxAttempts $maxAttemptsPerPage -RetryAfterBufferSec $retryAfterBufferSec

        foreach ($p in @($resp.value)) {
            $policiesCount++
            $pName = [string]$p.displayName

            $inc = @($p.conditions?.users?.includeGroups)
            $exc = @($p.conditions?.users?.excludeGroups)

            foreach ($gid in $inc) {
                Add-ToHashSetMap -Map $caIncludeByGroup -Key ([string]$gid) -Value $pName
            }

            foreach ($gid in $exc) {
                Add-ToHashSetMap -Map $caExcludeByGroup -Key ([string]$gid) -Value $pName
            }
        }

        if ($resp.'@odata.nextLink') { $caUri = [string]$resp.'@odata.nextLink' } else { $caUri = $null }
    }

    Write-Host ("CA policies loaded: {0}" -f $policiesCount) -ForegroundColor Green
}

# ---------------------------
# ROLE ASSIGNMENTS MAPPING
# ---------------------------
$assignedRolesByGroup = @{}

if ($includeRoleAssignments) {
    Write-Host "Retrieving Entra role assignments and definitions..." -ForegroundColor Cyan

    $roleDefinitionNameById = @{}

    $roleDefUri = "https://graph.microsoft.com/v1.0/roleManagement/directory/roleDefinitions"
    while ($roleDefUri) {
        $resp = Invoke-GraphGetWithRetry -Uri $roleDefUri -MaxAttempts $maxAttemptsPerPage -RetryAfterBufferSec $retryAfterBufferSec
        foreach ($rd in @($resp.value)) {
            if ($rd.id) { $roleDefinitionNameById[[string]$rd.id] = [string]$rd.displayName }
        }
        if ($resp.'@odata.nextLink') { $roleDefUri = [string]$resp.'@odata.nextLink' } else { $roleDefUri = $null }
    }

    $roleAsgUri = "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments"
    $roleAssignmentsCount = 0

    while ($roleAsgUri) {
        $resp = Invoke-GraphGetWithRetry -Uri $roleAsgUri -MaxAttempts $maxAttemptsPerPage -RetryAfterBufferSec $retryAfterBufferSec
        foreach ($ra in @($resp.value)) {
            $roleAssignmentsCount++
            $principalId = [string]$ra.principalId
            $roleDefId   = [string]$ra.roleDefinitionId

            if ($principalId -and $roleDefinitionNameById.ContainsKey($roleDefId)) {
                Add-ToHashSetMap -Map $assignedRolesByGroup -Key $principalId -Value $roleDefinitionNameById[$roleDefId]
            }
        }
        if ($resp.'@odata.nextLink') { $roleAsgUri = [string]$resp.'@odata.nextLink' } else { $roleAsgUri = $null }
    }

    Write-Host ("Role assignments loaded: {0}" -f $roleAssignmentsCount) -ForegroundColor Green
}

# ---------------------------
# ACCESS PACKAGES LIST
# ---------------------------
$accessPackages = [System.Collections.Generic.List[object]]::new()
$accessPackageReferencesByGroup = @{}

if ($includeAccessPackageRefs) {
    Write-Host "Retrieving Access Packages..." -ForegroundColor Cyan

    $apUri = "https://graph.microsoft.com/beta/identityGovernance/entitlementManagement/accessPackages?`$select=id,displayName&`$top=999"

    while ($apUri) {
        $resp = Invoke-GraphGetWithRetry -Uri $apUri -MaxAttempts $maxAttemptsPerPage -RetryAfterBufferSec $retryAfterBufferSec
        foreach ($ap in @($resp.value)) { $accessPackages.Add($ap) | Out-Null }
        if ($resp.'@odata.nextLink') { $apUri = [string]$resp.'@odata.nextLink' } else { $apUri = $null }
    }

    Write-Host ("Access Packages loaded: {0}" -f $accessPackages.Count) -ForegroundColor Green
}

# ---------------------------
# BUILD SUBREQUESTS - BATCHED
# ---------------------------
$resultByReqId   = @{}
$attemptsByReqId = @{}
$allRequests = @()

if ($includeOwners -or $includeMemberCounts -or $includeAppRoleRefs -or $includeAccessPackageRefs) {
    Write-Host "Building batched subrequests..." -ForegroundColor Cyan

    foreach ($g in $groups) {
        if ($includeOwners) {
            $allRequests += @{
                id     = "$($g.id):owners"
                method = "GET"
                url    = "groups/$($g.id)/owners?`$select=displayName,userPrincipalName"
            }
        }
        if ($includeMemberCounts) {
            $allRequests += @{
                id      = "$($g.id):membersCount"
                method  = "GET"
                url     = "groups/$($g.id)/members/`$count"
                headers = @{ "ConsistencyLevel" = "eventual" }
            }
        }
        if ($includeAppRoleRefs) {
            $allRequests += @{
                id     = "$($g.id):appRoles"
                method = "GET"
                url    = "groups/$($g.id)/appRoleAssignments?`$select=resourceDisplayName,appRoleId,principalDisplayName"
            }
        }
    }

    foreach ($ap in $accessPackages) {
        if ($includeAccessPackageRefs) {
            $allRequests += @{
                id     = "$($ap.id):accessPackage"
                method = "GET"
                url    = "identityGovernance/entitlementManagement/accessPackages/$($ap.id)?`$expand=accessPackageResourceRoleScopes(`$expand=accessPackageResourceScope)"
            }
        }
    }

    Write-Host "Sending batches (Retry-After aware)..." -ForegroundColor Cyan

    $pending = [System.Collections.Generic.List[hashtable]]::new()
    $allRequests | ForEach-Object { $pending.Add($_) }

    while ($pending.Count -gt 0) {

        $batchSubreqLimit = 20
        $batches = Split-IntoBatches -Items $pending -BatchSize $batchSubreqLimit
        $pending = [System.Collections.Generic.List[hashtable]]::new()

        $maxRetryAfterThisRound = 0
        $transientFailuresThisRound = 0

        foreach ($batch in $batches) {

            $body = @{ requests = @($batch) } | ConvertTo-Json -Depth 10

            $resp = $null
            try {
                $resp = Invoke-MgGraphRequest -Method POST -Uri "https://graph.microsoft.com/beta/`$batch" -ContentType "application/json" -Body $body -ErrorAction Stop
            }
            catch {
                foreach ($req in $batch) {
                    $rid = $req.id
                    $attemptsByReqId[$rid] = 1 + ($attemptsByReqId[$rid] ?? 0)

                    if ($attemptsByReqId[$rid] -lt $maxAttemptsPerRequest) {
                        $pending.Add($req)
                        $transientFailuresThisRound++
                    } else {
                        $resultByReqId[$rid] = [pscustomobject]@{
                            Status  = 0
                            Headers = $null
                            Body    = @{ error = @{ message = $_.Exception.Message } }
                        }
                    }
                }
                continue
            }

            foreach ($r in $resp.responses) {
                $rid     = [string]$r.id
                $status  = [int]$r.status
                $headers = $r.headers
                $bodyObj = $r.body

                $attemptsByReqId[$rid] = 1 + ($attemptsByReqId[$rid] ?? 0)

                if ($status -ge 200 -and $status -lt 300) {
                    $resultByReqId[$rid] = [pscustomobject]@{ Status=$status; Headers=$headers; Body=$bodyObj }
                    continue
                }

                if ($status -in 429,503,504) {
                    if ($attemptsByReqId[$rid] -lt $maxAttemptsPerRequest) {
                        $reqToRetry = ($batch | Where-Object { $_.id -eq $rid } | Select-Object -First 1)
                        if ($reqToRetry) { $pending.Add($reqToRetry); $transientFailuresThisRound++ }

                        $ra = Get-RetryAfterSeconds -headers $headers
                        if ($ra -and $ra -gt $maxRetryAfterThisRound) { $maxRetryAfterThisRound = $ra }
                    } else {
                        $resultByReqId[$rid] = [pscustomobject]@{ Status=$status; Headers=$headers; Body=$bodyObj }
                    }
                    continue
                }

                $resultByReqId[$rid] = [pscustomobject]@{ Status=$status; Headers=$headers; Body=$bodyObj }
            }
        }

        if ($pending.Count -gt 0) {
            $sleepSec = if ($maxRetryAfterThisRound -gt 0) { $maxRetryAfterThisRound + $retryAfterBufferSec } else { 5 }
            Write-Host ("Transient failures to retry: {0}. Sleeping {1}s..." -f $transientFailuresThisRound, $sleepSec) -ForegroundColor Yellow
            Start-Sleep -Seconds $sleepSec
        }
    }
}

# ---------------------------
# PROCESS ACCESS PACKAGE REFERENCES
# ---------------------------
if ($includeAccessPackageRefs -and $accessPackages.Count -gt 0) {
    Write-Host "Processing Access Package group references..." -ForegroundColor Cyan

    foreach ($ap in $accessPackages) {
        $apReqId = "$($ap.id):accessPackage"
        $apObj   = $resultByReqId[$apReqId]

        if (-not $apObj -or $apObj.Status -lt 200 -or $apObj.Status -ge 300) { continue }

        $roleScopes = @($apObj.Body.accessPackageResourceRoleScopes)
        foreach ($rs in $roleScopes) {
            $scope = $rs.accessPackageResourceScope
            if (-not $scope) { continue }

            $originId = [string]$scope.originId
            if (-not $originId) { continue }

            Add-ToHashSetMap -Map $accessPackageReferencesByGroup -Key $originId -Value ([string]$ap.displayName)
        }
    }
}

# ---------------------------
# BUILD FINAL REPORT
# ---------------------------
Write-Host "Building report objects..." -ForegroundColor Cyan

$report = foreach ($g in $groups) {

    $groupTypes = @($g.groupTypes)
    $isUnified  = $groupTypes -contains "Unified"
    $isDynamic  = $groupTypes -contains "DynamicMembership"

    $isTeamsTeam = $false
    if ($includeTeamsInfo) {
        $isTeamsTeam = $teamsGroupIds.Contains([string]$g.id)
    } else {
        $isTeamsTeam = @($g.resourceProvisioningOptions) -contains "Team"
    }

    $friendlyGroupType = if ($isUnified) { "Microsoft 365" } elseif ($g.securityEnabled) { "Security" } else { "Distribution / Mail-enabled" }
    $membershipType = if ($isDynamic) { "Dynamic" } else { "Assigned" }

    # Owners
    $ownersReqId = "$($g.id):owners"
    $ownersObj   = $resultByReqId[$ownersReqId]

    $ownersNames = @()
    $ownersUpns  = @()
    $ownersCount = $null

    $ownersStatus = $ownersObj?.Status
    $ownersError  = Get-ErrorMessageFromBody $ownersObj?.Body

    if ($includeOwners -and $ownersObj -and $ownersObj.Status -ge 200 -and $ownersObj.Status -lt 300) {
        $owners = @($ownersObj.Body.value)
        $ownersCount = $owners.Count

        foreach ($o in $owners) {
            if ($o.displayName) { $ownersNames += [string]$o.displayName }
            if ($o.userPrincipalName) { $ownersUpns += [string]$o.userPrincipalName }
        }
    }

    # MembersCount
    $membersCountReqId = "$($g.id):membersCount"
    $membersCountObj   = $resultByReqId[$membersCountReqId]

    $membersCount = $null
    $membersCountStatus = $membersCountObj?.Status
    $membersCountError  = Get-ErrorMessageFromBody $membersCountObj?.Body

    if ($includeMemberCounts -and $membersCountObj -and $membersCountObj.Status -ge 200 -and $membersCountObj.Status -lt 300) {
        try { $membersCount = [int]$membersCountObj.Body } catch {
            try { $membersCount = [int]([string]$membersCountObj.Body) } catch { $membersCount = $null }
        }
    }

    # App Role Assignments
    $appRolesReqId = "$($g.id):appRoles"
    $appRolesObj   = $resultByReqId[$appRolesReqId]

    $appRoleNames = @()
    $appRolesStatus = $appRolesObj?.Status
    $appRolesError  = Get-ErrorMessageFromBody $appRolesObj?.Body

    if ($includeAppRoleRefs -and $appRolesObj -and $appRolesObj.Status -ge 200 -and $appRolesObj.Status -lt 300) {
        $appRoles = @($appRolesObj.Body.value)
        foreach ($ar in $appRoles) {
            if ($ar.resourceDisplayName) { $appRoleNames += [string]$ar.resourceDisplayName }
        }
    }

    # CA mapping
    $caIncludeNames = @()
    $caExcludeNames = @()

    if ($includeCARefs) {
        $caIncludeNames = Get-HashSetValues -Map $caIncludeByGroup -Key ([string]$g.id)
        $caExcludeNames = Get-HashSetValues -Map $caExcludeByGroup -Key ([string]$g.id)
    }

    $usedInCA = $false
    if ($caIncludeNames.Count -gt 0 -or $caExcludeNames.Count -gt 0) { $usedInCA = $true }

    # Role assignments
    $assignedRoleNames = @()
    if ($includeRoleAssignments) {
        $assignedRoleNames = Get-HashSetValues -Map $assignedRolesByGroup -Key ([string]$g.id)
    }

    # Access Packages
    $accessPackageNames = @()
    if ($includeAccessPackageRefs) {
        $accessPackageNames = Get-HashSetValues -Map $accessPackageReferencesByGroup -Key ([string]$g.id)
    }

    [pscustomobject]@{
        GroupId        = $g.id
        DisplayName    = $g.displayName
        Description    = $g.description
        CreatedDateTime = $g.createdDateTime

        FriendlyGroupType = $friendlyGroupType
        MembershipType    = $membershipType

        MailEnabled    = $g.mailEnabled
        SecurityEnabled = $g.securityEnabled
        GroupTypes     = Join-Arr $groupTypes
        IsUnified      = $isUnified
        IsDynamic      = $isDynamic
        IsTeamsTeam    = $isTeamsTeam

        Mail           = $g.mail
        MailNickname   = $g.mailNickname
        Visibility     = $g.visibility

        OnPremisesSyncEnabled = $g.onPremisesSyncEnabled
        MembershipRule = $g.membershipRule
        MembershipRuleProcessingState = $g.membershipRuleProcessingState

        OwnersCount    = $ownersCount
        Owners         = ( ($ownersNames | Sort-Object -Unique) -join " ; " )
        OwnersUPNs     = ( ($ownersUpns  | Sort-Object -Unique) -join " ; " )

        MembersCount   = $membersCount

        UsedInConditionalAccess = $usedInCA
        CA_PoliciesInclude      = ( ($caIncludeNames | Sort-Object -Unique) -join " ; " )
        CA_PoliciesExclude      = ( ($caExcludeNames | Sort-Object -Unique) -join " ; " )

        AssignedRoles              = ( ($assignedRoleNames  | Sort-Object -Unique) -join " ; " )
        ReferencedInAppRoles       = ( ($appRoleNames       | Sort-Object -Unique) -join " ; " )
        ReferencedInAccessPackages = ( ($accessPackageNames | Sort-Object -Unique) -join " ; " )

        # Debug columns, useful for explaining blanks in the Python app
        OwnersStatus       = $ownersStatus
        OwnersError        = $ownersError
        MembersCountStatus = $membersCountStatus
        MembersCountError  = $membersCountError
        AppRolesStatus     = $appRolesStatus
        AppRolesError      = $appRolesError
    }
}

Disconnect-MgGraph

# ---------------------------
# EXPORT
# ---------------------------
Write-Host "Writing CSV to: $outFile" -ForegroundColor Green
$report | Export-Csv -Path $outFile -NoTypeInformation -Delimiter ';' -Encoding UTF8
Write-Host "Done." -ForegroundColor Green
