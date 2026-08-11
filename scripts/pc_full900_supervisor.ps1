# PC-FULL-900 HARD SUPERVISOR — multi-worker rules so the job cannot silently die.
#
# RULES (non-negotiable):
# R1 CDP live before any worker runs (ensure_chrome_cdp.ps1).
# R2 Max concurrent PC-CDP workers = 1 (one Chrome lock; more is thrash not speed).
# R3 Stale/dead lock file auto-cleared (PID gone or age).
# R4 Workers resume from results_shard_*.jsonl (idempotent).
# R5 If CDP dies mid-run: kill worker, revive CDP, restart worker — loop, no human "watch".
# R6 CF "請稍候" longer than threshold: leave tab open, log HUMAN_CF_NEEDED once, keep waiting
#    (runner already waits; supervisor only ensures browser process lives).
#
# Usage:
#   powershell -NoProfile -File scripts\pc_full900_supervisor.ps1
#   powershell -NoProfile -File scripts\pc_full900_supervisor.ps1 -Once   # one health cycle

param(
  [switch]$Once,
  [int]$PollSeconds = 45,
  [int]$Port = 9333
)

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$Out = Join-Path $Root "data\runtime\private-reports\fill\PC-FULL-900"
$Lock = Join-Path $Out "cdp.lock"
$Python = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
$Ensure = Join-Path $Root "scripts\ensure_chrome_cdp.ps1"
$Driver = Join-Path $Root "pipelines\pc_full_serial_driver.py"
$SupLog = Join-Path $Out "supervisor.log"

function Log([string]$m) {
  $line = "{0} {1}" -f (Get-Date -Format "o"), $m
  Add-Content -Path $SupLog -Value $line -Encoding utf8
  Write-Host $line
}

function Clear-DeadLock {
  if (-not (Test-Path $Lock)) { return }
  $txt = (Get-Content $Lock -Raw -ErrorAction SilentlyContinue)
  $pidStr = ($txt -split "\s+")[0]
  $pidNum = 0
  [void][int]::TryParse($pidStr, [ref]$pidNum)
  $ageMin = ((Get-Date) - (Get-Item $Lock).LastWriteTime).TotalMinutes
  $alive = $false
  if ($pidNum -gt 0) {
    $alive = $null -ne (Get-Process -Id $pidNum -ErrorAction SilentlyContinue)
  }
  if (-not $alive -or $ageMin -gt 3) {
    Remove-Item $Lock -Force -ErrorAction SilentlyContinue
    Log "LOCK_CLEARED pid=$pidStr alive=$alive ageMin=$([math]::Round($ageMin,1))"
  }
}

function Test-Cdp {
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:$Port/json/version" -UseBasicParsing -TimeoutSec 2
    return $r.StatusCode -eq 200
  } catch { return $false }
}

function Get-RunnerProcs {
  # Standalone shard runners only (not ideal; serial driver is preferred).
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match "pc_full_shard_runner") -and ($_.CommandLine -notmatch "_serial_driver") }
}

function Get-SerialProcs {
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match "_serial_driver|pc_full_all_serial") }
}

function Stop-AllRunners {
  Get-RunnerProcs | ForEach-Object {
    Log "KILL_RUNNER pid=$($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Get-SerialProcs | ForEach-Object {
    Log "KILL_SERIAL pid=$($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
}

function Start-OptimalWorker {
  # R2: exactly one worker — processes all shards sequentially in one process via loop
  $log = Join-Path $Out "runner_supervised.out.log"
  $err = Join-Path $Out "runner_supervised.err.log"
  $p = Start-Process -FilePath $Python -ArgumentList @("-X", "utf8", $Driver, "--shards", "6") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput $log -RedirectStandardError $err -PassThru
  Log "START_SERIAL_WORKER pid=$($p.Id)"
}

function Progress-Summary {
  $n = 0
  Get-ChildItem (Join-Path $Out "results_shard_*.jsonl") -ErrorAction SilentlyContinue | ForEach-Object {
    $n += @(Get-Content $_.FullName | Where-Object { $_.Trim() }).Count
  }
  return $n
}

New-Item -ItemType Directory -Force -Path $Out | Out-Null
Log "SUPERVISOR_START root=$Root port=$Port"

while ($true) {
  Clear-DeadLock

  if (-not (Test-Cdp)) {
    Log "CDP_DOWN — revive"
    Stop-AllRunners
    Clear-DeadLock
    & powershell -NoProfile -File $Ensure -Port $Port
    if (-not (Test-Cdp)) {
      Log "CDP_STILL_DOWN — sleep and retry"
      if ($Once) { exit 2 }
      Start-Sleep $PollSeconds
      continue
    }
    Log "CDP_OK after revive"
  }

  $runners = @(Get-RunnerProcs)
  $serials = @(Get-SerialProcs)
  $nSerial = $serials.Count
  $nRunner = $runners.Count

  # R2 optimal: exactly one serial driver (it may spawn one child shard_runner at a time — OK).
  # Forbidden: multiple serials, OR any standalone shard_runner without serial, OR many standalone runners.

  if ($nSerial -eq 0 -and $nRunner -eq 0) {
    $done = 0
    0..5 | ForEach-Object { if (Test-Path (Join-Path $Out "summary_shard_$_.json")) { $done++ } }
    $lines = Progress-Summary
    if ($done -ge 6) {
      Log "ALL_SHARDS_DONE lines=$lines"
      exit 0
    }
    Log "NO_WORKER lines=$lines — start serial worker (R2 max=1)"
    Start-OptimalWorker
  } elseif ($nSerial -gt 1 -or ($nSerial -eq 0 -and $nRunner -ge 1) -or ($nSerial -ge 1 -and $nRunner -ge 2)) {
    Log "TOO_MANY_WORKERS serial=$nSerial runner=$nRunner — collapse to 1 serial"
    Stop-AllRunners
    Clear-DeadLock
    Start-Sleep 2
    Start-OptimalWorker
  } else {
    Log "HEALTH_OK serial=$nSerial runner=$nRunner cdp=up lines=$(Progress-Summary)"
  }

  if ($Once) { exit 0 }
  Start-Sleep $PollSeconds
}
