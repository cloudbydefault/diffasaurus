#Requires -Version 7.0

param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

if (Get-Variable -Name PSStyle -Scope Global -ErrorAction SilentlyContinue) {
    $PSStyle.OutputRendering = 'PlainText'
}

try {
    Import-Module ExchangeOnlineManagement -ErrorAction Stop

    if (-not $OutputPath) {
        if ($env:REPORTS_DIR) {
            $reportsDir = $env:REPORTS_DIR
        }
        else {
            $projectRoot = Split-Path -Parent $PSScriptRoot
            $reportsDir = Join-Path $projectRoot "reports"
        }

        if (-not (Test-Path $reportsDir)) {
            New-Item -ItemType Directory -Path $reportsDir -Force | Out-Null
        }

        $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $OutputPath = Join-Path $reportsDir "Exchange_SharedMailboxes_$timestamp.csv"
    }

    function Join-Values {
        param($Values)

        $clean = @($Values) |
            Where-Object { $_ -and -not [string]::IsNullOrWhiteSpace([string]$_) } |
            ForEach-Object { [string]$_ } |
            Sort-Object -Unique

        return ($clean -join "; ")
    }

    function Safe-String {
        param($Value)
        if ($null -eq $Value) { return "" }
        return [string]$Value
    }

    Write-Host "Connecting to Exchange Online..." -ForegroundColor Cyan
    Connect-ExchangeOnline -ShowBanner:$false -ShowProgress $true -ErrorAction Stop

    Write-Host "Retrieving shared mailboxes..." -ForegroundColor Cyan

    $sharedMailboxes = @(Get-EXOMailbox `
        -RecipientTypeDetails SharedMailbox `
        -ResultSize Unlimited `
        -Properties HiddenFromAddressListsEnabled,ForwardingAddress,ForwardingSmtpAddress,DeliverToMailboxAndForward,LitigationHoldEnabled,RetentionPolicy,WhenCreated,GrantSendOnBehalfTo `
        -ErrorAction Stop)

    Write-Host "Shared mailboxes found: $($sharedMailboxes.Count)" -ForegroundColor Green

    $results = foreach ($mbx in $sharedMailboxes) {
        $identity = Safe-String $mbx.PrimarySmtpAddress
        if ([string]::IsNullOrWhiteSpace($identity)) {
            continue
        }

        Write-Host "Processing: $identity" -ForegroundColor DarkCyan

        $fullAccessUsers = @()
        $sendAsUsers = @()
        $sendOnBehalfUsers = @()

        try {
            $fullAccessUsers = @(Get-EXOMailboxPermission -Identity $identity -ErrorAction Stop |
                Where-Object {
                    $_.AccessRights -contains "FullAccess" -and
                    -not $_.IsInherited -and
                    $_.User -notlike "NT AUTHORITY\SELF"
                } |
                ForEach-Object { $_.User })
        }
        catch {
            $fullAccessUsers = @("ERROR: $($_.Exception.Message)")
        }

        try {
            $sendAsUsers = @(Get-RecipientPermission -Identity $identity -ErrorAction Stop |
                Where-Object {
                    $_.AccessRights -contains "SendAs" -and
                    -not $_.IsInherited -and
                    $_.Trustee -notlike "NT AUTHORITY\SELF"
                } |
                ForEach-Object { $_.Trustee })
        }
        catch {
            $sendAsUsers = @("ERROR: $($_.Exception.Message)")
        }

        try {
            $sendOnBehalfUsers = @($mbx.GrantSendOnBehalfTo | ForEach-Object { $_.Name })
        }
        catch {
            $sendOnBehalfUsers = @()
        }

        $fullAccessJoined = Join-Values $fullAccessUsers
        $sendAsJoined = Join-Values $sendAsUsers
        $sendOnBehalfJoined = Join-Values $sendOnBehalfUsers

        $hasFullAccess = -not [string]::IsNullOrWhiteSpace($fullAccessJoined) -and $fullAccessJoined -notlike "ERROR:*"
        $hasSendAs = -not [string]::IsNullOrWhiteSpace($sendAsJoined) -and $sendAsJoined -notlike "ERROR:*"
        $hasSendOnBehalf = -not [string]::IsNullOrWhiteSpace($sendOnBehalfJoined)

        $forwardingAddress = Safe-String $mbx.ForwardingAddress
        $forwardingSmtp = Safe-String $mbx.ForwardingSmtpAddress

        $hasForwarding = (
            -not [string]::IsNullOrWhiteSpace($forwardingAddress) -or
            -not [string]::IsNullOrWhiteSpace($forwardingSmtp)
        )

        [pscustomobject]@{
            DisplayName                   = Safe-String $mbx.DisplayName
            PrimarySmtpAddress            = $identity
            Alias                         = Safe-String $mbx.Alias
            ExternalDirectoryObjectId     = Safe-String $mbx.ExternalDirectoryObjectId
            RecipientTypeDetails          = Safe-String $mbx.RecipientTypeDetails
            HiddenFromAddressListsEnabled = Safe-String $mbx.HiddenFromAddressListsEnabled
            WhenCreated                   = Safe-String $mbx.WhenCreated

            HasFullAccessDelegates        = $hasFullAccess
            FullAccessDelegates           = $fullAccessJoined
            FullAccessDelegatesCount      = @($fullAccessUsers | Where-Object { $_ -and $_ -notlike "ERROR:*" }).Count

            HasSendAsDelegates            = $hasSendAs
            SendAsDelegates               = $sendAsJoined
            SendAsDelegatesCount          = @($sendAsUsers | Where-Object { $_ -and $_ -notlike "ERROR:*" }).Count

            HasSendOnBehalfDelegates      = $hasSendOnBehalf
            SendOnBehalfDelegates         = $sendOnBehalfJoined
            SendOnBehalfDelegatesCount    = @($sendOnBehalfUsers | Where-Object { $_ }).Count

            HasAnyDelegation              = ($hasFullAccess -or $hasSendAs -or $hasSendOnBehalf)

            ForwardingAddress             = $forwardingAddress
            ForwardingSmtpAddress         = $forwardingSmtp
            DeliverToMailboxAndForward    = Safe-String $mbx.DeliverToMailboxAndForward
            HasForwarding                 = $hasForwarding

            LitigationHoldEnabled         = Safe-String $mbx.LitigationHoldEnabled
            RetentionPolicy               = Safe-String $mbx.RetentionPolicy
        }
    }

    Write-Host "Exporting CSV..." -ForegroundColor Cyan

    $results |
        Sort-Object DisplayName, PrimarySmtpAddress |
        Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8

    Write-Host "Done." -ForegroundColor Green
    Write-Host "CSV: $OutputPath" -ForegroundColor Green
    Write-Host "Rows: $($results.Count)" -ForegroundColor Green
}
catch {
    Write-Host "FATAL ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    try {
        Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue
    }
    catch { }
}