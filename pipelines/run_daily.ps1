[CmdletBinding()]
param(
    [ValidateSet('staging', 'production')]
    [string]$Mode = 'staging',
    [string]$R2Bucket,
    [string]$PrivateAcquireScript = $env:CARDZ_PRIVATE_ACQUIRE_SCRIPT,
    [string]$PythonExe = $(if ($env:CARDZ_PYTHON) { $env:CARDZ_PYTHON } else { 'python.exe' }),
    [string]$CanaryOrigin = $env:CARDZ_CANARY_ORIGIN,
    [string]$GenerationCanaryCommandJson = $env:CARDZ_GENERATION_CANARY_COMMAND_JSON,
    [string]$PointerPromoteCommandJson = $env:CARDZ_POINTER_PROMOTE_COMMAND_JSON,
    [switch]$RefreshBootstrapSource,
    [switch]$SkipMarketSourceRefresh,
    [switch]$RequireGemRateRefresh,
    [switch]$RefreshActiveUniverse,
    [switch]$AllowStaleDemo,
    [switch]$LocalOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$UtcStamp = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH-mm-ssZ')
$LogDirectory = Join-Path $ProjectRoot 'data\runtime\logs'
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null

if ([string]::IsNullOrWhiteSpace($R2Bucket)) {
    $R2Bucket = if ($Mode -eq 'production') { $env:CARDZ_PRODUCTION_R2_BUCKET } else { $env:CARDZ_STAGING_R2_BUCKET }
}

if ($RefreshBootstrapSource -and [string]::IsNullOrWhiteSpace($PrivateAcquireScript)) {
    $PrivateAcquireScript = Join-Path $ProjectRoot 'integrations\grade10\run_service.py'
}
if (-not $LocalOnly -and [string]::IsNullOrWhiteSpace($R2Bucket)) {
    throw 'Remote daily publish is not configured. Set CARDZ_STAGING_R2_BUCKET or pass -R2Bucket.'
}
if (-not $LocalOnly -and [string]::IsNullOrWhiteSpace($CanaryOrigin)) {
    throw 'Remote daily publish requires an explicit CARDZ_CANARY_ORIGIN or -CanaryOrigin.'
}
if (-not $LocalOnly -and [string]::IsNullOrWhiteSpace($GenerationCanaryCommandJson)) {
    throw 'Remote daily publish requires an explicit generation canary command JSON.'
}
if (-not $LocalOnly -and [string]::IsNullOrWhiteSpace($PointerPromoteCommandJson)) {
    throw 'Remote daily publish requires an explicit pointer promoter command JSON.'
}
if (-not $LocalOnly) {
    $CanaryUri = [Uri]$CanaryOrigin
    if (-not $CanaryUri.IsAbsoluteUri -or $CanaryUri.Scheme -ne 'https') {
        throw 'CanaryOrigin must be an absolute HTTPS origin for unattended publication.'
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
    $env:CARDZ_DEPLOYMENT_ENV = $Mode
    $env:CARDZ_CANARY_ORIGIN = $CanaryUri.GetLeftPart([UriPartial]::Authority)
    $env:CARDZ_GENERATION_CANARY_COMMAND_JSON = $GenerationCanaryCommandJson
    $env:CARDZ_POINTER_PROMOTE_COMMAND_JSON = $PointerPromoteCommandJson
    if ($Mode -eq 'staging') {
        $env:CARDZ_STAGING_R2_BUCKET = $R2Bucket
    }
}

$Arguments = @(
    (Join-Path $PSScriptRoot 'run_daily.py'),
    '--mode', $Mode
)
if ($RefreshBootstrapSource) {
    $Arguments += '--refresh-bootstrap-source'
    $Arguments += @('--private-acquire-script', $PrivateAcquireScript)
}
if ($R2Bucket) {
    $Arguments += @('--r2-bucket', $R2Bucket)
}
if ($SkipMarketSourceRefresh) {
    $Arguments += '--skip-market-source-refresh'
}
if ($RequireGemRateRefresh) {
    $Arguments += '--require-gemrate-refresh'
}
if ($RefreshActiveUniverse) {
    $Arguments += '--refresh-active-universe'
}
if ($AllowStaleDemo) {
    $Arguments += '--allow-stale-demo'
}
if ($LocalOnly) {
    $Arguments += '--local-only'
}

$LogPath = Join-Path $LogDirectory "$UtcStamp.log"
& $PythonExe @Arguments 2>&1 | Tee-Object -FilePath $LogPath
if ($LASTEXITCODE -ne 0) {
    throw "CARDZ daily pipeline failed with exit code $LASTEXITCODE. The previous latest pointer was preserved."
}
