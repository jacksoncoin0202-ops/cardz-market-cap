# Logon + 10-minute keep-alive for CARDZ headed CDP (9333 PC, 9444 social).
# Never 9222. Hidden runner — no prompt, no extra console.
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$TaskName = "CARDZ-CDP-Fleet"
$Fleet = Join-Path $PSScriptRoot "ensure_cardz_cdp_fleet.ps1"
$SilentRunner = Join-Path $PSScriptRoot "cardz_silent_run.vbs"
$PsHost = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
foreach ($p in @($Fleet, $SilentRunner, $PsHost)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "missing: $p" }
}
$WScriptExe = Join-Path ([Environment]::SystemDirectory) "wscript.exe"
$Inner = "-NoProfile -ExecutionPolicy Bypass -File `"$Fleet`""
$Argument = "//nologo //B `"$SilentRunner`" `"$PsHost`" $Inner"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Remove) {
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "REMOVED $TaskName"
    } else {
        Write-Output "ABSENT $TaskName"
    }
    exit 0
}
if (-not $Apply) {
    [ordered]@{
        taskName = $TaskName
        ports = "9333 PC, 9444 social, never 9222"
        trigger = "at logon + every 10 min"
        existing = if ($existing) { "$($existing.State)" } else { "absent" }
    } | ConvertTo-Json
    exit 0
}

$action = New-ScheduledTaskAction -Execute $WScriptExe -Argument $Argument -WorkingDirectory $Repo
$logon = New-ScheduledTaskTrigger -AtLogOn
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 7)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$definition = New-ScheduledTask -Action $action -Trigger @($logon, $repeat) -Settings $settings -Principal $principal `
    -Description "CARDZ headed CDP fleet: 9333 PriceCharting + 9444 social. Never 9222. Never prompt."
Register-ScheduledTask -TaskName $TaskName -InputObject $definition -Force | Out-Null
Write-Output "APPLIED $TaskName"
