#!/usr/bin/env bash
set -euo pipefail

# Watchdog：daily 內建嘅 verify gate 只喺 daily 真係行完之後先跑，所以佢有一個天生盲點——
# 「部機冇著 / timer 被 disable / run 掛住冇 exit / unit 根本冇 trigger 過」呢類情況，
# daily 根本冇 run，亦即冇人叫 verify，結果係完全靜音。呢個獨立 unit 喺 daily 之後幾個
# 鐘照跑一次 verify，令「乜都冇發生」都會留低 alert 檔 + failed unit。

repo_root="${CARDZ_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
daily_unit="${CARDZ_DAILY_UNIT:-cardz-market-cap-daily.service}"
daily_timer="${CARDZ_DAILY_TIMER:-cardz-market-cap-daily.timer}"

verify_script="$repo_root/scripts/verify_daily_run.py"
if [[ ! -f "$verify_script" ]]; then
  echo "CARDZ watchdog requires scripts/verify_daily_run.py" >&2
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
  echo "CARDZ watchdog requires Python 3.10 or newer" >&2
  exit 1
fi

log_dir="$repo_root/data/runtime/logs"
mkdir -p "$log_dir"
log_file="$log_dir/watchdog_$(date -u +%Y%m%d_%H%M%S).log"

# 先記低 daily unit 自己嘅狀態：分辨「run 咗但數據唔啱」定「根本冇 run 過」。
# systemctl show 係唯讀，任何 user 都叫得，唔需要 root。
#
# 唔准用 `eval "$(systemctl show ...)"`：時間戳類屬性（ExecMainExitTimestamp、
# NextElapseUSecRealtime）個值係 "Sun 2026-07-26 09:30:00 JST" 咁有空格，eval 會
# 當第二個 token 係命令去執行 → "2026-07-26: command not found"、exit 127，仲要
# 死喺 verify 之前，即係專捉靜默失敗嗰個 watchdog 自己靜默失敗。--value 逐個讀。
show_prop() {
  systemctl show "$1" --property="$2" --value --no-pager 2>/dev/null
}

# LoadState=not-found 先係真正「unit 唔見咗」。systemctl show 對唔存在嘅 unit
# 一樣 exit 0 兼吐空值，所以唔可以靠 exit code 判斷。
if [[ "$(show_prop "$daily_unit" LoadState)" == "not-found" ]]; then
  echo "[watchdog] $daily_unit NOT FOUND — unit 本身唔見咗" | tee -a "$log_file"
else
  echo "[watchdog] $daily_unit state=$(show_prop "$daily_unit" ActiveState) result=$(show_prop "$daily_unit" Result) exitStatus=$(show_prop "$daily_unit" ExecMainStatus) lastExit=$(show_prop "$daily_unit" ExecMainExitTimestamp)" | tee -a "$log_file"
fi

if [[ "$(show_prop "$daily_timer" LoadState)" == "not-found" ]]; then
  echo "[watchdog] $daily_timer NOT FOUND — 排程本身唔見咗" | tee -a "$log_file"
else
  echo "[watchdog] $daily_timer state=$(show_prop "$daily_timer" ActiveState) lastTrigger=$(show_prop "$daily_timer" LastTriggerUSec) next=$(show_prop "$daily_timer" NextElapseUSecRealtime)" | tee -a "$log_file"
fi

set +e
"$python_bin" -X utf8 "$verify_script" --tag watchdog 2>&1 | tee -a "$log_file"
verify_exit=${PIPESTATUS[0]}
set -e

exit "$verify_exit"
