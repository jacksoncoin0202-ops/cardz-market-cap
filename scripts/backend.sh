#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${CARDZ_PYTHON:-}" ]]; then
  python_bin="$CARDZ_PYTHON"
else
  python_bin=""
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
      python_bin="$candidate"
      break
    fi
  done
fi

if [[ -z "$python_bin" ]]; then
  echo "CARDZ backend requires Python 3.10 or newer" >&2
  exit 1
fi

exec "$python_bin" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/backend.py" "$@"
