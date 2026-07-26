[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$PythonExe,
    [string]$ExpectedDate
)

# 每日 soak 檢查嘅單一入口。純唯讀：verify 用 --no-alert 跑，唔會寫 alert 檔，
# 所以可以隨時、重複執行都唔會污染 soak 記錄。

$ErrorActionPreference = 'Stop'
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
$root = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
if (-not $PythonExe) {
    $venv = Join-Path $root '.venv-backend\Scripts\python.exe'
    $PythonExe = if ([IO.File]::Exists($venv)) { $venv } else { 'python' }
}

function Show-Task([string]$name) {
    try {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $info = Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop
        $result = switch ($info.LastTaskResult) {
            0        { 'OK' }
            1        { 'FAIL (data checks)' }
            2        { 'FAIL (cannot verify)' }
            267009   { 'running now' }
            267011   { 'never run yet' }
            default  { "exit $($info.LastTaskResult)" }
        }
        "  {0,-32} {1,-9} last={2}  -> {3}" -f $name, $task.State, $info.LastRunTime, $result
        "  {0,-32} {1,-9} next={2}" -f '', '', $info.NextRunTime
    } catch {
        "  {0,-32} NOT REGISTERED  <-- 排程唔見咗" -f $name
    }
}

''
'=== 排程 ==='
Show-Task 'CARDZ-Market-Cap-Daily'
Show-Task 'CARDZ-Market-Cap-Watchdog'

''
'=== 數據新鮮度（唯讀，唔寫 alert）==='
$verifyArgs = @('-X', 'utf8', (Join-Path $root 'scripts\verify_daily_run.py'), '--no-alert')
if ($ExpectedDate) { $verifyArgs += @('--expected-date', $ExpectedDate) }
& $PythonExe @verifyArgs 2>&1 | Where-Object { $_ -notmatch '^\{' } | ForEach-Object { "  $_" }
$verifyExit = $LASTEXITCODE

''
'=== Alert 檔 ==='
$alerts = @(Get-ChildItem (Join-Path $root 'data\runtime\alerts\*.json') -ErrorAction SilentlyContinue)
if ($alerts.Count -eq 0) {
    '  冇 alert（好）'
} else {
    "  ⚠ $($alerts.Count) 個未清 alert："
    $alerts | Sort-Object LastWriteTime -Descending | Select-Object -First 10 |
        ForEach-Object { "    $($_.Name)" }
}

''
'=== 最近 log ==='
foreach ($pattern in @('daily_staging_*.log', 'watchdog_*.log')) {
    $log = Get-ChildItem (Join-Path $root "data\runtime\logs\$pattern") -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($log) {
        "  $($log.Name)  ($($log.LastWriteTime))"
        Get-Content -LiteralPath $log.FullName -Tail 40 |
            Select-String -Pattern '\[verify\]' |
            ForEach-Object { "    $_" }
    }
}
''
exit $verifyExit
