#!/usr/bin/env bash
set -euo pipefail

action="dry-run"
repo_root="/opt/cardz-market-cap"
unit_dir="/etc/systemd/system"
env_file="/etc/cardz-market-cap/backend.env"
service_user="cardz"

usage() {
  cat <<'EOF'
Usage: deploy/linux/cardz-daily-systemd.sh [install|status|uninstall|dry-run]
  [--repo-root PATH] [--unit-dir PATH] [--env-file PATH] [--user NAME]

The default action is dry-run and does not alter systemd or the repository.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    install|status|uninstall|dry-run) action="$1" ;;
    --repo-root) repo_root="$2"; shift ;;
    --unit-dir) unit_dir="$2"; shift ;;
    --env-file) env_file="$2"; shift ;;
    --user) service_user="$2"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

repo_root="$(cd "$repo_root" && pwd)"
template_dir="$repo_root/deploy/systemd"
service_template="$template_dir/cardz-market-cap-daily.service"
timer_template="$template_dir/cardz-market-cap-daily.timer"
runner="$template_dir/run-cardz-daily.sh"
for required in "$service_template" "$timer_template" "$runner" "$repo_root/scripts/backend.py"; do
  [[ -f "$required" ]] || { echo "Missing required file: $required" >&2; exit 1; }
done

if [[ "$action" == "status" ]]; then
  systemctl status --no-pager cardz-market-cap-daily.service cardz-market-cap-daily.timer || true
  exit 0
fi

if [[ "$action" == "uninstall" ]]; then
  [[ "$(id -u)" == "0" ]] || { echo "uninstall requires root" >&2; exit 1; }
  systemctl disable --now cardz-market-cap-daily.timer || true
  rm -f "$unit_dir/cardz-market-cap-daily.service" "$unit_dir/cardz-market-cap-daily.timer"
  systemctl daemon-reload
  exit 0
fi

if [[ ! -f "$env_file" ]]; then
  echo "Missing environment file: $env_file" >&2
  exit 1
fi
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
  printf 'action=dry-run\nrepo_root=%s\nenv_file=%s\nunit_dir=%s\nuser=%s\nschedule=06:30 Asia/Tokyo\nentrypoint=scripts/backend.py daily\n' \
    "$repo_root" "$env_file" "$unit_dir" "$service_user"
  exit 0
fi

[[ "$(id -u)" == "0" ]] || { echo "install requires root" >&2; exit 1; }
install -d -m 0755 "$unit_dir"
escaped_repo="${repo_root//|/\\|}"
escaped_env="${env_file//|/\\|}"
sed -e "s|/opt/cardz-market-cap|$escaped_repo|g" -e "s|/etc/cardz-market-cap/backend.env|$escaped_env|g" "$service_template" \
  | sed -e "s|User=cardz|User=$service_user|" -e "s|Group=cardz|Group=$service_user|" \
  > "$unit_dir/cardz-market-cap-daily.service"
install -m 0644 "$timer_template" "$unit_dir/cardz-market-cap-daily.timer"
systemctl daemon-reload
systemctl enable cardz-market-cap-daily.timer
systemctl start cardz-market-cap-daily.timer
systemctl status --no-pager cardz-market-cap-daily.timer
