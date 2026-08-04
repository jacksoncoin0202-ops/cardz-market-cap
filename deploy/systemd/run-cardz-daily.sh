#!/usr/bin/env bash
set -euo pipefail

repo_root="${CARDZ_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
env_file="${CARDZ_ENV_FILE:-/etc/cardz-market-cap/backend.env}"

# One canonical writer owns active refresh, candidate scan, and gap export.
if [[ "${CARDZ_WRITER_LOCK_HELD:-0}" != "1" ]]; then
  writer_lock="$repo_root/data/runtime/cardz-writer.lock"
  mkdir -p "$(dirname "$writer_lock")"
  exec /usr/bin/flock --nonblock "$writer_lock" \
    /usr/bin/env CARDZ_WRITER_LOCK_HELD=1 bash "$0" "$@"
fi

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
  echo "CARDZ daily runner requires CARDZ_PYTHON or .venv-backend/bin/python" >&2
  exit 1
fi
if [[ ! -f "$repo_root/pipelines/operator_control.py" ]]; then
  echo "CARDZ new-era operator control plane is missing" >&2
  exit 1
fi

log_dir="$repo_root/data/runtime/logs"
mkdir -p "$log_dir"
log_file="$log_dir/operator_daily_$(date -u +%Y%m%d_%H%M%S).log"

# The operator command itself is fail-closed and persists per-adapter counts,
# checkpoints, freshness, and errors in data/runtime/operator/collect.
"$python_bin" -X utf8 "$repo_root/pipelines/operator_control.py" daily --refresh \
  2>&1 | tee -a "$log_file"
