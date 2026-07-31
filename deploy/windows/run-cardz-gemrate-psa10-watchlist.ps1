[CmdletBinding()]
param(
    [string]$RepoRoot = 'C:\Users\jackson0202\Documents\Playground\cardz-market-cap',
    [string]$PythonExe = 'C:\Users\jackson0202\Documents\Playground\cardz-market-cap\.venv-backend-windows\Scripts\python.exe',
    [string]$EnvFile = 'C:\Users\jackson0202\Documents\Playground\cardz-market-cap\data\runtime\config\backend.env'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$python = (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).Path
$environment = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path

Get-Content -LiteralPath $environment | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        throw 'EnvFile contains an invalid environment assignment.'
    }
    Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2]
}

$script = Join-Path $root 'pipelines\gemrate_brute_harvest.py'
$logDir = Join-Path $root 'data\runtime\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("gemrate_psa10_watchlist_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))

Push-Location $root
try {
    & cmd.exe /c "`"$python`" -X utf8 `"$script`" --all-sets --sync-db > `"$log`" 2>&1"
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
