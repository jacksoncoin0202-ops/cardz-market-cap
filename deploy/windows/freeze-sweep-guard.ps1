# 週掃讓路守門員 —— 一次性，服務 GemRate key 07-29 死線。
#
# 點解要有呢個：凍結掃係手動 background process，唔喺 systemd 管轄之下，
# 所以 cardz-gemrate-freeze.service 個 `Conflicts=cardz-market-cap-daily.service`
# 完全冇生效。兩者同時行 = 爭同一個 GemRate quota 互相 429，而每日出數
# （公開 snapshot 嘅唯一來源）唔可以輸。
#
# 流程：09:20 JST 停掃 -> 等 daily 行完 -> 用 --speed slow 接返落去。
# gemrate_source._save 用 tmp + os.replace 原子寫入，所以硬殺最多蝕一張
# 未寫完嘅卡，--resume 下次會補返。
$ErrorActionPreference = 'Stop'

$repo = 'C:\Users\jackson0202\Documents\Playground\cardz-market-cap'
$py   = 'C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe'
$ids  = 'data/runtime/private-source-map/tracked-gemrate-ids.txt'
$log  = Join-Path $repo 'data\runtime\logs\freeze_sweep_guard.log'

New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function Write-Log($msg) {
    $line = "$([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')) $msg"
    Write-Output $line
    Add-Content -Path $log -Value $line
}

function Get-Sweep {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*gemrate_source.py*api-dump*' }
}

function Get-Daily {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*run_daily.py*' }
}

Write-Log '=== guard start ==='

# --- 1. 停低週掃 ---
$sweep = @(Get-Sweep)
if ($sweep.Count -eq 0) {
    Write-Log 'no sweep running; nothing to stop'
} else {
    foreach ($p in $sweep) {
        Write-Log "stopping sweep PID $($p.ProcessId)"
        try { Stop-Process -Id $p.ProcessId -Force } catch { Write-Log "stop failed: $_" }
    }
    Start-Sleep -Seconds 5
    if (@(Get-Sweep).Count -gt 0) { Write-Log 'WARN: sweep still alive after stop' }
    else { Write-Log 'sweep stopped' }
}

# --- 2. 等 daily 行完 ---
# daily 00:30Z 開，實測 2–2.5 鐘。3:30Z 之後先考慮接返，而且要確認 run_daily
# 真係退咗。6:00Z 硬上限：等到嗰陣就算 daily 仲喺度都照接（免得死等一日）。
$earliest = [DateTime]::UtcNow.Date.AddHours(3).AddMinutes(30)
if ([DateTime]::UtcNow -gt $earliest) { $earliest = $earliest.AddDays(1) }
$hardCap = $earliest.AddHours(2.5)

Write-Log "waiting for daily; earliest resume $($earliest.ToString('yyyy-MM-ddTHH:mm:ssZ')), hard cap $($hardCap.ToString('yyyy-MM-ddTHH:mm:ssZ'))"

while ($true) {
    $now = [DateTime]::UtcNow
    if ($now -ge $hardCap) { Write-Log 'hard cap reached; resuming regardless'; break }
    if ($now -ge $earliest -and @(Get-Daily).Count -eq 0) { Write-Log 'daily finished; resuming'; break }
    Start-Sleep -Seconds 300
}

# --- 3. 接返落去，改用 slow ---
# medium (1.0s) 實測 80% 回應係 429 —— 個節奏對 GemRate 嚟講太密，既嘈又
# 蝕時間喺 retry loop。slow (3.0s) 少啲被彈，淨吞吐未必差，而且 key 要撐到 07-29。
Set-Location $repo
$stamp   = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$outLog  = "data\runtime\logs\gemrate_freeze_$stamp.log"

Write-Log "resuming sweep --speed slow -> $outLog"
$proc = Start-Process -FilePath $py `
    -ArgumentList @('-X','utf8','pipelines/gemrate_source.py','api-dump','--ids-file',$ids,'--speed','slow','--resume') `
    -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError "$outLog.err"

Write-Log "resumed as PID $($proc.Id)"

# --- 4. 報返實際 roster 覆蓋率 ---
# 路線更新第 6 點：靜靜少咗嘢係最陰險嘅失敗，靠 error 捉唔到，要靠數量。
$covered = & $py -X utf8 -c @"
import pathlib
root = pathlib.Path(r'$repo')
ids = [l.strip() for l in (root/'data/runtime/private-source-map/tracked-gemrate-ids.txt').read_text().splitlines() if l.strip()]
cards = root/'data/private/gemrate/cards'
have = sum(1 for i in ids if (cards/i/'history_full.json').exists())
print(f'{have}/{len(ids)}')
"@
Write-Log "roster history coverage: $covered"
Write-Log '=== guard done ==='
