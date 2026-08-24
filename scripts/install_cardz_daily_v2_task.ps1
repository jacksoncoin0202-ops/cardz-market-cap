# Apply-gated Task Scheduler cutover for CARDZ Daily Chain V2.
# Without -Apply (or with -Print) this script is read-only and prints the exact
# intended change.
#
# Console policy (2026-08-22, empirical): a Task Scheduler action that executes
# powershell.exe under InteractiveToken is delegated to Windows Terminal and pops
# a visible window even with "-WindowStyle Hidden"; a human closed that window and
# killed 9 ticks with 0xC000013A. Every action below therefore goes through
# wscript.exe //nologo //B scripts\cardz_silent_run.vbs, which uses a classic
# hidden conhost and still propagates the child rc.
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Print,
    [string]$BackupDirectory = "",
    # Dry-run only: seed the clock so -Print can prove the StartBoundary guard at
    # a synthetic time without waiting for the real window.  Refused under -Apply.
    [datetime]$NowOverride = [datetime]::MinValue
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$TaskName = "CARDZ-Marketcap-Daily-V2"
$WatchdogTaskName = "CARDZ-037-Watchdog-Live-Release"
$PromoTaskName = "CARDZ-Promo-After-Publish"
# R2 (2026-08-24): the claim window, not the whole PT55M.  Claiming closes at
# 35 min and the tick drains live work for the remaining ~12 min inside the same
# ExecutionTimeLimit.  Must equal DEFAULT_MAX_RUNTIME_SECONDS in
# pipelines/daily_chain_v2.py and the launcher's default; scripts/test_v2_tick_budget.py
# parses all three and fails if any side drifts.
$MaxRuntimeSeconds = 2100
$LegacyTasks = @(
    "CARDZ-037-Nightly-Collect-Accept",
    "CARDZ-037-Morning-Browser-Lanes",
    "CARDZ-037-Refresh-Publish"
)
$Launcher = Join-Path $PSScriptRoot "cardz_daily_v2_launcher.ps1"
if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "V2 launcher missing: $Launcher"
}
$SilentRunner = Join-Path $PSScriptRoot "cardz_silent_run.vbs"
if (-not (Test-Path -LiteralPath $SilentRunner)) {
    throw "V2 silent runner missing: $SilentRunner"
}
$Watchdog = Join-Path $PSScriptRoot "watchdog_live_release.ps1"
if (-not (Test-Path -LiteralPath $Watchdog)) {
    throw "V2 watchdog missing: $Watchdog"
}

function Convert-ToWslPath {
    param([Parameter(Mandatory=$true)][string]$Path)
    $resolved = [System.IO.Path]::GetFullPath($Path)
    if ($resolved -notmatch '^[A-Za-z]:\\') {
        throw "V2 installer requires a drive-letter path: $resolved"
    }
    return "/mnt/" + $resolved.Substring(0, 1).ToLowerInvariant() + ($resolved.Substring(2) -replace '\\', '/')
}

$WScriptExe = Join-Path ([Environment]::SystemDirectory) "wscript.exe"
$quotedRunner = '"' + $SilentRunner + '"'
$quotedLauncher = '"' + $Launcher + '"'
$quotedWatchdog = '"' + $Watchdog + '"'
$PromoScriptWsl = (Convert-ToWslPath -Path $Repo) + "/scripts/promo_after_publish.py"
$PsHost = "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File"
$DailyArgument = "//nologo //B $quotedRunner $PsHost $quotedLauncher -AllowPublish -Notify -MaxRuntimeSeconds $MaxRuntimeSeconds"
$WatchdogArgument = "//nologo //B $quotedRunner $PsHost $quotedWatchdog"
$PromoArgument = "//nologo //B $quotedRunner wsl.exe -d Ubuntu -- python3 -X utf8 $PromoScriptWsl"
if ([string]::IsNullOrWhiteSpace($BackupDirectory)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $BackupDirectory = Join-Path $Repo "data\runtime\daily-chain-v2\scheduler-backup-$stamp"
}

# P0 (2026-08-24): StartBoundary used to roll to tomorrow whenever 03:30 had
# already passed, so a mid-window -Apply silently deleted the rest of the day's
# repetition window.  Evidence: 08-23 fired 75 ticks 03:30-17:00; the 03:58:36
# re-install on 08-24 left 4 (03:30, 03:40, 03:50, 03:59) and the next tick was
# 08-25 03:30 -- 79 ticks gone with no error.
# StartBoundary carries the DAILY recurrence time, so it must STAY at 03:30 and
# roll forward only once the window has closed.  A mid-window tick is safe:
# Journal.ensure_run keys the run on cardz-v2:<business_date>, so it resumes the
# day's existing run instead of opening a duplicate business date.
if ($NowOverride -ne [datetime]::MinValue) {
    if ($Apply) { throw "-NowOverride is a dry-run aid; it must not be combined with -Apply" }
    $nowLocal = $NowOverride
} else {
    $nowLocal = Get-Date
}
$DailyWindowMinutes = 810      # PT13H30M -- must match the daily Repetition Duration below
$WatchdogWindowMinutes = 840   # PT14H    -- must match the watchdog Repetition Duration below
$firstNaturalStart = $nowLocal.Date.AddHours(3).AddMinutes(30)
if ($nowLocal -ge $firstNaturalStart.AddMinutes($DailyWindowMinutes)) {
    $firstNaturalStart = $firstNaturalStart.AddDays(1)
}
$dailyInsideWindow = ($firstNaturalStart -le $nowLocal)
$dailyTicksPreserved = 0
if ($dailyInsideWindow) {
    $elapsedMinutes = ($nowLocal - $firstNaturalStart).TotalMinutes
    $dailyTicksPreserved = [int][math]::Floor($DailyWindowMinutes / 10) - [int][math]::Floor($elapsedMinutes / 10)
}

$plan = [ordered]@{
    apply = [bool]$Apply
    taskName = $TaskName
    launcher = $Launcher
    silentRunner = $SilentRunner
    trigger = "next 03:30 local; then daily; repeat PT10M for PT13H30M"
    nowLocal = $nowLocal.ToString("yyyy-MM-ddTHH:mm:ssK")
    startBoundary = $firstNaturalStart.ToString("yyyy-MM-ddTHH:mm:ssK")
    insideDailyWindow = $dailyInsideWindow
    todayTicksPreserved = $dailyTicksPreserved
    executionTimeLimit = "PT55M"
    startWhenAvailable = $true
    multipleInstances = "IgnoreNew"
    logonType = "Interactive"
    consoleWindowStyle = "wscript-hidden"
    actions = [ordered]@{
        daily = [ordered]@{
            taskName = $TaskName
            execute = $WScriptExe
            argument = $DailyArgument
            trigger = "daily 03:30 local; repeat PT10M for PT13H30M"
            executionTimeLimit = "PT55M"
        }
        watchdog = [ordered]@{
            taskName = $WatchdogTaskName
            execute = $WScriptExe
            argument = $WatchdogArgument
            trigger = "daily 04:00 local; repeat PT15M for PT14H"
            executionTimeLimit = "PT5M"
        }
        promo = [ordered]@{
            taskName = $PromoTaskName
            execute = $WScriptExe
            argument = $PromoArgument
            trigger = "daily 17:45 local"
            executionTimeLimit = "PT30M"
        }
    }
    oldTasks = $LegacyTasks
    oldTaskAction = "export XML then disable; never delete"
    backupDirectory = [System.IO.Path]::GetFullPath($BackupDirectory)
    publish = "enabled only by this apply-gated task action"
    notifications = "enabled on the daily action (-Notify); promo consumer never posts"
}
if ($Print -or -not $Apply) {
    $plan | ConvertTo-Json -Depth 6
    exit 0
}

$missingLegacy = @(
    $LegacyTasks | Where-Object {
        -not (Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue)
    }
)
if ($missingLegacy.Count -gt 0) {
    throw "V2 cutover refused: expected legacy tasks are missing: $($missingLegacy -join ', ')"
}
$ManagedTasks = @($TaskName, $WatchdogTaskName, $PromoTaskName)
$runningTargets = @(
    @($LegacyTasks + $ManagedTasks) | ForEach-Object {
        Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
    } | Where-Object { $_.State -eq 'Running' }
)
if ($runningTargets.Count -gt 0) {
    throw "V2 cutover refused while target tasks are running: $($runningTargets.TaskName -join ', ')"
}

$action = New-ScheduledTaskAction `
    -Execute $WScriptExe `
    -Argument $DailyArgument `
    -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Daily -At $firstNaturalStart
$trigger.Repetition = New-CimInstance `
    -Namespace "Root/Microsoft/Windows/TaskScheduler" `
    -ClassName "MSFT_TaskRepetitionPattern" `
    -ClientOnly `
    -Property @{
        Interval = "PT10M"
        Duration = "PT13H30M"
        StopAtDurationEnd = $false
    }
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 55)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$definition = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "CARDZ Marketcap Daily Chain V2: resumable WSL orchestrator; event 107 provenance"

$operationalLog = Get-WinEvent `
    -ListLog "Microsoft-Windows-TaskScheduler/Operational" `
    -ErrorAction Stop
if (-not $operationalLog.IsEnabled) {
    & wevtutil.exe sl Microsoft-Windows-TaskScheduler/Operational /e:true
    if ($LASTEXITCODE -ne 0) {
        throw "could not enable Task Scheduler Operational log"
    }
}

$backup = [System.IO.Path]::GetFullPath($BackupDirectory)
New-Item -ItemType Directory -Force -Path $backup | Out-Null
foreach ($name in @($LegacyTasks + $ManagedTasks)) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $existing) { continue }
    $xmlPath = Join-Path $backup ($name + ".xml")
    $xml = Export-ScheduledTask -TaskName $name
    [System.IO.File]::WriteAllText(
        $xmlPath,
        $xml,
        (New-Object System.Text.UTF8Encoding($false))
    )
}
foreach ($name in $LegacyTasks) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Disable-ScheduledTask -TaskName $name | Out-Null
    }
}
Register-ScheduledTask -TaskName $TaskName -InputObject $definition -Force | Out-Null

# Watchdog is no longer a legacy 037 task to be disabled: it is the out-of-chain
# health probe for V2 and is (re)registered here, every 15 min 04:00-18:00 local
# so the 17:30 final check is covered by the last repetition.
$watchdogFirstStart = $nowLocal.Date.AddHours(4)
if ($nowLocal -ge $watchdogFirstStart.AddMinutes($WatchdogWindowMinutes)) { $watchdogFirstStart = $watchdogFirstStart.AddDays(1) }
$watchdogTrigger = New-ScheduledTaskTrigger -Daily -At $watchdogFirstStart
$watchdogTrigger.Repetition = New-CimInstance `
    -Namespace "Root/Microsoft/Windows/TaskScheduler" `
    -ClassName "MSFT_TaskRepetitionPattern" `
    -ClientOnly `
    -Property @{
        Interval = "PT15M"
        Duration = "PT14H"
        StopAtDurationEnd = $false
    }
$watchdogDefinition = New-ScheduledTask `
    -Action (New-ScheduledTaskAction -Execute $WScriptExe -Argument $WatchdogArgument -WorkingDirectory $Repo) `
    -Trigger $watchdogTrigger `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)) `
    -Principal $principal `
    -Description "CARDZ V2 external watchdog: health.json freshness + live release check"
Register-ScheduledTask -TaskName $WatchdogTaskName -InputObject $watchdogDefinition -Force | Out-Null

# Promo pack builder (contract C4): reads the published snapshot, writes a pack +
# receipt under data/runtime/promo/<business_date>/. It never posts.
$promoFirstStart = $nowLocal.Date.AddHours(17).AddMinutes(45)
if ($promoFirstStart -le $nowLocal) { $promoFirstStart = $promoFirstStart.AddDays(1) }
$promoDefinition = New-ScheduledTask `
    -Action (New-ScheduledTaskAction -Execute $WScriptExe -Argument $PromoArgument -WorkingDirectory $Repo) `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $promoFirstStart) `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)) `
    -Principal $principal `
    -Description "CARDZ promo pack builder after publish (build only; never posts)"
Register-ScheduledTask -TaskName $PromoTaskName -InputObject $promoDefinition -Force | Out-Null

$plan["result"] = "applied"
$plan | ConvertTo-Json -Depth 5
