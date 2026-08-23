# CARDZ Marketcap Daily Chain V2 Windows boundary.
# Task Scheduler provenance is captured here; WSL never accepts a caller-set
# "scheduled" flag as autonomy evidence.
[CmdletBinding()]
param(
    [switch]$AllowPublish,
    [switch]$Notify,
    [switch]$ManualE2E,
    [switch]$RenewManualWindow,
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$BusinessDate,
    # R2 (2026-08-24): claiming closes at 35 min so the drain fills the
    # remaining ~12 min of the same PT55M window.  Must equal
    # DEFAULT_MAX_RUNTIME_SECONDS in pipelines/daily_chain_v2.py and
    # $MaxRuntimeSeconds in scripts/install_cardz_daily_v2_task.ps1;
    # scripts/test_v2_tick_budget.py fails if any side drifts.
    [int]$MaxRuntimeSeconds = 2100,
    [ValidatePattern('^[A-Z][A-Z0-9]{1,7}$')]
    [string]$RunLabel,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
$TaskName = "\CARDZ-Marketcap-Daily-V2"
$Repo = Split-Path -Parent $PSScriptRoot
$Runtime = if ([string]::IsNullOrWhiteSpace($env:CARDZ_V2_LAUNCHER_PROVENANCE_DIR)) {
    Join-Path $Repo "data\runtime\daily-chain-v2\provenance"
} else {
    $env:CARDZ_V2_LAUNCHER_PROVENANCE_DIR
}
$StartedAt = Get-Date

# The tick runs hidden (see scripts\cardz_silent_run.vbs); nothing survives on a
# console nobody sees.  Every line the launcher used to Write-Host now also lands
# in a dated file so a dead tick can still be read tomorrow morning.
$LogDir = if ([string]::IsNullOrWhiteSpace($env:CARDZ_V2_LAUNCHER_LOG_DIR)) {
    Join-Path $Repo "logs\daily-chain-v2"
} else {
    $env:CARDZ_V2_LAUNCHER_LOG_DIR
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir ("launcher-" + $StartedAt.ToString("yyyyMMdd") + ".log")
function Write-Log {
    param([Parameter(Mandatory=$true)][AllowEmptyString()][string]$Message)
    $line = (Get-Date).ToString("o") + " " + $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    Write-Host $line
}

if ($ManualE2E -and -not $AllowPublish) {
    throw "-ManualE2E requires explicit -AllowPublish"
}
if ($RenewManualWindow -and (-not $ManualE2E -or -not $AllowPublish)) {
    throw "-RenewManualWindow requires -ManualE2E and -AllowPublish"
}
# A rehearsal (A01, A02, ...) reruns a business date in its own journal and
# never publishes; the publish/manual-window switches do not apply to it.
if (-not [string]::IsNullOrWhiteSpace($RunLabel) -and ($AllowPublish -or $ManualE2E -or $RenewManualWindow)) {
    throw "-RunLabel names a rehearsal; it takes none of -AllowPublish/-ManualE2E/-RenewManualWindow"
}

# Windows owns the headed CARDZ Chrome.  WSL workers consume :9333 but never
# invent or substitute a browser profile. Nested powershell must stay Hidden
# so the 10-minute tick does not steal focus with a CMD/PowerShell console.
# Chrome itself stays headed (Cloudflare). Do not add --headless here.
if ($SelfTest) {
    Write-Log "SELFTEST_SKIP_PREFLIGHT cdp=9333"
} else {
    & powershell.exe -WindowStyle Hidden -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ensure_chrome_cdp.ps1") -Port 9333
    Write-Log "CARDZ_V2_PREFLIGHT cdp=9333 exit=$LASTEXITCODE"
    if ($LASTEXITCODE -ne 0) {
        throw "CARDZ CDP 9333 preflight failed"
    }
}

function Convert-ToWslPath {
    param([Parameter(Mandatory=$true)][string]$Path)
    $resolved = [System.IO.Path]::GetFullPath($Path)
    if ($resolved -notmatch '^[A-Za-z]:\\') {
        throw "V2 launcher requires a drive-letter path: $resolved"
    }
    return "/mnt/" + $resolved.Substring(0, 1).ToLowerInvariant() + ($resolved.Substring(2) -replace '\\', '/')
}

function Get-EventDataMap {
    param([Parameter(Mandatory=$true)]$Event)
    $xml = [xml]$Event.ToXml()
    $map = @{}
    foreach ($node in $xml.Event.EventData.Data) {
        $name = [string]$node.Name
        if (-not [string]::IsNullOrWhiteSpace($name)) {
            $map[$name] = [string]$node.'#text'
        }
    }
    return @{
        Xml = $xml
        Data = $map
    }
}

$self = Get-CimInstance Win32_Process -Filter "ProcessId=$PID"
$parent = $null
if ($self -and $self.ParentProcessId) {
    $parent = Get-Process -Id $self.ParentProcessId -ErrorAction SilentlyContinue
}
$parentName = if ($parent) { $parent.ProcessName + ".exe" } else { "unknown" }

$events = @(
    Get-WinEvent -FilterHashtable @{
        LogName = "Microsoft-Windows-TaskScheduler/Operational"
        Id = 107,110
        StartTime = $StartedAt.AddMinutes(-3)
    } -ErrorAction SilentlyContinue |
    Sort-Object TimeCreated -Descending
)
$selected = $null
$selectedData = $null
foreach ($event in $events) {
    $parsed = Get-EventDataMap -Event $event
    $eventTask = [string]$parsed.Data["TaskName"]
    if ([string]::IsNullOrWhiteSpace($eventTask)) {
        $eventTask = [string]$parsed.Xml.Event.UserData.TaskStart.TaskName
    }
    if ($eventTask.TrimEnd('\') -eq $TaskName.TrimEnd('\')) {
        $selected = $event
        $selectedData = $parsed
        break
    }
}

$eventId = 0
$recordId = 0
$instanceId = ""
$eventTime = $null
$ageSeconds = 999999
if ($selected) {
    $eventId = [int]$selected.Id
    $recordId = [long]$selected.RecordId
    $instanceId = [string]$selectedData.Data["InstanceId"]
    if ([string]::IsNullOrWhiteSpace($instanceId)) {
        $instanceId = [string]$selectedData.Xml.Event.System.Correlation.ActivityID
    }
    $eventTime = $selected.TimeCreated.ToUniversalTime().ToString("o")
    $ageSeconds = [Math]::Max(0, ($StartedAt - $selected.TimeCreated).TotalSeconds)
}

$receipt = [ordered]@{
    contract = "cardz-task-scheduler-provenance-v2"
    event_id = $eventId
    event_record_id = $recordId
    instance_id = $instanceId
    task_name = $TaskName
    parent_process = $parentName
    launcher_pid = $PID
    event_time = $eventTime
    event_age_seconds = [Math]::Round($ageSeconds, 3)
    captured_at = (Get-Date).ToUniversalTime().ToString("o")
    manual_e2e = [bool]$ManualE2E
    renew_manual_window = [bool]$RenewManualWindow
    business_date_override = $BusinessDate
    run_label = $RunLabel
}

New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssfffffffZ")
$receiptPath = Join-Path $Runtime ("provenance-" + $stamp + ".json")
$temporary = $receiptPath + ".next"
[System.IO.File]::WriteAllText(
    $temporary,
    ($receipt | ConvertTo-Json -Depth 5 -Compress) + "`n",
    (New-Object System.Text.UTF8Encoding($false))
)
Move-Item -LiteralPath $temporary -Destination $receiptPath -Force

$wslRepo = Convert-ToWslPath -Path $Repo
$wslReceipt = Convert-ToWslPath -Path $receiptPath
$args = @(
    "-d", "Ubuntu", "--",
    "python3", "-X", "utf8", "-u",
    "$wslRepo/pipelines/daily_chain_v2.py",
    "tick",
    "--provenance", $wslReceipt,
    "--max-runtime-seconds", "$MaxRuntimeSeconds"
)
if ($AllowPublish) { $args += "--allow-publish" }
if ($Notify) { $args += "--notify" }
if ($ManualE2E) { $args += "--manual-e2e-window" }
if ($RenewManualWindow) { $args += "--renew-manual-e2e-window" }
if (-not [string]::IsNullOrWhiteSpace($BusinessDate)) {
    $args += @("--business-date", $BusinessDate)
}
if (-not [string]::IsNullOrWhiteSpace($RunLabel)) {
    $args += @("--run-label", $RunLabel)
}

Write-Log ("CARDZ_V2_WSL_ARGS wsl.exe " + ($args -join " "))
if ($SelfTest) {
    Write-Log "SELFTEST_OK $LogPath"
    exit 0
}

# WSL Ubuntu must answer before the tick is handed to it (2026-08-23: three
# Wsl/Service/0x8007274c in one morning while docker on the same VM kept
# answering; every tick would have failed until a human ran `wsl -t Ubuntu`).
# wsl_ubuntu_selfheal.ps1 terminates ONLY the Ubuntu distro, and only when the
# VM is provably alive; it never runs `wsl --shutdown`.  Exit 3 = still dead.
$selfheal = & powershell.exe -WindowStyle Hidden -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "wsl_ubuntu_selfheal.ps1")
$selfhealExit = $LASTEXITCODE
Write-Log ("CARDZ_V2_WSL_PREFLIGHT exit=$selfhealExit " + (($selfheal | Out-String).Trim()))
if ($selfhealExit -ne 0) {
    Write-Log "CARDZ_V2_END exit=3 provenance=$receiptPath wsl=dead"
    exit 3
}
Write-Log "CARDZ_V2_START event=$eventId record=$recordId instance=$instanceId parent=$parentName"
try {
    & wsl.exe @args
    $exitCode = $LASTEXITCODE
} catch {
    Write-Log "CARDZ_V2_LAUNCHER_EXCEPTION $($_.Exception.Message)"
    exit 1
}
Write-Log "CARDZ_V2_END exit=$exitCode provenance=$receiptPath"
exit $exitCode
