# CARDZ 037 external watchdog: did the public site actually refresh today?
# Registered as Task Scheduler job CARDZ-037-Watchdog-Live-Release (17:30 JST).
#
# Why this exists: every chain (nightly/morning/refresh) only reports on itself,
# and a task that never launched, was killed by ExecutionTimeLimit, or crashed
# before its first log line reports nothing at all. This script is outside the
# chain: it asks the live site, compares against yesterday's numbers (ratchet),
# and checks that today's chain logs exist. Any red -> notify_hermes.py + exit 1.
#
# Proof of fire:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\watchdog_live_release.ps1 -ExpectDate 2000-01-01
# Dry run:        ... -NoNotify -StateDir $env:TEMP -LogDir $env:TEMP
# Reset ratchet:  ... -ResetRatchet   (after an intentional universe/box shrink)
param(
  [string]$HealthUrl = "https://app.cardzmarketcap.com/api/health",
  [string]$RepoRoot = "",
  [string]$StateDir = "",
  [string]$LogDir = "",
  [string]$ExpectDate = "",
  [double]$CardsDropPct = 1.0,
  [int]$BoxMaxAgeDays = 2,
  [string]$BoxSidecarPath = "\\wsl$\Ubuntu\home\jackson0202\cardz-market-cap-release-daily\data\public\box-subset.json",
  [switch]$SkipLogCheck,
  [switch]$SkipLiveCheck,
  [switch]$ResetRatchet,
  [switch]$NoNotify,
  [switch]$NotifyDryRun,
  [string]$HealthPath = "",
  # PowerShell variables are case-insensitive: a parameter literally named
  # $NowUtc would BE the working $nowUtc below and silently poison every clock
  # read in this script. Alias keeps the -NowUtc switch, different storage.
  [Alias('NowUtc')][string]$NowUtcOverride = "",
  [int]$HealthMaxAgeMin = 25
)
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) { $RepoRoot = Split-Path -Parent $PSScriptRoot }
. (Join-Path $RepoRoot "scripts\cardz_chain_lib.ps1")
if ([string]::IsNullOrWhiteSpace($StateDir)) { $StateDir = Join-Path $RepoRoot "data\runtime\operator" }
$chainLogDir = Join-Path $RepoRoot "data\runtime\logs"
if ([string]::IsNullOrWhiteSpace($LogDir)) { $LogDir = $chainLogDir }
$py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
New-Item -ItemType Directory -Force $LogDir | Out-Null
New-Item -ItemType Directory -Force $StateDir | Out-Null

if ([string]::IsNullOrWhiteSpace($HealthPath)) {
  if (-not [string]::IsNullOrWhiteSpace($env:CARDZ_V2_HEALTH_PATH)) {
    $HealthPath = $env:CARDZ_V2_HEALTH_PATH
  } else {
    $HealthPath = Join-Path $RepoRoot "data\runtime\daily-chain-v2\health.json"
  }
}

$nowUtc = [datetime]::UtcNow
if (-not [string]::IsNullOrWhiteSpace($NowUtcOverride)) {
  $nowUtc = [DateTimeOffset]::Parse(
    $NowUtcOverride, [cultureinfo]::InvariantCulture,
    [System.Globalization.DateTimeStyles]::AssumeUniversal).UtcDateTime
}
$stamp = $nowUtc.ToString("yyyyMMdd'T'HHmmss'Z'")
$log = Join-Path $LogDir "watchdog-$stamp.log"
$statePath = Join-Path $StateDir "watchdog_last_health.json"

$failures = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]
function Say([string]$m) { "[$([datetime]::UtcNow.ToString('o'))] $m" | Tee-Object -FilePath $log -Append }
function Fail([string]$m) { $failures.Add($m); Say "FAIL $m" }
function Warn([string]$m) { $warnings.Add($m); Say "WARN $m" }

$jst = [System.TimeZoneInfo]::FindSystemTimeZoneById("Tokyo Standard Time")
$nowJst = [System.TimeZoneInfo]::ConvertTimeFromUtc($nowUtc, $jst)
$todayJst = $nowJst.ToString("yyyy-MM-dd")
if (-not [string]::IsNullOrWhiteSpace($ExpectDate)) { $todayJst = $ExpectDate }
$todayUtc = $nowUtc.ToString("yyyyMMdd")
$yesterdayUtc = $nowUtc.AddDays(-1).ToString("yyyyMMdd")

# The V2 chain ticks 03:30-17:00 JST (last scheduled tick) and must be DONE or
# PUBLISHED by 17:30 JST. Inside that window we police health.json freshness;
# only after it do we police the live site.
$jstWindowStart = [TimeSpan]::FromMinutes(210)
$jstWindowEnd = [TimeSpan]::FromMinutes(1050)
$inWindow = ($nowJst.TimeOfDay -ge $jstWindowStart -and $nowJst.TimeOfDay -le $jstWindowEnd)
$afterWindow = ($nowJst.TimeOfDay -gt $jstWindowEnd)
$runLiveCheck = (-not $SkipLiveCheck) -and ($afterWindow -or -not [string]::IsNullOrWhiteSpace($ExpectDate))
$runLogCheck = $afterWindow -or -not [string]::IsNullOrWhiteSpace($ExpectDate)
$healthAlerts = New-Object System.Collections.Generic.List[string]

function Parse-Iso([string]$s) {
  try {
    return [DateTimeOffset]::Parse($s, [cultureinfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AssumeUniversal)
  } catch { return $null }
}

function Resolve-V2DayDirectory([string]$root, [string]$businessDate, [string]$runId) {
  # The journal run id is the lineage authority.  A superseded run /N writes
  # runtime artifacts under <date>-SN; probing the unsuffixed date would report
  # a false red after a successful replacement publish.
  $baseRunId = "cardz-v2:$businessDate"
  if ($runId -eq $baseRunId) {
    return (Join-Path $root "data\runtime\daily-chain-v2\$businessDate")
  }
  $escapedDate = [regex]::Escape($businessDate)
  if ($runId -match "^cardz-v2:$escapedDate/([1-9][0-9]*)$") {
    $seq = [int]$Matches[1]
    return (Join-Path $root "data\runtime\daily-chain-v2\$businessDate-S$seq")
  }
  throw "health run_id does not match business date: run_id='$runId' business_date='$businessDate'"
}

# Contract C3: notify_hermes.py only ever had `alert --key --text --level
# --cooldown-min`. The old probe for a `send` subcommand always missed and fell
# back to the `chain` subcommand, so watchdog alerts arrived labelled "morning".
function Send-Notify([string]$key, [string]$text, [string]$level = "error", [int]$CooldownMin = 60) {
  # HTML parse_mode on the TG side: strip angle brackets / ampersands.
  $safe = ($text -replace '[<>]', "'") -replace '&', 'and'
  if ($safe.Length -gt 1500) { $safe = $safe.Substring(0, 1480) + " ...(truncated)" }
  if ($NotifyDryRun) { Write-Output "NOTIFY_DRYRUN $key $level $safe"; Say "NOTIFY_DRYRUN $key $level $safe"; return }
  if ($NoNotify) { Say "notify suppressed (-NoNotify) key=$key"; return }
  $notify = Join-Path $RepoRoot "scripts\notify_hermes.py"
  if (-not (Test-Path -LiteralPath $notify)) { Say "notify_hermes.py missing at $notify; cannot notify"; return }
  & $py -X utf8 $notify alert --key $key --text $safe --level $level --cooldown-min $CooldownMin --require-delivery *>> $log
  $notifyExit = $LASTEXITCODE
  Say "notify key=$key exit=$notifyExit"
  if ($notifyExit -ne 0) {
    $failDir = Join-Path $StateDir "notify-failures"
    New-Item -ItemType Directory -Force $failDir | Out-Null
    $failPath = Join-Path $failDir "$stamp-$key.json"
    [ordered]@{
      contract = "cardz-watchdog-alert-delivery-failure-v1"
      key = $key
      atUtc = $nowUtc.ToString("o")
      exitCode = $notifyExit
    } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $failPath -Encoding UTF8
    Say "notify delivery failed artifact=$failPath"
  }
}

function Send-HealthAlert([string]$key, [string]$text, [string]$level) {
  $healthAlerts.Add($key)
  Say "ALERT $key $level $text"
  Send-Notify $key $text $level 60
}

$health = $null
$healthRaw = ""
$last = $null
$state = $null
try {
  Say "watchdog start jstDate=$todayJst expectOverride=$([bool]$ExpectDate) repo=$RepoRoot"

  if (Test-Path -LiteralPath $statePath) {
    try { $last = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Warn "state file unreadable: $statePath ($($_.Exception.Message))" }
  }

  # ---- 0. orchestrator health.json (contract C1) --------------------------
  # A tick that dies on CTRL_CLOSE writes nothing anywhere else; this file is the
  # only thing that says "the chain is still alive" between 03:30 and 17:30 JST.
  Say "health path=$HealthPath inWindow=$inWindow afterWindow=$afterWindow jst=$($nowJst.ToString('HH:mm'))"
  $hj = $null
  if (Test-Path -LiteralPath $HealthPath) {
    try { $hj = Get-Content -LiteralPath $HealthPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { $hj = $null; Warn "health.json unparsable: $($_.Exception.Message)" }
  }
  if ($null -eq $hj) {
    if ($inWindow) {
      Send-HealthAlert "v2-health-missing" "CARDZ V2 health.json missing/unreadable at $HealthPath inside the 03:30-17:30 JST window (JST $($nowJst.ToString('HH:mm')))" "error"
    } elseif ($afterWindow) {
      Send-HealthAlert "v2-not-done" "CARDZ V2 health.json missing/unreadable at $HealthPath after 17:30 JST; the chain never reported DONE/PUBLISHED" "error"
    } else {
      Say "health.json absent outside the JST window; no alert"
    }
  } else {
    $runState = [string]$hj.run_state
    $tickPhase = [string]$hj.tick_phase
    # DONE only counts for TODAY's business day: a leftover health.json from
    # yesterday's PUBLISHED run must not silence every alert until 17:30 JST.
    $healthDate = [string]$hj.business_date
    # Nor does a SUPERSEDED date count: its PUBLISHED run was archived so the
    # date could be run again, and the replacement has not confirmed its own
    # publication yet. Absent field (pre-supersede health.json) reads $false.
    $supersedePending = [bool]$hj.supersede_pending
    $done = (@("DONE", "PUBLISHED") -contains $runState) -and ($healthDate -eq $todayJst) -and (-not $supersedePending)
    $writtenAt = Parse-Iso ([string]$hj.written_at_utc)
    $ageMin = $null
    if ($null -ne $writtenAt) { $ageMin = [math]::Round(($nowUtc - $writtenAt.UtcDateTime).TotalMinutes, 1) }
    Say "health run_state=$runState tick_phase=$tickPhase ageMin=$ageMin businessDate=$healthDate expectedDate=$todayJst supersedePending=$supersedePending done=$done"

    if ($inWindow -and -not $done) {
      if ($null -eq $writtenAt) {
        Send-HealthAlert "v2-health-stale" "CARDZ V2 health.json written_at_utc unparsable ('$($hj.written_at_utc)') run_state=$runState tick_phase=$tickPhase" "error"
      } elseif ($ageMin -gt $HealthMaxAgeMin) {
        Send-HealthAlert "v2-health-stale" "CARDZ V2 health.json is ${ageMin}min old (max $HealthMaxAgeMin) run_state=$runState tick_phase=$tickPhase businessDate=$($hj.business_date)" "error"
      }
    }

    $parked = @()
    if ($null -ne $hj.parked) { $parked = @($hj.parked) }
    if ($parked.Count -gt 0) {
      Send-HealthAlert "v2-parked" "CARDZ V2 parked tasks: $($parked -join ', ') -- unpark with: python3 pipelines/daily_chain_v2.py unpark --task $([string]$parked[0])" "warn"
    }

    if ($afterWindow -and -not $done) {
      Send-HealthAlert "v2-not-done" "CARDZ V2 run_state=$runState after 17:30 JST (tick_phase=$tickPhase businessDate=$($hj.business_date) ageMin=$ageMin)" "error"
    }
  }

  # ---- 1. live health -----------------------------------------------------
  # Runs every 15 min now, but the live site is only expected to be today's
  # after the 17:30 JST publish window, so before that this section is skipped.
  $ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36 CARDZ-watchdog"
  if ($runLiveCheck) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
      try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UserAgent $ua -UseBasicParsing -TimeoutSec 20
        if ($resp.StatusCode -eq 200) {
          $healthRaw = [string]$resp.Content
          $health = $healthRaw | ConvertFrom-Json
          break
        }
        Warn "health http $($resp.StatusCode) attempt $attempt"
      } catch {
        Warn "health fetch attempt ${attempt}: $($_.Exception.Message)"
      }
      if ($attempt -lt 3) { Start-Sleep -Seconds 10 }
    }
  } else {
    Say "live-release check skipped (JST $($nowJst.ToString('HH:mm')) not past 17:30; no -ExpectDate)"
  }

  if ($null -eq $health) {
    if ($runLiveCheck) { Fail "health unreachable/unparsable after 3 attempts: $HealthUrl" }
  } else {
    Say "health $healthRaw"
    if ([string]$health.status -ne "ok") { Fail "status=$($health.status)" }

    $genAt = Parse-Iso ([string]$health.generatedAt)
    if ($null -eq $genAt) {
      Fail "generatedAt unparsable: '$($health.generatedAt)'"
    } else {
      $genJstDate = [System.TimeZoneInfo]::ConvertTime($genAt, $jst).ToString("yyyy-MM-dd")
      if ($genJstDate -ne $todayJst) {
        Fail "generatedAt JST date $genJstDate != $todayJst (generation=$($health.generation) generatedAt=$($health.generatedAt))"
      } else {
        Say "generatedAt ok jst=$genJstDate generation=$($health.generation)"
      }
    }

    $cards = 0
    try { $cards = [int]$health.cards } catch { $cards = 0 }
    if ($cards -le 0) {
      Fail "cards=$cards"
    } elseif ($last -and $last.cards -and -not $ResetRatchet) {
      $floor = [math]::Floor([double]$last.cards * (1.0 - ($CardsDropPct / 100.0)))
      if ($cards -lt $floor) { Fail "cards $cards dropped more than $CardsDropPct% below last $($last.cards) (floor $floor)" }
      else { Say "cards ok $cards (last $($last.cards), floor $floor)" }
    } else {
      Warn "cards ratchet seeded at $cards (no prior state or -ResetRatchet)"
    }

    $boxTotal = 0; $boxImaged = 0; $boxAsOf = ""
    if ($null -eq $health.box) {
      Fail "health.box missing"
    } else {
      try { $boxTotal = [int]$health.box.total } catch { $boxTotal = 0 }
      try { $boxImaged = [int]$health.box.imaged } catch { $boxImaged = 0 }
      if ($boxTotal -le 0) { Fail "box.total=$boxTotal" }
      if ($last -and $last.boxTotal -and -not $ResetRatchet) {
        $bfloor = [math]::Floor([double]$last.boxTotal * (1.0 - ($CardsDropPct / 100.0)))
        if ($boxTotal -lt $bfloor) { Fail "box.total $boxTotal dropped below last $($last.boxTotal) (floor $bfloor)" }
      }
      if ($last -and $last.boxImaged -and -not $ResetRatchet) {
        $ifloor = [math]::Floor([double]$last.boxImaged * (1.0 - ($CardsDropPct / 100.0)))
        if ($boxImaged -lt $ifloor) { Fail "box.imaged $boxImaged dropped below last $($last.boxImaged) (floor $ifloor)" }
      }
      $asOfSrc = ""
      if ($health.box.PSObject.Properties.Name -contains "asOf") {
        $boxAsOf = [string]$health.box.asOf; $asOfSrc = "health.box.asOf"
      } elseif (-not [string]::IsNullOrWhiteSpace($BoxSidecarPath) -and (Test-Path -LiteralPath $BoxSidecarPath)) {
        try {
          $sidecar = Get-Content -LiteralPath $BoxSidecarPath -Raw -Encoding UTF8 | ConvertFrom-Json
          $boxAsOf = [string]$sidecar.asOf; $asOfSrc = "sidecar $BoxSidecarPath"
        } catch { Warn "box sidecar unreadable: $($_.Exception.Message)" }
      }
      if ([string]::IsNullOrWhiteSpace($boxAsOf)) {
        Warn "box asOf not in health and sidecar unavailable; box age check skipped"
      } elseif ($BoxMaxAgeDays -gt 0) {
        $bo = Parse-Iso $boxAsOf
        if ($null -eq $bo) { Warn "box asOf unparsable: $boxAsOf" }
        else {
          $ageDays = [math]::Round(($nowUtc - $bo.UtcDateTime).TotalDays, 2)
          if ($ageDays -gt $BoxMaxAgeDays) { Fail "box asOf $boxAsOf is ${ageDays}d old (max $BoxMaxAgeDays; $asOfSrc)" }
          else { Say "box asOf ok ${ageDays}d ($asOfSrc)" }
        }
      }
    }

    $state = [ordered]@{
      checkedAtUtc = $nowUtc.ToString("o")
      jstDate      = $todayJst
      generation   = [string]$health.generation
      generatedAt  = [string]$health.generatedAt
      cards        = $cards
      boxTotal     = $boxTotal
      boxImaged    = $boxImaged
      boxAsOf      = $boxAsOf
    }
  }

  # ---- 2. did today's V2 chain launch? ------------------------------------
  # 037 nightly/morning/refresh logs are not the live path after V2 cutover.
  if ($runLogCheck -and -not $SkipLogCheck) {
    if ($null -eq $hj) {
      throw "V2 run lineage unavailable because health.json is missing/unreadable"
    }
    $v2RunId = [string]$hj.run_id
    $v2Day = Resolve-V2DayDirectory $RepoRoot $todayJst $v2RunId
    $v2Logs = Join-Path $v2Day "logs"
    Say "V2 run dir resolved runId=$v2RunId path=$v2Day"
    if (-not (Test-Path -LiteralPath $v2Day)) {
      Fail "V2 run dir missing for runId=$v2RunId`: $v2Day"
    } else {
      $files = @()
      if (Test-Path -LiteralPath $v2Logs) {
        $files = @(Get-ChildItem -LiteralPath $v2Logs -File -ErrorAction SilentlyContinue)
      }
      if ($files.Count -lt 1) {
        Fail "V2 logs missing under $v2Logs"
      } else {
        $latest = @($files | Sort-Object LastWriteTime)[-1]
        Say "V2 logs $($files.Count) latest=$($latest.Name) $($latest.LastWriteTime.ToString('o'))"
      }
    }
  }
} catch {
  Fail "watchdog CRASH: $($_.Exception.Message) @ $($_.InvocationInfo.ScriptLineNumber)"
}

# ---- 3. verdict -----------------------------------------------------------
if ($failures.Count -gt 0) {
  $summary = "RED CARDZ watchdog $todayJst JST: $($failures.Count) fail. " + ($failures -join " | ")
  if ($warnings.Count -gt 0) { $summary += " || warn: " + ($warnings -join " | ") }
  if ($health) { $summary += " || live gen=$($health.generation) generatedAt=$($health.generatedAt) cards=$($health.cards)" }
  $summary += " || log=data/runtime/logs/$(Split-Path -Leaf $log)"
  Say $summary
  Send-Notify "v2-live-release" $summary "error" 60
  exit 1
}

if ($state) {
  ($state | ConvertTo-Json -Depth 3) | Out-File -FilePath $statePath -Encoding utf8
  Say "state written $statePath"
}
if ($healthAlerts.Count -gt 0) {
  Say "RED CARDZ watchdog $todayJst JST health alerts: $($healthAlerts -join ', ')"
  exit 2
}
$okLine = "OK CARDZ watchdog $todayJst JST"
if ($health) { $okLine += " gen=$($health.generation) cards=$($health.cards) box=$($health.box.total)" }
else { $okLine += " live-check=skipped health=ok" }
if ($warnings.Count -gt 0) { $okLine += " warn: " + ($warnings -join " | ") }
Say $okLine
exit 0
