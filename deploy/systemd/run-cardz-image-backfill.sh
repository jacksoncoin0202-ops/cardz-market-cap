#!/usr/bin/env bash
# 按需補圖：只服務 roster，新入 roster 嘅卡先落圖。
#
# 點解唔綁喺 daily：補圖係網絡 I/O 重、失敗率高嘅一步，但佢唔影響出數
# （價格／POP／指數同圖完全無關）。綁埋一齊 = 補圖死拖冧成條每日鏈。
# 所以獨立成 unit，daily 完咗之後按需 `systemctl start cardz-image-backfill.service`。
set -euo pipefail

repo_root="${CARDZ_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
snapshot="${CARDZ_IMAGE_SNAPSHOT:-$repo_root/data/public/seed-snapshot.json}"

if [[ ! -f "$snapshot" ]]; then
  echo "image backfill requires a snapshot at $snapshot" >&2
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
  echo "CARDZ image backfill requires Python 3.10 or newer" >&2
  exit 1
fi

log_dir="$repo_root/data/runtime/logs"
mkdir -p "$log_dir"
log_file="$log_dir/image_backfill_$(date -u +%Y%m%dT%H%M%SZ).log"

script="$repo_root/pipelines/ensure_std_card_images.py"

# 先 dry-run 睇有冇嘢做。ensure_std_card_images.py 唔加 --write 咩都唔會改，
# 所以呢步係純測量。「按需」嘅實際定義就係呢個 gate：冇偏離標準就唔好郁
# data/public，慳返一次無謂嘅 asset 改動同 QC record。
set +e
survey="$("$python_bin" -X utf8 "$script" "$snapshot" 2>&1)"
survey_exit=$?
set -e
echo "$survey" | tee -a "$log_file"

if (( survey_exit != 0 )); then
  echo "[image-backfill] dry-run failed; not writing" >&2
  exit "$survey_exit"
fi

# 輸出格式：`<name>: N ok | N resized | N rounded | N missing asset`
pending="$(echo "$survey" | sed -n 's/.*| \([0-9]*\) resized | \([0-9]*\) rounded | \([0-9]*\) missing asset.*/\1 \2 \3/p' | awk '{print $1 + $2 + $3}')"
if [[ -z "$pending" ]]; then
  echo "[image-backfill] FAIL: could not parse the dry-run survey line" >&2
  exit 1
fi

if (( pending == 0 )); then
  echo "[image-backfill] nothing to do; every roster card already meets the standard" | tee -a "$log_file"
  exit 0
fi

echo "[image-backfill] $pending card(s) need work; writing" | tee -a "$log_file"
set +e
"$python_bin" -X utf8 "$script" "$snapshot" --write 2>&1 | tee -a "$log_file"
write_exit=${PIPESTATUS[0]}
set -e

exit "$write_exit"
