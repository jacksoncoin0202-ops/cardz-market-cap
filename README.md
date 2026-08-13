# CARDZ Market Cap

New era from **2026-08-03**.

## What it is

Qualified-pool market intelligence for PSA10 cards.

- Gate: GemRate PSA10 population >= 1000
- Maintain ~1k qualified identities only
- Full stock once per card, then daily incremental
- Engineering UI reads MySQL live
- Product UI reads freeze-qualified snapshot after pass/promote

## Start here

1. [AGENTS.md](AGENTS.md)
2. [docs/MODEL.md](docs/MODEL.md)
3. [docs/OPERATOR_DUAL_MODE.md](docs/OPERATOR_DUAL_MODE.md)
4. `pipelines/operator_control.py`

```bash
# WSL only
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap
python -X utf8 pipelines/operator_control.py status
```

## Layout

```text
apps/web/                 Next dual-mode frontend
packages/market-data/     public schema helpers
pipelines/                collectors + operator_control
pipelines/migrations/     DB schema
data/runtime/             DB env + operator exports
docs/                     new-era docs only
```

Old SOP docs were purged. DB and collectors remain.
