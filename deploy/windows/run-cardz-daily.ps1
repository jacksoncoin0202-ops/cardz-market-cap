[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$RepoRoot,
    [Parameter(Mandatory)]
    [string]$PythonExe,
    [Parameter(Mandatory)]
    [string]$EnvFile,
    [ValidateSet('staging', 'production')]
    [string]$Mode = 'production',
    # 對應 Linux 側 CARDZ_DAILY_PUBLISH（deploy/systemd/run-cardz-daily.sh）。
    # Linux 係主，呢度只求語義一致：local = 行足 publish 鏈但只寫本機 public tree，
    # remote = 再加 R2 上傳（EnvFile 要有齊 bucket + canary + pointer JSON），
    # off = 舊嘅 backend-only 行為。
    #
    # 預設**故意**同 Linux 唔同：Linux unit 有 Environment=CARDZ_DAILY_PUBLISH=local
    # 明寫；Windows 呢邊嘅 legacy scheduled task（CARDZ-Market-Cap-Daily）唔會傳
    # -Publish，所以預設必須係 'off' 先可以維持佢原本嘅 backend-only 行為。
    # Windows 要 publish 就喺 task action 明寫 -Publish local。
    [ValidateSet('local', 'remote', 'off')]
    [string]$Publish = 'off'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$python = (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).Path
$environment = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path
$backend = Join-Path $root 'scripts\backend.py'
if (-not [IO.File]::Exists($backend)) {
    throw 'scripts\backend.py is missing from RepoRoot.'
}

Get-Content -LiteralPath $environment | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        throw 'EnvFile contains an invalid environment assignment.'
    }
    Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2]
}

# Local docker-db mode: backend reads credentials from data/runtime/config/backend.env
# (--external-db 淨係俾有 SSL CA 嘅 managed RDS 用)
$dailyArgs = "daily --mode $Mode"
if ($Publish -ne 'off') {
    # publish 鏈最尾要 npm run build。行到嗰步已經燒咗成個 GemRate 抓取，
    # 所以喺開跑之前就要 fail，唔好死喺最後一步。
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw 'Daily publish requires npm on PATH.'
    }
    $dailyArgs += ' --publish'
    if ($Publish -eq 'local') { $dailyArgs += ' --local-only' }
}

$logDir = Join-Path $root 'data\runtime\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("daily_{0}_{1}_{2}.log" -f $Mode, $Publish, (Get-Date -Format 'yyyyMMdd_HHmmss'))
# PS 層 2>&1 + ErrorActionPreference=Stop 會將 python 第一行 stderr 變 terminating error，
# 必須用 cmd.exe 做 byte-level redirect
& cmd.exe /c "`"$python`" -X utf8 `"$backend`" $dailyArgs > `"$logFile`" 2>&1"
$dailyExit = $LASTEXITCODE

# Outcome gate: daily 鏈 exit 0 唔代表今日數據落咗地（replay / INSERT IGNORE no-op 都係 exit 0）。
# verify 檢查 price/snapshot/source 三項 freshness，fail 時寫 data\runtime\alerts\ 並以非零 exit
# 令 Task Scheduler LastTaskResult 反映真實結果。
$verifyScript = Join-Path $root 'scripts\verify_daily_run.py'
& cmd.exe /c "`"$python`" -X utf8 `"$verifyScript`" >> `"$logFile`" 2>&1"
$verifyExit = $LASTEXITCODE

if ($dailyExit -ne 0) { exit $dailyExit }
exit $verifyExit
