#!/usr/bin/env bash
set -uo pipefail

# CARDZ 排程狀態嘅單一入口（Linux），對應 Windows 側嘅 deploy/windows/cardz-status.ps1。
# 純唯讀：只讀 systemctl 屬性同 data/runtime 底下嘅檔，唔寫任何嘢、唔連 DB，
# 所以可以隨時重複跑都唔會污染 alert / soak 記錄。
#
# Exit code：0 = 一切正常；1 = 有 unit 唔見咗 / unit failed / 有未清 alert。

repo_root="${CARDZ_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
daily_service="${CARDZ_DAILY_UNIT:-cardz-market-cap-daily.service}"
daily_timer="${CARDZ_DAILY_TIMER:-cardz-market-cap-daily.timer}"
watchdog_service="${CARDZ_WATCHDOG_UNIT:-cardz-market-cap-watchdog.service}"
watchdog_timer="${CARDZ_WATCHDOG_TIMER:-cardz-market-cap-watchdog.timer}"

case "${1-}" in
  -h|--help)
    cat <<'EOF'
Usage: deploy/linux/cardz-status.sh

顯示 daily / watchdog timer 嘅 enabled+active 狀態同上次/下次觸發、上次 daily run
嘅 exit code 同結束時間、未清嘅 data/runtime/alerts/ 檔，同最新一次 outcome gate 判決。

環境變數：CARDZ_REPO_ROOT（預設 = 呢個腳本嘅 repo 根目錄）、CARDZ_DAILY_UNIT、
CARDZ_DAILY_TIMER、CARDZ_WATCHDOG_UNIT、CARDZ_WATCHDOG_TIMER。
EOF
    exit 0
    ;;
  "") ;;
  *) echo "Unknown argument: $1" >&2; exit 2 ;;
esac

problems=0

# 唔准用 `eval "$(systemctl show ...)"`：時間戳類屬性（ExecMainExitTimestamp、
# NextElapseUSecRealtime）個值係 "Sun 2026-07-26 09:30:00 JST" 咁有空格，eval 會當
# 第二個 token 係命令去執行 → "2026-07-26: command not found"、exit 127。
# 一律 --value 逐個屬性讀（同 deploy/systemd/run-cardz-watchdog.sh 一致）。
show_prop() {
  systemctl show "$1" --property="$2" --value --no-pager 2>/dev/null
}

prop() {
  local value
  value="$(show_prop "$1" "$2")"
  printf '%s' "${value:-n/a}"
}

# systemctl show 對唔存在嘅 unit 一樣 exit 0 兼吐空值，所以「unit 未裝」唔可以靠
# exit code 分辨，一定要睇 LoadState=not-found，否則會當成「unit 正常但冇資料」。
unit_present() {
  [[ "$(show_prop "$1" LoadState)" != "not-found" ]]
}

exit_hint() {
  # verify_daily_run.py 嘅 exit code 語義；226 係 systemd 自己嘅 namespace 失敗。
  case "$1" in
    0) printf '  -> OK' ;;
    1) printf '  -> FAIL（數據檢查唔過）' ;;
    2) printf '  -> FAIL（驗唔到：DB 連唔到或者 config 唔見咗）' ;;
    226) printf '  -> FAIL（226/NAMESPACE：ReadWritePaths 有路徑唔存在）' ;;
    *) printf '' ;;
  esac
}

show_timer() {
  local unit="$1"
  if ! unit_present "$unit"; then
    printf '  %-34s NOT INSTALLED  <-- 排程唔見咗\n' "$unit"
    problems=1
    return
  fi
  printf '  %-34s %s / %s\n' "$unit" "$(prop "$unit" UnitFileState)" "$(prop "$unit" ActiveState)"
  printf '  %-34s last=%s\n' '' "$(prop "$unit" LastTriggerUSec)"
  printf '  %-34s next=%s\n' '' "$(prop "$unit" NextElapseUSecRealtime)"
}

show_service() {
  local unit="$1" status result
  if ! unit_present "$unit"; then
    printf '  %-34s NOT INSTALLED  <-- unit 唔見咗\n' "$unit"
    problems=1
    return
  fi
  status="$(prop "$unit" ExecMainStatus)"
  result="$(prop "$unit" Result)"
  [[ "$result" == "success" || "$result" == "n/a" ]] || problems=1
  printf '  %-34s %s  result=%s  exitCode=%s%s\n' \
    "$unit" "$(prop "$unit" ActiveState)" "$result" "$status" "$(exit_hint "$status")"
  printf '  %-34s finished=%s\n' '' "$(prop "$unit" ExecMainExitTimestamp)"
}

newest_file() {
  find "$1" -maxdepth 1 -type f -name "$2" -printf '%T@\t%p\n' 2>/dev/null \
    | sort -rn | head -n 1 | cut -f2-
}

echo
echo '=== 排程 ==='
if ! command -v systemctl >/dev/null 2>&1; then
  echo '  搵唔到 systemctl —— 呢部機唔係 systemd 系統，排程狀態查唔到'
  problems=1
else
  show_timer "$daily_timer"
  show_timer "$watchdog_timer"

  echo
  echo '=== 上次執行 ==='
  show_service "$daily_service"
  show_service "$watchdog_service"
fi

echo
echo '=== Alert 檔 ==='
alerts_dir="$repo_root/data/runtime/alerts"
mapfile -t alert_files < <(
  find "$alerts_dir" -maxdepth 1 -type f -name '*.json' -printf '%T@\t%p\n' 2>/dev/null \
    | sort -rn | cut -f2-
)
if ((${#alert_files[@]} == 0)); then
  echo '  冇 alert（好）'
else
  printf '  %d 個未清 alert：\n' "${#alert_files[@]}"
  for alert in "${alert_files[@]:0:10}"; do
    printf '    %s\n' "${alert##*/}"
  done
  problems=1
fi

echo
echo '=== 最新一次 outcome gate 判決 ==='
# 唔重新跑 verify（會連 DB、亦可能寫 alert）；直接讀返 runner 留低嘅 [verify] 行，
# 即係最後一次真正跑過嘅判決。
log_dir="$repo_root/data/runtime/logs"
for pattern in 'daily_*.log' 'watchdog_*.log'; do
  log="$(newest_file "$log_dir" "$pattern")"
  if [[ -z "$log" ]]; then
    printf '  %-18s 冇 log\n' "$pattern"
    continue
  fi
  printf '  %s  (%s)\n' "${log##*/}" "$(date -r "$log" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || echo 'mtime n/a')"
  verdict="$(grep -aF '[verify]' "$log" | tail -n 12)"
  if [[ -z "$verdict" ]]; then
    echo '    log 入面搵唔到 [verify] 行 —— outcome gate 可能未跑過'
  else
    sed 's/^/    /' <<<"$verdict"
  fi
done

echo
exit "$problems"
