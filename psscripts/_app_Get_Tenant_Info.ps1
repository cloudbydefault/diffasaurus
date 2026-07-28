#Requires -Version 7.0

param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

if (Get-Variable -Name PSStyle -Scope Global -ErrorAction SilentlyContinue) {
    $PSStyle.OutputRendering = 'PlainText'
}

function Ensure-Folder {
    param([string]$Path)

    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

if (-not $OutputPath) {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $jsonDir = Join-Path $projectRoot "json"
    if (-not (Test-Path $jsonDir)) {
        New-Item -ItemType Directory -Path $jsonDir -Force | Out-Null
    }
    $OutputPath = Join-Path $jsonDir "tenant_info.json"
}

Connect-MgGraph -Scopes "Organization.Read.All,Domain.Read.All" -NoWelcome | Out-Null

try {
    $org = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/organization" -OutputType PSObject

    if (-not $org.value -or $org.value.Count -eq 0) {
        throw "No organization information returned."
    }

    $tenant = $org.value[0]

    $domainsResp = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/domains" -OutputType PSObject
    $domains = @()
    $defaultDomain = ""

    if ($domainsResp.value) {
        $domains = @($domainsResp.value | ForEach-Object { [string]$_.id })

        $default = $domainsResp.value | Where-Object { $_.isDefault -eq $true } | Select-Object -First 1
        if ($default) {
            $defaultDomain = [string]$default.id
        }
    }

    if (-not $defaultDomain) {
        $verifiedDomains = @($tenant.verifiedDomains)

        if ($verifiedDomains.Count -gt 0) {
            $default = $verifiedDomains | Where-Object { $_.isDefault -eq $true } | Select-Object -First 1
            if ($default) {
                $defaultDomain = [string]$default.name
            }
            else {
                $onmicrosoft = $verifiedDomains | Where-Object { $_.name -like "*.onmicrosoft.com" } | Select-Object -First 1
                if ($onmicrosoft) {
                    $defaultDomain = [string]$onmicrosoft.name
                }
                else {
                    $first = $verifiedDomains | Select-Object -First 1
                    if ($first) {
                        $defaultDomain = [string]$first.name
                    }
                }
            }
        }
    }

    if (-not $domains -or $domains.Count -eq 0) {
        if ($defaultDomain) {
            $domains = @($defaultDomain)
        }
    }

    $result = [pscustomobject]@{
        tenantDisplayName   = [string]$tenant.displayName
        tenantDefaultDomain = $defaultDomain
        tenantId            = [string]$tenant.id
        domains             = @($domains | Sort-Object -Unique)
        retrievedAt         = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    }

    Ensure-Folder -Path $OutputPath
    $result | ConvertTo-Json -Depth 5 | Set-Content -Path $OutputPath -Encoding UTF8

    Write-Host "Tenant info saved to: $OutputPath" -ForegroundColor Green
}
finally {
    Disconnect-MgGraph | Out-Null
}