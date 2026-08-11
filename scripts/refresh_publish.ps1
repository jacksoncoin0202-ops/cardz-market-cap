# CARDZ 036 refresh+publish slot: daily-accept -> public release.
# Registered as Task Scheduler job CARDZ-036-Refresh-Publish (repeats through the day).
#
# 冇 collector 喺呢度。呢個 slot 唔係補收貨，係補「將已經收到嘅嘢推出街」：
# 夜鏈 03:30 同朝鏈 09:30 各自收完貨就 re-rank + 發佈，但兩個 slot 之間收到嘅
# SNK 成交 / PC 銷售一路等到第二日先出到街，而任何一個 slot 死咗就成日冇更新。
# daily-accept 同 daily_public_release 兩個都係冪等（冇新嘢就 no-change 收工，
# 大約 50 秒），所以呢個 slot 可以一日行幾次，同時做重試同做加密更新。
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$repo = Split-Path -Parent $PSScriptRoot
$py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
$logDir = Join-Path $repo "data\runtime\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$log = Join-Path $logDir "refresh-$stamp.log"

Set-Location $repo
"[$stamp] refresh+publish slot start" | Tee-Object -FilePath $log -Append

& $py -X utf8 -u "pipelines\operator_control.py" daily-accept *>> $log
$acceptExit = $LASTEXITCODE

# daily-accept 紅就唔發佈：ranking generation 可能寫到一半。
if ($acceptExit -eq 0) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "daily_public_release.ps1") *>> $log
    $publishExit = $LASTEXITCODE
} else {
    "[$stamp] daily-accept failed exit=$acceptExit; public release skipped" | Tee-Object -FilePath $log -Append
    $publishExit = -1
}

$done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
"[$done] refresh+publish slot done accept=$acceptExit publish=$publishExit" | Tee-Object -FilePath $log -Append
if ($acceptExit -ne 0 -or $publishExit -ne 0) { exit 1 }
exit 0
