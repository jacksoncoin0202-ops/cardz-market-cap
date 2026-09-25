# CARDZ FE05 → FE04 rollback（owner hard requirement：FE05 出咗街之後，任何時候要退得返）
#
# 錨點：annotated tag `fe04-live`（= 現時 live 嗰個 FE04 commit）。
# 契約：push 上 GitHub `main`、commit message 含 literal `[deploy]` → webhook → AWS
#       git pull + docker compose up --build（見 AWS_GITHUB_PULL_DEPLOY.md 文首個表）。
#
# 用法：
#   pwsh -NoProfile -File scripts\fe05_rollback.ps1 -DryRun          # 只講會做咩，零改動
#   pwsh -NoProfile -File scripts\fe05_rollback.ps1                  # revert 全部 fe05( commit → push → 驗 live
#   pwsh -NoProfile -File scripts\fe05_rollback.ps1 -Method Tree     # revert 撞 conflict 先用呢個
#   pwsh -NoProfile -File scripts\fe05_rollback.ps1 -NoPush -SkipVerify   # 本機造 commit，唔推唔驗
#
# 硬規矩（AGENTS.md）：唔准 `git add -A` / `git add .` / `git commit -a`。
#   Revert 法：`git revert --no-commit` 自己已經 stage 晒（只會掂 tracked 檔），
#              下面嗰句 `git add -u apps/web` 純粹係 belt-and-braces，唔會撈到 untracked 嘢。
#   Tree 法：`git rm` + `git checkout <tag> -- apps/web` 兩句本身就 stage 咗，同樣冇 -A。
#
# 依賴一條命名規矩：**每個 FE05 commit 嘅 subject 都要以 `fe05(` 開頭**。
# 唔跟 = 呢個腳本 revert 唔到嗰粒 —— 但**唔會靜靜當做完**：下面第 2b 步會逐個列出
# 「掂過 apps/web 但唔會被今次 rollback 覆蓋」嘅 commit 並且 abort，第 4b 步再對錨點
# 做 post-condition assert。見 docs/FE05_ROLLBACK.md。
#
# Exit codes：
#   0  做完（或者 -DryRun 完 / 冇嘢要 revert 而且 apps/web 已經對得返錨點）
#   1  abort —— preflight 唔過、revert conflict、有未覆蓋嘅 apps/web commit、post-condition 唔對錨
#   2  **已經 push 上 main**，但 poll 咗 $VerifyTimeoutMinutes 分鐘都見唔到 live 轉返 FE04
#      （2 = 生產中途狀態，同 1 唔同級，唔好撈埋處理）

[CmdletBinding()]
param(
  [ValidateSet('Revert', 'Tree')][string]$Method = 'Revert',
  [switch]$DryRun,
  [switch]$NoPush,
  [switch]$SkipVerify,
  # 有「掂過 apps/web 但唔會被今次 rollback 正確處理」嘅 commit 時，預設 abort。
  # 人手睇過、確認過真係應該咁樣先加呢個 flag —— 唔准當佢係例行 flag。
  [switch]$AcceptCollateral,
  [string]$Anchor = 'fe04-live',
  [string]$RepoRoot = '',
  [string]$HealthUrl = 'https://app.cardzmarketcap.com/api/health',
  [string]$ExpectPresentation = 'FE04',
  [int]$VerifyTimeoutMinutes = 12,
  [int]$VerifyIntervalSeconds = 40
)

$ErrorActionPreference = 'Continue'
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'

$BrowserUa = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
$CommitSubject = 'rollback(fe): restore FE04 presentation from fe04-live [deploy]'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) { $RepoRoot = Split-Path -Parent $PSScriptRoot }

function Now { [datetime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ') }
function Say([string]$m) { Write-Host "[$(Now)] $m" }
function Warn([string]$m) { Write-Host "[$(Now)] WARN  $m" -ForegroundColor Yellow }
function Ok([string]$m) { Write-Host "[$(Now)] OK    $m" -ForegroundColor Green }

# 統一出口：任何 abort 都要非零 exit code，唔可以靜靜地當做完。
function Die([string]$m, [int]$code = 1) {
  Write-Host "[$(Now)] ABORT $m" -ForegroundColor Red
  exit $code
}

# 讀 git（只攞 stdout，行數陣列）。失敗唔拋，由 caller 睇 $script:GitExit。
function GitRead {
  param([Parameter(Mandatory = $true)][string[]]$GitArgs)
  $out = & git -C $RepoRoot @GitArgs 2>$null
  $script:GitExit = $LASTEXITCODE
  if ($null -eq $out) { return @() }
  return @($out)
}

# 行 git（會改嘢嗰啲）。回 exit code + 合併輸出；預設一紅就死。
function GitRun {
  param([Parameter(Mandatory = $true)][string[]]$GitArgs, [switch]$AllowFail)
  Say "git $($GitArgs -join ' ')"
  $out = & git -C $RepoRoot @GitArgs 2>&1
  $code = $LASTEXITCODE
  foreach ($line in @($out)) { Write-Host "        | $line" }
  if ($code -ne 0 -and -not $AllowFail) { Die "git $($GitArgs -join ' ') 失敗 (exit $code)" }
  return [pscustomobject]@{ Code = $code; Text = (@($out) -join "`n") }
}

Say "repo   = $RepoRoot"
Say "method = $Method   dryRun=$($DryRun.IsPresent) noPush=$($NoPush.IsPresent) skipVerify=$($SkipVerify.IsPresent)"

# ── 1. Preflight ─────────────────────────────────────────────────────────────
$null = GitRead @('rev-parse', '--is-inside-work-tree')
if ($script:GitExit -ne 0) { Die "$RepoRoot 唔係 git working tree" }

# 「乾淨」= tracked 檔零改動、index 空。untracked（temp/、未 commit 嘅新 doc）唔算髒：
# revert / checkout 都唔會掂佢哋，而呢棵樹平時就有 untracked 嘢，一刀切會令腳本永遠跑唔到。
$dirty = GitRead @('status', '--porcelain', '--untracked-files=no')
if ($dirty.Count -gt 0) {
  foreach ($line in $dirty) { Write-Host "        ! $line" }
  # -DryRun 零改動，髒樹傷害唔到佢，所以只警告（否則有第二個 session 揸緊呢棵樹就永遠 dry-run 唔到）。
  # 真跑一定要死：`git revert` 撞到 dirty index 會半途死，收唔返尾。
  if ($DryRun) {
    Warn "working tree 有 $($dirty.Count) 個未 commit 嘅 tracked 改動。DryRun 照行，但**真跑會 abort**——先 commit 或 stash。"
  }
  else {
    Die "working tree 有未 commit 嘅 tracked 改動（$($dirty.Count) 個）。先 commit 或 stash 再 rollback。"
  }
}
$untracked = GitRead @('status', '--porcelain', '--untracked-files=normal')
$untrackedOnly = @($untracked | Where-Object { $_ -like '?? *' })
if ($untrackedOnly.Count -gt 0) { Warn "有 $($untrackedOnly.Count) 個 untracked 檔／目錄，唔會入 rollback commit（正常）。" }

$fetch = GitRun @('fetch', 'origin', 'main') -AllowFail
if ($fetch.Code -ne 0) { Die "fetch origin main 失敗——冇網或者冇權限，唔敢繼續。" }

$anchorSha = (GitRead @('rev-parse', '--verify', '--quiet', "$Anchor^{commit}") | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($anchorSha)) {
  # tag 可能只喺 origin；試 fetch 返嚟先算數。
  Warn "本機冇 tag $Anchor，試 fetch origin tag $Anchor"
  $null = GitRun @('fetch', 'origin', 'tag', $Anchor) -AllowFail
  $anchorSha = (GitRead @('rev-parse', '--verify', '--quiet', "$Anchor^{commit}") | Select-Object -First 1)
}
if ([string]::IsNullOrWhiteSpace($anchorSha)) { Die "搵唔到錨點 tag `"$Anchor`"。冇佢就唔知退去邊，停。" }
Ok "anchor $Anchor = $anchorSha"

$headSha = (GitRead @('rev-parse', 'HEAD') | Select-Object -First 1)
$originSha = (GitRead @('rev-parse', 'origin/main') | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($originSha)) { Die "讀唔到 origin/main" }
Say "HEAD        = $headSha"
Say "origin/main = $originSha"

if ($headSha -ne $originSha) {
  $null = GitRead @('merge-base', '--is-ancestor', $headSha, $originSha)
  if ($script:GitExit -eq 0) {
    # HEAD 落後 origin/main → fast-forward 上去先 rollback，否則 revert 嘅範圍會漏咗新 commit。
    Warn "HEAD 落後 origin/main，先 fast-forward。"
    if ($DryRun) {
      Say "DRYRUN 會行：git merge --ff-only origin/main"
    }
    else {
      $null = GitRun @('merge', '--ff-only', 'origin/main')
      $headSha = (GitRead @('rev-parse', 'HEAD') | Select-Object -First 1)
      Ok "HEAD 已 fast-forward 去 $headSha"
    }
  }
  else {
    $null = GitRead @('merge-base', '--is-ancestor', $originSha, $headSha)
    if ($script:GitExit -eq 0) {
      Die "HEAD 行前咗 origin/main（有未推 commit，可能係上次 -NoPush 造嘅 rollback commit）。先 ``git push origin HEAD:main`` 再跑。"
    }
    Die "HEAD 同 origin/main 分叉。先處理咗佢（rebase / reset）再 rollback。"
  }
}

# 錨點必須係 origin/main 嘅祖先，否則「退返去」呢個講法本身唔成立。
$null = GitRead @('merge-base', '--is-ancestor', $anchorSha, $originSha)
if ($script:GitExit -ne 0) { Die "tag $Anchor 唔係 origin/main 嘅祖先——歷史被改寫過？停。" }

# ── 2. 搵要 revert 嘅 fe05( commit ─────────────────────────────────────────────
$US = [char]0x1f
# --grep 收窄，但真正判準係 subject 起首（喺呢邊 client-side 再篩一次，唔靠 regex 嘅 multiline 語意）。
$rawLog = GitRead @('log', "$anchorSha..$originSha", '--grep=^fe05(', "--format=%H$US%s")
$fe05 = @()
foreach ($line in $rawLog) {
  if ([string]::IsNullOrWhiteSpace($line)) { continue }
  $parts = $line -split $US, 2
  if ($parts.Count -lt 2) { continue }
  if ($parts[1].StartsWith('fe05(')) {
    $fe05 += [pscustomobject]@{ Sha = $parts[0]; Subject = $parts[1] }
  }
}

# 已經退過嘅唔好再退。兩個 marker（兩個都由下面 Build-RollbackMessage 寫入 commit body）：
#   - Revert 法：`This reverts commit <40hex>.`（git 標準講法；因為我哋用 -m 自己寫 message，
#     `git revert --no-commit` 唔會幫手加，所以要自己補返——冇補嘅話同一粒會被 revert 兩次）
#   - Tree 法：`fe05-neutralized: <40hex>`（Tree 只還原 apps/web，唔敢講「This reverts」，
#     嗰粒 commit 喺 apps/web 以外嘅改動並未回退）
$revertedBodies = (GitRead @('log', "$anchorSha..$originSha", '--format=%B')) -join "`n"
$already = @{}
foreach ($m in [regex]::Matches($revertedBodies, '(?:This reverts commit|fe05-neutralized:)\s+([0-9a-f]{40})')) {
  $already[$m.Groups[1].Value] = $true
}

$todo = @($fe05 | Where-Object { -not $already.ContainsKey($_.Sha) })
$skipped = @($fe05 | Where-Object { $already.ContainsKey($_.Sha) })

Say "----------------------------------------------------------------"
Say "$anchorSha..origin/main 之間 subject 以 'fe05(' 開頭嘅 commit：$($fe05.Count) 個（已被 revert：$($skipped.Count)，要處理：$($todo.Count)）"
foreach ($c in $fe05) {
  $mark = if ($already.ContainsKey($c.Sha)) { 'skip(已revert)' } else { 'revert' }
  Say ("  {0,-14} {1}  {2}" -f $mark, $c.Sha.Substring(0, 8), $c.Subject)
}
Say "----------------------------------------------------------------"

# ── 2b. 未覆蓋嘅 apps/web commit ───────────────────────────────────────────────
# 個腳本靠 `fe05(` 前綴搵 target。手滑冇跟前綴嗰粒，Revert 法根本掂唔到佢，
# 但之前一樣會 exit 0 + push —— 即係「印綠、其實 FE05 仲喺 production」。
# 呢度改為**枚舉所有掂過 apps/web 嘅 commit**，凡係唔會被今次處理嘅一律列出並 abort。
# 靠命名自律嘅嘢一定會犯，所以要有一格 code 擋住（owner 記憶：有檢查但零 call site = 冇檢查）。
#
# 例外：腳本自己造嘅 rollback commit 都會掂 apps/web，但佢正正就係「退返去」嗰個動作。
# 靠 commit body 嘅 `fe05-rollback-marker:` 認佢（下面第 4 步寫入）。
# 注意唔可以靠 `This reverts commit`：`git revert` 造嘅 roll-forward commit 一樣有嗰行，
# 但佢係**重新裝返 FE05**，必須要被當成未覆蓋。
$markerLog = GitRead @('log', "$anchorSha..$originSha", '--grep=fe05-rollback-marker:', '--format=%H')
$ourRollbacks = @{}
foreach ($h in $markerLog) { if (-not [string]::IsNullOrWhiteSpace($h)) { $ourRollbacks[$h.Trim()] = $true } }

$webLog = GitRead @('log', "$anchorSha..$originSha", "--format=%H$US%s", '--', 'apps/web')
$webCommits = @()
foreach ($line in $webLog) {
  if ([string]::IsNullOrWhiteSpace($line)) { continue }
  $parts = $line -split $US, 2
  if ($parts.Count -lt 2) { continue }
  $webCommits += [pscustomobject]@{ Sha = $parts[0]; Subject = $parts[1] }
}
$collateral = @($webCommits | Where-Object {
    -not $_.Subject.StartsWith('fe05(') -and -not $ourRollbacks.ContainsKey($_.Sha)
  })

if ($collateral.Count -gt 0) {
  Warn "$anchorSha..origin/main 之間有 $($collateral.Count) 個掂過 apps/web、但**唔跟 fe05( 前綴**嘅 commit："
  foreach ($p in $collateral) { Warn ("    {0}  {1}" -f $p.Sha.Substring(0, 8), $p.Subject) }
  if (-not $AcceptCollateral) {
    if ($Method -eq 'Revert') {
      Die @"
Revert 法**唔會掂**上面嗰啲 commit。如果佢哋其實係 FE05（漏咗前綴）或者係 roll-forward，
今次 rollback 會靜靜留低 FE05 喺 production。三條路：
  1. 佢哋真係 FE05／roll-forward  → 用 -Method Tree（apps/web 一鑊過攞返錨點）
  2. 佢哋真係唔關 FE05 事、應該留低 → 加 -AcceptCollateral 再跑（會跳過嚴格對錨）
  3. 唔肯定                        → 逐粒 ``git show`` 睇清楚先，唔好靠估
"@
    }
    Die @"
Tree 法會將 apps/web 一鑊過攞返錨點，即係上面 $($collateral.Count) 個**非 FE05** commit 對
apps/web 嘅改動會喺 main 上面消失。要咁做就明寫 -AcceptCollateral；
唔想跌就用 -Method Revert，或者 rollback 之後 cherry-pick 返。
"@
  }
  Warn "-AcceptCollateral：明知有上面呢啲 commit，照行。"
}
else {
  Ok "anchor 之後掂 apps/web 嘅 commit 全部跟 fe05( 前綴（或者係本腳本自己嘅 rollback）。"
}

# Post-condition：真正判準唔係「revert 咗幾多粒」，係「apps/web 而家同唔同錨點一樣」。
# 冇呢句嘅話，漏網 commit / 靜靜 no-op 嘅 revert 都會報綠。push 之前一定要行。
function Assert-WebAtAnchor([string]$stage) {
  $null = GitRead @('diff', '--quiet', $anchorSha, 'HEAD', '--', 'apps/web')
  if ($script:GitExit -eq 0) {
    # 完全對得返錨點 —— 呢個係最強講法，唔理有冇 -AcceptCollateral 都照報。
    Ok "[$stage] apps/web 同 $Anchor 零 diff（post-condition 過）"
    return
  }
  $residual = @(GitRead @('diff', '--name-only', $anchorSha, 'HEAD', '--', 'apps/web') | Where-Object { $_ })
  if ($collateral.Count -gt 0) {
    # 已經明示接受咗非 FE05 改動留低，冇得逐 byte 對；列出殘留清單俾人自己睇。
    Warn "[$stage] -AcceptCollateral 生效，唔做嚴格對錨。apps/web 仲同 $Anchor 唔同嘅檔：$($residual.Count) 個"
    foreach ($f in $residual) { Say "    ~ $f" }
    return
  }
  foreach ($f in $residual) { Write-Host "        ! $f" -ForegroundColor Red }
  Die "[$stage] apps/web 未退返 $Anchor（$($residual.Count) 個檔仲有 diff，上面列晒）。冇 push。改用 -Method Tree，或者查清楚點解 revert 冇覆蓋到。"
}

if ($Method -eq 'Revert' -and $todo.Count -eq 0) {
  Ok "冇嘢要 revert——現時 origin/main 上面冇未被 revert 嘅 fe05( commit。"
  # 「冇嘢要 revert」唔等於「已經係 FE04」。一定要對返錨點先可以講收工。
  Assert-WebAtAnchor 'no-op'
  if ($DryRun) { Say "DRYRUN 完，零改動。"; exit 0 }
  Say "唔會造空 commit。收工。"
  exit 0
}

# ── 3. 造 rollback 改動 ───────────────────────────────────────────────────────
if ($Method -eq 'Tree') {
  # Tree 法 = 直接攞返 anchor 嗰刻嘅 apps/web。粗暴但零 conflict。
  # 代價：**anchor 之後所有掂過 apps/web 嘅非 FE05 改動一齊冇咗**。
  # 嗰批 commit 喺上面第 2b 步已經枚舉 + 閘住（冇 -AcceptCollateral 就已經 abort 咗）。
  if ($collateral.Count -gt 0) {
    Warn "Tree 法會一併回退上面嗰 $($collateral.Count) 個非 FE05 commit 對 apps/web 嘅改動（已 -AcceptCollateral）。"
    Warn "唔想跌咗佢哋 → 改用 -Method Revert，或者 rollback 之後再 cherry-pick 返。"
  }

  # `git checkout <tag> -- apps/web` 只還原 tag 入面有嘅路徑；anchor 之後**新增**嘅檔要自己 rm，
  # 否則 FE05 嘅新 component/css 會留喺度（rollback 唔乾淨）。
  $added = @(GitRead @('diff', '--name-only', '--diff-filter=A', $anchorSha, $originSha, '--', 'apps/web') | Where-Object { $_ })
  Say "anchor 之後喺 apps/web 新增嘅檔：$($added.Count) 個"
  foreach ($f in $added) { Say "    + $f" }

  if ($DryRun) {
    Say "DRYRUN 會行："
    if ($added.Count -gt 0) { Say "  git rm -f --quiet -- <上面 $($added.Count) 個新檔>" }
    Say "  git checkout $Anchor -- apps/web"
  }
  else {
    if ($added.Count -gt 0) { $null = GitRun (@('rm', '-f', '--quiet', '--') + $added) }
    $null = GitRun @('checkout', $anchorSha, '--', 'apps/web')
  }
}
else {
  if ($DryRun) {
    Say "DRYRUN 會行（由新到舊）："
    foreach ($c in $todo) { Say "  git revert --no-commit $($c.Sha)   # $($c.Subject)" }
  }
  else {
    $before = (GitRead @('rev-parse', 'HEAD') | Select-Object -First 1)
    foreach ($c in $todo) {
      $r = GitRun @('revert', '--no-commit', '--no-rerere-autoupdate', $c.Sha) -AllowFail
      if ($r.Code -ne 0) {
        Warn "revert $($c.Sha.Substring(0,8)) 撞 conflict — 收手，還原工作樹。"
        $null = GitRun @('revert', '--abort') -AllowFail
        $null = GitRun @('reset', '--hard', $before) -AllowFail
        Write-Host ''
        Write-Host '  Revert 法過唔到。改行：' -ForegroundColor Yellow
        Write-Host "    pwsh -NoProfile -File scripts\fe05_rollback.ps1 -Method Tree" -ForegroundColor Yellow
        Write-Host '  （Tree 法直接攞返 fe04-live 嗰刻嘅 apps/web，同時會列出一併回退咗嘅非 FE05 commit）' -ForegroundColor Yellow
        Die "revert conflict：$($c.Sha) $($c.Subject)"
      }
    }
  }
}

if ($DryRun) {
  Say "DRYRUN 會行：git add -u apps/web"
  Say "DRYRUN 會行：git commit -m `"$CommitSubject`""
  if (-not $NoPush) { Say "DRYRUN 會行：git push origin HEAD:main" }
  if (-not $SkipVerify -and -not $NoPush) { Say "DRYRUN 會行：poll $HealthUrl 直到 presentation == $ExpectPresentation" }
  Ok "DRYRUN 完，零改動。"
  exit 0
}

# ── 4. Commit ────────────────────────────────────────────────────────────────
# revert / checkout / rm 已經 stage 晒；`add -u apps/web` 只係補漏（-u = 只掂已 tracked 檔，
# 唔會好似 `git add -A` 咁撈起 temp/ 或者其他未 commit 嘅嘢）。**唔用 `git commit -a`。**
$null = GitRun @('add', '-u', '--', 'apps/web')

$null = GitRead @('diff', '--cached', '--quiet')
if ($script:GitExit -eq 0) {
  if ($Method -eq 'Tree') {
    # apps/web 已經同 anchor 一模一樣 = 已經係 FE04，唔造空 commit。
    Ok "apps/web 已經同 $Anchor 一樣，冇嘢要 commit。"
    Assert-WebAtAnchor 'tree-noop'
    exit 0
  }
  # Revert 法有嘢要 revert 但 index 空 = 唔正常，要人睇。
  Die "revert 咗 $($todo.Count) 粒 commit 但 index 空——唔通有人改過歷史？停低查清楚。"
}

$staged = GitRead @('diff', '--cached', '--name-status')
Say "staged $($staged.Count) 個檔："
foreach ($s in $staged) { Say "    $s" }

# commit body 記低今次中和咗邊幾粒 fe05( commit —— 下一次跑呢個腳本會讀返呢啲行嚟跳過，
# 唔記低嘅話同一粒會被 revert 第二次（真係中過招，2026-08-16 clone 測試）。
# `fe05-rollback-marker:` 係「呢粒係本腳本造嘅 rollback」嘅唯一認人記號 —— 第 2b 步靠佢
# 唔好將自己當成「未覆蓋嘅 apps/web commit」。唔可以用 `This reverts commit` 代替：
# `git revert` 造嘅 roll-forward 一樣有嗰行，但佢係裝返 FE05，必須要被捉。
$bodyLines = @("fe05-rollback-marker: $anchorSha", "anchor: $Anchor ($($anchorSha.Substring(0,8)))", "method: $Method", '')
if ($Method -eq 'Revert') {
  foreach ($c in $todo) { $bodyLines += "This reverts commit $($c.Sha)." }
}
else {
  $bodyLines += 'apps/web restored wholesale from the anchor; changes outside apps/web are NOT reverted.'
  foreach ($c in $fe05) { $bodyLines += "fe05-neutralized: $($c.Sha)" }
}
$null = GitRun @('commit', '-m', $CommitSubject, '-m', ($bodyLines -join "`n"))
$rollbackSha = (GitRead @('rev-parse', 'HEAD') | Select-Object -First 1)
Ok "rollback commit = $rollbackSha"

# ── 4b. Post-condition（push 之前）─────────────────────────────────────────────
# 造咗 commit ≠ 退到。呢句先係真正判準；唔對錨就唔准 push（commit 仲喺本機，收得返）。
Assert-WebAtAnchor 'post-commit'

# ── 5. Push ──────────────────────────────────────────────────────────────────
if ($NoPush) {
  Warn "-NoPush：冇推上去，live 仲係 FE05。要出街行：git push origin HEAD:main"
}
else {
  $null = GitRun @('push', 'origin', 'HEAD:main')
  Ok "已 push origin HEAD:main（commit message 含 [deploy]，webhook 會觸發 AWS pull + rebuild）"
}

# ── 6. 驗 live ───────────────────────────────────────────────────────────────
$verified = $false
if ($SkipVerify -or $NoPush) {
  Warn "跳過 live 驗證（-SkipVerify 或 -NoPush）。未驗過就唔准講「已退返 FE04」。"
}
else {
  $start = [datetime]::UtcNow
  $deadline = $start.AddMinutes($VerifyTimeoutMinutes)
  Say "開始 poll $HealthUrl（每 ${VerifyIntervalSeconds}s，最多 ${VerifyTimeoutMinutes} 分鐘），等 presentation == $ExpectPresentation"
  $attempt = 0
  while ([datetime]::UtcNow -lt $deadline) {
    $attempt++
    $pres = $null; $build = $null; $err = $null
    try {
      # 公開站要 browser UA，urllib/裸 curl 會食 Cloudflare 403。
      $resp = Invoke-WebRequest -Uri $HealthUrl -UserAgent $BrowserUa -TimeoutSec 30 -MaximumRedirection 3
      $json = $resp.Content | ConvertFrom-Json
      # 現行 /api/health 個 shape：presentation 喺 top level，generation 係字串 build id。
      # 舊／未來 shape 可能係 generation.presentation，兩邊都接。
      if ($json.PSObject.Properties.Name -contains 'presentation') { $pres = [string]$json.presentation }
      if ([string]::IsNullOrWhiteSpace($pres) -and $json.generation -and $json.generation.PSObject.Properties.Name -contains 'presentation') {
        $pres = [string]$json.generation.presentation
      }
      $build = [string]$json.build
    }
    catch { $err = $_.Exception.Message }

    if ($err) { Say ("  #{0,-3} {1}  http 失敗：{2}" -f $attempt, (Now), $err) }
    else { Say ("  #{0,-3} {1}  presentation={2} build={3}" -f $attempt, (Now), $pres, $build) }

    if ($pres -eq $ExpectPresentation) {
      $elapsed = [int]([datetime]::UtcNow - $start).TotalSeconds
      Ok "live 已回到 $ExpectPresentation（起 $(($start).ToString('yyyy-MM-ddTHH:mm:ssZ')) → confirm $(Now)，用咗 ${elapsed}s，第 $attempt 次）"
      $verified = $true
      break
    }
    if ([datetime]::UtcNow.AddSeconds($VerifyIntervalSeconds) -ge $deadline) { break }
    Start-Sleep -Seconds $VerifyIntervalSeconds
  }
}

# ── 7. 收尾：點 roll forward 返 FE05 ──────────────────────────────────────────
# roll-forward 個 subject **一定要** 以 `fe05(` 開頭：咁樣下一次 rollback 會見到佢、
# revert 佢就即係重新收起 FE05（已測：rollback → rollforward → rollback 三輪，
# apps/web 每次都返到同 fe04-live 一模一樣）。改個名唔跟前綴 = 退唔返。
Write-Host ''
Write-Host '  ── 想 roll forward 返 FE05（撤銷今次 rollback）──' -ForegroundColor Cyan
Write-Host "    git revert --no-commit $rollbackSha" -ForegroundColor Cyan
Write-Host "    git commit -m 'fe05(rollforward): re-apply FE05 presentation [deploy]'" -ForegroundColor Cyan
Write-Host '    git push origin HEAD:main' -ForegroundColor Cyan
Write-Host '  （Tree 法造出嚟嘅 rollback 一樣 revert 得返；non-FE05 附帶回退亦會一併還原）' -ForegroundColor Cyan
Write-Host ''

if ($SkipVerify -or $NoPush) { exit 0 }
if (-not $verified) {
  Die "poll 咗 $VerifyTimeoutMinutes 分鐘都見唔到 presentation == $ExpectPresentation。commit $rollbackSha 已經喺 main 上面——去睇 webhook / AWS docker build log。" 2
}
exit 0
