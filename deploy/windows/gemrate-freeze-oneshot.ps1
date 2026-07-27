# GemRate POP 歷史一次性凍結抓取 —— 服務 API key 2026-07-29 死線。
#
# 點解呢個一次性任務唔違反 install_gemrate_task.ps1 嗰條「禁止獨立 GemRate 排程」規矩：
#   嗰條規矩禁嘅係「獨立嘅每日 GemRate 任務」，因為佢會令價格、POP、排名、pointer
#   落喺唔同 run id / 唔同 fail-closed generation，整出 mixed-date rankings。
#   呢個 script 只做一件事：`gemrate_source.py api-dump`，即係將 history_full.json
#   寫落 data/private/gemrate/cards/<sha1>/。佢**唔產生 ranking、唔寫 snapshot、
#   唔郁 pointer、唔 publish**，所以結構上整唔出 mixed-date ranking。
#   ⚠️ 下一個 agent：唔准喺呢度加 run_daily.py / canonical_public_snapshot.py /
#      publish / pointer 任何一 call。一加就真係違反嗰條規矩。
#
# 點解要 poll 而唔係淨係定時跑：
#   GemRate 行 AWS API Gateway usage-plan quota（429 body = {"message":"Limit Exceeded"}，
#   x-amzn-ErrorType: LimitExceededException）。個 429 **冇** Retry-After，亦冇任何
#   rate-limit header，所以 API 唔會話你聽幾時開。實測 2026-07-26：07-25 20:32Z 開始
#   燒到 500 張卡 ×2 endpoint = 1000 call 就冚，之後 07-26 02:36:38Z 得 4 個 call 過到，
#   34 秒後又 429 —— 即係 quota 係逐少 aged-out 咁滲返出嚟，唔係定時一次過 reset。
#   所以「估個時間去跑一次」好易摸空。呢度改為：到時間就開始 poll，開到就即刻抓。
#   AWS usage-plan 打回頭嘅 429 唔計入 quota，所以 poll 本身唔燒額度。
#
# 三個 pass：
#   Pass A  132 張缺歷史（--resume）          264 call  ─┐ 優先，抓齊 = 成功
#   Pass B   52 張要 interval=week 重抓（冇 --resume） 104 call ─┘
#   Pass C  剩額填充：roster 減走已有 history 嘅差集，排名靠前先。
#           每個窗口 1000 call，優先 pass 只食 368，剩 632 唔用就係永久蒸發
#           （key 死咗攞唔返，係單向門）。Pass C 一路抓到 429 為止。
#   ⚠️ Pass C 抓唔晒係正常，**唔准**因為 Pass C 未完就報 fail 或者唔收工。
#
# temp/gemrate_freeze_result.txt 嘅 status 分辨：
#   NOT_RUN_YET                  呢個 script 從來冇寫過呢個值 —— 見到即係人手擺嘅佔位符，
#                                代表未跑過。
#   QUOTA_NEVER_OPENED           跑過，poll 足鐘都攞唔到一個 200。抓咗零張。
#   PRIORITY_PARTIAL             quota 開過，但 Pass A/B 未跑完。
#   PRIORITY_DONE_SPARE_PARTIAL  優先 184 張抓齊，Pass C 食剩額食到 429 為止。**成功**。
#   PRIORITY_DONE_SPARE_DONE     優先抓齊，而且 roster 已經冇卡缺歷史。全清。
#   KEY_EXPIRED_CLEANUP          過咗 07-29，唔再試 API，淨係自刪任務收場。
[CmdletBinding()]
param(
    [string]$RepoRoot = 'C:\Users\jackson0202\Documents\Playground\cardz-market-cap',
    [string]$PythonExe = 'C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe',
    # 跑完之後自刪嘅 task 名。留空 = 唔自刪（手動跑嗰陣用）。
    [string]$SelfDeleteTaskName = '',
    # Poll 幾耐先放棄。3.5 鐘 = 短過兩個觸發點之間隔（4 鐘），避免上一輪未收工
    # 就撞到下一個觸發（Windows 預設 IgnoreNew 會靜靜跳過嗰次觸發）。
    [double]$MaxPollHours = 3.5,
    [int]$PollIntervalMinutes = 10,
    # key 死線。到咗呢日就唔好再試 API，直接清走自己。
    [string]$KeyDeadlineUtc = '2026-07-29T00:00:00Z'
)

$ErrorActionPreference = 'Stop'

$root       = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$python     = (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).Path
$gemrateEnv = Join-Path $root 'data\runtime\config\gemrate.env'
$backendEnv = Join-Path $root 'data\runtime\config\backend.env'
$passA      = Join-Path $root 'temp\freeze_pass_a_missing.txt'
$passB      = Join-Path $root 'temp\freeze_pass_b_biweekly.txt'
$passC      = Join-Path $root 'data\runtime\private-source-map\freeze-pass-c-spare-ids.txt'
$resultFile = Join-Path $root 'temp\gemrate_freeze_result.txt'
$marker     = Join-Path $root 'data\runtime\gemrate_freeze_complete.json'
$logDir     = Join-Path $root 'data\runtime\logs'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $resultFile) | Out-Null

$stamp   = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$runLog  = Join-Path $logDir "gemrate_freeze_oneshot_$stamp.log"

function Write-Log([string]$Message) {
    $line = '{0} {1}' -f [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'), $Message
    Write-Output $line
    Add-Content -LiteralPath $runLog -Value $line
}

function Remove-SelfTask {
    if ([string]::IsNullOrWhiteSpace($SelfDeleteTaskName)) { return }
    try {
        $task = Get-ScheduledTask -TaskName $SelfDeleteTaskName -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskName $SelfDeleteTaskName -Confirm:$false
            Write-Log "self-deleted scheduled task '$SelfDeleteTaskName'"
        }
    } catch {
        Write-Log "WARN self-delete failed: $_"
    }
}

# gemrate.env / backend.env 只喺呢個 process 入面 expand。key 永遠唔會出現喺
# 任何 child process 嘅 command line（gemrate_source.py 靠 GEMRATE_API_KEY env var 攞）。
function Import-EnvFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "environment file missing: $Path"
    }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            throw 'environment file contains an invalid assignment'
        }
        Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2]
    }
}

# 一個 GET，冇 retry。回 HTTP status（網絡失敗回 0）。
function Get-QuotaStatus([string]$Key) {
    $probeId = (Get-Content -LiteralPath $passA -TotalCount 1).Trim()
    $uri = "https://api.gemrate.com/v1/cards/$probeId/population?parsed_description=true"
    try {
        $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 45 `
            -Headers @{ 'x-api-key' = $Key; 'Accept' = 'application/json' }
        return [int]$response.StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

# api-dump 一趟。exit: 0 ok / 2 冇 key 或冇 id / 3 key died / 4 quota spent。
# gemrate_source.py 逐張卡即刻 _save() 落盤，所以中途 429 都保得住已抓嗰啲。
function Invoke-ApiDump([string]$IdsFile, [bool]$Resume, [string]$Label) {
    $arguments = @('-X', 'utf8', 'pipelines/gemrate_source.py', 'api-dump',
                   '--ids-file', $IdsFile, '--speed', 'slow')
    if ($Resume) { $arguments += '--resume' }
    $out = Join-Path $logDir "gemrate_freeze_oneshot_${stamp}_$Label.log"
    Write-Log "$Label start (resume=$Resume) -> $out"
    $process = Start-Process -FilePath $python -ArgumentList $arguments `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru -Wait `
        -RedirectStandardOutput $out -RedirectStandardError "$out.err"
    Write-Log "$Label exit=$($process.ExitCode)"
    return $process.ExitCode
}

# 有幾多張卡真係落咗 history_full.json —— 用嚟量呢一輪嘅實際收穫，唔靠 log 講。
function Get-HistoryCount {
    $cards = Join-Path $root 'data\private\gemrate\cards'
    if (-not (Test-Path -LiteralPath $cards -PathType Container)) { return 0 }
    return @(Get-ChildItem -LiteralPath $cards -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'history_full.json') -PathType Leaf }).Count
}

$summary = [ordered]@{
    startedAtUtc = [DateTime]::UtcNow.ToString('o')
    quotaOpenedAtUtc = $null
    passA = 'not-attempted'
    passB = 'not-attempted'
    passC = 'not-attempted'
    historyBefore = 0
    historyAfter = 0
    cardsGained = 0
    status = 'PRIORITY_PARTIAL'
}
$didWork = $false
$priorityDone = $false

try {
    Write-Log '=== gemrate freeze one-shot start ==='
    $summary.historyBefore = Get-HistoryCount
    Write-Log "history_full.json before = $($summary.historyBefore)"

    if ([DateTime]::UtcNow -ge [DateTime]::Parse($KeyDeadlineUtc).ToUniversalTime()) {
        Write-Log "past key deadline $KeyDeadlineUtc; nothing can be harvested, cleaning up"
        $summary.status = 'KEY_EXPIRED_CLEANUP'
        Remove-SelfTask
        exit 0
    }

    # marker 只代表「優先 184 張抓齊咗」。**唔代表冇嘢做** —— 剩額 Pass C 仍然要跑，
    # 唔係就會白白蒸發成個窗口嘅額度。
    if (Test-Path -LiteralPath $marker -PathType Leaf) {
        $done = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
        if ($done.status -eq 'COMPLETE' -or $done.priorityStatus -eq 'DONE') {
            $priorityDone = $true
            Write-Log "priority passes already done at $($done.completedAtUtc); skipping A/B, going straight to spare fill"
            $summary.passA = 'skipped (already done)'
            $summary.passB = 'skipped (already done)'
        }
    }

    foreach ($required in @($passA, $passB)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "id file missing: $required"
        }
    }

    Import-EnvFile $gemrateEnv
    $key = $env:GEMRATE_API_KEY
    if ([string]::IsNullOrWhiteSpace($key)) { throw 'GEMRATE_API_KEY is empty' }

    # --- 1. 等 quota 開 ---
    $deadline = [DateTime]::UtcNow.AddHours($MaxPollHours)
    $opened = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        $status = Get-QuotaStatus $key
        Write-Log "probe -> HTTP $status"
        if ($status -eq 200) { $opened = $true; break }
        if ($status -eq 401 -or $status -eq 403) {
            throw "key rejected (HTTP $status); the 2026-07-29 expiry may have landed early"
        }
        Start-Sleep -Seconds ($PollIntervalMinutes * 60)
    }
    if (-not $opened) {
        Write-Log "quota never opened within $MaxPollHours h; giving up this run"
        $summary.status = 'QUOTA_NEVER_OPENED'
        exit 4
    }
    $summary.quotaOpenedAtUtc = [DateTime]::UtcNow.ToString('o')
    Write-Log 'quota open; starting harvest'

    # --- 2. Pass A：132 張缺歷史。--resume 令重跑只補未做嗰啲。 ---
    # --- 3. Pass B：52 張要用 interval=week 重抓，**故意冇 --resume** ---
    #        （history_full.json 已經存在，加 --resume 會全部 skip，白行）。
    if (-not $priorityDone) {
        $passes = @(
            @{ Label = 'passA'; File = $passA; Resume = $true },
            @{ Label = 'passB'; File = $passB; Resume = $false }
        )
        foreach ($pass in $passes) {
            $attempt = 0
            while ($true) {
                $attempt++
                $didWork = $true
                $code = Invoke-ApiDump $pass.File $pass.Resume $pass.Label
                if ($code -eq 0) { $summary[$pass.Label] = "ok (attempt $attempt)"; break }
                if ($code -eq 3) { $summary[$pass.Label] = 'key-dead'; throw 'GemRate key lost access (HTTP 401/403)' }
                if ($code -ne 4) { $summary[$pass.Label] = "failed exit $code"; throw "$($pass.Label) failed with exit $code" }
                # quota 中途乾咗：等下一注 aged-out 額度再接。Pass A 有 --resume，
                # 重跑係遞增嘅；Pass B 重跑會重抓，但 52 張 ×2 = 104 call，蝕得起。
                if ([DateTime]::UtcNow -ge $deadline) {
                    $summary[$pass.Label] = "quota-exhausted after $attempt attempts"
                    Write-Log "$($pass.Label) still short when the poll deadline hit"
                    break
                }
                Write-Log "$($pass.Label) hit quota; waiting $PollIntervalMinutes min then retrying"
                Start-Sleep -Seconds ($PollIntervalMinutes * 60)
            }
        }
        $priorityDone = ($summary.passA -like 'ok*' -and $summary.passB -like 'ok*')
    }

    if (-not $priorityDone) {
        $summary.status = 'PRIORITY_PARTIAL'
        Write-Log 'priority passes incomplete; skipping spare fill so the next window retries them first'
    } else {
        # --- 4. Pass C：剩額填充。優先次序由 build_freeze_pass_c.py 定
        #        （榜內排名靠前 → 市值 → roster 原順序），純檔案運算，唔使 DB。
        #        429 就係停止訊號，唔預留、唔估剩幾多額。抓唔晒係預期之內。 ---
        $buildLog = Join-Path $logDir "gemrate_freeze_oneshot_${stamp}_passC_build.log"
        $build = Start-Process -FilePath $python `
            -ArgumentList @('-X', 'utf8', 'scripts/build_freeze_pass_c.py') `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru -Wait `
            -RedirectStandardOutput $buildLog -RedirectStandardError "$buildLog.err"
        Write-Log "passC build exit=$($build.ExitCode) -> $buildLog"

        if ($build.ExitCode -eq 3) {
            $summary.passC = 'nothing left to fetch'
            $summary.status = 'PRIORITY_DONE_SPARE_DONE'
        } elseif ($build.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $passC -PathType Leaf)) {
            $summary.passC = "build failed exit $($build.ExitCode)"
            $summary.status = 'PRIORITY_DONE_SPARE_PARTIAL'
            Write-Log 'WARN passC list could not be built; priority work still counts as success'
        } else {
            $didWork = $true
            $code = Invoke-ApiDump $passC $true 'passC'
            switch ($code) {
                0 { $summary.passC = 'ok (list exhausted)'; $summary.status = 'PRIORITY_DONE_SPARE_DONE' }
                4 { $summary.passC = 'stopped on quota (expected)'; $summary.status = 'PRIORITY_DONE_SPARE_PARTIAL' }
                3 { $summary.passC = 'key-dead'; $summary.status = 'PRIORITY_DONE_SPARE_PARTIAL' }
                default { $summary.passC = "exit $code"; $summary.status = 'PRIORITY_DONE_SPARE_PARTIAL' }
            }
        }
    }

    # --- 5. 覆蓋率覆核（唯讀：measure_pop_coverage.py 只 SELECT，寫 temp/ JSON，
    #        唔會寫 snapshot、唔會 publish）。DB 唔喺度就當冇覆核到，唔算 harvest 失敗。
    #        ⚠️ 一定要叫 scripts/ 嗰份，唔准叫 temp/ 嗰份 —— temp/ 隨時被清，
    #        排程依賴 temp/ 會靜靜死（tests/test_verify_doc_refs.py 守住呢條）。 ---
    try {
        Import-EnvFile $backendEnv
        $coverLog = Join-Path $logDir "gemrate_freeze_oneshot_${stamp}_coverage.log"
        $cover = Start-Process -FilePath $python `
            -ArgumentList @('-X', 'utf8', 'scripts/measure_pop_coverage.py', 'after') `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru -Wait `
            -RedirectStandardOutput $coverLog -RedirectStandardError "$coverLog.err"
        Write-Log "coverage exit=$($cover.ExitCode) -> $coverLog"
        $summary.coverage = "exit $($cover.ExitCode); see $coverLog"
    } catch {
        Write-Log "WARN coverage measurement skipped: $_"
        $summary.coverage = 'skipped'
    }
} catch {
    Write-Log "ERROR $_"
    $summary.error = "$_"
} finally {
    $summary.historyAfter = Get-HistoryCount
    $summary.cardsGained = $summary.historyAfter - $summary.historyBefore
    $summary.finishedAtUtc = [DateTime]::UtcNow.ToString('o')
    $summary.runLog = $runLog

    # 冇做過嘢嘅一輪唔准冚走上一輪真正 harvest 嘅記錄。
    if ($didWork -or -not (Test-Path -LiteralPath $resultFile -PathType Leaf)) {
        $lines = $summary.GetEnumerator() | ForEach-Object { '{0}={1}' -f $_.Key, $_.Value }
        Set-Content -LiteralPath $resultFile -Value $lines -Encoding UTF8
    } else {
        Write-Log "no work done this run; kept the previous $resultFile"
    }

    if ($priorityDone) {
        Set-Content -LiteralPath $marker -Encoding UTF8 -Value (
            [pscustomobject]@{
                status = 'COMPLETE'
                priorityStatus = 'DONE'
                completedAtUtc = $summary.finishedAtUtc
            } | ConvertTo-Json
        )
    }

    Write-Log "cards gained this run = $($summary.cardsGained) (history_full.json $($summary.historyBefore) -> $($summary.historyAfter))"
    Write-Log "result -> $resultFile (status=$($summary.status))"

    # 只有真係冇嘢再抓（或者 key 已死）先自刪。**唔准**因為 Pass C 未抓晒就 keep，
    # 亦唔准因為優先 pass 抓齊就即刻刪 —— 刪咗就冇人再幫手食剩額。
    if ($summary.status -eq 'PRIORITY_DONE_SPARE_DONE' -or $summary.status -eq 'KEY_EXPIRED_CLEANUP') {
        Remove-SelfTask
    } else {
        Write-Log "keeping scheduled task '$SelfDeleteTaskName' so later triggers can keep filling spare quota"
    }
    Write-Log '=== gemrate freeze one-shot done ==='
}
