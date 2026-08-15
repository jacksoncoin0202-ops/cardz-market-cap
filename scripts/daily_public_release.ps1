# CARDZ 036 / FE03 daily public release entrypoint.
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

# 朝鏈／refresh 都經呢度。bake／sync／push 死一次唔等於當日 [deploy] 完。
# 11:30／16:30 係另一個 slot；呢度係同一轉入面重試 3 次。
$maxAttempts = 3
$code = 1
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    Write-Host "daily_public_release attempt $attempt/$maxAttempts"
    & wsl.exe -d Ubuntu -- bash $wslScript
    $code = $LASTEXITCODE
    if ($code -eq 0) { exit 0 }
    if ($attempt -lt $maxAttempts) {
        Start-Sleep -Seconds 20
    }
}
exit $code
