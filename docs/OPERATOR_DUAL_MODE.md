# CARDZ Operator Dual Mode

## Engineering

- `CARDZ_DATA_MODE=operator`
- WSL operator 由 canonical MySQL 生成 engineering snapshot。
- 用途：維護 active pool、candidate、gap、freeze、daily incremental。

## Product

- `CARDZ_DATA_MODE=product`
- Frontend 只讀 baked `data/public/seed-snapshot.json`，永不直連 MySQL。
- Product surface：Top 100 Market Cap + watchlist；已取消 Graders route/nav 同 Grading Pulse。

## Pass contract

```bash
PY=/home/jackson0202/cardz-market-cap/.venv-backend/bin/python
"$PY" -X utf8 pipelines/operator_control.py daily --refresh --pass
```

已完成 refresh 而只獲批准重生 presentation pass：

```bash
"$PY" -X utf8 pipelines/operator_control.py daily \
  --refresh-report data/runtime/operator/pass_receipt.json --pass
```

Pass 必須綁定：

- active 762 全部 product-ready、gap 0；
- 776 qualified candidates 留 backlog；
- 五個 adapter 成功與 freshness；
- 實際 market-cap Top 100 每卡 SNK-first image decision；
- `product-top100-no-graders-v1` 完整 frontend bundle hash。

## Promote／release

`promote-product-subset` 只把同一 snapshot + pass receipt 變成 production candidate，唔 deploy。Materialize 後，`bake_tonight_release.py` deterministic mirror pass-bound frontend/runtime，同時只帶 snapshot 引用圖片。完整唯一順序見：

- `docs/ONE_TIME_0_TO_1_RUNBOOK.md`
- `docs/FAST_E2E_RELEASE_RUNTIME.md`

DADDY visual approve 前禁止 commit、push、webhook、live deploy。
