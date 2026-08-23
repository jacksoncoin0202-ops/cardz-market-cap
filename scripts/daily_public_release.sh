#!/usr/bin/env bash
set -euo pipefail
export GIT_TERMINAL_PROMPT=0
export GCM_INTERACTIVE=Never

# --scheduled：由 daily_public_release.ps1 -Scheduled 帶落嚟。argv token 先係
# 自動成功憑證；env CARDZ_DAILY_CHAIN 只係陪跑（stamp 對數）。
STAMP_ARGS=()
V2_MODE=0
V2_RUN_ID=""
V2_BUSINESS_DATE=""
V2_EXPECTED_GENERATION=""
for arg in "$@"; do
  case "$arg" in
    --scheduled) STAMP_ARGS+=(--scheduled) ;;
    --v2-run-id=*) V2_MODE=1; V2_RUN_ID="${arg#*=}" ;;
    --v2-business-date=*) V2_MODE=1; V2_BUSINESS_DATE="${arg#*=}" ;;
    --v2-expected-generation=*) V2_MODE=1; V2_EXPECTED_GENERATION="${arg#*=}" ;;
    *) printf 'daily_public_release.sh: unknown arg %s\n' "$arg" >&2; exit 2 ;;
  esac
done
if ((V2_MODE == 1)); then
  if [[ -z "$V2_RUN_ID" || -z "$V2_EXPECTED_GENERATION" || ! "$V2_BUSINESS_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ || ${#STAMP_ARGS[@]} -ne 0 ]]; then
    printf 'daily_public_release.sh: V2 requires run id + business date + expected generation and forbids --scheduled\n' >&2
    exit 2
  fi
  export CARDZ_DAILY_CHAIN_V2=1
  export CARDZ_V2_RUN_ID="$V2_RUN_ID"
  export CARDZ_V2_BUSINESS_DATE="$V2_BUSINESS_DATE"
fi
stamp_autonomy() {
  if ((V2_MODE == 1)); then
    return 0
  fi
  local rc=0
  python3 -X utf8 "$SOURCE_REPO/scripts/stamp_daily_chain_autonomy.py" \
    --generation "$1" --generated-at "$2" --outcome "$3" "${STAMP_ARGS[@]}" || rc=$?
  if ((rc != 0)); then
    printf 'daily release: autonomy stamp FAILED rc=%s outcome=%s generation=%s (publish itself succeeded)\n' "$rc" "$3" "$1" >&2
  fi
  return "$rc"
}

SOURCE_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_REPO="/home/jackson0202/cardz-market-cap-release-daily"
LOCK_FILE="/tmp/cardz-market-cap-daily-release.lock"

exec 9>"$LOCK_FILE"
# audit P2-15: under `set -euo pipefail` a refused flock exited 1 with no
# output, so V2 read "release exit=1: " and spent the 2/5/10/20/30 minute
# publish ladder waiting for a hand publish to release the lock.  Exit 75
# (EX_TEMPFAIL) plus a spoken reason lets the classifier call it contention.
flock -n 9 || { printf 'daily release: another publisher holds %s\n' "$LOCK_FILE" >&2; exit 75; }

V2_MANIFEST=""
if ((V2_MODE == 1)); then
  v2_key="$(printf '%s' "$V2_RUN_ID" | sha256sum | awk '{print $1}')"
  V2_MANIFEST="$SOURCE_REPO/data/runtime/daily-chain-v2/publication/${v2_key}.json"
fi

v2_write_manifest() {
  local commit="$1" generation_value="$2" generated_at_value="$3"
  local snapshot_sha public_tree_sha
  snapshot_sha="$(git -C "$RELEASE_REPO" show "${commit}:data/public/seed-snapshot.json" | sha256sum | awk '{print $1}')"
  public_tree_sha="$(git -C "$RELEASE_REPO" ls-tree -r "$commit" -- data/public | sha256sum | awk '{print $1}')"
  mkdir -p "$(dirname "$V2_MANIFEST")"
  python3 - "$V2_MANIFEST" "$V2_RUN_ID" "$V2_BUSINESS_DATE" "$commit" "$generation_value" "$generated_at_value" "$snapshot_sha" "$public_tree_sha" <<'PY'
import json,os,sys
path,run_id,business_date,commit,generation,generated_at,snapshot_sha,public_tree_sha=sys.argv[1:]
doc={"contract":"cardz-v2-immutable-publication-v1","runId":run_id,
     "businessDate":business_date,"commit":commit,"generation":generation,
     "generatedAt":generated_at,"snapshotSha256":snapshot_sha,
     "publicTreeSha256":public_tree_sha}
tmp=f"{path}.{os.getpid()}.next"
with open(tmp,"w",encoding="utf-8",newline="\n") as fh:
    json.dump(doc,fh,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    fh.write("\n")
os.replace(tmp,path)
PY
}

# The commit is durable before the sidecar can be written.  If the process was
# killed in that tiny window, recover only an exact expected-generation release
# commit whose diff is confined to the public artifact allowlist.
if ((V2_MODE == 1)) && [[ ! -s "$V2_MANIFEST" ]]; then
  head_generation="$(git -C "$RELEASE_REPO" show HEAD:data/public/seed-snapshot.json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("generation",{}).get("id",""))' 2>/dev/null || true)"
  head_generated_at="$(git -C "$RELEASE_REPO" show HEAD:data/public/seed-snapshot.json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("generation",{}).get("generatedAt",""))' 2>/dev/null || true)"
  head_subject="$(git -C "$RELEASE_REPO" log -1 --format=%s 2>/dev/null || true)"
  recovery_paths_ok=1
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    case "$path" in
      data/public/seed-snapshot.json|data/public/box-subset.json|data/public/market-assets/*.webp) ;;
      *) recovery_paths_ok=0 ;;
    esac
  done < <(git -C "$RELEASE_REPO" diff-tree --no-commit-id --name-only -r HEAD)
  if [[ "$head_generation" == "$V2_EXPECTED_GENERATION" \
        && "$head_subject" == "release: daily CARDZ 037 FE04 $V2_EXPECTED_GENERATION [deploy]" \
        && -n "$head_generated_at" && "$recovery_paths_ok" -eq 1 ]]; then
    v2_write_manifest "$(git -C "$RELEASE_REPO" rev-parse HEAD)" "$head_generation" "$head_generated_at"
  fi
fi

NOTIFY_PY="$SOURCE_REPO/scripts/notify_hermes.py"
RELEASE_STAGE="preflight"
notify_release() {
  if ((V2_MODE == 0)) && [[ -f "$NOTIFY_PY" ]]; then
    python3 -X utf8 "$NOTIFY_PY" release "$@" || true
  fi
}
on_release_exit() {
  local rc=$?
  if (( rc != 0 )); then
    notify_release --outcome failed --stage "$RELEASE_STAGE" --exit-code "$rc" --generation "${generation:-}"
  fi
}
trap on_release_exit EXIT

test -e "$RELEASE_REPO/.git"
if ((V2_MODE == 1)) && [[ -s "$V2_MANIFEST" ]]; then
  RELEASE_STAGE="v2-resume-immutable-publication"
  mapfile -t v2_saved < <(python3 - "$V2_MANIFEST" "$V2_RUN_ID" "$V2_BUSINESS_DATE" "$V2_EXPECTED_GENERATION" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
if d.get("contract")!="cardz-v2-immutable-publication-v1": raise SystemExit("bad contract")
if d.get("runId")!=sys.argv[2] or d.get("businessDate")!=sys.argv[3] or d.get("generation")!=sys.argv[4]:
    raise SystemExit("manifest identity mismatch")
for key in ("commit","generation","generatedAt","snapshotSha256","publicTreeSha256"):
    print(d[key])
PY
  )
  v2_commit="${v2_saved[0]:-}"
  generation="${v2_saved[1]:-}"
  generated_at="${v2_saved[2]:-}"
  saved_snapshot_sha="${v2_saved[3]:-}"
  saved_public_tree_sha="${v2_saved[4]:-}"
  actual_snapshot_sha="$({ git -C "$RELEASE_REPO" show "${v2_commit}:data/public/seed-snapshot.json" 2>/dev/null || true; } | sha256sum | awk '{print $1}')"
  actual_public_tree_sha="$({ git -C "$RELEASE_REPO" ls-tree -r "$v2_commit" -- data/public 2>/dev/null || true; } | sha256sum | awk '{print $1}')"
  if [[ -z "$v2_commit" || -z "$generation" || -z "$generated_at" ]] \
     || [[ "$generation" != "$V2_EXPECTED_GENERATION" ]] \
     || [[ "$saved_snapshot_sha" != "$actual_snapshot_sha" || "$saved_public_tree_sha" != "$actual_public_tree_sha" ]] \
     || ! git -C "$RELEASE_REPO" cat-file -e "${v2_commit}^{commit}"; then
    printf 'V2 immutable publication schema contract is invalid or commit is missing: %s\n' "$V2_MANIFEST" >&2
    exit 1
  fi
  git -C "$RELEASE_REPO" push origin "${v2_commit}:main"
  for _ in $(seq 1 60); do
    if body="$(curl --fail --silent --show-error https://app.cardzmarketcap.com/api/health)"; then
      if PUBLIC_HEALTH="$body" python3 -c 'import json,os,sys; h=json.loads(os.environ["PUBLIC_HEALTH"]); sys.exit(0 if h.get("status")=="ok" and h.get("generation")==sys.argv[1] and h.get("generatedAt")==sys.argv[2] else 1)' "$generation" "$generated_at"; then
        printf '%s\n' "$body"
        exit 0
      fi
    fi
    sleep 10
  done
  printf 'V2 immutable generation %s@%s did not reach live\n' "$generation" "$generated_at" >&2
  exit 1
fi
# 條鏈自己每次都會重新生成 data/public/seed-snapshot.json 同
# data/public/market-assets/*.webp，所以嗰條路徑下面嘅殘留冇保留價值。
# 舊版係 `test -z "$(git status --porcelain)"`：只要有一次 run 死喺後面
# （2026-08-11 個 asset gate 就係咁），prune 掉嘅 159 個 sha 會留低 160 行未
# commit 嘅刪除，之後每一日 09:30 都會死喺呢一行，要人手入去 checkout 先郁得返。
# 自己 reset 返自己嘅輸出；data/public 以外有任何未 commit 嘅嘢就照樣硬死，
# 唔會靜靜蓋走人手改動。
if [[ -n "$(git -C "$RELEASE_REPO" status --porcelain -- ':!data/public')" ]]; then
  printf 'daily release refused: uncommitted changes outside data/public\n' >&2
  git -C "$RELEASE_REPO" status --porcelain -- ':!data/public' >&2
  exit 1
fi
git -C "$RELEASE_REPO" reset -q HEAD -- data/public
git -C "$RELEASE_REPO" checkout -- data/public
git -C "$RELEASE_REPO" fetch origin main
# 對 FETCH_HEAD 快進，唔好淨係 assert 相等。原意係「一定要由 main 嗰個 tree
# bake」，但 assert 版本嘅副作用係：source repo 每次推一個 code commit 上 main，
# release repo 就即刻落後，之後每一次自動發佈都會死喺呢一行，要人手入去 pull。
# --ff-only 保住原意（唔會接受分叉），同時令條鏈自己追返 main。
# 比 FETCH_HEAD 唔好比 refs/remotes/origin/main：release repo 個
# remote.origin.fetch 曾經係空（冇 refspec），origin/main 永遠停喺 clone 嗰刻。
BEFORE="$(git -C "$RELEASE_REPO" rev-parse HEAD)"
git -C "$RELEASE_REPO" merge --ff-only FETCH_HEAD
# node_modules 要同 lockfile 同步，唔靠人手記得入去 npm ci。2026-08-17：main 加咗 eslint dev dep
# + test-eslint-ratchet.mjs，release checkout 個 node_modules 停喺 08-11 → guards 死三次
# （「eslint 未裝」）。lockfile 呢次 ff 有變、或者 lockfile 比上次安裝新，就 npm ci 一次。
if ! git -C "$RELEASE_REPO" diff --quiet "$BEFORE" HEAD -- package-lock.json \
   || [[ "$RELEASE_REPO/package-lock.json" -nt "$RELEASE_REPO/node_modules/.package-lock.json" ]]; then
  printf 'daily release: package-lock.json newer than node_modules, running npm ci\n' >&2
  (cd "$RELEASE_REPO" && npm ci --no-audit --no-fund)
fi
test -f "$RELEASE_REPO/node_modules/typescript/bin/tsc"

# The release checkout is the exact tree that will bake. Run no-DB guards
# from that tree after the fast-forward and before the first generated byte.
# Snapshot-only ff 跳過 FE／pipelines／script tests；validate self-test 仍然跑。
TEST_PY="/home/jackson0202/cardz-market-cap/.venv-backend/bin/python"
test -x "$TEST_PY"
# Diff against the last tree that PASSED the guards, not this run's ff.
# A dead slot after ff used to leave CHANGED empty → every later slot skipped tests.
# RELEASE_REPO 係 git worktree：.git 係 file，唔係目錄。
TESTED_MARK="$(git -C "$RELEASE_REPO" rev-parse --absolute-git-dir)/cardz-last-tested-head"
BASE="$(cat "$TESTED_MARK" 2>/dev/null || true)"
git -C "$RELEASE_REPO" cat-file -e "${BASE:-nonexistent}^{commit}" 2>/dev/null || BASE="$BEFORE"
CHANGED="$(git -C "$RELEASE_REPO" diff --name-only "$BASE" HEAD || true)"
TEST_ARGS=(--no-db)
if ! echo "$CHANGED" | grep -Eq '^(apps/web/|packages/|scripts/test-)'; then
  TEST_ARGS+=(--skip-fe)
fi
if ! echo "$CHANGED" | grep -Eq '^(pipelines/|scripts/)'; then
  TEST_ARGS+=(--skip-pipelines)
fi
if ! echo "$CHANGED" | grep -Eq '^(pipelines/|scripts/|apps/web/|packages/)'; then
  TEST_ARGS+=(--skip-script-tests)
fi
if "$TEST_PY" -X utf8 "$RELEASE_REPO/scripts/run_all_tests.py" --help 2>/dev/null | grep -q -- '--skip-fe'; then
  "$TEST_PY" -X utf8 "$RELEASE_REPO/scripts/run_all_tests.py" "${TEST_ARGS[@]}"
else
  "$TEST_PY" -X utf8 "$RELEASE_REPO/scripts/run_all_tests.py" --no-db
fi
git -C "$RELEASE_REPO" rev-parse HEAD > "$TESTED_MARK"

# PC 成交 title↔卡號矛盾隔離 receipt（runbook 形狀 29）：bake 之前一定要由判別器
# 重新生成，唔准食舊檔。讀者（live-db-snapshot.ts loadSaleQuarantine）fail-closed：
# 冇 receipt 就 bake 死；呢一步負責「有而且唔過期」。用 SOURCE repo 嘅腳本係因為
# receipt 寫入 SOURCE 嘅 data/runtime（bake 個 CARDZ_REPO_ROOT 都係指 SOURCE）。
"$TEST_PY" -X utf8 "$SOURCE_REPO/pipelines/pc_sale_title_quarantine.py"

# BOX sidecar: SOURCE (fe-db) data/public/box-subset.json is the producer output.
# No SOURCE file → keep whatever release git already carries (never publish empty /box).
BOX_SRC="$SOURCE_REPO/data/public/box-subset.json"
BOX_DST="$RELEASE_REPO/data/public/box-subset.json"
BOX_PREV="$(mktemp /tmp/cardz-box-prev.XXXXXX.json)"
git -C "$RELEASE_REPO" show HEAD:data/public/box-subset.json > "$BOX_PREV" 2>/dev/null || : > "$BOX_PREV"
# R4 2026-08-24: staging is a function because the asset retry loop below runs
# `git checkout -- data/public`, which reverts this sidecar to the release
# repo's HEAD -- byte-identical to $BOX_PREV.  A retried bake would then
# republish YESTERDAY's /box in silence: validate_daily_release.py compares
# --box against --box-previous and sees no regression because it is comparing
# the file with itself.  Every pass must see exactly what pass 1 saw.
stage_box_sidecar() {
  if [[ -s "$BOX_SRC" ]]; then
    python3 -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$BOX_SRC"
    cp "$BOX_SRC" "$BOX_DST"
  else
    printf 'BOX sidecar missing in SOURCE; publishing previous sidecar\n' >&2
  fi
}
stage_box_sidecar

# Bake 喺 release checkout 跑。佢內建 prune 只識 PSA10 seed，會當 BOX sidecar
# 897 張圖係 stale 搬走。037 sync 跟住又要 SOURCE（fe-db）有呢 897 張——
# BOX 圖只活喺 release git，從來冇入 PSA10 source。2026-08-15 朝鏈／11:30
# 就係咁：prune movedFiles=897，sync「source tree is missing 897」，retry
# 位（11:30／16:30）撞同一個洞，當日 [deploy] 出唔到。
# --no-prune 交俾下面 SOURCE sync：PSA10 由 source 抄，BOX 留 dest，多餘先刪。
publish_assets() {
  CARDZ_REPO_ROOT="$SOURCE_REPO" node "$RELEASE_REPO/scripts/bake-public-snapshot.mjs" \
    --output "$RELEASE_REPO/data/public/seed-snapshot.json" \
    --no-prune
  python3 -X utf8 "$SOURCE_REPO/scripts/sync_public_release_assets.py" \
    --snapshot "$RELEASE_REPO/data/public/seed-snapshot.json" \
    --source "$SOURCE_REPO/data/public/market-assets" \
    --destination "$RELEASE_REPO/data/public/market-assets"
  python3 -X utf8 "$RELEASE_REPO/scripts/validate_daily_release.py" \
    --snapshot "$RELEASE_REPO/data/public/seed-snapshot.json" \
    --assets "$RELEASE_REPO/data/public/market-assets" \
    --box "$BOX_DST" \
    --box-previous "$BOX_PREV"
}

asset_attempt=1
asset_max_attempts=3
# R4 2026-08-24: V2 used to force ONE bake/sync/validate attempt here, so a
# single flaky bake spent a whole orchestrator attempt and a step of the
# 2/5/10/20/30 minute publish ladder.  The retry below restores data/public
# first, so replaying the leg is idempotent.  3 == PUBLISH_ASSET_MAX_ATTEMPTS
# in pipelines/daily_chain_v2_contract.py; scripts/test_v2s_classify.py
# parses this line and refuses to let the two sides drift.
if ((V2_MODE == 1)); then asset_max_attempts=3; fi
# One bake/sync/validate pass, measured.  == PUBLISH_ASSET_PASS_SECONDS in
# pipelines/daily_chain_v2_contract.py; scripts/test_v2s_classify.py parses
# this line so the two sides cannot drift.
asset_pass_seconds=180
# R4 2026-08-24: the orchestrator's publish ladder is capped at
# `final - one SUCCEEDING leg`, not at the leg where all three bake attempts
# fail -- charging every retry the worst case refused retries that would have
# published.  The retry budget above is therefore bounded here instead: the
# parent exports CARDZ_V2_STAGE_DEADLINE_EPOCH (daily_chain_v2.py, derived from
# a work deadline that is never later than the 17:00 JST `final`) and SIGTERMs
# this process on it, so a pass started with less than a pass left is a bake
# that gets killed mid-flight.  Refuse it and report instead.  No deadline in
# the environment (manual / legacy run) keeps the pre-R4 behaviour.
asset_retry_fits() {
  local deadline=${CARDZ_V2_STAGE_DEADLINE_EPOCH:-}
  local secs=${deadline%%.*}
  if [[ ! $secs =~ ^-?[0-9]+$ ]]; then
    return 0
  fi
  (( secs - $(date +%s) >= asset_pass_seconds ))
}
while true; do
  if publish_assets; then
    break
  fi
  if ((asset_attempt >= asset_max_attempts)); then
    printf 'daily release bake/sync/validate failed after %s attempts\n' "$asset_attempt" >&2
    exit 1
  fi
  if ! asset_retry_fits; then
    printf 'daily release bake/sync/validate failed after %s attempts: less than %ss before the stage deadline, no retry\n' \
      "$asset_attempt" "$asset_pass_seconds" >&2
    exit 1
  fi
  asset_attempt=$((asset_attempt + 1))
  printf 'daily release bake/sync/validate retry %s/%s\n' "$asset_attempt" "$asset_max_attempts" >&2
  git -C "$RELEASE_REPO" checkout -- data/public
  stage_box_sidecar
  sleep 15
done

mapfile -t changed < <(git -C "$RELEASE_REPO" status --porcelain=v1 | sed 's/^...//')

# snapshot 有三個 run-stamp —— 唔係數據，係「我幾時跑咗」：
#   generation.generatedAt      bake 嘅 wall clock（live-db-snapshot.ts:564）
#   generation.effectiveAt      max(metric_accepted_at, price_observed_date,
#                               population_effective_at)（:552-553），而
#                               metric_accepted_at 係 daily-accept 每次 run 嘅 now()
#   currencies.rates.USD.asOf   USD 恆等於 1，佢個 asOf 直接借 effectiveAt（:556）
# 三個喺零數據改動嘅情況下一樣會郁，所以「淨係 pop generatedAt」嘅舊版 fire 唔到：
# 任何行過 daily-accept 嘅 slot 都會推一個內容一模一樣嘅 commit 上 main 兼觸發一次
# 部署（實測 2026-08-11 13:29 commit 61f1ad3b，3 行 diff 全部係呢三個 stamp）。
# 真數據郁嗰陣卡本身嗰啲欄一定跟住郁，個 diff 唔會空，所以 pop 呢三個唔會食咗真更新。
# 將來多一個 run-stamp，個閘只會停止 fire（照推一個多餘 commit），唔會漏推。
if ((V2_MODE == 0)) \
   && [[ ${#changed[@]} -eq 1 && ${changed[0]} == data/public/seed-snapshot.json ]] \
   && git -C "$RELEASE_REPO" show HEAD:data/public/seed-snapshot.json \
      | python3 -c 'import json,sys
RUN_STAMPS=(("generation","generatedAt"),("generation","effectiveAt"),("currencies","rates","USD","asOf"))
def strip(doc):
    for path in RUN_STAMPS:
        node=doc
        for key in path[:-1]:
            node=node.get(key) if isinstance(node,dict) else None
        if isinstance(node,dict): node.pop(path[-1],None)
    return doc
sys.exit(0 if strip(json.load(sys.stdin))==strip(json.load(open(sys.argv[1]))) else 1)' "$RELEASE_REPO/data/public/seed-snapshot.json"; then
  git -C "$RELEASE_REPO" checkout -- data/public/seed-snapshot.json
  changed=()
fi

if ((${#changed[@]} == 0)); then
  if ((V2_MODE == 1)); then
    printf 'V2 release refused: daily acceptance produced no immutable generation change\n' >&2
    exit 1
  fi
  # 冇嘢要推唔等於出咗街。條鏈試過推咗 commit 但公開站冇跟上（見 docs 嘅 deploy
  # postmortem），嗰陣每一日都會report「no-change」然後大家以為正常。
  # 所以冇變都要對一次 live，唔啱就大聲死，等下一個 retry slot 再試。
  generation="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"]["id"])' "$RELEASE_REPO/data/public/seed-snapshot.json")"
  live="$(curl --fail --silent --show-error --max-time 20 https://app.cardzmarketcap.com/api/health || true)"
  live_generation="$(PUBLIC_HEALTH="$live" python3 -c 'import json,os; print(json.loads(os.environ["PUBLIC_HEALTH"]).get("generation",""))' 2>/dev/null || true)"
  if [[ "$live_generation" == "$generation" ]]; then
    generated_at="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"]["generatedAt"])' "$RELEASE_REPO/data/public/seed-snapshot.json")"
    stamp_rc=0
    stamp_autonomy "$generation" "$generated_at" no-change || stamp_rc=$?
    notify_release --outcome no-change --generation "$generation"
    printf '{"dailyRelease":"no-change","generation":"%s"}\n' "$generation"
    if ((stamp_rc != 0)); then
      printf 'autonomy stamp failed rc=%s (release itself succeeded)\n' "$stamp_rc" >&2
      exit 3
    fi
    exit 0
  fi
  printf 'release repo already carries %s but live serves %s\n' "$generation" "${live_generation:-<unreachable>}" >&2
  exit 1
fi

for path in "${changed[@]}"; do
  case "$path" in
    data/public/seed-snapshot.json|data/public/box-subset.json|data/public/market-assets/*.webp) ;;
    *) printf 'daily release refused unexpected path: %s\n' "$path" >&2; exit 1 ;;
  esac
done
for path in "${changed[@]}"; do
  git -C "$RELEASE_REPO" add -- "$path"
done

generation="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"]["id"])' "$RELEASE_REPO/data/public/seed-snapshot.json")"
generated_at="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"]["generatedAt"])' "$RELEASE_REPO/data/public/seed-snapshot.json")"
if ((V2_MODE == 1)) && [[ "$generation" != "$V2_EXPECTED_GENERATION" ]]; then
  printf 'V2 release refused: accepted generation %s but bake produced %s\n' "$V2_EXPECTED_GENERATION" "$generation" >&2
  exit 1
fi
git -C "$RELEASE_REPO" commit -m "release: daily CARDZ 037 FE04 $generation [deploy]"
if ((V2_MODE == 1)); then
  v2_commit="$(git -C "$RELEASE_REPO" rev-parse HEAD)"
  v2_write_manifest "$v2_commit" "$generation" "$generated_at"
fi
push_attempt=1
push_max_attempts=3
if ((V2_MODE == 1)); then push_max_attempts=1; fi
until git -C "$RELEASE_REPO" push origin HEAD:main; do
  if ((push_attempt >= push_max_attempts)); then
    printf 'daily release push failed after %s attempts\n' "$push_attempt" >&2
    exit 1
  fi
  push_attempt=$((push_attempt + 1))
  printf 'daily release push retry %s/%s\n' "$push_attempt" "$push_max_attempts" >&2
  sleep 15
done

for _ in $(seq 1 60); do
  if body="$(curl --fail --silent --show-error https://app.cardzmarketcap.com/api/health)"; then
    # 判「新 bundle 上到未」睇 generatedAt（每次 bake 都變，由 snapshot 自己帶），
    # 唔再睇 build。build 要部署方 set CARDZ_PUBLIC_BUILD_ID，而實際 deploy 路徑
    # 由頭到尾冇 set 過，永遠係 "local"，所以舊 gate 恆假：每次都白等足 10 分鐘
    # 然後報 fail，明明個站已經更新咗。
    if PUBLIC_HEALTH="$body" python3 -c 'import json,os,sys; h=json.loads(os.environ["PUBLIC_HEALTH"]); sys.exit(0 if h.get("status")=="ok" and h.get("generation")==sys.argv[1] and h.get("generatedAt")==sys.argv[2] else 1)' "$generation" "$generated_at"; then
      stamp_rc=0
      stamp_autonomy "$generation" "$generated_at" published || stamp_rc=$?
      notify_release --outcome published --generation "$generation"
      printf '%s\n' "$body"
      if ((stamp_rc != 0)); then
        printf 'autonomy stamp failed rc=%s (release itself succeeded)\n' "$stamp_rc" >&2
        exit 3
      fi
      exit 0
    fi
  fi
  sleep 10
done

printf 'public release did not reach generation %s (generatedAt %s) within 10 minutes\n' "$generation" "$generated_at" >&2
exit 1
