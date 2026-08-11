#!/usr/bin/env bash
set -euo pipefail
export GIT_TERMINAL_PROMPT=0
export GCM_INTERACTIVE=Never

SOURCE_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_REPO="/home/jackson0202/cardz-market-cap-release-daily"
LOCK_FILE="/tmp/cardz-market-cap-daily-release.lock"

exec 9>"$LOCK_FILE"
flock -n 9

test -e "$RELEASE_REPO/.git"
test -z "$(git -C "$RELEASE_REPO" status --porcelain)"
git -C "$RELEASE_REPO" fetch origin main
# 比 FETCH_HEAD，唔好比 refs/remotes/origin/main：release repo 個
# remote.origin.fetch 曾經係空（冇 refspec），origin/main 永遠停喺 clone 嗰刻，
# 呢個 guard 就變咗恆真，鏈照跑落一個落後幾個 commit 嘅 tree 上面。
# `git fetch origin main` 無論有冇 refspec 都一定寫 FETCH_HEAD。
test "$(git -C "$RELEASE_REPO" rev-parse HEAD)" = "$(git -C "$RELEASE_REPO" rev-parse FETCH_HEAD)"
test -f "$RELEASE_REPO/node_modules/typescript/bin/tsc"

CARDZ_REPO_ROOT="$SOURCE_REPO" node "$RELEASE_REPO/scripts/bake-public-snapshot.mjs" \
  --output "$RELEASE_REPO/data/public/seed-snapshot.json"

python3 -X utf8 "$RELEASE_REPO/scripts/sync_public_release_assets.py" \
  --snapshot "$RELEASE_REPO/data/public/seed-snapshot.json" \
  --source "$SOURCE_REPO/data/public/market-assets" \
  --destination "$RELEASE_REPO/data/public/market-assets"

python3 -X utf8 "$RELEASE_REPO/scripts/validate_daily_release.py" \
  --snapshot "$RELEASE_REPO/data/public/seed-snapshot.json" \
  --assets "$RELEASE_REPO/data/public/market-assets"

mapfile -t changed < <(git -C "$RELEASE_REPO" status --porcelain=v1 | sed 's/^...//')
if ((${#changed[@]} == 0)); then
  printf '%s\n' '{"dailyRelease":"no-change"}'
  exit 0
fi

for path in "${changed[@]}"; do
  case "$path" in
    data/public/seed-snapshot.json|data/public/market-assets/*.webp) ;;
    *) printf 'daily release refused unexpected path: %s\n' "$path" >&2; exit 1 ;;
  esac
done
for path in "${changed[@]}"; do
  git -C "$RELEASE_REPO" add -- "$path"
done

generation="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"]["id"])' "$RELEASE_REPO/data/public/seed-snapshot.json")"
generated_at="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"]["generatedAt"])' "$RELEASE_REPO/data/public/seed-snapshot.json")"
git -C "$RELEASE_REPO" commit -m "release: daily CARDZ 036 FE03 $generation [deploy]"
git -C "$RELEASE_REPO" push origin HEAD:main

for _ in $(seq 1 60); do
  if body="$(curl --fail --silent --show-error https://app.cardzmarketcap.com/api/health)"; then
    # 判「新 bundle 上到未」睇 generatedAt（每次 bake 都變，由 snapshot 自己帶），
    # 唔再睇 build。build 要部署方 set CARDZ_PUBLIC_BUILD_ID，而實際 deploy 路徑
    # 由頭到尾冇 set 過，永遠係 "local"，所以舊 gate 恆假：每次都白等足 10 分鐘
    # 然後報 fail，明明個站已經更新咗。
    if PUBLIC_HEALTH="$body" python3 -c 'import json,os,sys; h=json.loads(os.environ["PUBLIC_HEALTH"]); sys.exit(0 if h.get("status")=="ok" and h.get("generation")==sys.argv[1] and h.get("generatedAt")==sys.argv[2] else 1)' "$generation" "$generated_at"; then
      printf '%s\n' "$body"
      exit 0
    fi
  fi
  sleep 10
done

printf 'public release did not reach generation %s (generatedAt %s) within 10 minutes\n' "$generation" "$generated_at" >&2
exit 1
