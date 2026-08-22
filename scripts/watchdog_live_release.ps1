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
  [switch]$ResetRatchet,
  [switch]$NoNotify
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

$nowUtc = [datetime]::UtcNow
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

function Parse-Iso([string]$s) {
  try {
    return [DateTimeOffset]::Parse($s, [cultureinfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AssumeUniversal)
  } catch { return $null }
}

function Send-Notify([string]$text) {
  if ($NoNotify) { Say "notify suppressed (-NoNotify)"; return }
  $notify = Join-Path $RepoRoot "scripts\notify_hermes.py"
  if (-not (Test-Path -LiteralPath $notify)) { Say "notify_hermes.py missing at $notify; cannot notify"; return }
  # HTML parse_mode on the TG side: strip angle brackets / ampersands.
  $safe = ($text -replace '[<>]', "'") -replace '&', 'and'
  if ($safe.Length -gt 1500) { $safe = $safe.Substring(0, 1480) + " ...(truncated)" }
  $help = (& $py -X utf8 $notify --help 2>&1 | Out-String)
  if ($help -match '\{[^}]*\bsend\b[^}]*\}') {
    & $py -X utf8 $notify send --text $safe *>> $log
  } else {
    # Stash-era notify_hermes.py has no `send`; ride the `chain` subcommand.
    & $py -X utf8 $notify chain --chain morning --status $safe --exit-code 1 --log $log *>> $log
  }
  Say "notify exit=$LASTEXITCODE"
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

  # ---- 1. live health -----------------------------------------------------
  $ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36 CARDZ-watchdog"
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

  if ($null -eq $health) {
    Fail "health unreachable/unparsable after 3 attempts: $HealthUrl"
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
  if (-not $SkipLogCheck) {
    $v2Day = Join-Path $RepoRoot "data\runtime\daily-chain-v2\$todayJst"
    $v2Logs = Join-Path $v2Day "logs"
    if (-not (Test-Path -LiteralPath $v2Day)) {
      Fail "V2 run dir missing: $v2Day"
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
  Send-Notify $summary
  exit 1
}

if ($state) {
  ($state | ConvertTo-Json -Depth 3) | Out-File -FilePath $statePath -Encoding utf8
  Say "state written $statePath"
}
$okLine = "OK CARDZ watchdog $todayJst JST gen=$($health.generation) cards=$($health.cards) box=$($health.box.total)"
if ($warnings.Count -gt 0) { $okLine += " warn: " + ($warnings -join " | ") }
Say $okLine
exit 0
