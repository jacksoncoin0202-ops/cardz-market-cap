#!/usr/bin/env bash
# 每週 GemRate roster + POP 全掃（api-dump）。
#
# 點解要同 daily 分開：daily 掃 ~600 roster 嘅價格，2–2.5 鐘；全 roster 1468 張
# 帶 history 再要多 1.5 鐘。綁埋一齊 = 斷一次乜都冇（2026-07-25 就係喺 825/1468
# 俾 ^C 斷咗，連當日價格都出唔到）。分開之後全掃斷咗唔會拖冧每日出數。
set -euo pipefail

repo_root="${CARDZ_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
env_file="${CARDZ_ENV_FILE:-/etc/cardz-market-cap/backend.env}"
speed="${CARDZ_FREEZE_SPEED:-medium}"

ids_file="${CARDZ_FREEZE_IDS:-$repo_root/data/runtime/private-source-map/tracked-gemrate-ids.txt}"
# tracked-gemrate-ids.txt 先係現行 roster（run_daily.py:282 default 就係佢）。
# gemrate-ids.txt / active-gemrate-ids.txt 係舊世代產物，唔好掃錯個檔。
if [[ ! -f "$ids_file" ]]; then
  echo "GemRate freeze requires a roster id file at $ids_file" >&2
  exit 1
fi

if [[ ! -f "$env_file" ]]; then
  echo "CARDZ environment file is missing" >&2
  exit 1
fi
permissions="$(stat -c '%a' "$env_file")"
owner="$(stat -c '%u' "$env_file")"
if (( (8#$permissions & 8#077) != 0 )) || [[ "$owner" != "0" ]]; then
  echo "CARDZ environment file must be root-owned and not group/world-readable" >&2
  exit 1
fi

python_bin="${CARDZ_PYTHON:-}"
if [[ -z "$python_bin" && -x "$repo_root/.venv-backend/bin/python" ]]; then
  python_bin="$repo_root/.venv-backend/bin/python"
fi
if [[ -z "$python_bin" ]]; then
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      python_bin="$(command -v "$candidate")"
      break
    fi
  done
fi
if [[ -z "$python_bin" ]] || ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "CARDZ GemRate freeze requires Python 3.10 or newer" >&2
  exit 1
fi

# key 到期（~2026-07-29）之後 cmd_api_dump 會 `return 2`。呢度預先講清楚，
# 因為 unit 失敗時 journal 得個 exit code，唔講就要人去翻源碼先知咩事。
if [[ -z "${GEMRATE_API_KEY:-}" ]]; then
  echo "GEMRATE_API_KEY is not set; api-dump cannot run (daily still works via the Grade10 mirror, but it never writes population history)" >&2
  exit 2
fi

log_dir="$repo_root/data/runtime/logs"
mkdir -p "$log_dir"
log_file="$log_dir/gemrate_freeze_$(date -u +%Y%m%dT%H%M%SZ).log"

cards_dir="$repo_root/data/private/gemrate/cards"
before="$(find "$cards_dir" -name history_full.json 2>/dev/null | wc -l || echo 0)"
echo "[freeze] roster=$(wc -l < "$ids_file") speed=$speed history_full_before=$before" | tee -a "$log_file"

# --resume 只跳過已經有 history_full.json 嗰啲，所以斷咗重跑係接住上次，
# 唔會由零開始。呢個係全掃可以安全被打斷嘅唯一理由。
set +e
"$python_bin" -X utf8 "$repo_root/pipelines/gemrate_source.py" api-dump \
  --ids-file "$ids_file" \
  --speed "$speed" \
  --resume 2>&1 | tee -a "$log_file"
harvest_exit=${PIPESTATUS[0]}
set -e

after="$(find "$cards_dir" -name history_full.json 2>/dev/null | wc -l || echo 0)"
echo "[freeze] history_full_after=$after (+$((after - before))) exit=$harvest_exit" | tee -a "$log_file"

# 靜靜地少咗嘢係最陰險嘅失敗：api-dump 可以 exit 0 但一張都冇新增（quota 用完、
# 全部 resume skip、roster 讀成空）。行完冇任何進度就當失敗，等 OnFailure 出聲。
if (( harvest_exit == 0 && after == before && before < $(wc -l < "$ids_file") )); then
  echo "[freeze] FAIL: exit 0 but no new history captured and roster is not complete" >&2
  exit 1
fi

exit "$harvest_exit"
