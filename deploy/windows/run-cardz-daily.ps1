[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$RepoRoot,
    [Parameter(Mandatory)]
    [string]$PythonExe,
    [Parameter(Mandatory)]
    [string]$EnvFile,
    [ValidateSet('staging', 'production')]
    [string]$Mode = 'production'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$python = (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).Path
$environment = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path
$backend = Join-Path $root 'scripts\backend.py'
if (-not [IO.File]::Exists($backend)) {
    throw 'scripts\backend.py is missing from RepoRoot.'
}

Get-Content -LiteralPath $environment | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        throw 'EnvFile contains an invalid environment assignment.'
    }
    Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2]
}

& $python -X utf8 $backend daily --external-db --mode $Mode
exit $LASTEXITCODE
