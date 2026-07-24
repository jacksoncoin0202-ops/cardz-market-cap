#!/usr/bin/env bash
set -euo pipefail

repo_root="${CARDZ_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
env_file="${CARDZ_ENV_FILE:-/etc/cardz-market-cap/backend.env}"
mode="${CARDZ_DAILY_MODE:-production}"

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

exec "$python_bin" -X utf8 "$repo_root/scripts/backend.py" daily --external-db --mode "$mode"
