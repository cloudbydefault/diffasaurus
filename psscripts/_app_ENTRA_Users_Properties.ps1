#Requires -Version 7

<#
Entra - User Properties Report (Batched + Retry-After aware)

Goal: export the most useful "User properties" you see in Entra admin center:
Identity / Contact info / Job info / Settings / On-premises + Manager + Sponsors

Graph calls:
1) v1.0 users list with $select=important properties (paged; $top=999)
2) beta/$batch (manager + sponsors per user) with retries for 429/503/504 and Retry-After handling

Permissions (delegated scopes):
- User.Read.All
- Directory.Read.All
(Manager & sponsors are directory relationships; Directory.Read.All is the safest.)
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
$usersPerBatch         = 10   # 2 subrequests per user => 20 subrequests per batch
$maxAttemptsPerRequest = 6
$retryAfterBufferSec   = 2
$timestamp             = Get-Date -Format "yyyyMMdd-HHmmss"
$outFile               = Join-Path $OutDir "Entra_Users_Properties_${timestamp}.csv"

# ---------------------------
# CONNECT
# ---------------------------
Connect-MgGraph -Scopes `
    "User.Read.All", `
    "Directory.Read.All" `
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
    param(
        $arr,
        [string]$sep = " ; "
    )
    if ($null -eq $arr) { return $null }
    $vals = @($arr | ForEach-Object { $_ } | Where-Object { $_ -and ($_ -ne "") })
    if ($vals.Count -eq 0) { return $null }
    return ($vals -join $sep)
}

function Get-AllUsersWithSelectedProperties {
    Write-Host "Retrieving users (v1.0) with selected properties (paged)..." -ForegroundColor Cyan

    # This list focuses on what admins actually want in a “user properties” report.
    # Add/remove fields here safely (must exist on the Graph user resource).
    $select = @(
        # Identity
        "id","displayName","givenName","surname","userPrincipalName","userType","accountEnabled",
        "createdDateTime","mail","mailNickname","identities",

        # Contact information
        "streetAddress","city","state","postalCode","country",
        "businessPhones","mobilePhone","otherMails","proxyAddresses","imAddresses",

        # Job information
        "jobTitle","companyName","department","officeLocation",
        "employeeId","employeeType","employeeHireDate",

        # Settings
        "usageLocation","preferredLanguage","preferredDataLocation",

        # On-premises
        "onPremisesSyncEnabled","onPremisesLastSyncDateTime",
        "onPremisesDistinguishedName","onPremisesImmutableId",
        "onPremisesExtensionAttributes"
    ) -join ","

    $uri = "https://graph.microsoft.com/v1.0/users?`$select=$select&`$top=999"

    $all = [System.Collections.Generic.List[object]]::new()

    while ($uri) {
        $resp = Invoke-MgGraphRequest -Method GET -Uri $uri -ErrorAction Stop

        foreach ($u in @($resp.value)) {
            $all.Add($u) | Out-Null
        }

        if ($resp.'@odata.nextLink') {
            $uri = [string]$resp.'@odata.nextLink'
        } else {
            $uri = $null
        }
    }

    return $all.ToArray()
}

# ---------------------------
# GET USERS
# ---------------------------
$users = Get-AllUsersWithSelectedProperties

# --- DEBUG: run only on first 20 users (uncomment to enable) ---
# $users = $users | Select-Object -First 20

Write-Host ("Users loaded: {0}" -f $users.Count) -ForegroundColor Green

# ---------------------------
# BUILD SUBREQUESTS (2 per user)
# ---------------------------
Write-Host "Building subrequests (manager + sponsors)..." -ForegroundColor Cyan

$allRequests = foreach ($u in $users) {
    @(
        @{
            id     = "$($u.id):manager"
            method = "GET"
            url    = "users/$($u.id)/manager?`$select=displayName,userPrincipalName"
        },
        @{
            id     = "$($u.id):sponsors"
            method = "GET"
            url    = "users/$($u.id)/sponsors?`$select=displayName,userPrincipalName"
        }
    )
}

# ---------------------------
# EXECUTE BATCHES WITH SMART RETRY
# ---------------------------
Write-Host "Sending batches (Retry-After aware)..." -ForegroundColor Cyan

$resultByReqId   = @{}
$attemptsByReqId = @{}

$pending = [System.Collections.Generic.List[hashtable]]::new()
$allRequests | ForEach-Object { $pending.Add($_) }

while ($pending.Count -gt 0) {

    $batchSubreqLimit = $usersPerBatch * 2
    $batches = Split-IntoBatches -Items $pending -BatchSize $batchSubreqLimit
    $pending = [System.Collections.Generic.List[hashtable]]::new()

    $maxRetryAfterThisRound = 0
    $transientFailuresThisRound = 0

    foreach ($batch in $batches) {

        $body = @{ requests = @($batch) } | ConvertTo-Json -Depth 8

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

# ---------------------------
# BUILD FINAL REPORT
# ---------------------------
Write-Host "Building report objects..." -ForegroundColor Cyan

$report = foreach ($u in $users) {

    $mgrReqId = "$($u.id):manager"
    $spoReqId = "$($u.id):sponsors"

    $mgrObj = $resultByReqId[$mgrReqId]
    $spoObj = $resultByReqId[$spoReqId]

    $managerName = $null
    $managerUpn  = $null
    if ($mgrObj -and $mgrObj.Status -ge 200 -and $mgrObj.Status -lt 300) {
        $managerName = $mgrObj.Body.displayName
        $managerUpn  = $mgrObj.Body.userPrincipalName
    }

    $sponsors = @()
    if ($spoObj -and $spoObj.Status -ge 200 -and $spoObj.Status -lt 300) {
        $sponsors = @($spoObj.Body.value | ForEach-Object { $_.displayName } | Where-Object { $_ })
    }

    # Flatten common arrays
    $businessPhones = Join-Arr $u.businessPhones
    $otherMails     = Join-Arr $u.otherMails
    $proxyAddresses = Join-Arr $u.proxyAddresses
    $imAddresses    = Join-Arr $u.imAddresses

    # Identities: array of {signInType, issuer, issuerAssignedId}
    $identities = $null
    if ($u.identities) {
        $identities = Join-Arr ($u.identities | ForEach-Object {
            $sid = $_.signInType
            $iss = $_.issuer
            $iid = $_.issuerAssignedId
            if ($sid -or $iss -or $iid) { "$sid|$iss|$iid" }
        })
    }

    # onPremisesExtensionAttributes: {extensionAttribute1..15}
    $ext = $u.onPremisesExtensionAttributes
    $extPacked = $null
    if ($ext) {
        $pairs = @()
        1..15 | ForEach-Object {
            $k = "extensionAttribute$_"
            $v = $ext.$k
            if ($v) { $pairs += "$k=$v" }
        }
        $extPacked = Join-Arr $pairs
    }

    [pscustomobject]@{
        # Identity
        Id              = $u.id
        DisplayName     = $u.displayName
        GivenName       = $u.givenName
        Surname         = $u.surname
        UPN             = $u.userPrincipalName
        Mail            = $u.mail
        MailNickname    = $u.mailNickname
        UserType        = $u.userType
        AccountEnabled  = $u.accountEnabled
        CreatedDateTime = $u.createdDateTime
        Identities      = $identities

        # Contact information
        StreetAddress   = $u.streetAddress
        City            = $u.city
        State           = $u.state
        PostalCode      = $u.postalCode
        Country         = $u.country
        BusinessPhones  = $businessPhones
        MobilePhone     = $u.mobilePhone
        OtherMails      = $otherMails
        ProxyAddresses  = $proxyAddresses
        IMAddresses     = $imAddresses

        # Job information
        JobTitle        = $u.jobTitle
        CompanyName     = $u.companyName
        Department      = $u.department
        OfficeLocation  = $u.officeLocation
        EmployeeId      = $u.employeeId
        EmployeeType    = $u.employeeType
        EmployeeHireDate = $u.employeeHireDate

        # Settings
        UsageLocation       = $u.usageLocation
        PreferredLanguage   = $u.preferredLanguage
        PreferredDataLocation = $u.preferredDataLocation

        # On-premises
        OnPremisesSyncEnabled      = $u.onPremisesSyncEnabled
        OnPremisesLastSyncDateTime = $u.onPremisesLastSyncDateTime
        OnPremisesDistinguishedName = $u.onPremisesDistinguishedName
        OnPremisesImmutableId      = $u.onPremisesImmutableId
        ExtensionAttributes        = $extPacked

        # Relationships
        ManagerDisplayName = $managerName
        ManagerUPN         = $managerUpn
        Sponsors           = ($sponsors | Sort-Object -Unique) -join " ; "

        # Debug (optional but extremely useful when something is blank)
        ManagerStatus  = $mgrObj?.Status
        ManagerError   = Get-ErrorMessageFromBody $mgrObj?.Body
        SponsorsStatus = $spoObj?.Status
        SponsorsError  = Get-ErrorMessageFromBody $spoObj?.Body
    }
}

Disconnect-MgGraph

# ---------------------------
# EXPORT
# ---------------------------
Write-Host "Writing CSV to: $outFile" -ForegroundColor Green
$report | Export-Csv -Path $outFile -NoTypeInformation -Delimiter ';' -Encoding UTF8
Write-Host "Done." -ForegroundColor Green