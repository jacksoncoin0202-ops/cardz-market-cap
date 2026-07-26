[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$RepoRoot,
    [Parameter(Mandatory)]
    [string]$PythonExe
)

# Watchdog：verify gate 只喺 daily run 行完之後先跑，所以佢有一個天生盲點——
# 「今日部機冇著 / task 被 disable / run 掛住冇 exit」呢類情況，daily 根本冇 run，
# 亦即冇人叫 verify，結果係完全靜音。呢個 task 獨立喺 daily 之後幾個鐘照跑一次 verify，
# 令「乜都冇發生」都會留低 alert 檔 + 非零 LastTaskResult。

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$python = (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).Path
$verifyScript = Join-Path $root 'scripts\verify_daily_run.py'
if (-not [IO.File]::Exists($verifyScript)) {
    throw 'scripts\verify_daily_run.py is missing from RepoRoot.'
}

$logDir = Join-Path $root 'data\runtime\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("watchdog_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))

# 先記低 daily task 自己嘅狀態：分辨「run 咗但數據唔啱」定「根本冇 run 過」
$header = @()
try {
    $info = Get-ScheduledTaskInfo -TaskName 'CARDZ-Market-Cap-Daily' -ErrorAction Stop
    $state = (Get-ScheduledTask -TaskName 'CARDZ-Market-Cap-Daily').State
    $header += "[watchdog] daily task state=$state lastRun=$($info.LastRunTime) lastResult=$($info.LastTaskResult)"
} catch {
    $header += "[watchdog] daily task NOT FOUND — 排程本身唔見咗"
}
$header | Set-Content -LiteralPath $logFile -Encoding UTF8

& cmd.exe /c "`"$python`" -X utf8 `"$verifyScript`" --tag watchdog >> `"$logFile`" 2>&1"
$verifyExit = $LASTEXITCODE
Get-Content -LiteralPath $logFile -Tail 20 | Write-Output
exit $verifyExit
