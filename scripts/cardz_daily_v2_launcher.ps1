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
$NotifyPython = if ([string]::IsNullOrWhiteSpace($env:CARDZ_V2_NOTIFY_PYTHON)) {
    "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
} else {
    $env:CARDZ_V2_NOTIFY_PYTHON
}

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

function Write-LauncherAlertLog {
    param([Parameter(Mandatory=$true)][AllowEmptyString()][string]$Message)
    try {
        Write-Log $Message
    } catch {
        Write-Host ((Get-Date).ToString("o") + " " + $Message)
    }
}

function Send-LauncherAlert {
    param(
        [Parameter(Mandatory=$true)][string]$Key,
        [Parameter(Mandatory=$true)][string]$Text
    )
    $safeKey = ($Key -replace '[^A-Za-z0-9_-]', '-').Trim('-')
    if ([string]::IsNullOrWhiteSpace($safeKey)) { $safeKey = "launcher-alert" }
    $alertDir = Join-Path $Runtime "launcher-alerts"
    $alertStamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssfffffffZ")
    $artifactPath = Join-Path $alertDir ("alert-" + $alertStamp + "-" + $safeKey + ".json")
    try {
        New-Item -ItemType Directory -Force -Path $alertDir | Out-Null
        $artifact = [ordered]@{
            contract = "cardz-v2-launcher-alert-v1"
            key = $Key
            text = $Text
            launcher_pid = $PID
            started_at = $StartedAt.ToUniversalTime().ToString("o")
            recorded_at = (Get-Date).ToUniversalTime().ToString("o")
            log_path = $LogPath
        }
        $artifactTmp = $artifactPath + ".next"
        [System.IO.File]::WriteAllText(
            $artifactTmp,
            ($artifact | ConvertTo-Json -Depth 4 -Compress) + "`n",
            (New-Object System.Text.UTF8Encoding($false))
        )
        Move-Item -LiteralPath $artifactTmp -Destination $artifactPath -Force
        Write-LauncherAlertLog "CARDZ_V2_LAUNCHER_ALERT_ARTIFACT key=$Key path=$artifactPath"
    } catch {
        Write-LauncherAlertLog "CARDZ_V2_LAUNCHER_ALERT_ARTIFACT_FAILED key=$Key error=$($_.Exception.Message)"
    }

    $notifyScript = if ([string]::IsNullOrWhiteSpace($env:CARDZ_V2_NOTIFY_HERMES_PY)) {
        Join-Path $PSScriptRoot "notify_hermes.py"
    } else {
        $env:CARDZ_V2_NOTIFY_HERMES_PY
    }
    $notifyExit = 127
    try {
        & $NotifyPython -X utf8 $notifyScript alert --key $Key --text $Text --level error --cooldown-min 1440 --require-delivery 2>&1 |
            ForEach-Object { Write-LauncherAlertLog ("CARDZ_V2_LAUNCHER_ALERT_NOTIFY_OUTPUT " + $_) }
        $notifyExit = $LASTEXITCODE
    } catch {
        Write-LauncherAlertLog "CARDZ_V2_LAUNCHER_ALERT_NOTIFY_FAILED key=$Key error=$($_.Exception.Message)"
    }
    Write-LauncherAlertLog "CARDZ_V2_LAUNCHER_ALERT_NOTIFY key=$Key exit=$notifyExit"
    if ($notifyExit -eq 0) { return }

    $popupMarker = Join-Path $alertDir ("popup-" + (Get-Date).ToString("yyyyMMdd") + "-" + $safeKey + ".marker")
    if (Test-Path -LiteralPath $popupMarker) {
        Write-LauncherAlertLog "CARDZ_V2_LAUNCHER_ALERT_POPUP_SUPPRESSED key=$Key marker=$popupMarker"
        return
    }
    $popupExe = if ([string]::IsNullOrWhiteSpace($env:CARDZ_V2_LAUNCHER_MSG_EXE)) {
        Join-Path ([Environment]::SystemDirectory) "msg.exe"
    } else {
        $env:CARDZ_V2_LAUNCHER_MSG_EXE
    }
    $popupExit = 127
    try {
        & $popupExe "*" "/TIME:60" $Text | Out-Null
        $popupExit = $LASTEXITCODE
        if ($popupExit -eq 0) {
            [System.IO.File]::WriteAllText(
                $popupMarker,
                ((Get-Date).ToUniversalTime().ToString("o") + "`n"),
                (New-Object System.Text.UTF8Encoding($false))
            )
        }
    } catch {
        Write-LauncherAlertLog "CARDZ_V2_LAUNCHER_ALERT_POPUP_FAILED key=$Key error=$($_.Exception.Message)"
    }
    Write-LauncherAlertLog "CARDZ_V2_LAUNCHER_ALERT_POPUP key=$Key exit=$popupExit"
}

trap {
    $launcherError = $_.Exception.Message
    Write-LauncherAlertLog "CARDZ_V2_LAUNCHER_EXCEPTION $launcherError"
    Send-LauncherAlert -Key "v2-launcher-exception" -Text (
        "CARDZ V2 launcher failed before or during tick startup: $launcherError. " +
        "No successful tick handoff was recorded. Inspect: $LogPath"
    )
    exit 1
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

# Nested powershell.exe as a child still pops Windows Terminal if it allocates
# a new console. -WindowStyle Hidden is not enough; CREATE_NO_WINDOW is.
# Chrome itself stays headed (Cloudflare). Do not add --headless here.
function Invoke-HiddenPowerShellFile {
    param(
        [Parameter(Mandatory=$true)][string]$File,
        [string]$ArgumentString = "",
        [int]$CapMs = 120000
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $arg = "-WindowStyle Hidden -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$File`""
    if (-not [string]::IsNullOrWhiteSpace($ArgumentString)) { $arg = "$arg $ArgumentString" }
    $psi.Arguments = $arg
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $null = $p.Handle
    if (-not $p.WaitForExit($CapMs)) {
        try { $p.Kill() } catch {}
        return @{ ExitCode = 124; Output = "timeout ${CapMs}ms" }
    }
    return @{
        ExitCode = [int]$p.ExitCode
        Output = ($p.StandardOutput.ReadToEnd() + $p.StandardError.ReadToEnd())
    }
}

if ($SelfTest) {
    Write-Log "SELFTEST_SKIP_PREFLIGHT cdp=9333"
} else {
    $cdp = Invoke-HiddenPowerShellFile -File (Join-Path $PSScriptRoot "ensure_chrome_cdp.ps1") -ArgumentString "-Port 9333"
    Write-Log "CARDZ_V2_PREFLIGHT cdp=9333 exit=$($cdp.ExitCode)"
    if ($cdp.ExitCode -ne 0) {
        $cdpOutput = [string]$cdp.Output
        if ($cdpOutput.Length -gt 8000) { $cdpOutput = $cdpOutput.Substring(0, 8000) + " [truncated]" }
        Write-Log ("CARDZ_V2_PREFLIGHT_OUTPUT cdp=9333 " + $cdpOutput)
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

$selected = $null
$selectedData = $null
$eventReadAttempts = 1
# Task Scheduler writes event 107 before starting wscript, but its Operational
# log can become queryable a few seconds after the child PowerShell begins.
# Re-read the same strict event evidence only for the production hidden-wrapper
# process shape. Direct CLI/manual launches stay single-shot and fail closed.
$maxEventReadAttempts = if ($parentName -eq "wscript.exe") { 11 } else { 1 }
for ($attempt = 1; $attempt -le $maxEventReadAttempts; $attempt++) {
    $eventReadAttempts = $attempt
    $events = @(
        Get-WinEvent -FilterHashtable @{
            LogName = "Microsoft-Windows-TaskScheduler/Operational"
            Id = 107,110
            StartTime = $StartedAt.AddMinutes(-3)
        } -ErrorAction SilentlyContinue |
        Sort-Object TimeCreated -Descending
    )
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
    if ($selected -or $attempt -eq $maxEventReadAttempts) {
        break
    }
    Start-Sleep -Milliseconds 500
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
    event_read_attempts = $eventReadAttempts
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
    # Phase-2 alignment is a property of the scheduled launcher, not an
    # ambient Windows/WSL shell. Windows user env is not forwarded into WSL
    # unless WSLENV is configured, and this machine deliberately has no such
    # bridge. Pass the narrowly scoped flag on the command line so every real
    # scheduled tick can repair an early-created published business date.
    "env", "CARDZ_V2_AUTO_SUPERSEDE=1",
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

# WSL Ubuntu must answer before the tick is handed to it (2026-08-23: three
# Wsl/Service/0x8007274c in one morning while docker on the same VM kept
# answering; every tick would have failed until a human ran `wsl -t Ubuntu`).
# wsl_ubuntu_selfheal.ps1 terminates ONLY the Ubuntu distro, and only when the
# VM is provably alive; it never runs `wsl --shutdown`.  Exit 3 = still dead.
$selfhealScript = if ([string]::IsNullOrWhiteSpace($env:CARDZ_V2_WSL_SELFHEAL_PS1)) {
    Join-Path $PSScriptRoot "wsl_ubuntu_selfheal.ps1"
} else {
    $env:CARDZ_V2_WSL_SELFHEAL_PS1
}
# Self-heal worst case is ~155s (30s probe + 30s docker + 60s terminate + 5s
# sleep + 30s re-probe). The default 120s child cap kills it first and the
# launcher then reports wsl=dead with no revive attempt.
$selfhealRun = Invoke-HiddenPowerShellFile -File $selfhealScript -CapMs 180000
$selfheal = $selfhealRun.Output
$selfhealExit = $selfhealRun.ExitCode
Write-Log ("CARDZ_V2_WSL_PREFLIGHT exit=$selfhealExit " + (($selfheal | Out-String).Trim()))
if ($selfhealExit -ne 0) {
    Send-LauncherAlert -Key "v2-launcher-wsl-dead" -Text (
        "CARDZ V2 tick could not start: wsl_ubuntu_selfheal exit=$selfhealExit " +
        "($(($selfheal | Out-String).Trim())). No tick ran; the chain is stalled until WSL Ubuntu answers. " +
        "Inspect: wsl -l -v. Revive: wsl -t Ubuntu (never wsl --shutdown: it takes MySQL 3308 down)."
    )
    Write-Log "CARDZ_V2_END exit=3 provenance=$receiptPath wsl=dead"
    exit 3
}
if ($SelfTest) {
    Write-Log "SELFTEST_OK $LogPath"
    exit 0
}
Write-Log "CARDZ_V2_START event=$eventId record=$recordId instance=$instanceId parent=$parentName"
try {
    & wsl.exe @args
    $exitCode = $LASTEXITCODE
} catch {
    $launcherError = $_.Exception.Message
    Write-LauncherAlertLog "CARDZ_V2_LAUNCHER_EXCEPTION $launcherError"
    Send-LauncherAlert -Key "v2-launcher-exception" -Text (
        "CARDZ V2 launcher failed while handing the tick to WSL: $launcherError. " +
        "Inspect: $LogPath"
    )
    exit 1
}
Write-Log "CARDZ_V2_END exit=$exitCode provenance=$receiptPath"
exit $exitCode
