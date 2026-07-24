[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('install', 'status', 'uninstall', 'dry-run')]
    [string]$Action = 'dry-run',
    [string]$TaskName = 'CARDZ-Market-Cap-Daily',
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$At = '06:30',
    [ValidateSet('staging', 'production')]
    [string]$Mode = 'production',
    [string]$RepoRoot,
    [string]$PythonExe,
    [Parameter(Mandatory)]
    [string]$EnvFile,
    [switch]$AllowNonJstHost
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
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
    throw "Host timezone is '$timezone'. Refusing to schedule 06:30 JST as local time; use Tokyo Standard Time or explicitly pass -AllowNonJstHost."
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
        timeoutMinutes = 120
        restart = '3 retries, 10 minute backoff'
    } | ConvertTo-Json -Compress
    exit 0
}

$runAt = [DateTime]::ParseExact($At, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $runAt
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
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
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
