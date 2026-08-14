#!/usr/bin/env bash
# AWS helper after `git pull`. Rebuild only when FE / lockfile / image recipe changed.
# Data-only (seed / box sidecar / webp) → compose up --no-build + restart.
# Webhook 而家仍然 `docker compose up --build`。換呢條先至會食到 data-only 捷徑。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! git rev-parse --verify ORIG_HEAD >/dev/null 2>&1; then
  echo "compose_up_needed: no ORIG_HEAD; rebuilding" >&2
  exec docker compose up -d --build
fi

changed="$(git diff --name-only ORIG_HEAD HEAD || true)"
if echo "$changed" | grep -Eq '^(apps/web/|packages/|package-lock\.json|package\.json|apps/web/Dockerfile|compose\.yaml|Dockerfile)'; then
  echo "compose_up_needed: FE/lockfile changed; --build"
  exec docker compose up -d --build
fi

echo "compose_up_needed: data-only; restart without next build"
docker compose up -d --no-build
exec docker compose restart web
