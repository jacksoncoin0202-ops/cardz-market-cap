#!/usr/bin/env bash
set -euo pipefail

action="dry-run"
repo_root="/opt/cardz-market-cap"
unit_dir="/etc/systemd/system"
env_file="/etc/cardz-market-cap/backend.env"
service_user="cardz"
enable_units=0
start_units=0
confirm_single_writer=0

usage() {
  cat <<'EOF'
Usage: deploy/linux/cardz-daily-systemd.sh [install|status|uninstall|dry-run]
  [--repo-root PATH] [--unit-dir PATH] [--env-file PATH] [--user NAME]
  [--enable] [--start] [--confirm-single-writer]

Installs the web runtime, daily stack and two disabled-by-default weekly plans:
  cardz-market-cap-daily.timer     00:30 UTC (09:30 JST)  full pipeline + outcome gate
  cardz-market-cap-watchdog.timer  05:07 UTC (14:07 JST)  verify-only, catches "never ran"
  cardz-gemrate-freeze.timer       Sun 14:23 UTC +-1h     weekly roster + population freeze
  cardz-market-cap-candidate-refresh.timer  additive universe candidate intake
  cardz-market-cap-retention.timer          dry-run retention plan only

Deliberately NOT covered -- install these by hand, see deploy/systemd/README.md:
  cardz-market-cap-bootstrap.service  one-shot, run once before enabling any timer
  cardz-grade10-discovery.*           optional operator preflight
  cardz-image-backfill.service        on-demand, no timer by design

The default action is dry-run and does not alter systemd or the repository.
`install` writes units and daemon-reloads only. `--enable` and `--start` are
separate explicit state changes and require `--confirm-single-writer`.
Candidate refresh and retention are never enabled or started by this installer.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    install|status|uninstall|dry-run) action="$1" ;;
    --repo-root) repo_root="$2"; shift ;;
    --unit-dir) unit_dir="$2"; shift ;;
    --env-file) env_file="$2"; shift ;;
    --user) service_user="$2"; shift ;;
    --enable) enable_units=1 ;;
    --start) start_units=1 ;;
    --confirm-single-writer) confirm_single_writer=1 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if ((enable_units || start_units)) && ((confirm_single_writer == 0)); then
  echo "--enable/--start requires --confirm-single-writer after the legacy Windows writer is disabled" >&2
  exit 2
fi

repo_root="$(cd "$repo_root" && pwd)"
template_dir="$repo_root/deploy/systemd"
service_template="$template_dir/cardz-market-cap-daily.service"
timer_template="$template_dir/cardz-market-cap-daily.timer"
runner="$template_dir/run-cardz-daily.sh"
watchdog_service_template="$template_dir/cardz-market-cap-watchdog.service"
watchdog_timer_template="$template_dir/cardz-market-cap-watchdog.timer"
watchdog_runner="$template_dir/run-cardz-watchdog.sh"
alert_service_template="$template_dir/cardz-market-cap-alert@.service"
alert_runner="$template_dir/run-cardz-alert.sh"
freeze_service_template="$template_dir/cardz-gemrate-freeze.service"
freeze_timer_template="$template_dir/cardz-gemrate-freeze.timer"
freeze_runner="$template_dir/run-cardz-gemrate-freeze.sh"
web_service_template="$template_dir/cardz-market-cap-web.service"
web_refresh_service_template="$template_dir/cardz-market-cap-web-refresh.service"
web_refresh_runner="$template_dir/run-cardz-web-refresh.sh"
candidate_service_template="$template_dir/cardz-market-cap-candidate-refresh.service"
candidate_timer_template="$template_dir/cardz-market-cap-candidate-refresh.timer"
retention_service_template="$template_dir/cardz-market-cap-retention.service"
retention_timer_template="$template_dir/cardz-market-cap-retention.timer"

# 缺檔分兩級。分級唔係為咗放水，係因為兩者嘅後果根本唔同級數：
#
#   CORE  缺咗 = 就算裝到 timer，佢每一日都一定失敗。裝出嚟係個扮成功嘅空殼，
#         比裝唔到更差 —— 因為 `systemctl list-timers` 睇落好正常。
#   GUARD 缺咗 = pipeline 照跑照出數據，但佢幾時死冇人會知。真係「冇護欄」。
#
# verify_daily_run.py 擺 CORE 唔擺 GUARD，因為佢唔係「冇閘就冇閘」：
# run-cardz-daily.sh:50-54 揾唔到佢即刻 exit 1，即係裝出嚟嘅 timer 失敗率 100%。
#
# 通報層（alert@ template + runner + notify_alert.py）先係真 GUARD：daily / watchdog
# unit 寫住 OnFailure=cardz-market-cap-alert@%n.service，個 template unit 唔喺
# /etc/systemd/system 嘅話，systemd 只會喺 journal 留一行 "Unit not found" 就乜都
# 唔會發生 —— 靜音通報比冇通報更誤導，所以缺就一定要喺 stderr、journal 同最後
# summary 三個地方大聲嘈，唔准靜靜 skip 扮裝好咗。
#
# 一次過收晒全部缺件先報，唔好見一個死一個：缺 4 個要人跑 4 次先知全貌。
missing_core=()
missing_guard=()

require() {  # require <core|guard> <path> <why it is needed>
  [[ -f "$2" ]] && return 0
  local entry
  entry="$(printf '%s\n        why: %s' "${2#"$repo_root"/}" "$3")"
  case "$1" in
    core) missing_core+=("$entry") ;;
    guard) missing_guard+=("$entry") ;;
  esac
}

require core  "$service_template" "the daily unit itself; there is nothing to install without it"
require core  "$timer_template"   "the 00:30 UTC (09:30 JST) schedule"
require core  "$runner"           "ExecStart= of the daily unit"
require core  "$repo_root/scripts/backend.py" \
  "pipeline entrypoint that run-cardz-daily.sh invokes"
require core  "$repo_root/scripts/verify_daily_run.py" \
  "outcome gate. run-cardz-daily.sh:50-54 exits 1 without it, so an installed timer would fail 100% of its runs"
require core  "$web_service_template" "the WSL Node runtime unit"
require core  "$web_refresh_service_template" "the post-publish restart and health unit"
require core  "$web_refresh_runner" "ExecStart= of the post-publish health unit"
require core  "$repo_root/apps/web/scripts/verify-runtime-health.mjs" "verifies the promoted generation after restart"
require core  "$candidate_service_template" "the disabled-by-default additive candidate service"
require core  "$candidate_timer_template" "the disabled-by-default additive candidate schedule"
require core  "$retention_service_template" "the disabled-by-default retention dry-run service"
require core  "$retention_timer_template" "the disabled-by-default retention schedule"
require core  "$repo_root/pipelines/db_retention.py" "retention dry-run planner"
require guard "$alert_service_template" "OnFailure= target of the daily and watchdog units"
require guard "$alert_runner"           "ExecStart= of the alert unit"
require guard "$repo_root/scripts/notify_alert.py" "delivers the failure notification"

notify_ok=1
((${#missing_guard[@]} == 0)) || notify_ok=0

missing_remedy() {
  cat <<EOF
How to fix:
  These paths are tracked design artefacts of this repo. If they are absent from
  a fresh 'git clone', they were never committed -- that is a packaging bug on
  the source side, not a mistake on this host. Either
    (a) have the packager 'git add' them and re-clone here, or
    (b) copy them onto this host at exactly the paths above, chmod +x the .sh ones.
  Prove the clone is whole before deploying:
    python -X utf8 scripts/verify_clean_clone.py
EOF
}

if ((${#missing_core[@]})); then
  {
    echo "FATAL: this checkout cannot run the daily pipeline. NOTHING was installed."
    echo
    echo "Missing core files (${#missing_core[@]}) -- each one alone makes every scheduled run fail:"
    printf '  %s\n' "${missing_core[@]}"
    if ((${#missing_guard[@]})); then
      echo
      echo "Also missing, but non-fatal (see the unguarded warning below once core is fixed):"
      printf '  %s\n' "${missing_guard[@]}"
    fi
    echo
    missing_remedy
  } >&2
  exit 1
fi

unguarded_banner() {
  cat <<EOF
######################################################################
# WARNING -- INSTALLED WITHOUT A FAILURE NOTIFIER
#
# The daily and watchdog units declare
#   OnFailure=cardz-market-cap-alert@%n.service
# but that template unit is NOT being installed, because these files
# are missing from this checkout:
$(printf '%s\n' "${missing_guard[@]}" | sed 's/^/#   /')
#
# Consequence: when a run fails, systemd logs ONE "Unit not found"
# line and does nothing else. No webhook. No alert file. No fan-out.
# A broken pipeline will look exactly like a healthy one.
#
# Until this is fixed you MUST poll it yourself, e.g. daily:
#   systemctl --failed | grep cardz
#   systemctl list-timers 'cardz-*'
#   journalctl -u cardz-market-cap-daily.service --since yesterday
#
$(missing_remedy | sed 's/^/# /')
######################################################################
EOF
}

# 只喺 install 先喺呢度嘈：
#   - status / uninstall 乜都冇裝，印「INSTALLED WITHOUT A FAILURE NOTIFIER」係講錯嘢。
#   - dry-run 自己喺最後會印一次（見下面 exit 0 之前嗰句）。呢度再印一次就變咗同一屏
#     連續兩份一模一樣嘅 30 行，人睇兩次就開始跳過——嘈到冇人睇等於冇嘈過。
#   - dry-run 亦都唔應該寫 journal：usage 寫明佢 "does not alter systemd or the repository"。
# install 就三個地方都要有（stderr 開頭、journal、最後 summary），因為裝完個人多數
# 已經 scroll 走咗開頭嗰段。
if ((notify_ok == 0)) && [[ "$action" == "install" ]]; then
  unguarded_banner >&2
  # journal 都要有一行，唔可以淨係留喺跑 installer 嗰個人嘅 terminal scrollback。
  command -v logger >/dev/null 2>&1 && logger -t cardz-installer -p daemon.warning \
    "installing cardz timers WITHOUT failure notifier; missing ${#missing_guard[@]} alert file(s); failures will be silent"
fi

install_watchdog=0
if [[ -f "$watchdog_service_template" && -f "$watchdog_timer_template" && -f "$watchdog_runner" ]]; then
  install_watchdog=1
fi

# 週掃同 daily 一樣係數據鏈嘅一部分（GemRate key 一死就冇 population 歷史），
# 所以入 installer 而唔係叫人手裝。相反 cardz-image-backfill.service 冇 timer、
# 係按需 `systemctl start` 嘅操作工具，同 bootstrap 一樣留喺 README 手裝。
install_freeze=0
if [[ -f "$freeze_service_template" && -f "$freeze_timer_template" && -f "$freeze_runner" ]]; then
  install_freeze=1
fi

# ProtectSystem=strict 配 ReadWritePaths= 有個硬要求：每個列出嘅路徑喺 systemd 起
# mount namespace 嗰陣必須實際存在，否則個 unit 死於 status=226/NAMESPACE —— 而且
# 係死喺 ExecStart 之前，journalctl 連一行 Python 輸出都冇，睇落好似乜都冇發生過。
# daily unit collect 側四個目錄（integrations/grade10/data、data/runtime、
# data/private/gemrate、.venv-backend）同 watchdog 嗰個 data/runtime 全部 gitignored，
# fresh clone 之後根本唔存在，所以唔可以指意 clone 帶落嚟，一定要喺裝 unit 之前開返，
# 兼且 chown 俾真正跑 service 嗰個用戶（unit 寫住 User=/Group=）。
# publish 側三條（data/public、manifests、packages/market-data/dist）入面頭兩個
# clone 帶落嚟、第三個係 npm build 產物；三個一樣要行 install -d，因為 tracked 目錄
# 都要 chown 返俾 service user 先寫得入，而 dist 未 build 過就真係唔存在。
# 路徑直接由 unit 檔讀返出嚟：將來改 unit 嘅 ReadWritePaths=，呢度自動跟，唔會走音。
# 只掃真係存在嘅 unit：sed 食到一個唔存在嘅檔就非零離開，而成個腳本行緊
# `set -euo pipefail`，會喺呢度靜靜死喺一個同 ReadWritePaths 完全無關嘅位。
scan_units=("$service_template")
if ((notify_ok)); then
  scan_units+=("$alert_service_template")
fi
if ((install_watchdog)); then
  scan_units+=("$watchdog_service_template")
fi
if ((install_freeze)); then
  scan_units+=("$freeze_service_template")
fi
scan_units+=("$candidate_service_template" "$retention_service_template")

readwrite_paths() {
  local path
  sed -n 's/^ReadWritePaths=//p' "${scan_units[@]}" \
    | tr ' \t' '\n\n' \
    | awk 'NF && !seen[$0]++' \
    | while read -r path; do
        printf '%s\n' "${path/#\/opt\/cardz-market-cap/$repo_root}"
      done
}

if [[ "$action" == "status" ]]; then
  systemctl status --no-pager \
    cardz-market-cap-daily.service cardz-market-cap-daily.timer \
    cardz-market-cap-watchdog.service cardz-market-cap-watchdog.timer \
    cardz-gemrate-freeze.service cardz-gemrate-freeze.timer \
    cardz-market-cap-web.service cardz-market-cap-web-refresh.service \
    cardz-market-cap-candidate-refresh.service cardz-market-cap-candidate-refresh.timer \
    cardz-market-cap-retention.service cardz-market-cap-retention.timer || true
  exit 0
fi

if [[ "$action" == "uninstall" ]]; then
  [[ "$(id -u)" == "0" ]] || { echo "uninstall requires root" >&2; exit 1; }
  systemctl disable --now cardz-market-cap-daily.timer || true
  systemctl disable --now cardz-market-cap-watchdog.timer || true
  systemctl disable --now cardz-gemrate-freeze.timer || true
  systemctl disable --now cardz-market-cap-web.service || true
  systemctl disable --now cardz-market-cap-candidate-refresh.timer || true
  systemctl disable --now cardz-market-cap-retention.timer || true
  rm -f \
    "$unit_dir/cardz-market-cap-daily.service" "$unit_dir/cardz-market-cap-daily.timer" \
    "$unit_dir/cardz-market-cap-watchdog.service" "$unit_dir/cardz-market-cap-watchdog.timer" \
    "$unit_dir/cardz-gemrate-freeze.service" "$unit_dir/cardz-gemrate-freeze.timer" \
    "$unit_dir/cardz-market-cap-web.service" "$unit_dir/cardz-market-cap-web-refresh.service" \
    "$unit_dir/cardz-market-cap-candidate-refresh.service" "$unit_dir/cardz-market-cap-candidate-refresh.timer" \
    "$unit_dir/cardz-market-cap-retention.service" "$unit_dir/cardz-market-cap-retention.timer" \
    "$unit_dir/cardz-market-cap-alert@.service"
  systemctl daemon-reload
  exit 0
fi

if [[ ! -f "$env_file" ]]; then
  echo "Missing environment file: $env_file" >&2
  exit 1
fi
for private_env in "$env_file" "$(dirname "$env_file")/gemrate.env"; do
  [[ -f "$private_env" ]] || continue
  if LC_ALL=C grep -q $'\r$' "$private_env"; then
    echo "Environment file must use LF line endings: $private_env" >&2
    exit 1
  fi
done
mode="$(stat -c '%a' "$env_file")"
owner="$(stat -c '%u' "$env_file")"
if (( (8#$mode & 8#077) != 0 )); then
  echo "Environment file must not be group/world-readable: $env_file" >&2
  exit 1
fi
if [[ "$action" == "install" && "$owner" != "0" ]]; then
  echo "Installed systemd environment file must be root-owned: $env_file" >&2
  exit 1
fi

if [[ "$action" == "dry-run" ]]; then
  rw_all=""
  rw_missing=""
  while read -r rw_path; do
    [[ -n "$rw_path" ]] || continue
    rw_all+="$rw_path "
    [[ -d "$rw_path" ]] || rw_missing+="$rw_path "
  done < <(readwrite_paths)
  rw_all="${rw_all% }"
  rw_missing="${rw_missing% }"
  # 只睇 key 存唔存在，永遠唔會印個 URL 值出嚟。
  alert_webhook='not set (alert file + failed unit only)'
  if grep -qE '^[[:space:]]*CARDZ_ALERT_WEBHOOK=[^[:space:]]' "$env_file" 2>/dev/null; then
    alert_webhook='set'
  fi
  # 週掃冇 key 就 exit 2 即死，所以裝之前要睇得到 key 喺唔喺度。同 webhook 一樣
  # 只報存在與否，永遠唔會印個值。key 可以擺 backend.env 或者隔籬 gemrate.env
  # （unit 兩個檔都讀，後者 optional）。
  gemrate_env="$(dirname "$env_file")/gemrate.env"
  gemrate_key='NOT SET -- weekly freeze will exit 2'
  if grep -qhE '^[[:space:]]*GEMRATE_API_KEY=[^[:space:]]' "$env_file" "$gemrate_env" 2>/dev/null; then
    gemrate_key='set'
  fi
  printf 'action=dry-run\nrepo_root=%s\nenv_file=%s\nunit_dir=%s\nuser=%s\nenableRequested=%s\nstartRequested=%s\nsingleWriterConfirmed=%s\nschedule=00:30 UTC (09:30 Asia/Tokyo)\nwatchdogSchedule=%s\nfreezeSchedule=%s\ncandidateSchedule=Mon 14:17 UTC +-1h (installed disabled)\nretentionSchedule=Tue 17:17 UTC +-30m (installed disabled; no --apply)\nentrypoint=scripts/backend.py daily + scripts/verify_daily_run.py\ntimeoutSeconds=21600\nreadWritePaths=%s\nreadWritePathsMissing=%s\nalertUnit=%s\nfailureNotifier=%s\nalertWebhook=%s\ngemrateApiKey=%s\n' \
    "$repo_root" "$env_file" "$unit_dir" "$service_user" \
    "$enable_units" "$start_units" "$confirm_single_writer" \
    "$( ((install_watchdog)) && echo '05:07 UTC (14:07 Asia/Tokyo)' || echo 'not installed (templates missing)')" \
    "$( ((install_freeze)) && echo 'Sun 14:23 UTC +-1h jitter (Mon 23:23 Asia/Tokyo)' || echo 'not installed (templates missing)')" \
    "$rw_all" "${rw_missing:-none (install 會照樣確保存在)}" \
    "$( ((notify_ok)) && echo 'cardz-market-cap-alert@.service (OnFailure, no enable needed)' || echo 'NOT INSTALLED -- files missing')" \
    "$( ((notify_ok)) && echo 'ok' || echo "DEGRADED -- ${#missing_guard[@]} file(s) missing, run failures will be SILENT")" \
    "$alert_webhook" "$gemrate_key"
  ((notify_ok)) || unguarded_banner >&2
  exit 0
fi

[[ "$(id -u)" == "0" ]] || { echo "install requires root" >&2; exit 1; }
if ! id -u "$service_user" >/dev/null 2>&1; then
  echo "Service user does not exist: $service_user (see deploy/systemd/README.md)" >&2
  exit 1
fi

# 見上面 readwrite_paths() 嗰段註解：呢步冇做嘅話，unit 一 start 就 226/NAMESPACE，
# 靜靜地死喺 ExecStart 之前。mode 0750 + owner=service_user 同 README 嘅手動步驟一致。
while read -r rw_path; do
  [[ -n "$rw_path" ]] || continue
  install -d -m 0750 -o "$service_user" -g "$service_user" "$rw_path"
done < <(readwrite_paths)

install -d -m 0755 "$unit_dir"
escaped_repo="${repo_root//|/\\|}"
escaped_env="${env_file//|/\\|}"

render_unit() {
  sed -e "s|/opt/cardz-market-cap|$escaped_repo|g" -e "s|/etc/cardz-market-cap/backend.env|$escaped_env|g" "$1" \
    | sed -e "s|User=cardz|User=$service_user|" -e "s|Group=cardz|Group=$service_user|"
}

render_unit "$service_template" > "$unit_dir/cardz-market-cap-daily.service"
install -m 0644 "$timer_template" "$unit_dir/cardz-market-cap-daily.timer"

# Template unit：唔使 enable，由 OnFailure= 按名觸發，但一定要喺 unit_dir 入面。
if ((notify_ok)); then
  render_unit "$alert_service_template" > "$unit_dir/cardz-market-cap-alert@.service"
fi

if ((install_watchdog)); then
  render_unit "$watchdog_service_template" > "$unit_dir/cardz-market-cap-watchdog.service"
  install -m 0644 "$watchdog_timer_template" "$unit_dir/cardz-market-cap-watchdog.timer"
fi

if ((install_freeze)); then
  render_unit "$freeze_service_template" > "$unit_dir/cardz-gemrate-freeze.service"
  install -m 0644 "$freeze_timer_template" "$unit_dir/cardz-gemrate-freeze.timer"
fi

render_unit "$web_service_template" > "$unit_dir/cardz-market-cap-web.service"
render_unit "$web_refresh_service_template" > "$unit_dir/cardz-market-cap-web-refresh.service"
render_unit "$candidate_service_template" > "$unit_dir/cardz-market-cap-candidate-refresh.service"
install -m 0644 "$candidate_timer_template" "$unit_dir/cardz-market-cap-candidate-refresh.timer"
render_unit "$retention_service_template" > "$unit_dir/cardz-market-cap-retention.service"
install -m 0644 "$retention_timer_template" "$unit_dir/cardz-market-cap-retention.timer"

systemctl daemon-reload

# install 預設只落 unit + daemon-reload。WSL writer 未通過兩次 soak、Windows task
# 未停之前，絕對唔可以因為「裝 unit」就自動變成第二個 writer。
if ((enable_units)); then
  systemctl enable cardz-market-cap-web.service
  systemctl enable cardz-market-cap-daily.timer
  if ((install_watchdog)); then
    systemctl enable cardz-market-cap-watchdog.timer
  fi
  if ((install_freeze)); then
    systemctl enable cardz-gemrate-freeze.timer
  fi
fi
if ((start_units)); then
  systemctl start cardz-market-cap-web.service
  systemctl start cardz-market-cap-daily.timer
  if ((install_watchdog)); then
    systemctl start cardz-market-cap-watchdog.timer
  fi
  if ((install_freeze)); then
    systemctl start cardz-gemrate-freeze.timer
  fi
fi
systemctl list-timers --no-pager 'cardz-market-cap-*' || true

# 最後一句一定要係壞消息。上面 list-timers 印出一版好靚嘅 timer 表，人見到就會
# 收工 —— 護欄缺失嘅警告如果淨係喺開頭出過一次，已經被 scroll 走咗。
if ((notify_ok)); then
  echo
  echo "OK: units installed with the failure notifier in place (enable=$enable_units start=$start_units)."
  echo "Candidate refresh and retention remain disabled; this installer never starts them."
else
  echo
  unguarded_banner >&2
fi
