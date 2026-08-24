# Idempotent Task Scheduler limits for CARDZ-037 daily chain.
# Nightly must finish before 09:30 JST. Inner GemRate timeout is 10800s, so
# PT5H30M (03:30+5.5h=09:00) leaves a gap before morning.
# Morning inner caps: PC 90min + SNK 40min + publish ~20min < PT3H.
#
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\apply_037_daily_task_limits.ps1
$ErrorActionPreference = "Stop"

$specs = @(
    @{ Name = "CARDZ-037-Morning-Browser-Lanes"; Limit = "PT3H" },
    @{ Name = "CARDZ-037-Nightly-Collect-Accept"; Limit = "PT5H30M" },
    @{ Name = "CARDZ-037-Refresh-Publish"; Limit = "PT1H" }
)

foreach ($s in $specs) {
    $task = Get-ScheduledTask -TaskName $s.Name -ErrorAction Stop
    $task.Settings.ExecutionTimeLimit = $s.Limit
    $task.Settings.MultipleInstances = "IgnoreNew"
    $task.Settings.StopIfGoingOnBatteries = $false
    Set-ScheduledTask -InputObject $task | Out-Null
    $read = Get-ScheduledTask -TaskName $s.Name
    "{0} Limit={1} Multiple={2}" -f $read.TaskName, $read.Settings.ExecutionTimeLimit, $read.Settings.MultipleInstances
}

$morning = Get-ScheduledTask -TaskName "CARDZ-037-Morning-Browser-Lanes"
$nightly = Get-ScheduledTask -TaskName "CARDZ-037-Nightly-Collect-Accept"
if ($morning.Settings.ExecutionTimeLimit -ne "PT3H") { throw "morning limit not PT3H" }
if ($nightly.Settings.ExecutionTimeLimit -ne "PT5H30M") { throw "nightly limit not PT5H30M" }
Write-Output "OK CARDZ-037 task limits applied"
