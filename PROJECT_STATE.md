# PROJECT_STATE — CARDZ Market Cap

## Runtime authority

- Canonical MySQL: Windows `127.0.0.1:3308`.
- Operator runtime: WSL; it connects to the Windows-owned database.
- Local engineering frontend (`127.0.0.1:3800`): `CARDZ_DATA_MODE=live-db`; server-side direct read from Windows MySQL `127.0.0.1:3308` on every snapshot load. It never reads baked card data and never exposes DB credentials to the browser.
- AWS/public frontend: baked `data/public/seed-snapshot.json` plus referenced `data/public/market-assets/*.webp` only; it never connects to MySQL.
- Current operator entrypoints: `pipelines/operator_control.py`, `pipelines/collect_control.py`, `scripts/materialize_snapshot_assets.py`.

## Locked baseline

- Frontend source baseline: `14eb3fe74c19945d4c2cd421653361cb2587451f`.
- 025 generation: `product_subset_20260804T151703Z`.
- 025 snapshot SHA-256: `4edcf4444b32132894b4f6df09a39b800f14987e8a663cd1eea551ea1291b4d0`.
- Active universe: 762 cards. A later generation must not remove cards or historical dates from this baseline.

## 026

- Use the exact SNK EN international product identity and its default primary image when available.
- Never substitute a rejected, conflicting, JP, search-result, or other SNK image.
- Current canonical projection: 762 unique cards, with 741 exact SNK EN selections and 21 non-SNK accepted images.
- One Piece public Top 100: 97 exact SNK EN selections and 3 true fallbacks:
  - variant 128 (`OP11-118` Manga): no exact SNK EN product identity/page authority exists locally.
  - variant 958 (`OPCD-093` Gold DON!!): no exact SNK EN product identity/page authority exists locally.
  - variant 1450 (`OP01-016` AA Errata): SNK EN item 93521 is bound to `aa`, not the canonical `aa-errata` printing, so it must not be substituted.
- Migration 030 makes `operator_canonical_image_projection` one row per variant by selecting the single canonical `snkrdunk` image freeze for exact SNK EN lineage.
- All public surfaces reuse the same canonical `card.image` from the snapshot.
- The immutable 025 generation still contains all 762 cards and every referenced base/200/600 asset; its snapshot SHA-256 remains `4edcf4444b32132894b4f6df09a39b800f14987e8a663cd1eea551ea1291b4d0`.

There is no QC/finalizer/audit/runbook release layer. Build and run the direct snapshot artifact shown in `README.md`.

## 033 fast incremental baseline

- 033 fixes the canonical image projection lineage and is applied on Windows MySQL 3308.
- The retained fast daily lane is `snk_market_data.py` with one exact-ID worklist, 16 HTTP workers and zero delay; it does not open Chrome.
- Its input is ingested through the same file with `--ingest-jsonl`, then `operator_control.py db-tidy --project-ingested-history` rebuilds canonical price, market-cap and rank projection.
- `snkrdunk_bulk.py` (SNK API layer) and `collect_control.py` (stock/provider delta controller) remain retained project assets. They are not substitutes for the fast daily lane.

## Canonical name rule

- One card has one canonical full name: `catalog_variant.canonical_name` is the exact GemRate/PSA title completed with the exact canonical collector number. DB, PSA-facing UI and file identity use this same string; a shorter display-name layer is forbidden.

## 2026-08-07 release state

- Database projection generation: `db3308_ab0b51aa013eb50b`.
- Product snapshot generation: `product_subset_20260807T094818Z`.
- Public snapshot content SHA-256: `8e800a2ac69a03a4de6e8635075e37e75b3c2f42a6095d890af02471839e7ce8`.
- Active cohort: 762/762 product-ready, 0 gaps, 776 qualified backlog candidates.
- Migrations are implemented and materialized through 033; the local presentation remains FE02.
- FE02 health readback: 762 cards from Windows DB 3308 with build ID `fe02`.
