#!/usr/bin/env bash
set -euo pipefail

repo_root="${CARDZ_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
web_unit="${CARDZ_WEB_UNIT:-cardz-market-cap-web.service}"
health_url="${CARDZ_WEB_HEALTH_URL:-http://127.0.0.1:3000/api/health}"
pointer_path="${MARKET_DATA_POINTER_PATH:-$repo_root/data/runtime/publish-staging/latest.json}"

if [[ ! "$web_unit" =~ ^[A-Za-z0-9@_.:-]+\.service$ ]]; then
  echo "CARDZ web unit name is invalid" >&2
  exit 2
fi
if [[ ! -r "$pointer_path" ]]; then
  echo "CARDZ runtime snapshot pointer is missing" >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "CARDZ web refresh requires node on PATH" >&2
  exit 1
fi

systemctl restart "$web_unit"
node "$repo_root/apps/web/scripts/verify-runtime-health.mjs" \
  --pointer "$pointer_path" \
  --url "$health_url" \
  --attempts 30 \
  --delay-ms 1000
