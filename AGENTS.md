# CARDZ Market Cap — Operator Entry（New Era 2026-08-04）

Runtime：**WSL Ubuntu only**。

Daily authority only：

1. `AGENTS.md`
2. `docs/MODEL.md`
3. `docs/OPERATOR_DUAL_MODE.md`
4. `pipelines/operator_control.py`

舊 SOP／funnel／policy 文件唔係 daily authority。

## Product split

| Surface | Mode | Data | Goal |
|---|---|---|---|
| Engineering | `CARDZ_DATA_MODE=operator` | live MySQL | instant agent feedback |
| Product | `CARDZ_DATA_MODE=product` | freeze-qualified baked snapshot | after DADDY pass |

## Core model（locked）

- Product = Top 100 Market Cap + watchlist 101+；daily chase current PSA10 POP level。
- GemRate PSA10 POP 係 universe gate；POP >= 1000 進 candidate path，唔做跌穿 1000 delist。
- Maintain qualified pool；每張卡一次 full stock，freeze-complete 後 daily incremental。
- Multi-grader Grading Pulse／grader routes 唔係 product surface；frontend 禁止 Graders nav/route/pulse。
- G10 可供 identity clues、images、price、sales、market-cap facts；只禁 `g10_kline`。
- Identity 必須分 language、set/box、collector、edition/parallel/finish；唔同 TCG 唔混。
- Accepted links/freeze 唔 batch reopen；錯 link 要有原因先 unbind/rebind。
- Price/sales pull exact-bound data、unitize lots、drop extreme outliers；SNK history用 deep API。
- Accepted freeze discovery image priority：G10 > SNK > TCGplayer language-aware。RAW only，human accept once。
- Product-pass exception：用 pass 當刻 price × current PSA10 POP 重排實際 Top 100；每張有 exact/public-approved SNK raw-front 就必須用 SNK，冇 eligible SNK 先保留 accepted freeze；101+ 不 override。
- Frontend release authority：先由修改時間辨認包含最新意圖嘅 source tree，再由 pass 綁完整 managed frontend bundle hash。Bake deterministic mirror，唔准手揀舊/新檔混合。

## Daily commands

```bash
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap
PY=/home/jackson0202/cardz-market-cap/.venv-backend/bin/python

$PY -X utf8 pipelines/operator_control.py status
$PY -X utf8 pipelines/operator_control.py export-gaps
$PY -X utf8 pipelines/operator_control.py scan-candidates
$PY -X utf8 pipelines/operator_control.py daily --refresh --pass
$PY -X utf8 pipelines/operator_control.py promote-product-subset
```

只在 DADDY 明示批准 presentation-only pass regeneration、已有 completed five-adapter receipt 時：

```bash
$PY -X utf8 pipelines/operator_control.py daily \
  --refresh-report <completed-receipt.json> --pass
```

## Freeze

```bash
$PY -X utf8 pipelines/operator_control.py accept-binding --variant-id <ID> --kind identity
$PY -X utf8 pipelines/operator_control.py accept-binding --variant-id <ID> --kind source --source-code snkrdunk
$PY -X utf8 pipelines/operator_control.py accept-binding --variant-id <ID> --kind image
```

## Keep / kill

Keep：

- MySQL `cardz_market_cap`
- migrations
- harvest/bind/ingest collectors under `pipelines/`
- `operator_control.py`
- dual-mode web loader

Kill from daily path：

- A01–A12 funnel
- generate-docs control plane
- old policy/runbook mountains
- image VLM factory as daily SOP
- any batch reopening of accepted freezes

Non-daily recoverables：`pipelines/_archived_non_daily/`、`config/_archived/`。

## Artifacts

- `data/runtime/operator/candidates.json`
- `data/runtime/operator/human_attention.json`
- `data/runtime/operator/daily_summary.json`
- `data/runtime/operator/pass_receipt.json`
- `data/runtime/operator/product-subset-snapshot.json`
- `data/runtime/operator/frontend-bundle-receipt.json`

## DB tidy

```bash
$PY -X utf8 pipelines/operator_control.py db-tidy
```

Writes warehouse schema + quarantines banned `g10_kline` price authority。Details：`docs/DB_NEW_ERA.md`。

## Pull-first warehouse write

```bash
$PY -X utf8 pipelines/warehouse_write.py --variant-id ID --source-code snkrdunk --kind listing --payload-json @payload.json
```

Exact-bound only。Banned：`g10_kline`。
