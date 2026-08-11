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

& wsl.exe -d Ubuntu -- bash $wslScript
exit $LASTEXITCODE
