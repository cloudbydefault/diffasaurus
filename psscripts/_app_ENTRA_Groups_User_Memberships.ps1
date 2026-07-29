#Requires -Version 7

<#
.COCO_GRAPH_SCOPES
Group.Read.All
Directory.Read.All
User.Read.All
.COCO_GRAPH_SCOPES_END
#>

param(
    [string]$OutputPath = "",
    [int]$MaxGroups = 0,
    [string]$GroupNameFilter = ""
)

$ErrorActionPreference = "Stop"

if (Get-Variable -Name PSStyle -Scope Global -ErrorAction SilentlyContinue) {
    $PSStyle.OutputRendering = "PlainText"
}

$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

function Write-Info {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Cyan
}

function Safe-Get {
    param($Object, [string]$PropertyName)

    try {
        if ($null -eq $Object) { return "" }
        $value = $Object.$PropertyName
        if ($null -eq $value) { return "" }
        return [string]$value
    }
    catch {
        return ""
    }
}

function Invoke-GraphGetAll {
    param(
        [Parameter(Mandatory)]
        [string]$Uri
    )

    $all = [System.Collections.Generic.List[object]]::new()
    $next = $Uri

    while ($next) {
        $attempt = 0

        while ($true) {
            try {
                $response = Invoke-MgGraphRequest `
                    -Uri $next `
                    -Method GET `
                    -OutputType PSObject `
                    -ErrorAction Stop

                break
            }
            catch {
                $attempt++
                $message = $_.Exception.Message

                if (
                    $message -match "429" -or
                    $message -match "503" -or
                    $message -match "504"
                ) {
                    $delay = [Math]::Min(60, [Math]::Pow(2, $attempt))

                    Write-Warning "Graph throttling detected. Waiting $delay second(s)..."
                    Start-Sleep -Seconds $delay
                    continue
                }

                throw
            }
        }

        foreach ($item in @($response.value)) {
            $all.Add($item) | Out-Null
        }

        $next = $response.'@odata.nextLink'
    }

    return $all
}

# ==========================================================
# OUTPUT
# ==========================================================

if (-not $OutputPath) {
    $reportsDir = $env:REPORTS_DIR
    if (-not $reportsDir) {
        $projectRoot = Split-Path -Parent $PSScriptRoot
        $reportsDir = Join-Path $projectRoot "reports"
    }

    New-Item -Path $reportsDir -ItemType Directory -Force | Out-Null

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $reportsDir "Entra_Group_User_Memberships_$timestamp.csv"
}
else {
    $dir = Split-Path -Parent $OutputPath

    if ($dir) {
        New-Item -Path $dir -ItemType Directory -Force | Out-Null
    }
}

# ==========================================================
# CONNECT
# ==========================================================

Write-Info "Connecting to Microsoft Graph..."

$scopes = @(
    "Group.Read.All",
    "Directory.Read.All",
    "User.Read.All"
)

$ctx = Get-MgContext -ErrorAction SilentlyContinue

if (-not $ctx) {
    Connect-MgGraph -Scopes $scopes -NoWelcome | Out-Null
}

# ==========================================================
# GET GROUPS
# ==========================================================

Write-Info "Retrieving groups..."

$groupsUri = "https://graph.microsoft.com/v1.0/groups?`$select=id,displayName,mail,mailEnabled,securityEnabled,groupTypes,onPremisesSyncEnabled,membershipRule&`$top=999"
$groups = Invoke-GraphGetAll -Uri $groupsUri

Write-Info "Groups found: $($groups.Count)"

if ($GroupNameFilter) {
    $groups = $groups | Where-Object {
        $_.displayName -like "*$GroupNameFilter*"
    }

    Write-Info "Groups after filter '$GroupNameFilter': $($groups.Count)"
}

if ($MaxGroups -gt 0) {
    $groups = $groups | Select-Object -First $MaxGroups
    Write-Info "Group limit enabled: $MaxGroups"
}

# ==========================================================
# GET MEMBERS PER GROUP
# ==========================================================

$rows = [System.Collections.Generic.List[object]]::new()
$index = 0

foreach ($group in $groups) {
    $index++

    if (
        $index -eq 1 -or
        $index % 25 -eq 0 -or
        $index -eq $groups.Count
    ) {
        $elapsedMinutes = [Math]::Round($Stopwatch.Elapsed.TotalMinutes, 2)

        if ($index -gt 0) {
            $avgSecondsPerGroup = $Stopwatch.Elapsed.TotalSeconds / $index
            $remainingGroups = $groups.Count - $index
            $etaMinutes = [Math]::Round(($remainingGroups * $avgSecondsPerGroup) / 60, 1)
        }
        else {
            $etaMinutes = 0
        }

        Write-Host ""
        Write-Host "===================================="
        Write-Host "Progress : $index / $($groups.Count)"
        Write-Host "Elapsed  : $elapsedMinutes min"
        Write-Host "ETA      : $etaMinutes min"
        Write-Host "Rows     : $($rows.Count)"
        Write-Host "===================================="
        Write-Host ""
    }

    $groupId = Safe-Get $group "id"
    $groupName = Safe-Get $group "displayName"

    if (-not $groupId) {
        continue
    }

    $groupTypes = @($group.groupTypes)
    $isUnified = $groupTypes -contains "Unified"
    $isDynamic = $groupTypes -contains "DynamicMembership"

    $groupType =
        if ($isUnified) { "Microsoft 365" }
        elseif ($group.securityEnabled -eq $true) { "Security" }
        else { "Other" }

    $membershipType =
        if ($isDynamic) { "Dynamic" }
        else { "Assigned" }

    $membersUri = "https://graph.microsoft.com/v1.0/groups/$groupId/members/microsoft.graph.user?`$select=id,displayName,userPrincipalName,mail,userType,accountEnabled&`$top=999"

    try {
        $members = Invoke-GraphGetAll -Uri $membersUri
    }
    catch {
        Write-Warning "Failed to retrieve members for group '$groupName': $($_.Exception.Message)"
        continue
    }

    if (-not $members -or $members.Count -eq 0) {
        continue
    }

    foreach ($member in $members) {
        $rows.Add(
            [PSCustomObject]@{
                UserPrincipalName      = Safe-Get $member "userPrincipalName"
                UserDisplayName        = Safe-Get $member "displayName"
                UserMail               = Safe-Get $member "mail"
                UserId                 = Safe-Get $member "id"
                UserType               = Safe-Get $member "userType"
                AccountEnabled         = Safe-Get $member "accountEnabled"

                GroupName              = $groupName
                GroupId                = $groupId
                GroupMail              = Safe-Get $group "mail"
                GroupType              = $groupType
                MembershipType         = $membershipType
                SecurityEnabled        = Safe-Get $group "securityEnabled"
                MailEnabled            = Safe-Get $group "mailEnabled"
                IsMicrosoft365Group    = [string]$isUnified
                IsDynamicGroup         = [string]$isDynamic
                OnPremisesSyncEnabled  = Safe-Get $group "onPremisesSyncEnabled"
                MembershipRule         = Safe-Get $group "membershipRule"
            }
        ) | Out-Null
    }
}

# ==========================================================
# EXPORT
# ==========================================================

if ($rows.Count -eq 0) {
    Write-Host "No group memberships found." -ForegroundColor Yellow

    $rows.Add(
        [PSCustomObject]@{
            UserPrincipalName      = ""
            UserDisplayName        = ""
            UserMail               = ""
            UserId                 = ""
            UserType               = ""
            AccountEnabled         = ""
            GroupName              = ""
            GroupId                = ""
            GroupMail              = ""
            GroupType              = ""
            MembershipType         = ""
            SecurityEnabled        = ""
            MailEnabled            = ""
            IsMicrosoft365Group    = ""
            IsDynamicGroup         = ""
            OnPremisesSyncEnabled  = ""
            MembershipRule         = ""
        }
    ) | Out-Null
}

Write-Info "Saving CSV to: $OutputPath"

$rows |
    Sort-Object UserPrincipalName, GroupName |
    Export-Csv `
        -Path $OutputPath `
        -NoTypeInformation `
        -Encoding UTF8BOM `
        -Delimiter ";"

$Stopwatch.Stop()

Write-Host ""
Write-Host "===================================="
Write-Host "Execution Summary"
Write-Host "===================================="
Write-Host "Groups processed : $($groups.Count)"
Write-Host "Rows exported    : $($rows.Count)"
Write-Host "Elapsed          : $($Stopwatch.Elapsed)"
Write-Host "===================================="

Write-Host ""
Write-Host "Done!" -ForegroundColor Green
Write-Host "CSV exported to: $OutputPath"
Write-Host "Rows exported: $($rows.Count)"
