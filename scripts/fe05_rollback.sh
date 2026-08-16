#!/usr/bin/env bash
# FE05 → FE04 rollback，Git Bash / WSL 入口。
#
# **canonical 實作係 scripts/fe05_rollback.ps1**，呢個檔只係 launcher：
# 同一份邏輯寫兩次 = 一定會走樣（其中一份會靜靜咁跌咗 conflict 處理或者 skip 判斷），
# 所以呢度唔重寫，只負責搵 pwsh 同轉 flag。冇 pwsh 就印晒手動步驟俾人自己行。
#
# 用法（flag 同 ps1 一樣）：
#   ./scripts/fe05_rollback.sh --dry-run
#   ./scripts/fe05_rollback.sh
#   ./scripts/fe05_rollback.sh --method Tree --no-push --skip-verify
#   ./scripts/fe05_rollback.sh --accept-collateral
#
# Exit codes：搵到 pwsh 就原封不動傳返 ps1 嘅（0 ok / 1 abort / **2 = 已 push 但驗唔到 live**）。
# 呢個 launcher 自己嘅錯用另一段號碼，唔可以同 ps1 撞：
#   64  唔識嘅 flag
#   69  搵唔到 pwsh（EX_UNAVAILABLE）—— 唔係「生產中途狀態」，唔好當 2 咁處理
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PS1_PATH="$SCRIPT_DIR/fe05_rollback.ps1"

METHOD="Revert"
PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --method)      METHOD="$2"; shift 2 ;;
    --method=*)    METHOD="${1#*=}"; shift ;;
    -m)            METHOD="$2"; shift 2 ;;
    --dry-run)     PASS+=("-DryRun"); shift ;;
    --no-push)     PASS+=("-NoPush"); shift ;;
    --skip-verify) PASS+=("-SkipVerify"); shift ;;
    --accept-collateral) PASS+=("-AcceptCollateral"); shift ;;
    --anchor)      PASS+=("-Anchor" "$2"); shift 2 ;;
    -h|--help)     sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 64 ;;
  esac
done

PWSH="$(command -v pwsh || command -v pwsh.exe || true)"

if [ -n "$PWSH" ]; then
  # WSL 入面 Windows 路徑要轉；Git Bash 直接畀原路徑就得。
  PS1_ARG="$PS1_PATH"
  ROOT_ARG="$REPO_ROOT"
  if command -v wslpath >/dev/null 2>&1 && [ -e /proc/version ] && grep -qi microsoft /proc/version; then
    PS1_ARG="$(wslpath -w "$PS1_PATH")"
    ROOT_ARG="$(wslpath -w "$REPO_ROOT")"
  fi
  exec "$PWSH" -NoProfile -File "$PS1_ARG" -RepoRoot "$ROOT_ARG" -Method "$METHOD" ${PASS[@]+"${PASS[@]}"}
fi

cat <<EOF >&2
搵唔到 pwsh。canonical 腳本係 PowerShell 7：$PS1_PATH
喺 Windows 行：  pwsh -NoProfile -File scripts\\fe05_rollback.ps1 -Method $METHOD ${PASS[*]-}

冇 pwsh 就手動行以下步驟（等同 -Method Revert）：

  cd "$REPO_ROOT"
  git status --porcelain --untracked-files=no        # 一定要空
  git fetch origin main
  git rev-parse HEAD; git rev-parse origin/main      # 一定要一樣（唔一樣先 merge --ff-only origin/main）
  git rev-parse fe04-live^{commit}                   # 錨點必須存在

  # 由新到舊 revert 每一粒 subject 以 fe05( 開頭嘅 commit
  for sha in \$(git log fe04-live..origin/main --grep='^fe05(' --format=%H); do
    git revert --no-commit "\$sha" || { git revert --abort; git reset --hard HEAD; \\
      echo '撞 conflict → 改用 Tree 法'; exit 1; }
  done

  # Tree 法（conflict 時先用；會一併回退 anchor 之後所有掂 apps/web 嘅改動）
  #   git log fe04-live..origin/main --oneline -- apps/web    # 先睇會跌咗啲乜
  #   git diff --name-only --diff-filter=A fe04-live origin/main -- apps/web | xargs -r git rm -f
  #   git checkout fe04-live -- apps/web

  git add -u -- apps/web                             # 唔准 git add -A / git commit -a
  git commit -m 'rollback(fe): restore FE04 presentation from fe04-live [deploy]'
  git push origin HEAD:main

  # 驗 live（要 browser UA，裸 curl 會食 Cloudflare）
  curl -s -A 'Mozilla/5.0' https://app.cardzmarketcap.com/api/health | python3 -c \\
    'import json,sys; print(json.load(sys.stdin)["presentation"])'     # 等到出 FE04

  # 注意：上面淨係 Revert 法，**冇** ps1 個 post-condition assert
  # （`git diff --quiet fe04-live HEAD -- apps/web`）。手動行完一定要自己補呢句：
  #   git diff --stat fe04-live HEAD -- apps/web     # 一定要空，唔空就即係退唔乾淨，唔好 push
  # 同埋 ps1 會 abort 嘅「有 commit 冇跟 fe05( 前綴」情況，手動行係冇人幫你捉。

詳情：docs/FE05_ROLLBACK.md
EOF
# 69 = EX_UNAVAILABLE（冇 interpreter）。**唔可以用 2** —— ps1 個 2 係「已 push 上 main
# 但 12 分鐘都驗唔到 live」，兩者危險程度差好遠，撈埋 wrapper 就分唔出。
exit 69
