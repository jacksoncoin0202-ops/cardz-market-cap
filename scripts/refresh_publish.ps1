# CARDZ 036 refresh+publish slot: daily-accept -> public release.
# Registered as Task Scheduler job CARDZ-036-Refresh-Publish (repeats through the day).
#
# 冇 collector 喺呢度 —— 呢個 slot 淨係重試，唔係第二次更新。
# owner 要一日更新一次：09:30 朝鏈（收貨 → re-rank → 發佈）就係嗰一次。
# 呢個 slot 11:30 / 16:30 再行一次 accept + publish，用嚟救朝鏈死咗嗰日：
#   - 朝鏈 accept 死咗 → 呢度 accept 返，個站當日仍然更新到
#   - 朝鏈 publish 死咗 → 呢度推返
# 兩個 slot 之間冇 collector 行過，所以 DB 冇新嘢，accept 出返同一個 ranking sha，
# daily_public_release 個 no-change 閘就會 fire，零 commit 零部署（實測 106 秒收工）。
# 即係朝鏈正常嗰日，呢兩個 slot 唔會令個站更新多過一次。
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
