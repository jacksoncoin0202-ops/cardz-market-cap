# Open / reuse Chrome with remote debugging so the CF session can be captured.
# Usage (PowerShell):
#   powershell -NoProfile -File scripts\open_chrome_pricecharting_debug.ps1
#
# If Chrome is already running WITHOUT debugging, this still opens a new window
# but CDP may not attach to the existing process. In that case close all Chrome
# windows and re-run this script once.

$ErrorActionPreference = "Stop"
$port = 9333
$url = "https://www.pricecharting.com/game/pokemon-base-set/charizard-4"

$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw "Chrome not found" }

Write-Host "Chrome: $chrome"
Write-Host "Starting with --remote-debugging-port=$port"
Write-Host "Open product page (pass CF if prompted): $url"

Start-Process -FilePath $chrome -ArgumentList @(
  "--remote-debugging-port=$port",
  "--remote-allow-origins=*",
  $url
)

Write-Host ""
Write-Host "After the product page loads (not 'Just a moment'), run:"
Write-Host "  python -X utf8 scripts\pricecharting_capture_and_parse.py --mode connect --port $port"
