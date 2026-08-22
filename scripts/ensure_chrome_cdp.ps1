# CARDZ owns one dedicated Windows Chrome DevTools session.
# Port 9333 + profile cardz-chrome-cdp-9333 only. Headed Windows only.
# Headless is rejected (CF blocks it; local CF tool needs a real window).
# A live /json/version is NOT enough: WSL Chrome on 9333 answers 200
# and the old script reused it (2026-08-18 morning would have crawled CF).
# This script is the single identity gate. Foreign listener → evict → revive.
param(
  [int]$Port = 9333,
  [string]$UserDataDir = "",
  [string]$StartUrl = "https://www.pricecharting.com/",
  [switch]$SelfTest,
  [switch]$IdentityOnly
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($UserDataDir)) {
  $UserDataDir = Join-Path $env:LOCALAPPDATA "cardz-chrome-cdp-$Port"
}

function Get-CdpVersion {
  param([int]$CandidatePort)
  try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$CandidatePort/json/version" -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -ne 200 -or [string]::IsNullOrWhiteSpace($response.Content)) {
      return $null
    }
    return ($response.Content | ConvertFrom-Json)
  } catch {
    return $null
  }
}

function Get-CdpTargetList {
  param([int]$CandidatePort, [int]$TimeoutSec = 5)
  # Version 200 with a hung /json/list is a jammed DevTools session.
  # Invoke-WebRequest -TimeoutSec does not reliably abort that hang on Windows;
  # curl.exe --max-time does. Empty/non-JSON/timeout → treat as jammed.
  try {
    $raw = & curl.exe -sS --max-time $TimeoutSec "http://127.0.0.1:$CandidatePort/json/list" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) {
      return $null
    }
    return ($raw | ConvertFrom-Json)
  } catch {
    return $null
  }
}

function Test-CdpTargetsReady {
  param([int]$CandidatePort)
  return $null -ne (Get-CdpTargetList -CandidatePort $CandidatePort)
}

function Get-CdpRejectReason {
  param($Version)
  if ($null -eq $Version) { return "no-listener" }
  $browser = [string]$Version.Browser
  $ua = [string]$Version.'User-Agent'
  $blob = "$browser $ua"
  if ($blob -match 'Linux|X11|WSL') { return "linux-or-wsl" }
  if ($blob -notmatch 'Windows NT') { return "not-windows" }
  if ($blob -match 'Headless') { return "headless" }
  return $null
}

function Clear-ForeignCdp {
  param([int]$CandidatePort)
  if ($CandidatePort -eq 9222) {
    throw "refusing to touch Codex/browser port 9222"
  }
  Write-Host "CDP_EVICT port=$CandidatePort"

  $listenPids = @()
  $net = netstat -ano
  foreach ($line in $net) {
    if ($line -match ":$CandidatePort\s+.*LISTENING\s+(\d+)\s*$") {
      $listenPids += [int]$Matches[1]
    }
  }
  $listenPids = $listenPids | Select-Object -Unique
  foreach ($listenPid in $listenPids) {
    $proc = Get-Process -Id $listenPid -ErrorAction SilentlyContinue
    if (-not $proc) { continue }
    if ($proc.ProcessName -eq "chrome") {
      $cmd = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$listenPid" -ErrorAction SilentlyContinue).CommandLine
      if ($cmd -match "--remote-debugging-port=9222\b") { continue }
      Write-Host "CDP_EVICT win-chrome pid=$listenPid"
      Stop-Process -Id $listenPid -Force -ErrorAction SilentlyContinue
    }
  }

  $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
  if ($wsl) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    # Only the process advertising this exact debug port. Never 9222/9223.
    $pattern = "--remote-debugging-port=$CandidatePort"
    Write-Host "CDP_EVICT wsl-pkill $pattern"
    & wsl.exe -d Ubuntu -- pkill -f -- $pattern 2>$null | Out-Null
    Start-Sleep -Milliseconds 400
    & wsl.exe -d Ubuntu -- pkill -9 -f -- $pattern 2>$null | Out-Null
    $ErrorActionPreference = $prev
  }

  $deadline = (Get-Date).AddSeconds(8)
  while ((Get-Date) -lt $deadline) {
    if ($null -eq (Get-CdpVersion -CandidatePort $CandidatePort)) { return }
    Start-Sleep -Milliseconds 300
  }
  Write-Host "CDP_EVICT_STILL_UP port=$CandidatePort"
}

function Start-CardzChrome {
  param([int]$CandidatePort, [string]$ProfileDir, [string]$Url)
  $chromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
  )
  $chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  if (-not $chrome) {
    Write-Host "CHROME_NOT_FOUND"
    return $false
  }
  New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
  Start-Process -FilePath $chrome -ArgumentList @(
    "--no-first-run",
    "--remote-debugging-port=$CandidatePort",
    "--remote-debugging-address=127.0.0.1",
    "--remote-allow-origins=*",
    "--user-data-dir=$ProfileDir",
    $Url
  )
  $deadline = (Get-Date).AddSeconds(20)
  while ((Get-Date) -lt $deadline) {
    $ver = Get-CdpVersion -CandidatePort $CandidatePort
    $why = Get-CdpRejectReason -Version $ver
    if ($null -eq $why -and (Test-CdpTargetsReady -CandidatePort $CandidatePort)) {
      Write-Host "CDP_REVIVED port=$CandidatePort profile=$ProfileDir"
      return $true
    }
    Start-Sleep -Milliseconds 500
  }
  Write-Host "CDP_REVIVE_FAILED port=$CandidatePort"
  return $false
}

function Invoke-SelfTest {
  $failed = 0
  function Expect([string]$label, $got, $want) {
    if ($got -ne $want) {
      Write-Host "SELFTEST_FAIL $label got=$got want=$want"
      $script:failed++
    } else {
      Write-Host "SELFTEST_OK $label"
    }
  }

  $headless = [pscustomobject]@{
    Browser = "Chrome/146.0.7680.75"
    "User-Agent" = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/146.0.0.0 Safari/537.36"
  }
  Expect "reject-wsl-headless" (Get-CdpRejectReason -Version $headless) "linux-or-wsl"

  $linuxHeaded = [pscustomobject]@{
    Browser = "Chrome/146.0.7680.75"
    "User-Agent" = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
  }
  Expect "reject-linux" (Get-CdpRejectReason -Version $linuxHeaded) "linux-or-wsl"

  $windowsHeaded = [pscustomobject]@{
    Browser = "Chrome/146.0.7680.75"
    "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
  }
  Expect "accept-windows-headed" (Get-CdpRejectReason -Version $windowsHeaded) $null

  $windowsHeadless = [pscustomobject]@{
    Browser = "Chrome/146.0.7680.75"
    "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/146.0.0.0 Safari/537.36"
  }
  Expect "reject-windows-headless" (Get-CdpRejectReason -Version $windowsHeadless) "headless"
  Expect "reject-empty" (Get-CdpRejectReason -Version $null) "no-listener"

  if ($Port -eq 9222) {
    Write-Host "SELFTEST_FAIL port-9222-accepted"
    $script:failed++
  } else {
    Write-Host "SELFTEST_OK refuse-default-not-9222"
  }

  # Planted live fire: if 9333 is up and wrong, the same function must reject it.
  if ($Port -ne 9222) {
    $live = Get-CdpVersion -CandidatePort $Port
    if ($null -ne $live) {
      $liveWhy = Get-CdpRejectReason -Version $live
      $blob = "$($live.Browser) $($live.'User-Agent')"
      if ($blob -match "Linux|X11|WSL") {
        if ($null -eq $liveWhy) {
          Write-Host "SELFTEST_FAIL live-wrong-chrome-not-rejected $blob"
          $script:failed++
        } else {
          Write-Host "SELFTEST_OK live-wrong-chrome-rejected reason=$liveWhy"
        }
      } else {
        Write-Host "SELFTEST_OK live-version-classified reason=$liveWhy"
      }
      $liveList = Get-CdpTargetList -CandidatePort $Port
      if ($null -eq $liveWhy -and $null -eq $liveList) {
        Write-Host "SELFTEST_OK live-identity-ok-but-targets-jammed"
      } elseif ($null -ne $liveList) {
        Write-Host "SELFTEST_OK live-targets-ready"
      } else {
        Write-Host "SELFTEST_OK live-targets-skipped reason=$liveWhy"
      }
    } else {
      Write-Host "SELFTEST_OK live-no-listener"
    }
  }

  if ($failed -gt 0) {
    Write-Host "SELFTEST_FAILED count=$failed"
    return 1
  }
  Write-Host "SELFTEST_PASSED"
  return 0
}

if ($Port -eq 9222) {
  Write-Host "CDP_REFUSED port=9222 (Codex browser profile; CARDZ uses 9333 only)"
  exit 1
}

if ($SelfTest) {
  exit (Invoke-SelfTest)
}

$version = Get-CdpVersion -CandidatePort $Port
$reason = Get-CdpRejectReason -Version $version
if ($null -eq $reason -and -not (Test-CdpTargetsReady -CandidatePort $Port)) {
  $reason = "jammed-targets"
}
if ($null -eq $reason) {
  if ($IdentityOnly) {
    Write-Host "CDP_IDENTITY_OK port=$Port"
    exit 0
  }
  Write-Host "CDP_OK port=$Port"
  exit 0
}

if ($IdentityOnly) {
  Write-Host "CDP_IDENTITY_REJECT port=$Port reason=$reason"
  exit 1
}

if ($reason -eq "jammed-targets") {
  Write-Host "CDP_JAMMED port=$Port reason=jammed-targets"
} elseif ($null -ne $version) {
  Write-Host "CDP_WRONG port=$Port reason=$reason browser=$($version.Browser)"
} else {
  Write-Host "CDP_EMPTY port=$Port (also evict IPv6-only leftovers)"
}
Clear-ForeignCdp -CandidatePort $Port

if (Start-CardzChrome -CandidatePort $Port -ProfileDir $UserDataDir -Url $StartUrl) {
  $again = Get-CdpRejectReason -Version (Get-CdpVersion -CandidatePort $Port)
  if ($null -ne $again) {
    Write-Host "CDP_REVIVE_STILL_WRONG reason=$again"
    exit 1
  }
  if (-not (Test-CdpTargetsReady -CandidatePort $Port)) {
    Write-Host "CDP_REVIVE_STILL_WRONG reason=jammed-targets"
    exit 1
  }
  exit 0
}
exit 1
