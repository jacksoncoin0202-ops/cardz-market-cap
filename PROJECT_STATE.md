# PROJECT_STATE — CARDZ Market Cap (New Era pointer)

> 舊 QC 時代數字已封存，唔再係日常真相。
> Archived: [docs/archive/PROJECT_STATE_STALE_QC_20260729.md](docs/archive/PROJECT_STATE_STALE_QC_20260729.md)

## Live product pointer（2026-08-14）

呢棵樹**唔係 live**。唔准 pass／bake／`[deploy]`。

- Live：`https://app.cardzmarketcap.com`
- 真身：`../cardz-market-cap-fe-db-20260805`
- 開代：**037 / FE04** = 036 PSA10 + BOX sidecar（`/box`）
- Live 已確認（2026-08-14）：health `product=037` · `presentation=FE04` · `/box` 200
- PSA10 每日自動成功閘：連續兩個 JST 日排程自己對到 live 先算。人手唔計。`proven` 見 fe-db `data/runtime/operator/daily_chain_autonomy.json`。
- Fallback：036 / FE03
- 契約：`../cardz-market-cap-fe-db-20260805/docs/HANDOFF_037_FE04.md`

## Authority now

Daily truth is **operator live MySQL**, not the old release-gate QC envelope.

```bash
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap
/home/jackson0202/cardz-market-cap/.venv-backend/bin/python -X utf8 pipelines/operator_control.py status
```

Core docs:

- [docs/MODEL.md](docs/MODEL.md)
- [docs/OPERATOR_DUAL_MODE.md](docs/OPERATOR_DUAL_MODE.md)
- [docs/DB_NEW_ERA.md](docs/DB_NEW_ERA.md)
- [docs/ONE_TIME_0_TO_1_RUNBOOK.md](docs/ONE_TIME_0_TO_1_RUNBOOK.md)
- `pipelines/operator_control.py`

## Operator UI

- http://localhost:3800 (`CARDZ_DATA_MODE=operator`)
- Snapshot export: `data/runtime/operator/operator-snapshot.json`
- Product subset (after DADDY pass only): `data/runtime/operator/product-subset-snapshot.json`

## Do not use

- Old `scripts/render_project_state.py` QC release-gate numbers as daily status
- Old generation / QC receipt blockers as operator freeze truth

## Product surface (2026-08-03)

- Product = **Top 100 Market Cap** + **daily PSA10 POP chase**.
- Universe update: POP **>= 1000**.
- Single-card POP growth = derived from daily POP points (not multi-grader history project).
- Drop multi-grader Grading Pulse as product work.
- Product frontend policy `product-top100-no-graders-v1`: no Graders nav/route, Grading Pulse, grader share, or card-detail multi-grader panel.
- Product pass image policy `displayed-top100-snk-public-exact-first-v1`: recompute current market-cap Top 100; every eligible exact/public-approved SNK raw-front must be selected. Rank 101+ keeps its accepted freeze image.
- Pass receipt binds the complete current frontend bundle hash; clean release is a deterministic mirror of that bundle, not a hand-picked set of files.
- Daily truth: operator live MySQL via `operator_control.py`.
