# CARDZ 036 morning chain: browser-dependent collectors -> daily-accept re-rank.
# Registered as Task Scheduler job CARDZ-036-Morning-Browser-Lanes.
# These lanes (pc_ebay_sales, en_price_ref) need a headed Chrome CDP session,
# so they run in the morning slot where ensure_chrome_cdp can own the desktop;
# the HTTP-only lanes live in nightly_collect_accept.ps1.
$ErrorActionPreference = "Continue"
# Windows PowerShell 5.1 redirects as UTF-16LE by default; force UTF-8 so the
# morning log stays greppable from every tool on the box.
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$repo = Split-Path -Parent $PSScriptRoot
$py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
$logDir = Join-Path $repo "data\runtime\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$log = Join-Path $logDir "morning-$stamp.log"

Set-Location $repo
"[$stamp] morning browser chain start" | Tee-Object -FilePath $log -Append

# 收唔到貨 ≠ 出街數據壞。舊版一係 CDP 起唔到、一係 PC lane 俾 Cloudflare 擋，
# 就 `exit 1` 收工，連 daily-accept 同發佈都唔行 —— 明明 DB 入面上一版 accepted
# generation 完全有效，個站就咁停一日唔更新。而家 browser lane 死咗照落去，
# 最後用 exit code 報返邊一步紅，唔會靜靜當成功。
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ensure_chrome_cdp.ps1") -Port 9333 *>> $log
$cdpExit = $LASTEXITCODE

# `--adapter browser` = ADAPTER_LANE 入面標住 "browser" 嗰批。同夜鏈嗰邊
# `--adapter http` 合埋一定覆蓋晒 CHECKPOINT_ADAPTERS，新 adapter 唔會再漏喺
# 兩張硬編名單之間。
if ($cdpExit -eq 0) {
    & $py -X utf8 -u "pipelines\collect_control.py" incr --adapter browser --ensure-browser *>> $log
    $collectExit = $LASTEXITCODE
} else {
    "[$stamp] ensure_chrome_cdp failed exit=$cdpExit; browser lanes skipped, chain continues" | Tee-Object -FilePath $log -Append
    $collectExit = -1
}

& $py -X utf8 -u "pipelines\operator_control.py" daily-accept *>> $log
$acceptExit = $LASTEXITCODE

# daily-accept 紅就唔發佈：個 ranking generation 可能寫到一半，發出去就係出錯數。
# 收集紅唔擋發佈，接受紅先擋。
if ($acceptExit -eq 0) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "daily_public_release.ps1") *>> $log
    $publishExit = $LASTEXITCODE
} else {
    "[$stamp] daily-accept failed exit=$acceptExit; public release skipped" | Tee-Object -FilePath $log -Append
    $publishExit = -1
}

$done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
"[$done] morning browser chain done cdp=$cdpExit collect=$collectExit accept=$acceptExit publish=$publishExit" | Tee-Object -FilePath $log -Append
if ($cdpExit -ne 0 -or $collectExit -ne 0 -or $acceptExit -ne 0 -or $publishExit -ne 0) { exit 1 }
exit 0
