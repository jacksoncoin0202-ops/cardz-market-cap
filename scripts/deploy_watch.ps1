# scripts/deploy_watch.ps1 — push 完之後，一句命令幫你睇實條 deploy 鏈。
#
# 呢個腳本存在嘅原因（2026-08-17 事故）：
#   895f9f76 `14:28:28Z` push 咗上 main、subject 有 [deploy]，個站 15 分鐘零變化。
#   當時查 `hooks/658470027/deliveries` 見唔到任何對得返嘅 delivery，於是判咗
#   「GitHub 冇派」。事後用 delivery guid（UUIDv1，內含事件產生時間）翻查：
#   GitHub 其實 14:28:28.722Z 就已經產生咗個 event，只係到 14:59:24.105Z 先派出去
#   —— 遲咗 30 分 55 秒。deliveries 列表係按 delivered_at 排，**未派出去嘅 event
#   喺列表度完全睇唔到**，所以「查唔到」≠「冇派」，只係「嗰刻未派到」。
#
#   2026-08-17 量（過去 3 日 87 件 delivery，全部 push / 全部 200 / 0 redelivery / 0 throttled）：
#   event→delivery lag median 1.2s、p90 1.6s、max 1855.4s（就係出事嗰件）。
#   本機 56 粒 push 全部 1:1 對得返 delivery —— 一件都冇真正漏，只係一件遲咗。
#   2026-08-18 用呢個腳本覆量（窗口滑走咗，件數少咗）：82 件、median 1.2s、p90 1.4s、
#   max 1855.4s、非 2xx 0、本機 52/52 對得返。結論同上，冇變。
#
# 所以呢個腳本唔會再靠「有冇 delivery」一刀切，而係：
#   1. push 之前成個 message（subject + body）冇 [deploy] → 即刻停（呢個先係最常見嘅
#      自己做錯）；body 有而 subject 冇 → 當佢會出街，大聲警告再照睇（2026-08-21 盲點）
#   2. sha 唔喺 origin/main → 即刻停（根本未 push）
#   3. 一路 poll GitHub delivery + live 內容 marker，兩邊分開講
#   4. 過咗 grace 都冇 delivery → 大聲嗌「GitHub 未派，唔係你 build 炸」+ 畀埋下一步
#   5. 有 delivery、2xx，但 live 唔郁 → 大聲嗌「GitHub 派咗，去睇 AWS docker log」
#
# 用法：
#   pwsh -NoProfile -File scripts\deploy_watch.ps1                       # push 完即刻跑
#   pwsh -NoProfile -File scripts\deploy_watch.ps1 -Marker 'card-art'    # 加內容 marker（regex）
#   pwsh -NoProfile -File scripts\deploy_watch.ps1 -DeliveryOnly         # 只等 GitHub 派，唔等上街
#   pwsh -NoProfile -File scripts\deploy_watch.ps1 -AutoKick             # 冇派就自動踢一腳（⚠️ 見下）
#   pwsh -NoProfile -File scripts\deploy_watch.ps1 -AuditOnly -Sha 895f9f76   # 事後翻查一粒
#   pwsh -NoProfile -File scripts\deploy_watch.ps1 -Audit -AuditHours 72       # 配對率統計
#   pwsh -NoProfile -File scripts\deploy_watch.ps1 -Simulate NoDelivery -TimeoutMinutes 0.2
#
# `-Marker` 係 **regex**，唔係 literal：`card-art` OK，但 `foo(bar)` 要自己 escape，
# 唔係就會靜靜當 marker 對唔到（或者更衰，對到唔應該對嘅嘢）。
#
# Exit codes（要攞到 exit code 就用 `pwsh -File`，`pwsh -Command` 唔會照傳）：
#   0  上街驗到（delivery 2xx + 內容真係轉咗）
#   2  timeout，但 delivery 有而且 2xx  → AWS 側 git pull / next build 問題
#   3  timeout，而且由頭到尾冇 delivery → GitHub 未派（唔關 code 事）
#   4  delivery 有但係非 2xx           → 接收端 spwebhook.funtoken.me 死
#   5  未量到就已經錯：成個 message 冇 [deploy]／未 push／gh 未登入／攞唔到 deliveries／
#      連 live 基線都抓唔到（冇基線就冇得比較，fail-closed，唔准估）
#
# 實跑證過會 fire（2026-08-18，真 GitHub API / 真站 / 假 live server，唔係講）：
#   exit 0  -AuditOnly -Sha 005935a3                → 對到 payload.after，lag 1.2s
#   exit 0  -DeliveryOnly（HEAD 1d9cda26）          → 真站真 delivery，一 poll 就綠
#   exit 2  -Marker <實冇嘅字> 短 timeout            → 「delivery 有但 live 冇轉」
#   exit 3  -Simulate NoDelivery                    → 成段紅色警告 + 下一步
#   exit 4  -Simulate FailedDelivery                → 印足 19 位 delivery id（冇被截）
#   exit 5  -Sha 76a0a5c6（成個 message 冇 [deploy]）／ -Sha deadbeef1（認唔到 commit）／
#           -BaseUrl 死站（攞唔到 live 基線）
#   -Audit  72 小時：82 件、median 1.2s、p90 1.4s、max 1855.4s、本機 52/52 對得返
#   -AuditOnly -Sha 895f9f76：exact=True、lag 1855.4s → 重現返成件事故
#
# ⚠️ **未實跑過**（唔准當已驗）：
#   - `-AutoKick` 入面條 `git commit --allow-empty` + `git push`（寫腳本嗰陣唔准 push）
#   - 真・push 之後 0→1 嗰次「派出 → 上街」計時（要真出街嗰日先做得到）
#   第一次用 -AutoKick 要有人睇住個 terminal，唔好放落 Task Scheduler 無人理。
[CmdletBinding()]
param(
  [string]$Sha = "",
  [string]$Repo = "jacksoncoin0202-ops/cardz-market-cap",
  [string]$HookId = "658470027",
  [string]$BaseUrl = "https://app.cardzmarketcap.com",
  [string]$MarkerPath = "/",
  [string[]]$Marker = @(),
  # html=淨睇頁面 HTML；css=淨睇 .css chunk；both=兩樣（預設）；js=淨睇 .js chunk；all=三樣都睇。
  # ⚠️ `both` **唔包 .js**。UI 文案／標籤（例：選單個 "1.91:1"）住喺 JS chunk，
  #    用 both 就永遠 0/1。要驗 JS 入面嘅字就要 `-MarkerScope js`（或 all）。
  [ValidateSet("html", "css", "both", "js", "all")][string]$MarkerScope = "both",
  [double]$TimeoutMinutes = 12,
  [int]$PollSeconds = 20,
  [int]$DeliveryGraceSeconds = 120,
  [double]$BuildGraceMinutes = 5,
  [double]$KickAfterMinutes = 6,
  [switch]$AutoKick,
  [switch]$DeliveryOnly,
  [switch]$AuditOnly,
  [switch]$Audit,
  [int]$AuditHours = 72,
  [ValidateSet("none", "NoDelivery", "FailedDelivery", "StaleLive")][string]$Simulate = "none",
  [string]$JsonOut = ""
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 CARDZ-deploy-watch"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot ".git"))) { $RepoRoot = (& git rev-parse --show-toplevel 2>$null) }

# ConvertFrom-Json 會自動將 ISO 字串變 [datetime]（唔再係 string），
# 再 [datetimeoffset]::Parse() 落去就會用本機 culture 格式 → "was not recognized"。
# 所有時間一律經呢個 helper 收成 UTC DateTime。
function AsUtc($v) {
  if ($null -eq $v) { return $null }
  if ($v -is [datetime]) {
    $d = [datetime]$v
    if ($d.Kind -eq [DateTimeKind]::Unspecified) { $d = [datetime]::SpecifyKind($d, [DateTimeKind]::Utc) }
    return $d.ToUniversalTime()
  }
  if ($v -is [datetimeoffset]) { return ([datetimeoffset]$v).UtcDateTime }
  return ([datetimeoffset]::Parse([string]$v, [cultureinfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AssumeUniversal)).UtcDateTime
}

# 截字唔准直接 .Substring()：抓唔到 live 嗰陣 ChunkSha 係空字串，
# `"".Substring(0,12)` 會喺 $ErrorActionPreference="Stop" 之下直接炸死成個腳本
# —— 即係個站一死，呢個「幫你診斷個站」嘅腳本自己先死（2026-08-18 實測踩到）。
function Cut([string]$s, [int]$n) {
  if ([string]::IsNullOrEmpty($s)) { return "(none)" }
  if ($s.Length -le $n) { return $s }
  return $s.Substring(0, $n)
}

function Now { [datetime]::UtcNow.ToString("HH:mm:ss") + "Z" }
function Say([string]$m) { Write-Host "[$(Now)] $m" }
function Ok([string]$m) { Write-Host "[$(Now)] OK    $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "[$(Now)] WARN  $m" -ForegroundColor Yellow }
function Bad([string]$m) { Write-Host "[$(Now)] FAIL  $m" -ForegroundColor Red }
function Die([string]$m, [int]$code) { Bad $m; exit $code }

# ---------------------------------------------------------------- git helpers
# 唔可以叫佢做 `Git`：PowerShell 唔分大細楷，function Git 入面 call `git` 會 call 返自己
# → call depth overflow（實測過，第一版就係咁死）。名 + 執行檔都寫死清楚。
function GitRun([Parameter(ValueFromRemainingArguments = $true)][string[]]$a) {
  $out = & git.exe -C $RepoRoot @a 2>&1
  return @{ Code = $LASTEXITCODE; Out = ($out | Out-String).Trim() }
}

# origin/main reflog 只記得**本機**推嘅 push（WSL release checkout 推嗰啲唔會喺度）。
# 攞到就用真 push 時間做配對窗，攞唔到就用腳本開跑時間。
function Get-PushTimeUtc([string]$sha) {
  $r = GitRun reflog show --date=iso-strict origin/main
  if ($r.Code -ne 0) { return $null }
  foreach ($line in ($r.Out -split "`r?`n")) {
    # 一定要即刻抄低 capture group：下一句再 -match 會覆蓋 $matches（實測踩過）。
    if ($line -notmatch "^([0-9a-f]+) refs/remotes/origin/main@\{([^}]+)\}: (.+)$") { continue }
    $abbrev = $matches[1]; $when = $matches[2]; $what = $matches[3]
    if ($what -notlike "*update by push*") { continue }
    if ($sha.StartsWith($abbrev)) { return (AsUtc $when) }
  }
  return $null
}

# ------------------------------------------------------- GitHub API (gh only)
# 唔用 `gh api --jq` 攞 delivery id。id 係 19 位整數，gojq 一 **轉字串** 就跌精度
# （2026-08-18 實測，gh 2.86.0，同一粒 id）：
#     --jq '.[0].id'          → 3837521760775839744  ← 冇郁過就準
#     --jq '.[] | "\(.id)"'   → 3837521760775840000  ← 尾四位變 0000
#     --jq '.[0].id|tostring' → 3837521760775840000  ← 同上
# 衰喺「喺 shell 入面印個 id 出嚟」正正就係要字串內插嗰種寫法。攞住走樣嗰個 id 去
# GET .../deliveries/<id> 會 404，而 gh 會加多句「needs the admin:repo_hook scope」
# 引你去申請 scope —— 純粹係紅鯡魚：`repo` scope 已經夠，用返準嘅 id 就 200。
# PowerShell ConvertFrom-Json 出 Int64，全程唔經字串，所以呢度一律自己 parse。
# 同上：唔可以叫 `Gh`，否則 `& gh` 會 call 返自己。執行檔一律寫 `.exe`。
function GhApi([string]$path, [string]$method = "GET") {
  $ghArgs = @("api", "-X", $method, "-H", "Accept: application/vnd.github+json", $path)
  $raw = & gh.exe @ghArgs 2>&1
  if ($LASTEXITCODE -ne 0) { return @{ Ok = $false; Err = ($raw | Out-String).Trim() } }
  try { return @{ Ok = $true; Data = (($raw | Out-String) | ConvertFrom-Json) } }
  catch { return @{ Ok = $false; Err = "JSON parse: $($_.Exception.Message)" } }
}

# delivery guid 係 UUIDv1，頭 60 bit = event **產生**時間（100ns since 1582-10-15）。
# 呢個係全份腳本最重要嘅一招：delivered_at 只講「幾時派到」，
# guid 先講「GitHub 幾時已經知道有呢粒 push」。兩者相減 = 真 lag。
function Get-GuidCreatedUtc([string]$guid) {
  try {
    $p = $guid.Split("-")
    if ($p.Count -lt 3) { return $null }
    $low = [Convert]::ToUInt64($p[0], 16)
    $mid = [Convert]::ToUInt64($p[1], 16)
    $hi = [Convert]::ToUInt64($p[2], 16) -band 0x0FFF
    $ticks = $hi * 281474976710656 + $mid * 4294967296 + $low
    return ([datetime]::new(1582, 10, 15, 0, 0, 0, [DateTimeKind]::Utc)).AddTicks([long]$ticks)
  } catch { return $null }
}

function Get-Deliveries([int]$perPage = 100) {
  $r = GhApi "repos/$Repo/hooks/$HookId/deliveries?per_page=$perPage"
  if (-not $r.Ok) { return @{ Ok = $false; Err = $r.Err } }
  $rows = @()
  foreach ($d in $r.Data) {
    $created = Get-GuidCreatedUtc $d.guid
    $delivered = AsUtc $d.delivered_at
    $rows += [pscustomobject]@{
      Id = [long]$d.id; Guid = $d.guid; Event = $d.event
      Delivered = $delivered
      Created = $created
      LagSec = if ($created) { ($delivered - $created).TotalSeconds } else { $null }
      Status = $d.status; Code = [int]$d.status_code
      Redelivery = [bool]$d.redelivery; ThrottledAt = $d.throttled_at
    }
  }
  return @{ Ok = $true; Rows = ($rows | Sort-Object Created) }
}

function Get-DeliveryPayload([long]$id) {
  $r = GhApi "repos/$Repo/hooks/$HookId/deliveries/$id"
  if (-not $r.Ok) { return $null }
  $p = $r.Data.request.payload
  if ($p -is [string]) { try { $p = $p | ConvertFrom-Json } catch { return $null } }
  return $p
}

# ------------------------------------------------------------- live probing
# `-UseBasicParsing` 喺 pwsh 7 係 no-op，但喺 Windows PowerShell 5.1 冇佢就會叫 IE 引擎，
# 部機冇 IE 就直接炸。加咗兩邊都行得。
function Get-Text([string]$url) {
  try {
    $r = Invoke-WebRequest -Uri $url -UserAgent $UA -TimeoutSec 30 -MaximumRedirection 3 -UseBasicParsing
    return @{ Ok = $true; Code = [int]$r.StatusCode; Body = [string]$r.Content; Headers = $r.Headers }
  } catch { return @{ Ok = $false; Code = 0; Body = ""; Err = $_.Exception.Message } }
}

function Sha256Hex([string]$s) {
  $h = [System.Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($s))
  return ([BitConverter]::ToString($h)).Replace("-", "").ToLower()
}

# live `X-CARDZ-Build` 永遠係 `local`（出街 build 冇設 CARDZ_PUBLIC_BUILD_ID），
# 所以認唔到 SHA。唯一可靠嘅係內容：
#   FE code 變 → /_next/static/chunks/* 檔名（內容 hash）會變
#   data 變    → /api/health 嘅 generation / generatedAt 會變
# PageOk / HealthOk 一定要跟住行出去：抓唔到嗰陣 ChunkSha 同 GeneratedAt 會係空字串，
# 空字串同基線「唔同」，如果就咁比就會將「個站死咗」讀成「內容轉咗 = deploy 成功」。
function Get-LiveFingerprint() {
  $cb = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  $page = Get-Text ("{0}{1}{2}_cb={3}" -f $BaseUrl, $MarkerPath, $(if ($MarkerPath.Contains("?")) { "&" } else { "?" }), $cb)
  $fp = [ordered]@{ PageOk = $page.Ok; PageCode = $page.Code; HealthOk = $false; ChunkCount = 0; ChunkSha = ""; Html = ""; Generation = ""; GeneratedAt = ""; Presentation = ""; Cards = 0; Build = "" }
  if ($page.Ok) {
    $fp.Html = $page.Body
    $chunks = [regex]::Matches($page.Body, "/_next/static/[^`"']+\.(?:js|css)") | ForEach-Object { $_.Value } | Sort-Object -Unique
    $fp.ChunkCount = @($chunks).Count
    $fp.ChunkSha = Sha256Hex (($chunks -join "`n"))
    $fp.Chunks = @($chunks)
  }
  $h = Get-Text "$BaseUrl/api/health"
  if ($h.Ok) {
    try {
      $j = $h.Body | ConvertFrom-Json
      # generatedAt 經 AsUtc 收成 ISO，唔可以直接 [string]：ConvertFrom-Json 出嘅係
      # [datetime]，`[string]` 會跟本機 culture 出 "08/17/2026 08:19:14"，換部機就對唔到數。
      $fp.Generation = [string]$j.generation; $fp.GeneratedAt = (AsUtc $j.generatedAt).ToString("o")
      $fp.Presentation = [string]$j.presentation; $fp.Cards = [int]$j.cards; $fp.Build = [string]$j.build
      $fp.HealthOk = $true
    } catch { }
  }
  return $fp
}

function Get-MarkerText($fp) {
  $wantHtml = $MarkerScope -in @("html", "both", "all")
  $wantCss = $MarkerScope -in @("css", "both", "all")
  $wantJs = $MarkerScope -in @("js", "all")
  $text = ""
  if ($wantHtml) { $text += $fp.Html }
  if (($wantCss -or $wantJs) -and $fp.Chunks) {
    foreach ($c in $fp.Chunks) {
      $isCss = $c -match "\.css$"
      if (($isCss -and $wantCss) -or ((-not $isCss) -and $wantJs)) {
        $r = Get-Text ($BaseUrl + $c); if ($r.Ok) { $text += $r.Body }
      }
    }
  }
  return $text
}

# ------------------------------------------------------------------- alarms
function Alarm-NoDelivery([string]$sha, [string]$elapsed, $stats) {
  Write-Host ""
  Write-Host "==================================================================" -ForegroundColor Red
  Write-Host "  GitHub 未派件 —— 唔係你 build 炸，本機點改都冇用" -ForegroundColor Red
  Write-Host "==================================================================" -ForegroundColor Red
  Write-Host "  commit    : $sha（已經喺 origin/main、message 有 [deploy]）"
  Write-Host "  等咗      : $elapsed"
  Write-Host "  hook      : $HookId → https://spwebhook.funtoken.me/hooks/deploy-cardzmarketcap"
  if ($stats) { Write-Host "  正常 lag  : median $($stats.Median)s / p90 $($stats.P90)s（過去 $AuditHours 小時 $($stats.N) 件）" }
  Write-Host ""
  Write-Host "  唔好做：改 code、重 build、清 Cloudflare、重推同一份嘢。呢三樣都唔關事。" -ForegroundColor Yellow
  Write-Host "  『查唔到 delivery』≠『GitHub 冇派』——列表按 delivered_at 排，" -ForegroundColor Yellow
  Write-Host "  未派出去嘅 event 喺列表度係隱形嘅。實測試過遲 30 分 55 秒先出現。" -ForegroundColor Yellow
  Write-Host ""
  Write-Host "  下一步（由平到貴）："
  Write-Host "   1) 等 —— 呢個腳本會繼續 poll 到 $TimeoutMinutes 分鐘。"
  Write-Host "   2) 踢一腳（實測有效，最快）："
  Write-Host "        git commit --allow-empty -m `"chore(deploy): kick webhook for $(Cut $sha 8) [deploy]`"" -ForegroundColor Cyan
  Write-Host "        git push origin HEAD:main" -ForegroundColor Cyan
  Write-Host "      AWS 側係 git pull 去 origin/main 個 tip，一腳會連之前積落嗰啲一齊帶上街。"
  Write-Host "      （呢個腳本加 -AutoKick 就會喺 $KickAfterMinutes 分鐘後自動幫你踢）"
  Write-Host "   3) 見到有 delivery 但係紅色先用 redeliver（GitHub **唔會**自動重試）："
  Write-Host "        gh api -X POST repos/$Repo/hooks/$HookId/deliveries/<id>/attempts" -ForegroundColor Cyan
  Write-Host "      要 repo admin 權限；只可以 redeliver 過去 3 日嘅 delivery。"
  Write-Host "   4) 仲係一片死寂 → 升 IT 睇 spwebhook.funtoken.me；順便睇 githubstatus.com。"
  Write-Host ""
}

function Alarm-BuildSide([string]$sha, $del) {
  Write-Host ""
  Write-Host "==================================================================" -ForegroundColor Yellow
  Write-Host "  GitHub 派咗、接收端收咗 200，但個站冇轉 —— 問題喺 AWS 側" -ForegroundColor Yellow
  Write-Host "==================================================================" -ForegroundColor Yellow
  Write-Host "  delivery  : $($del.Guid)  $($del.Status) $($del.Code)  delivered $($del.Delivered.ToString('HH:mm:ssZ'))"
  Write-Host "  即係       : git pull / next build / docker compose 嗰邊出事，唔係 webhook。"
  Write-Host "  下一步     : 睇 AWS docker build log（要 AWS 存取；本機點驗都冇用）。"
  # 引文首個表，唔准引 §1–§3：嗰三節開宗明義寫住「係提案，唔係現況」，
  # 2026-08-10 就有人當咗 §2 嗰句 cards==762 係實裝閘，攔住咗一個完全正確嘅 release。
  Write-Host "               200 只代表『收到單』，唔代表 build 成功 —— 主機側『卡數／build 檢查：冇』"
  Write-Host "               （AWS_GITHUB_PULL_DEPLOY.md **文首個表**，唔係 §1–§3 嗰啲提案）。"
  Write-Host ""
}

# ------------------------------------------------------------ audit (歷史統計)
function Invoke-Audit([datetime]$since) {
  $d = Get-Deliveries 100
  if (-not $d.Ok) { Die "攞唔到 deliveries：$($d.Err)" 5 }
  $rows = @($d.Rows | Where-Object { $_.Created -and $_.Created -ge $since })
  if ($rows.Count -eq 0) { Warn "呢段時間冇 delivery"; return $null }
  # guid 解唔到嗰啲 LagSec 係 $null；唔隔走佢就會排喺最前，median/p90 直接讀錯格。
  $lags = @($rows | ForEach-Object { $_.LagSec } | Where-Object { $null -ne $_ } | Sort-Object)
  if ($lags.Count -eq 0) { Warn "呢段時間冇一件 delivery 嘅 guid 解得出時間，計唔到 lag"; return $null }
  $stats = [ordered]@{
    N = $rows.Count
    Median = [math]::Round($lags[[int][math]::Floor($lags.Count / 2)], 1)
    P90 = [math]::Round($lags[[int][math]::Floor($lags.Count * 0.9)], 1)
    Max = [math]::Round($lags[-1], 1)
    Non2xx = @($rows | Where-Object { $_.Code -lt 200 -or $_.Code -ge 300 }).Count
    Throttled = @($rows | Where-Object { $_.ThrottledAt }).Count
    Redelivery = @($rows | Where-Object { $_.Redelivery }).Count
  }
  Say "delivery 統計（自 $($since.ToString('yyyy-MM-ddTHH:mm:ssZ'))）：$($stats.N) 件，lag median $($stats.Median)s / p90 $($stats.P90)s / max $($stats.Max)s；非 2xx $($stats.Non2xx)；throttled $($stats.Throttled)；redelivery $($stats.Redelivery)"
  foreach ($r in ($rows | Where-Object { $_.LagSec -gt 10 })) {
    Warn ("遲到 delivery：{0} created {1} delivered {2} lag {3}s {4} {5}" -f (Cut $r.Guid 13), $r.Created.ToString("HH:mm:ssZ"), $r.Delivered.ToString("HH:mm:ssZ"), [math]::Round($r.LagSec, 0), $r.Status, $r.Code)
  }
  # 本機 push ↔ delivery 配對率（只計本 worktree 推嘅）
  $rl = GitRun reflog show --date=iso-strict origin/main
  $pushes = @()
  foreach ($line in ($rl.Out -split "`r?`n")) {
    if ($line -match "^([0-9a-f]+) refs/remotes/origin/main@\{([^}]+)\}: .*update by push") {
      $t = (AsUtc $matches[2])
      if ($t -ge $since) { $pushes += [pscustomobject]@{ Sha = $matches[1]; T = $t } }
    }
  }
  $unpaired = @()
  foreach ($p in $pushes) {
    $hit = $rows | Where-Object { [math]::Abs(($_.Created - $p.T).TotalSeconds) -le 15 }
    if (-not $hit) { $unpaired += $p }
  }
  if ($pushes.Count -eq 0) { Warn "本機 reflog 呢段時間冇 push（可能全部由 WSL release checkout 推）" }
  else {
    $line = "本機 push ↔ delivery 配對：$($pushes.Count - $unpaired.Count)/$($pushes.Count)"
    if ($unpaired.Count -eq 0) { Ok "$line —— 一件都冇漏" } else { Bad "$line；對唔返：$(($unpaired | ForEach-Object { $_.Sha + '@' + $_.T.ToString('HH:mm:ssZ') }) -join ', ')" }
  }
  $stats.UnpairedPushes = $unpaired.Count
  $stats.LocalPushes = $pushes.Count
  return $stats
}

# ============================================================ 0. 契約前置檢查
if ($Audit) {
  $s = Invoke-Audit ([datetime]::UtcNow.AddHours(-$AuditHours))
  if ($JsonOut) { ($s | ConvertTo-Json -Depth 4) | Out-File -FilePath $JsonOut -Encoding utf8 }
  exit 0
}

if (-not $Sha) { $r = GitRun rev-parse HEAD; if ($r.Code -ne 0) { Die "唔喺 git repo 入面" 5 }; $Sha = $r.Out }
$r = GitRun rev-parse $Sha
if ($r.Code -ne 0) { Die "認唔到 commit：$Sha" 5 }
$Sha = $r.Out
$short = Cut $Sha 8
$subject = (GitRun log -1 --format=%s $Sha).Out
# ⚠️ 接收端認嘅係 **成個 `head_commit.message`**（subject + body）入面有冇個 literal，
#    唔係淨睇 subject —— 呢個腳本本身第 375 行嘅錯誤訊息一直都咁寫，但上面條判斷
#    以前淨係讀 `%s`。2026-08-21 `2851497c` 就係咁踩到：body 入面順口寫咗個 literal，
#    AWS 側按契約會當佢係 deploy，而呢度反而 exit 5 拒絕睇 = 睇門狗有盲點。
#    而家兩樣都讀：**判斷一律用全文（保守，同接收端一致）**，subject 只做「意圖」信號。
$fullMsg = (GitRun log -1 --format=%B $Sha).Out
$tagInSubject = $subject -match "\[deploy\]"
$tagInFull = $fullMsg -match "\[deploy\]"

Say "commit  $short  $subject"

if (-not $tagInFull) {
  Die "呢粒 commit 成個 message（subject + body）都冇 [deploy] —— AWS 接收端係認 head_commit.message 入面嘅 literal [deploy]，冇就一世唔會 deploy。補一粒：git commit --allow-empty -m 'chore(deploy): … [deploy]' 再 push。" 5
}
if (-not $tagInSubject) {
  Write-Host ""
  Write-Host "==================================================================" -ForegroundColor Yellow
  Write-Host "  模糊形態：body 有 [deploy]、subject 冇 —— 你可能唔為意觸發咗出街" -ForegroundColor Yellow
  Write-Host "==================================================================" -ForegroundColor Yellow
  Write-Host "  commit  : $short  $subject"
  Write-Host "  接收端係喺成個 message 度搵 literal，所以呢粒**當佢會 deploy** 嚟睇實。"
  Write-Host "  規矩（AGENTS 17）：個 literal 要出現喺 message 度，就一定要出現喺 subject。"
  Write-Host "  想講而唔想出街，寫成 `"deploy tag`" / `"個 deploy 標記`"，唔好打個 literal。"
  Write-Host "  commit-msg hook 會擋呢個形態：pwsh -NoProfile -File scripts\install_githooks.ps1"
  Write-Host ""
}

$null = GitRun fetch -q origin main
$anc = GitRun merge-base --is-ancestor $Sha origin/main
if ($anc.Code -ne 0) { Die "$short 唔喺 origin/main 上面 —— 即係你根本未 push（或者 push 咗去第二條 branch）。" 5 }
$tip = (GitRun rev-parse origin/main).Out
if ($tip -ne $Sha) { Warn "origin/main tip 係 $(Cut $tip 8)，唔係 $short。AWS 係 pull 去 tip，所以實際上街嗰份係 tip。" }

$pushAt = Get-PushTimeUtc $Sha
if (-not $pushAt) { $pushAt = [datetime]::UtcNow; Say "reflog 搵唔到呢粒嘅 push 記錄（可能由第二棵 checkout 推），用而家時間做配對起點" }
else { Say "push 時間（本機 reflog）$($pushAt.ToString('HH:mm:ssZ'))" }

$ghCheck = GhApi "repos/$Repo/hooks/$HookId"
if (-not $ghCheck.Ok) { Die "gh api 用唔到（未登入？scope 唔夠？）：$($ghCheck.Err)" 5 }
Say "hook $HookId active=$($ghCheck.Data.active) last_response=$($ghCheck.Data.last_response.status) $($ghCheck.Data.last_response.code)"

$baseStats = Invoke-Audit ([datetime]::UtcNow.AddHours(-$AuditHours))

# ================================================== 1. AuditOnly：事後翻查一粒
function Find-Delivery([datetime]$since) {
  $d = Get-Deliveries 100
  if (-not $d.Ok) { return @{ Ok = $false; Err = $d.Err } }
  if ($Simulate -eq "NoDelivery") { return @{ Ok = $true; Hit = $null; Checked = 0 } }
  $cands = @($d.Rows | Where-Object { $_.Created -and $_.Created -ge $since.AddSeconds(-15) } | Sort-Object Created)
  foreach ($c in $cands) {
    $p = Get-DeliveryPayload $c.Id
    if (-not $p) { continue }
    if ([string]$p.ref -ne "refs/heads/main") { continue }
    $after = [string]$p.after
    $msg = ""
    $msgFull = ""
    # `$msg` 淨係攞第一行嚟**印**（唔想個 report 拉成十行）；判斷 [deploy] 一律用
    # `$msgFull` 全文 —— 接收端讀嘅就係全文，截咗第一行去判就會同接收端唔同答案。
    if ($p.head_commit) {
      $msgFull = [string]$p.head_commit.message
      $msg = ($msgFull -split "`n")[0]
    }
    $isMine = ($after -eq $Sha)
    $covers = $false
    if (-not $isMine -and $after) {
      $chk = GitRun merge-base --is-ancestor $Sha $after
      $covers = ($chk.Code -eq 0)
    }
    if ($isMine -or $covers) {
      $hit = [pscustomobject]@{
        Id = $c.Id; Guid = $c.Guid; Created = $c.Created; Delivered = $c.Delivered; LagSec = $c.LagSec
        Status = $c.Status; Code = $c.Code; After = $after; Msg = $msg
        Exact = $isMine; HasDeployTag = ($msgFull -match "\[deploy\]")
        TagOnlyInBody = (($msgFull -match "\[deploy\]") -and -not ($msg -match "\[deploy\]"))
      }
      if ($Simulate -eq "FailedDelivery") { $hit.Code = 502; $hit.Status = "failed" }
      return @{ Ok = $true; Hit = $hit; Checked = $cands.Count }
    }
  }
  return @{ Ok = $true; Hit = $null; Checked = $cands.Count }
}

if ($AuditOnly) {
  $f = Find-Delivery $pushAt
  if (-not $f.Ok) { Die "查 delivery 失敗：$($f.Err)" 5 }
  if (-not $f.Hit) { Bad "$short：查咗 $($f.Checked) 件候選 delivery，一件都對唔返（如果係啱啱 push，先等下再查）"; exit 3 }
  $h = $f.Hit
  $line = "{0}：delivery {1}  created {2}  delivered {3}  lag {4}s  {5} {6}  exact={7}  [deploy]={8}" -f `
    $short, (Cut $h.Guid 13), $h.Created.ToString("HH:mm:ssZ"), $h.Delivered.ToString("HH:mm:ssZ"), [math]::Round($h.LagSec, 1), $h.Status, $h.Code, $h.Exact, $h.HasDeployTag
  if ($h.Code -ge 200 -and $h.Code -lt 300) { Ok $line } else { Bad $line }
  if ($h.LagSec -gt 10) { Warn "呢件遲咗 $([math]::Round($h.LagSec/60,1)) 分鐘先派 —— 當時查會以為『冇派』。" }
  if ($h.Code -lt 200 -or $h.Code -ge 300) { exit 4 }
  exit 0
}

# ==================================================== 2. Watch：push 完睇實佢
$t0 = [datetime]::UtcNow
$deadline = $t0.AddMinutes($TimeoutMinutes)
$baseline = Get-LiveFingerprint
# 冇基線就冇得比較。呢度 fail-closed（唔准估）：一開波就抓唔到個站，
# 之後隨便一次抓得到都會睇落「內容轉咗」，等於白紙一張報綠燈。
if (-not $baseline.PageOk) { Die "攞唔到 live 基線（$BaseUrl$MarkerPath HTTP $($baseline.PageCode)）—— 個站而家本身就抓唔到，先去睇佢係咪已經死咗，唔好用呢個腳本估。" 5 }
Say "live 基線 chunks=$($baseline.ChunkCount) chunkSha=$(Cut $baseline.ChunkSha 12) generation=$($baseline.Generation) generatedAt=$($baseline.GeneratedAt) presentation=$($baseline.Presentation) build=$($baseline.Build)"
if ($baseline.Build -ne "local") { Warn "live build header = '$($baseline.Build)'（一路都係 'local'）—— 契約變咗，記得更新文件" }
if ($Marker.Count -gt 0) { Say "內容 marker（$MarkerScope）：$($Marker -join ' | ')" }

$delivery = $null
$kicked = $false
$alarmed = @{}
$attempt = 0

while ($true) {
  $attempt++
  $elapsed = ([datetime]::UtcNow - $t0)
  $elapsedTxt = "{0:mm\:ss}" -f $elapsed

  if (-not $delivery) {
    $f = Find-Delivery $pushAt
    if ($f.Ok -and $f.Hit) {
      $delivery = $f.Hit
      $lagTxt = [math]::Round($delivery.LagSec, 1)
      if ($delivery.Code -ge 200 -and $delivery.Code -lt 300) {
        Ok "GitHub 派咗：$(Cut $delivery.Guid 13) $($delivery.Status) $($delivery.Code)（event 產生 $($delivery.Created.ToString('HH:mm:ssZ')) → 派出 $($delivery.Delivered.ToString('HH:mm:ssZ'))，lag ${lagTxt}s）"
        if ($delivery.LagSec -gt 60) { Warn "呢件遲咗 $([math]::Round($delivery.LagSec/60,1)) 分鐘 —— 之前查唔到係正常，唔係漏派。" }
        if (-not $delivery.HasDeployTag) { Warn "但 payload head_commit 成個 message 都冇 [deploy]（『$($delivery.Msg)』）—— 接收端會唔理，等於冇 deploy。" }
        elseif ($delivery.TagOnlyInBody) { Warn "payload head_commit 個 [deploy] 淨係喺 body（subject：『$($delivery.Msg)』）—— 接收端讀全文，所以呢粒照計會出街。" }
      } else {
        Write-Host ""
        Write-Host "==================================================================" -ForegroundColor Red
        Write-Host "  接收端收唔到 —— spwebhook.funtoken.me 嗰邊死，唔關 GitHub 事" -ForegroundColor Red
        Write-Host "==================================================================" -ForegroundColor Red
        Write-Host "  delivery : $($delivery.Guid)  $($delivery.Status) $($delivery.Code)  delivered $($delivery.Delivered.ToString('HH:mm:ssZ'))"
        Write-Host "  GitHub **唔會**自動重試失敗嘅 delivery（官方文件；timed out = 10 秒冇回應）。"
        Write-Host "  接收端返生之後，要自己叫 redeliver（只可以 redeliver 過去 3 日、要 repo admin）："
        Write-Host "    gh api -X POST repos/$Repo/hooks/$HookId/deliveries/$($delivery.Id)/attempts" -ForegroundColor Cyan
        Write-Host "  或者最直接：再推一粒空 [deploy] commit（AWS pull 去 tip，效果一樣）。"
        Write-Host ""
        Bad "delivery 非 2xx：$($delivery.Status) $($delivery.Code)"
        exit 4
      }
    }
  }

  $fp = Get-LiveFingerprint
  $changed = $false
  if ($Simulate -ne "StaleLive") {
    if (-not $fp.PageOk) {
      # 抓唔到 ≠ 轉咗。deploy 途中 container 重啟／Cloudflare 一嘢 5xx 都會咁，
      # 照當「未轉」繼續等先啱；當成「轉咗」就會喺個站死緊嗰陣報綠燈（實測過會）。
      Warn "live 抓唔到（HTTP $($fp.PageCode)）—— 當『未轉』繼續等。"
    } else {
      $changed = ($fp.ChunkSha -ne $baseline.ChunkSha)
      # generation / generatedAt 淨係喺兩邊都真係讀到 /api/health 先可以比，
      # 否則「health 抓唔到 → 空字串 ≠ 基線」一樣會扮成內容轉咗。
      if ($fp.HealthOk -and $baseline.HealthOk) {
        $changed = $changed -or ($fp.GeneratedAt -ne $baseline.GeneratedAt) -or ($fp.Generation -ne $baseline.Generation)
      }
    }
  }
  $markerOk = $true
  $markerNote = ""
  if ($Marker.Count -gt 0) {
    $text = Get-MarkerText $fp
    $miss = @($Marker | Where-Object { $text -notmatch $_ })
    $markerOk = ($miss.Count -eq 0)
    $markerNote = "  marker=$($Marker.Count - $miss.Count)/$($Marker.Count)"
    if (-not $markerOk) { $markerNote += " 差：$($miss -join ',')" }
  }

  Say ("#{0,-3} {1}  delivery={2}  chunkSha={3}{4}  gen={5}  changed={6}{7}" -f `
      $attempt, $elapsedTxt, $(if ($delivery) { "$($delivery.Code)" } else { "—" }), (Cut $fp.ChunkSha 8), `
      $(if ($fp.PageOk -and $fp.ChunkSha -ne $baseline.ChunkSha) { "*" } else { " " }), $fp.Generation, $changed, $markerNote)

  $done = $false
  if ($DeliveryOnly) { $done = ($null -ne $delivery) -and $delivery.Code -ge 200 -and $delivery.Code -lt 300 }
  else { $done = $changed -and $markerOk }

  if ($done) {
    Ok "上街驗到：等咗 $elapsedTxt"
    if ($delivery) {
      $l = "delivery lag $([math]::Round($delivery.LagSec,1))s"
      # 「派出 → 上街」只有喺真係睇住佢由舊變新嗰次先有意義；replay / -DeliveryOnly 唔好報。
      if (-not $DeliveryOnly) { $l += "、派出 → 上街 ≤ $([math]::Round((([datetime]::UtcNow) - $delivery.Delivered).TotalSeconds))s（poll 間隔 ${PollSeconds}s，所以係上限）" }
      Ok $l
    }
    Ok "live: chunks=$($fp.ChunkCount) chunkSha=$(Cut $fp.ChunkSha 12) gen=$($fp.Generation) generatedAt=$($fp.GeneratedAt) presentation=$($fp.Presentation) cards=$($fp.Cards)"
    if ($JsonOut) {
      ([ordered]@{
          sha = $Sha; subject = $subject; pushedAtUtc = $pushAt.ToString("o"); okAtUtc = [datetime]::UtcNow.ToString("o")
          deliveryGuid = $(if ($delivery) { $delivery.Guid } else { "" }); deliveryLagSec = $(if ($delivery) { $delivery.LagSec } else { $null })
          baseline = @{ chunkSha = $baseline.ChunkSha; generation = $baseline.Generation; generatedAt = $baseline.GeneratedAt }
          live = @{ chunkSha = $fp.ChunkSha; generation = $fp.Generation; generatedAt = $fp.GeneratedAt; presentation = $fp.Presentation; cards = $fp.Cards }
          markers = $Marker
        } | ConvertTo-Json -Depth 5) | Out-File -FilePath $JsonOut -Encoding utf8
      Say "receipt → $JsonOut"
    }
    exit 0
  }

  # ---- 分流嗌人（每種只嗌一次，之後靜靜 poll）
  if (-not $delivery -and $elapsed.TotalSeconds -gt $DeliveryGraceSeconds -and -not $alarmed.ContainsKey("nodelivery")) {
    $alarmed["nodelivery"] = $true
    Alarm-NoDelivery $Sha $elapsedTxt $baseStats
  }
  if ($delivery -and -not $changed -and $elapsed.TotalMinutes -gt $BuildGraceMinutes -and -not $alarmed.ContainsKey("buildside")) {
    $alarmed["buildside"] = $true
    Alarm-BuildSide $Sha $delivery
  }
  # 內容明明換咗（chunkSha 郁咗）但 marker 由頭到尾一次都撞唔到 → 十有八九係支尺
  # 量錯層，唔係個 deploy 死。唔嗌嘅話會靜靜等到 timeout 再報「live 內容冇轉」，
  # report 講嘅嘢同事實啱啱相反（2026-08-20 就係噉嘥咗一轉）。
  if ($changed -and -not $markerOk -and $Marker.Count -gt 0 -and -not $alarmed.ContainsKey("markerlayer")) {
    $alarmed["markerlayer"] = $true
    Warn "chunkSha 已經由 $(Cut $baseline.ChunkSha 8) 轉咗做 $(Cut $fp.ChunkSha 8)，但 marker 仲係 0 中 —— 大機會 marker 揀錯層。"
    Write-Host "      而家 -MarkerScope $MarkerScope 抓緊：$(if ($MarkerScope -in @('html','both','all')) { 'HTML ' })$(if ($MarkerScope -in @('css','both','all')) { '.css ' })$(if ($MarkerScope -in @('js','all')) { '.js' })"
    Write-Host "      UI 文案／標籤住喺 .js chunk，要 -MarkerScope js（或 all）先抓到。"
    Write-Host "      CSS class／selector 先至係 both 抓到嗰批。"
  }

  # ---- 自動踢一腳（要明寫 -AutoKick 先會做；只踢一次）
  # ⚠️ 呢段係全份腳本唯一未實跑過嘅分支（寫同審腳本嗰陣都唔准 push），第一次用要人睇住。
  #    佢會喺你條 branch 上面造一粒空 commit 再 `push origin HEAD:main` —— 即係真出街。
  #    唔准放落 Task Scheduler 無人理。
  if ($AutoKick -and -not $delivery -and -not $kicked -and $elapsed.TotalMinutes -gt $KickAfterMinutes) {
    Warn "冇 delivery 過 $KickAfterMinutes 分鐘 → 自動踢一腳空 commit"
    $c = GitRun commit --allow-empty -m "chore(deploy): kick webhook for $short [deploy]"
    if ($c.Code -eq 0) {
      $p = GitRun push origin HEAD:main
      if ($p.Code -eq 0) { $kicked = $true; Ok "踢咗：新 commit 已上 main，AWS 會 pull 去 tip（連 $short 一齊帶上街）" }
      else { Bad "push 失敗：$($p.Out)" }
    } else { Bad "commit 失敗：$($c.Out)" }
  }

  if ([datetime]::UtcNow -ge $deadline) {
    if (-not $delivery) {
      Bad "timeout $TimeoutMinutes 分鐘：GitHub 由頭到尾冇派過 —— 唔好改 code。照上面第 2/3/4 步做。"
      exit 3
    }
    Bad "timeout $TimeoutMinutes 分鐘：delivery 有（$($delivery.Code)）但 live 內容冇轉 —— 去睇 AWS docker build log。"
    exit 2
  }
  Start-Sleep -Seconds $PollSeconds
}
