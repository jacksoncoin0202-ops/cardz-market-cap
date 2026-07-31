# Import self-issued Camofox local key into 1Password (FREE local key — not SaaS).
# Prereq: unlock 1Password desktop, then:
#   powershell -NoProfile -File scripts\camofox_1password_import.ps1

$ErrorActionPreference = "Stop"
$envFile = Join-Path $PSScriptRoot "..\data\private\camofox_local.env"
if (-not (Test-Path $envFile)) {
  throw "Missing $envFile — generate via WSL ~/.camofox/local-api.env first"
}
$key = $null
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*CAMOFOX_API_KEY=(.+)$') { $key = $Matches[1].Trim() }
}
if (-not $key) { throw "CAMOFOX_API_KEY not found in $envFile" }

$op = Get-Command op -ErrorAction SilentlyContinue
if (-not $op) {
  $wingetOp = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\AgileBits.1Password.CLI_Microsoft.Winget.Source_8wekyb3d8bbwe\op.exe"
  if (Test-Path $wingetOp) { $opPath = $wingetOp } else { throw "op CLI not found" }
} else { $opPath = $op.Source }

Write-Host "Using op: $opPath"
& $opPath whoami
if ($LASTEXITCODE -ne 0) { throw "op not signed in — unlock 1Password desktop / op signin" }

# upsert-ish: delete old title then create
& $opPath item delete "Camofox Local API Key" --vault Private 2>$null | Out-Null
& $opPath item create `
  --category "API Credential" `
  --title "Camofox Local API Key" `
  --vault Private `
  "credential=$key" `
  "username=local-self-issued" `
  "notesPlain=Self-issued FREE local key for WSL docker camofox-browser on 127.0.0.1:9377. NOT a paid SaaS key. Files: ~/.camofox/local-api.env ; cardz data/private/camofox_local.env"

Write-Host "OK: 1Password item Camofox Local API Key (suffix ...$($key.Substring($key.Length-6)))"
