#!/usr/bin/env bash
set -euo pipefail

repo_root="${CARDZ_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
env_file="${CARDZ_ENV_FILE:-/etc/cardz-market-cap/backend.env}"
mode="${CARDZ_DAILY_MODE:-production}"

# WSL systemd 內所有 canonical writer 共用一把鎖。weekly full-backfill 亦用同一檔；
# 撞期即 fail，唔會兩條 writer 同時改 DB / universe。Windows → WSL cutover 仍由
# installer 嘅 --confirm-single-writer 閘住，因為兩個 OS 唔可以靠同一個 flock。
if [[ "${CARDZ_WRITER_LOCK_HELD:-0}" != "1" ]]; then
  writer_lock="$repo_root/data/runtime/cardz-writer.lock"
  mkdir -p "$(dirname "$writer_lock")"
  exec /usr/bin/flock --nonblock "$writer_lock" \
    /usr/bin/env CARDZ_WRITER_LOCK_HELD=1 bash "$0" "$@"
fi

# local  = 行足 publish 鏈，但淨係寫本機 public tree（唔使 R2／canary／pointer env）
# remote = 同上再加 R2 上傳同 pointer promotion，backend.env 要有齊
#          CARDZ_PRODUCTION_R2_BUCKET + CARDZ_GENERATION_CANARY_COMMAND_JSON
#          + CARDZ_POINTER_PROMOTE_COMMAND_JSON，欠一個 run_daily.py 會即拒
# off    = 舊行為，只做 collect + DB sync（run_daily.py --backend-only）
publish_mode="${CARDZ_DAILY_PUBLISH:-local}"
case "$publish_mode" in
  local|remote|off) ;;
  *)
    echo "CARDZ_DAILY_PUBLISH must be local, remote or off" >&2
    exit 1
    ;;
esac

if [[ ! -f "$env_file" ]]; then
  echo "CARDZ daily environment file is missing" >&2
  exit 1
fi
permissions="$(stat -c '%a' "$env_file")"
owner="$(stat -c '%u' "$env_file")"
if (( (8#$permissions & 8#077) != 0 )) || [[ "$owner" != "0" ]]; then
  echo "CARDZ daily environment file must be root-owned and not group/world-readable" >&2
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
  echo "CARDZ daily runner requires Python 3.10 or newer" >&2
  exit 1
fi

verify_script="$repo_root/scripts/verify_daily_run.py"
if [[ ! -f "$verify_script" ]]; then
  echo "CARDZ daily runner requires scripts/verify_daily_run.py" >&2
  exit 1
fi

daily_args=(daily --external-db --mode "$mode")
if [[ "$mode" == production ]]; then
  # Production publication is live-refresh-only. A partial/fallback GemRate
  # pass may retain last-good DB facts, but it cannot advance an evaluation or
  # runtime pointer.
  daily_args+=(--require-gemrate-refresh)
fi
if [[ "$publish_mode" != off ]]; then
  # 前置檢查，唔係擺設：publish 鏈個 npm run build 喺全鏈最尾，行到嗰步已經燒咗
  # 2–2.5 鐘 GemRate quota。npm 唔喺 PATH 就即刻死，唔好死喺最後一步。
  # systemd 個 PATH 係 /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin，
  # nvm 裝嘅 node 喺 $HOME 底下，ProtectHome=true 之下連睇都睇唔到 → 要用 apt/NodeSource
  # 裝去 /usr/bin。
  if ! command -v npm >/dev/null 2>&1; then
    echo "CARDZ daily publish requires npm on PATH; install Node system-wide (nvm shims are unreachable under ProtectHome=true)" >&2
    exit 1
  fi
  # ProtectSystem=strict 令 service user 個 home 唯讀，npm 寫唔到自己個 cache/_logs。
  # 掉去 data/runtime（已喺 ReadWritePaths）。
  # 唔准順手連 HOME 都改：playwright 喺 $HOME/.cache/ms-playwright 揾 chromium，
  # 改咗 HOME 會令 GemRate keyless 抓取由頭死起。
  export npm_config_cache="$repo_root/data/runtime/npm-cache"
  mkdir -p "$npm_config_cache"
  daily_args+=(--publish)
  if [[ "$publish_mode" == local ]]; then
    daily_args+=(--local-only)
  fi
fi

log_dir="$repo_root/data/runtime/logs"
mkdir -p "$log_dir"
log_file="$log_dir/daily_${mode}_${publish_mode}_$(date -u +%Y%m%d_%H%M%S).log"

# stdout 同時入 journald（systemd 收）同 log 檔（cardz-status.sh 同 runbook 讀）。
# 唔用 exec：daily 之後仲要行 outcome gate。
set +e
"$python_bin" -X utf8 "$repo_root/scripts/backend.py" "${daily_args[@]}" 2>&1 | tee -a "$log_file"
daily_exit=${PIPESTATUS[0]}

# Outcome gate：daily 鏈 exit 0 唔代表今日數據落咗地（collector replay / INSERT IGNORE
# no-op / stale effective_date 全部都係 exit 0）。verify 查 price / snapshot / source
# 三項 freshness，fail 就寫 data/runtime/alerts/ 並以非零 exit 令 systemd 個 unit
# 變 failed，唔會再靜靜地報 SUCCESS。無論 daily 成功與否都要行，因為 daily 中途死
# 一樣要留低 alert。
"$python_bin" -X utf8 "$verify_script" 2>&1 | tee -a "$log_file"
verify_exit=${PIPESTATUS[0]}
set -e

if (( daily_exit != 0 )); then
  exit "$daily_exit"
fi
exit "$verify_exit"
