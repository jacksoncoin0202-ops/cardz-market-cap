# Apply-gated Task Scheduler registration for the V2 run observer
# (scripts\v2_run_observer.py watch). Read-only without -Apply / -Remove: prints
# the exact intended definition.
#
# The observer is read-only (journal via WSL snapshot, launcher log, host /
# CDP 9333 / WSL / MySQL-3308 long-session probes) and writes only under
# data\runtime\daily-chain-v2\observer\<business-date>\. It starts 5 minutes
# before the first 03:30 JST tick, auto-resolves the JST business date, and
# once the run is terminal (+25 min settle) waits for the 17:45 JST
# CARDZ-Promo-After-Publish task and records its result, then exits; -MaxHours
# (14.75 h from 03:25 = 18:10 JST) is the hard stop, inside the PT15H limit.
# Same console policy as the daily task: wscript //B + the hidden
# runner, never powershell -WindowStyle Hidden (pops a Windows Terminal window).
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Remove,
    [switch]$Print,
    [double]$MaxHours = 7.5,
    [string]$PythonExe = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$TaskName = "CARDZ-V2-Run-Observer"
$Observer = Join-Path $PSScriptRoot "v2_run_observer.py"
$SilentRunner = Join-Path $PSScriptRoot "cardz_silent_run.vbs"
foreach ($p in @($Observer, $SilentRunner, $PythonExe)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "missing: $p" }
}
$WScriptExe = Join-Path ([Environment]::SystemDirectory) "wscript.exe"
$Argument = "//nologo //B `"$SilentRunner`" `"$PythonExe`" -X utf8 -u `"$Observer`" watch --max-hours $MaxHours --notify"
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

$plan = [ordered]@{
    taskName = $TaskName
    execute = $WScriptExe
    argument = $Argument
    workingDirectory = $Repo
    trigger = "daily 10:50 local (JST), no repetition"
    executionTimeLimit = "PT8H"
    multipleInstances = "IgnoreNew"
    principal = "current user, Interactive, Limited"
    writes = "data\runtime\daily-chain-v2\observer\<day>\ plus notify failure artifacts (gitignored)"
    existing = if ($existing) { "$($existing.State)" } else { "absent" }
    revert = "powershell -NoProfile -File scripts\install_v2_observer_task.ps1 -Remove"
}
if ($Print -or (-not $Apply -and -not $Remove)) {
    $plan | ConvertTo-Json -Depth 4
    exit 0
}
if ($Remove) {
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "REMOVED $TaskName"
    } else {
        Write-Output "ABSENT $TaskName"
    }
    exit 0
}

$action = New-ScheduledTaskAction -Execute $WScriptExe -Argument $Argument -WorkingDirectory $Repo
$nowLocal = Get-Date
$start = $nowLocal.Date.AddHours(10).AddMinutes(50)
if ($start -le $nowLocal) { $start = $start.AddDays(1) }
$trigger = New-ScheduledTaskTrigger -Daily -At $start
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "CARDZ V2 run observer: monitor + warn/error delivery; writes data/runtime/daily-chain-v2/observer/<day>/"
Register-ScheduledTask -TaskName $TaskName -InputObject $definition -Force | Out-Null
$task = Get-ScheduledTask -TaskName $TaskName
$info = $task | Get-ScheduledTaskInfo
[ordered]@{
    taskName = $TaskName
    state = "$($task.State)"
    nextRunTime = "$($info.NextRunTime)"
    argument = $task.Actions[0].Arguments
    revert = $plan.revert
} | ConvertTo-Json -Depth 3
