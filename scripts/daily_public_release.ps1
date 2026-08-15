# CARDZ 036 / FE03 daily public release entrypoint.
# -Scheduled：只有 Task Scheduler 起嘅 wrapper（morning_browser_lanes.ps1 /
# refresh_publish.ps1 偵測到自己個 parent 係 Schedule service）先會傳。人手直接行呢個
# 檔（冇 -Scheduled）= 人手補推：出街照出，但 autonomy receipt 只記 manual-catchup。
param([switch]$Scheduled)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "daily_public_release.sh"
# 唔可以行 `wsl.exe -- wslpath -a $script`：Windows PowerShell 5.1 交俾 wsl.exe
# 嘅 argv 會被剝走 backslash，Linux 側收到 `C:Usersjackson0202...`，wslpath
# 出空字串，跟住 .Trim() 就 throw on null —— 09:30 條 Task Scheduler 朝早鏈
# 一直靜靜哋死喺呢一行。drive-letter 路徑轉 /mnt/<x>/ 係固定形狀，自己砌。
if ($script -notmatch '^[A-Za-z]:\\') {
    throw "daily release script must live on a drive-letter path, got: $script"
}
$wslScript = "/mnt/" + $script.Substring(0, 1).ToLowerInvariant() + ($script.Substring(2) -replace '\\', '/')

# wrapper 度 set 嘅 $env:CARDZ_DAILY_CHAIN 過唔到 wsl.exe（WSLENV 冇 set，wsl.exe 唔會
# 抄 Windows env 落 Linux）。2026-08-15 16:30 排程 slot 明明出到 no-change，stamp 都記
# manual-catchup-ignored（refresh-20260815T073001Z.log:978）。所以：
#   1) 用 `env` 前綴明文帶 CARDZ_DAILY_CHAIN 落 Linux（1=排程、0=人手；0 係要冚走
#      WSL shell 可能殘留嘅舊值）；
#   2) 再傳 --scheduled 俾 .sh → stamp。stamp 要兩樣齊先記排程日；得 env 冇 token 當人手。
$chainFlag = if ($Scheduled) { "1" } else { "0" }
$shArgs = @()
if ($Scheduled) { $shArgs += "--scheduled" }
Write-Host "daily_public_release launcher scheduled=$($Scheduled.IsPresent) CARDZ_DAILY_CHAIN=$chainFlag"

# 朝鏈／refresh 都經呢度。bake／sync／push 死一次唔等於當日 [deploy] 完。
# 11:30／16:30 係另一個 slot；呢度係同一轉入面重試 3 次。
$maxAttempts = 3
$code = 1
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    Write-Host "daily_public_release attempt $attempt/$maxAttempts"
    & wsl.exe -d Ubuntu -- env "CARDZ_DAILY_CHAIN=$chainFlag" bash $wslScript @shArgs
    $code = $LASTEXITCODE
    if ($code -eq 0) { exit 0 }
    # 3 = 已出街／live 對到，只係 autonomy stamp 死（見 .sh stamp_autonomy）。
    # 唔好重試成條鏈（會再 bake 一次），直接紅住出去俾 wrapper／Task Scheduler 見到。
    if ($code -eq 3) { exit 3 }
    if ($attempt -lt $maxAttempts) {
        Start-Sleep -Seconds 20
    }
}
exit $code
