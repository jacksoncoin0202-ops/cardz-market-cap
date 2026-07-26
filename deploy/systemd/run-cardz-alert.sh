#!/usr/bin/env bash
set -uo pipefail

# 由 cardz-market-cap-alert@.service 經 OnFailure= 觸發。$1 = 死咗嗰個 unit 全名。
#
# 呢個 handler 唔重跑任何嘢、唔連 DB：佢淨係讀 systemctl 屬性同最新一份 log，
# 然後叫 scripts/notify_alert.py 出一次通報。dedupe key 由 unit 名剝出嚟
# （cardz-market-cap-daily.service -> daily），同 verify_daily_run.py --tag 一致，
# 所以「gate 判 fail」同「unit 因為呢個 exit code 變 failed」唔會出兩次聲。
#
# 唔准用 `eval "$(systemctl show ...)"`：時間戳屬性個值有空格，eval 會當第二個
# token 係命令去執行 → exit 127，即係專捉靜默失敗嗰個 handler 自己靜默失敗。

failed_unit="${1:-unknown.service}"
repo_root="${CARDZ_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

key="${failed_unit%.service}"
key="${key#cardz-market-cap-}"

notifier="$repo_root/scripts/notify_alert.py"
if [[ ! -f "$notifier" ]]; then
  echo "CARDZ alert handler requires scripts/notify_alert.py" >&2
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
if [[ -z "$python_bin" ]]; then
  echo "CARDZ alert handler could not resolve a Python 3.10+ interpreter" >&2
  exit 1
fi

show_prop() {
  systemctl show "$1" --property="$2" --value --no-pager 2>/dev/null
}

exit_code="$(show_prop "$failed_unit" ExecMainStatus)"
result="$(show_prop "$failed_unit" Result)"
[[ "$exit_code" =~ ^[0-9]+$ ]] || exit_code=1
[[ -n "$result" ]] || result=unknown

# result 入 status：timeout 同 exit-code 係兩種唔同故障，唔應該互相 dedupe 掉。
"$python_bin" -X utf8 "$notifier" \
  --key "$key" \
  --status "unit_failed:${result}:${exit_code}" \
  --stage "systemd unit ${failed_unit} failed before the outcome gate could report (result=${result})" \
  --exit-code "$exit_code" \
  --unit "$failed_unit" \
  --log-glob "${key}_*.log" \
  --write-alert
