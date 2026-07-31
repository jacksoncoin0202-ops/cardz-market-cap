# HARD RULE: PC / PriceCharting work MUST have live CDP before any fetch.
# This script is the single revive path — agents call it; humans do not "remember".
#
# Usage:
#   powershell -NoProfile -File scripts\ensure_chrome_cdp.ps1
#   powershell -NoProfile -File scripts\ensure_chrome_cdp.ps1 -Port 9222
# Exit 0 = CDP live; 1 = failed after revive attempt

param(
  [int]$Port = 9222,
  [string]$UserDataDir = "$env:LOCALAPPDATA\cardz-chrome-cdp-9222",
  [string]$StartUrl = "https://www.pricecharting.com/"
)

$ErrorActionPreference = "Stop"

function Test-Cdp {
  param([int]$P)
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$P/json/version" -UseBasicParsing -TimeoutSec 2
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

if (Test-Cdp -P $Port) {
  Write-Host "CDP_OK port=$Port"
  exit 0
}

Write-Host "CDP_DOWN port=$Port — reviving Chrome debug profile"
$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
  Write-Host "CHROME_NOT_FOUND"
  exit 1
}

New-Item -ItemType Directory -Force -Path $UserDataDir | Out-Null
Start-Process -FilePath $chrome -ArgumentList @(
  "--remote-debugging-port=$Port",
  "--remote-allow-origins=*",
  "--user-data-dir=$UserDataDir",
  $StartUrl
) -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
  if (Test-Cdp -P $Port) {
    Write-Host "CDP_REVIVED port=$Port profile=$UserDataDir"
    exit 0
  }
  Start-Sleep -Milliseconds 500
}

Write-Host "CDP_REVIVE_FAILED port=$Port"
exit 1
