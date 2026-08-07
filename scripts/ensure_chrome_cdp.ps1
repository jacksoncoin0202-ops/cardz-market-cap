# CARDZ owns one dedicated Chrome DevTools session for serial PriceCharting work.
param(
  [int]$Port = 9333,
  [string]$UserDataDir = "",
  [string]$StartUrl = "https://www.pricecharting.com/"
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($UserDataDir)) {
  $UserDataDir = Join-Path $env:LOCALAPPDATA "cardz-chrome-cdp-$Port"
}

function Test-CardzCdp {
  param([int]$CandidatePort)
  try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$CandidatePort/json/version" -UseBasicParsing -TimeoutSec 2
    return $response.StatusCode -eq 200
  } catch {
    return $false
  }
}

if (Test-CardzCdp -CandidatePort $Port) {
  Write-Host "CDP_OK port=$Port"
  exit 0
}

$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
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
  if (Test-CardzCdp -CandidatePort $Port) {
    Write-Host "CDP_REVIVED port=$Port profile=$UserDataDir"
    exit 0
  }
  Start-Sleep -Milliseconds 500
}

Write-Host "CDP_REVIVE_FAILED port=$Port"
exit 1
