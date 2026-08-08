# Cardz Market Cap

Cardz Market Cap has two explicit data modes.

The local engineering frontend on port `3800` always reads the Windows MySQL
database at `127.0.0.1:3308` from server-side code. It does not read a baked
snapshot and never sends database credentials to the browser.

```bash
CARDZ_DATA_MODE=live-db npm run dev -- --hostname 127.0.0.1 --port 3800
```

The AWS/Node production runtime reads exactly:

- `data/public/seed-snapshot.json`
- `data/public/market-assets/*.webp`

AWS does not connect to MySQL and does not require QC receipts, release capsules,
operator scripts, browser collectors, or an earlier generation.

## cardzmc 資料入口

保留所有已累積的採集資產，但日常快速更新只走一條已實測的資料路徑：

```text
exact SNK ID worklist
  -> pipelines/snk_market_data.py (16 workers, HTTP only)
  -> --ingest-jsonl 寫入 Windows MySQL 3308
  -> pipelines/operator_control.py db-tidy --project-ingested-history
```

- `snkrdunk_bulk.py` 是 SNK API 共用層，供 `snk_market_data.py` 使用，保留。
- `snk_market_data.py` 是日常 exact PSA10 價格、成交及本機 harvest 疊加入口。
- PSA 身份修正入口係 `pipelines/psa_identity_repair.py`；active 762 resolution 入口係 `pipelines/resolve_active_psa_identity.py`。`canonical_name` 只可係 GemRate raw `population_data` 唯一 PSA row 嘅原文 `description`；完整卡號只保留喺 structured field。GemRate source coverage 必須來自獨立正數 PSA10 POP observation acceptance，receipt coverage 唔係 source coverage；任何 `database_lineage` binding 都唔可以進 `operator_strict_source_identity`。
- `collect_control.py` 保留作 stock／provider binding 的增量控制；不混入以上快速日常路徑。
- `operator_control.py` 保留作 canonical projection rebuild 和產品 snapshot 操作。

以上係角色分工，唔係刪減。垃圾桶內未找到較新或較快的同名版本；現行
`snk_market_data.py` 是唯一已實測可用的快速入口。

## Baked production build

```bash
npm ci
CARDZ_PUBLIC_BUILD_ID=local CARDZ_BUILD_TARGET=node npm run build
MARKET_DATA_SNAPSHOT_PATH="$PWD/data/public/seed-snapshot.json" \
  CARDZ_PUBLIC_BUILD_ID=local \
  npm --workspace @cardz/web run start -- --hostname 127.0.0.1 --port 3800
```

## AWS container

```bash
docker compose up --build -d
```

The container includes the built frontend, the single snapshot, and its referenced
market assets. Replace those data files in a new commit when publishing a newer
generation.

Production GitHub push → AWS pull-deploy setup and the day-to-day release command are
documented in [`AWS_GITHUB_PULL_DEPLOY.md`](AWS_GITHUB_PULL_DEPLOY.md).
