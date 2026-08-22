# Apply-gated Task Scheduler cutover for CARDZ Daily Chain V2.
# Without -Apply this script is read-only and prints the exact intended change.
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$BackupDirectory = ""
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$TaskName = "CARDZ-Marketcap-Daily-V2"
$LegacyTasks = @(
    "CARDZ-037-Nightly-Collect-Accept",
    "CARDZ-037-Morning-Browser-Lanes",
    "CARDZ-037-Refresh-Publish",
    "CARDZ-037-Watchdog-Live-Release"
)
$Launcher = Join-Path $PSScriptRoot "cardz_daily_v2_launcher.ps1"
if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "V2 launcher missing: $Launcher"
}
if ([string]::IsNullOrWhiteSpace($BackupDirectory)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $BackupDirectory = Join-Path $Repo "data\runtime\daily-chain-v2\scheduler-backup-$stamp"
}

$plan = [ordered]@{
    apply = [bool]$Apply
    taskName = $TaskName
    launcher = $Launcher
    trigger = "next 03:30 local; then daily; repeat PT10M for PT13H30M"
    executionTimeLimit = "PT55M"
    startWhenAvailable = $true
    multipleInstances = "IgnoreNew"
    logonType = "Interactive"
    consoleWindowStyle = "Hidden"
    oldTasks = $LegacyTasks
    oldTaskAction = "export XML then disable; never delete"
    backupDirectory = [System.IO.Path]::GetFullPath($BackupDirectory)
    publish = "enabled only by this apply-gated task action"
    notifications = "disabled; promotion and Telegram consumers remain disconnected"
}
if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 5
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
$runningTargets = @(
    @($LegacyTasks + $TaskName) | ForEach-Object {
        Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
    } | Where-Object { $_.State -eq 'Running' }
)
if ($runningTargets.Count -gt 0) {
    throw "V2 cutover refused while target tasks are running: $($runningTargets.TaskName -join ', ')"
}

$quotedLauncher = '"' + $Launcher + '"'
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $quotedLauncher -AllowPublish" `
    -WorkingDirectory $Repo
$nowLocal = Get-Date
$firstNaturalStart = $nowLocal.Date.AddHours(3).AddMinutes(30)
if ($firstNaturalStart -le $nowLocal) {
    $firstNaturalStart = $firstNaturalStart.AddDays(1)
}
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
foreach ($name in @($LegacyTasks + $TaskName)) {
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

$plan["result"] = "applied"
$plan | ConvertTo-Json -Depth 5
