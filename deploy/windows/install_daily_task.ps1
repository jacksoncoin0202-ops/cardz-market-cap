[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('install', 'status', 'uninstall', 'dry-run')]
    [string]$Action = 'dry-run',
    [string]$TaskName = 'CARDZ-Market-Cap-Daily',
    # 09:30 JST = 00:30 UTC，同 run_daily.py 用 UTC 日期砌 market_run_id 對齊。
    # 唔准改返 06:30：06:30 JST = 前一日 21:30 UTC，run_id 會落返舊日期，
    # collector 見到同名 run_id 就 replay 舊輸出 → 全鏈 exit 0 但零新數據（2026-07-25 事故）。
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$At = '09:30',
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$WatchdogAt = '14:07',
    [ValidateSet('staging', 'production')]
    [string]$Mode = 'production',
    [string]$RepoRoot,
    [string]$PythonExe,
    [Parameter(Mandatory)]
    [string]$EnvFile,
    [switch]$AllowNonJstHost
)

$ErrorActionPreference = 'Stop'

# 部分機嘅 PSModulePath 被 PowerShell 7 嘅模組目錄污染（PS7 嘅 Modules 排喺 v1.0\Modules 前面），
# powershell.exe 5.1 自動載入 Microsoft.PowerShell.Security 會撞到 Core-only 版本而失敗，
# Get-Acl 直接變 CommandNotFoundException。重設返系統預設路徑令腳本喺污染環境下都行得。
if (-not (Get-Command Get-Acl -ErrorAction SilentlyContinue)) {
    $env:PSModulePath = (@(
        [Environment]::GetEnvironmentVariable('PSModulePath', 'Machine'),
        [Environment]::GetEnvironmentVariable('PSModulePath', 'User'),
        (Join-Path $PSHOME 'Modules')
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join ';'
    Import-Module Microsoft.PowerShell.Security -ErrorAction Stop
}

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

function Resolve-Python310([string]$Requested) {
    $candidate = if ([string]::IsNullOrWhiteSpace($Requested)) {
        (Get-Command python.exe -CommandType Application -All -ErrorAction Stop | Select-Object -First 1).Source
    } else {
        (Resolve-Path -LiteralPath $Requested -ErrorAction Stop).Path
    }
    $version = & $candidate -c 'import sys; print(str(sys.version_info[0])+chr(46)+str(sys.version_info[1]))'
    if ($LASTEXITCODE -ne 0 -or [version]$version -lt [version]'3.10') {
        throw 'CARDZ scheduler requires Python 3.10 or newer.'
    }
    return $candidate
}

function Assert-PrivateEnvironmentFile([string]$Path) {
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    if (-not [IO.File]::Exists($resolved)) {
        throw 'EnvFile must be a file.'
    }
    $unsafe = (Get-Acl -LiteralPath $resolved).Access | Where-Object {
        $_.AccessControlType -eq 'Allow' -and
        $_.IdentityReference.Value -match '(?i)(everyone|authenticated users|\\users)$' -and
        ($_.FileSystemRights.ToString() -match 'Read|FullControl|Modify')
    }
    if ($unsafe) {
        throw 'EnvFile ACL permits a broad reader. Restrict it before installing the task.'
    }
    return $resolved
}

function Quote-TaskArgument([string]$Value) {
    return '"{0}"' -f $Value.Replace('"', '\"')
}

if ($Action -eq 'status') {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    [pscustomobject]@{
        taskName = $TaskName
        installed = [bool]$task
        state = if ($task) { $task.State.ToString() } else { 'absent' }
    } | ConvertTo-Json -Compress
    exit 0
}

if ($Action -eq 'uninstall') {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and $PSCmdlet.ShouldProcess($TaskName, 'Remove CARDZ daily task')) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    [pscustomobject]@{ taskName = $TaskName; installed = $false; action = 'uninstall' } | ConvertTo-Json -Compress
    exit 0
}

$timezone = (Get-TimeZone).Id
if ($timezone -ne 'Tokyo Standard Time' -and -not $AllowNonJstHost) {
    throw "Host timezone is '$timezone'. Refusing to schedule $At JST as local time; use Tokyo Standard Time or explicitly pass -AllowNonJstHost."
}
$root = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$backend = Join-Path $root 'scripts\backend.py'
$runner = Join-Path $root 'deploy\windows\run-cardz-daily.ps1'
if (-not (Test-Path -LiteralPath $backend -PathType Leaf) -or -not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw 'RepoRoot must contain scripts\backend.py and deploy\windows\run-cardz-daily.ps1.'
}
$python = Resolve-Python310 $PythonExe
$environment = Assert-PrivateEnvironmentFile $EnvFile
$arguments = @(
    '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', (Quote-TaskArgument $runner),
    '-RepoRoot', (Quote-TaskArgument $root),
    '-PythonExe', (Quote-TaskArgument $python),
    '-EnvFile', (Quote-TaskArgument $environment),
    '-Mode', $Mode
) -join ' '

if ($Action -eq 'dry-run') {
    [pscustomobject]@{
        action = 'dry-run'
        taskName = $TaskName
        at = "$At JST"
        timezone = $timezone
        execute = 'powershell.exe'
        arguments = $arguments
        workingDirectory = $root
        singleton = 'IgnoreNew plus backend daily lock'
        timeoutMinutes = 360
        restart = '3 retries, 10 minute backoff'
        watchdogTaskName = "$TaskName-Watchdog"
        watchdogAt = "$WatchdogAt JST"
    } | ConvertTo-Json -Compress
    exit 0
}

$runAt = [DateTime]::ParseExact($At, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $runAt
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Limited

if ($PSCmdlet.ShouldProcess($TaskName, "Register CARDZ backend daily at $At JST")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $taskAction `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description 'CARDZ daily canonical DB sync. Runs scripts/backend.py daily only; no deploy or public publish.' `
        -Force | Out-Null
}

# Watchdog：daily 內建嘅 verify gate 只喺 daily 真係行完之後先跑，所以「部機冇著 / task 被
# disable / run 掛住唔 exit」呢類情況係完全靜音。呢個獨立 task 喺 daily 之後幾個鐘照跑一次
# verify，令「乜都冇發生」都會留低 alert 檔同非零 LastTaskResult。
$watchdogName = "$TaskName-Watchdog"
$watchdogRunner = Join-Path $root 'deploy\windows\run-cardz-watchdog.ps1'
if (Test-Path -LiteralPath $watchdogRunner -PathType Leaf) {
    $watchdogArguments = @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', (Quote-TaskArgument $watchdogRunner),
        '-RepoRoot', (Quote-TaskArgument $root),
        '-PythonExe', (Quote-TaskArgument $python)
    ) -join ' '
    if ($PSCmdlet.ShouldProcess($watchdogName, "Register CARDZ watchdog at $WatchdogAt JST")) {
        Register-ScheduledTask `
            -TaskName $watchdogName `
            -Action (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $watchdogArguments -WorkingDirectory $root) `
            -Trigger (New-ScheduledTaskTrigger -Daily -At ([DateTime]::ParseExact($WatchdogAt, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture))) `
            -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)) `
            -Principal $principal `
            -Description 'CARDZ outcome watchdog. Read-only verify of the daily run; writes an alert file when the daily produced no fresh data.' `
            -Force | Out-Null
    }
}

Get-ScheduledTask -TaskName $TaskName, $watchdogName -ErrorAction SilentlyContinue | Select-Object TaskName, State
