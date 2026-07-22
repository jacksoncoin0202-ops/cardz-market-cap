[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'CARDZ-Market-Cap-Daily-Staging',
    [string]$At = '06:30',
    [ValidateSet('staging', 'production')]
    [string]$Mode = 'staging',
    [Parameter(Mandatory)]
    [string]$PrivateAcquireScript,
    [string]$PythonExe,
    [string]$R2Bucket,
    [Parameter(Mandatory)]
    [string]$CanaryOrigin,
    [string]$GenerationCanaryCommandJson,
    [string]$PointerPromoteCommandJson,
    [string]$ProductionRunner = $env:CARDZ_JLP_PRODUCTION_RUNNER
)

$ErrorActionPreference = 'Stop'
$RunScript = (Resolve-Path (Join-Path $PSScriptRoot 'run_daily.ps1')).Path
$AcquireScript = (Resolve-Path -LiteralPath $PrivateAcquireScript).Path
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonPath = (Get-Command python.exe -CommandType Application -All -ErrorAction Stop | Select-Object -First 1).Source
} else {
    $PythonPath = (Resolve-Path -LiteralPath $PythonExe).Path
}
if (-not [IO.File]::Exists($PythonPath)) {
    throw "Python executable does not exist: $PythonPath"
}
if ([string]::IsNullOrWhiteSpace($R2Bucket)) {
    $R2Bucket = if ($Mode -eq 'production') { $env:CARDZ_PRODUCTION_R2_BUCKET } else { $env:CARDZ_STAGING_R2_BUCKET }
}
$CanaryUri = $null
if (-not ([Uri]::TryCreate($CanaryOrigin, [UriKind]::Absolute, [ref]$CanaryUri)) -or $CanaryUri.Scheme -ne 'https') {
    throw 'CanaryOrigin must be an absolute HTTPS origin.'
}
if ([IO.Path]::GetFileName($AcquireScript) -ne 'grade10_scraper.py') {
    throw 'PrivateAcquireScript must point directly to grade10_scraper.py, not a legacy wrapper.'
}
if ([string]::IsNullOrWhiteSpace($R2Bucket)) {
    throw 'R2Bucket is required for an unattended website data publish task.'
}
if ($Mode -eq 'production' -and [string]::IsNullOrWhiteSpace($ProductionRunner)) {
    throw 'ProductionRunner is required for JLP MySQL production authority.'
}

$NodePath = (Get-Command node.exe -CommandType Application -All -ErrorAction Stop | Select-Object -First 1).Source
if ([string]::IsNullOrWhiteSpace($GenerationCanaryCommandJson)) {
    $GenerationCanaryCommandJson = ConvertTo-Json -InputObject @($NodePath, (Resolve-Path (Join-Path $PSScriptRoot 'run-generation-canary.mjs')).Path) -Compress
}
if ([string]::IsNullOrWhiteSpace($PointerPromoteCommandJson)) {
    if ($Mode -eq 'production') {
        throw 'Production requires an external atomic pointer promoter command JSON.'
    }
    $PointerPromoteCommandJson = ConvertTo-Json -InputObject @($NodePath, (Resolve-Path (Join-Path $PSScriptRoot 'promote-staging-pointer.mjs')).Path) -Compress
}
foreach ($Entry in @(
    @{ Name = 'GenerationCanaryCommandJson'; Value = $GenerationCanaryCommandJson },
    @{ Name = 'PointerPromoteCommandJson'; Value = $PointerPromoteCommandJson }
)) {
    $Command = ConvertFrom-Json -InputObject ([string]$Entry.Value) -ErrorAction Stop
    if ($Command -isnot [Array] -or $Command.Count -eq 0) {
        throw "$($Entry.Name) must be a non-empty JSON string array."
    }
    foreach ($Part in $Command) {
        if ($Part -isnot [string] -or [string]::IsNullOrWhiteSpace($Part)) {
            throw "$($Entry.Name) must contain only non-empty strings."
        }
    }
}

function ConvertTo-SingleQuotedArgument([string]$Value) {
    return "'{0}'" -f $Value.Replace("'", "''")
}

$RunAt = [DateTime]::ParseExact($At, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$PowerShellArguments = @(
    '-NoLogo',
    '-NoProfile',
    '-NonInteractive',
    '-WindowStyle', 'Hidden',
    '-ExecutionPolicy', 'Bypass',
    '-File', ('"{0}"' -f $RunScript),
    '-Mode', $Mode,
    '-PythonExe', ('"{0}"' -f $PythonPath),
    '-PrivateAcquireScript', ('"{0}"' -f $AcquireScript),
    '-R2Bucket', ('"{0}"' -f $R2Bucket),
    '-CanaryOrigin', (ConvertTo-SingleQuotedArgument $CanaryUri.GetLeftPart([UriPartial]::Authority)),
    '-GenerationCanaryCommandJson', (ConvertTo-SingleQuotedArgument $GenerationCanaryCommandJson),
    '-PointerPromoteCommandJson', (ConvertTo-SingleQuotedArgument $PointerPromoteCommandJson)
)
if ($ProductionRunner) {
    $ResolvedRunner = (Resolve-Path -LiteralPath $ProductionRunner).Path
    $PowerShellArguments += @('-ProductionRunner', ('"{0}"' -f $ResolvedRunner))
}
$Action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ($PowerShellArguments -join ' ') `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$Trigger = New-ScheduledTaskTrigger -Daily -At $RunAt
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Limited

if ($WhatIfPreference) {
    Write-Output ("CARDZ_ACTION_ARGUMENTS={0}" -f $Action.Arguments)
}

if ($PSCmdlet.ShouldProcess($TaskName, "Register unattended CARDZ pipeline at $At local time")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description 'CARDZ G10 acquisition, immutable incremental intake, canonical derivation, validation, and pointer-safe publish.' `
        -Force | Out-Null
    Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
}
