# PROJECT_STATE — CARDZ Market Cap

> **2026-08-14：而家開代係 037 / FE04**（036 live PSA10 + BOX sidecar）。036 / FE03 係靚仔 fallback。契約：[docs/HANDOFF_037_FE04.md](docs/HANDOFF_037_FE04.md)。
> 036 每日鏈同閘仍然睇 [docs/HANDOFF_036_20260812.md](docs/HANDOFF_036_20260812.md)。
> 下面 025–035 係歷史記錄。

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

- One card has one PSA canonical name: `catalog_variant.canonical_name` is byte-for-byte the `description` of the unique GemRate raw `population_data` row whose `grader="psa"`. Never use GemRate's top-level `description`, never append/replace a collector number, and never reconstruct the string. Full collector number, language, set, printing and parallel remain structured fields.
- `catalog_psa_identity_acceptance` is current name authority. `catalog_official_name_acceptance` is retained history only.
- `operator_strict_source_identity` accepts only provider-native payload evidence; `evidence.type="database_lineage"` is never a current strict binding.

## 034 local PSA identity repair

- Migration `034_psa_source_identity_repair.mysql.sql` is applied only to local Windows MySQL `127.0.0.1:3308`; no AWS/public snapshot was rebuilt or published.
- The one full-catalog audit was completed from the read-only 70-card Sheet and 762 local GemRate raw receipts. Its initial inner join exposed 53 variants with no `catalog_printing_identity`; apply-time manifest completion records those variants as unresolved rather than omitting them.
- The repair transaction created 637 current literal-description acceptances and left 1,145 variants outside that new acceptance projection (`review`/`incomplete`). This does **not** mean those cards lack GemRate provenance: 1,100/1,145 already have a positive GemRate PSA10 POP observation and GemRate external ID. The missing item is the new gate's literal PSA-row-description material or a resolved printing/language comparison, not the upstream portfolio source.
- All 125 active variants outside the new acceptance projection have positive GemRate PSA10 POP, a GemRate binding and historical official-name acceptance. Their GemRate/portfolio lineage is confirmed; they must not be described as source-unknown or evidence-free.
- The transaction changed 375 stored canonical names, demoted 1,095 remaining exact `database_lineage` bindings, rejected 450 conflicting downstream bindings, and quarantined 46,939 price rows, 30,575 sale rows, 668 public image pointers and 753 freezes.
- Product readiness is intentionally fail-closed until provider-native identities and the unresolved printing/language conflicts are established. Do not restore the 033 product projection or publish a new snapshot from historical bindings.
- The one permitted integrated validator run is FAIL: 637/762 active variants have new literal-description acceptance. The remaining 125/762 are still confirmed GemRate portfolio members with positive PSA10 POP; they failed the newly introduced literal-name/printing comparison gate and are not missing GemRate provenance. Passed invariants include 70/70 Sheet mapping, all six green controls, literal PSA description/hash equality for accepted rows, zero top-level authority, one current name per payload, 1,782 unique classifications, zero strict `database_lineage`, and migration ledger 034. The validator's original red-product query counted catalog-shell rows rather than `product_ready=1`; its source is corrected but was not rerun. The same run proved the 13 red variants have zero ready prices, non-quarantined sales, public image pointers and accepted freezes.

## 035 active identity and GemRate provenance resolution

- Migration `035_gemrate_provenance_psa_identity_resolution.mysql.sql` is ledgered on local Windows MySQL `127.0.0.1:3308`. It creates a distinct immutable GemRate POP provenance acceptance; a raw-capture receipt is not counted as source coverage.
- All 762 active variants now have a literal PSA raw acceptance, positive native GemRate PSA10 POP provenance, one exact GemRate binding and a complete structured printing hash. The former 125 active blockers were resolved as concrete identity work: 63 name changes, 22 language changes, 38 set-name changes, 168 parallel completions and 14 superseded GemRate bindings.
- Redirected GemRate IDs retain both roles explicitly: the current ID owns POP provenance/binding while the settled provider entity ID identifies the raw `/card-details` payload. PSA's unnumbered Gold DON!! row retains an empty PSA card number; `OPCD-093` remains the structured collector number and is not appended to its canonical name.
- Non-GemRate prices, sales, images and freezes quarantined by 034 remain fail-closed. Migration 035 does not infer their identities from GemRate. No AWS/public snapshot was rebuilt or published; the published 033 artifact remains unchanged.
- The first 035 validator run exposed an omitted `parallel_code` UPDATE for 168 rows and an incorrect expectation that all 1,782 variants had printing rows despite 53 known non-active unresolved variants lacking one. The same immutable manifest amended exactly 168 parallel rows and the catalog invariant was corrected. DADDY then explicitly removed the one-validation restriction; the rerun returned PASS: 762/762 literal PSA names, 762/762 positive POP provenance, 762/762 strict GemRate bindings, zero name/language/collector/set/parallel/hash conflicts, zero ambiguous exact GemRate bindings, zero strict `database_lineage`, 70/70 Sheet mapping, all six green controls and full 13-red-card quarantine.

## 2026-08-07 release state

- Database projection generation: `db3308_ab0b51aa013eb50b`.
- Product snapshot generation: `product_subset_20260807T094818Z`.
- Public snapshot content SHA-256: `8e800a2ac69a03a4de6e8635075e37e75b3c2f42a6095d890af02471839e7ce8`.
- Active cohort: 762/762 product-ready, 0 gaps, 776 qualified backlog candidates.
- Migrations are implemented and materialized through 033; the local presentation remains FE02.
- The 2026-08-07 release snapshot above remains the last public artifact. Local DB schema is now through 035, but 034/035 have not been published.
- FE02 health readback: 762 cards from Windows DB 3308 with build ID `fe02`.

## 036 / FE03 — 現狀（2026-08-12）

- Generation **036**，presentation **FE03**。2026-08-14 起 036 係 fallback；開代係 **037 / FE04**（只加 BOX）。
- Active universe **1322** 張（唔再係 762）。有 PriceCharting 身份 993 張；英文 919/919 = 100%；322 張日文卡 PC 冇貨。
- Migration 落到 **042**（`042_sale_observation_listing_evidence.mysql.sql`）。
- 每日三個自動 slot（JST）：03:30 夜鏈 HTTP lanes → 09:30 朝鏈 browser lanes + bake + push `[deploy]` → 11:30 / 16:30 純重試。
- 真身 tree 係 `cardz-market-cap-fe-db-20260805`；release 由 WSL `~/cardz-market-cap-release-daily` 行，**只睇得到 `origin/main`**。
- 🔴 **2026-08-31 有一個已知失效**：`MAX_CURRENT_PRICE_AGE_DAYS = 30` 細過 PriceCharting 月線週期，
  嗰日約 70% 卡會一齊失去排名，而四層閘全部接唔住、receipt 照寫正常。詳情同修法見 handoff §7。

## 037 / FE04 — 現狀（2026-08-14）

- 開代：**037 / FE04** = live 036 PSA10 + BOX sidecar。唔開 `rebuild_037`。
- PSA10：`generation=db3308_b0cb6e76228b4a99` · 1368 張 · `sealedInSeed=false`。
- BOX：`data/public/box-subset.json` · 307／275／307 · 公開路徑 `/box`（`/sealed` 只 308）。
- 內頁：跟主站 `.detail-art`（桌面 sticky／手機 relative、無 lightbox）；askFloor `wide-metric`；`/box/[id]` Product + BreadcrumbList。
- Daily：`sync_public_release_assets.py` 要保留 sidecar 圖；commit 訊息 `release: daily CARDZ 037 FE04 $generation [deploy]`。
- Fallback：拎走 overlay／nav／`/box` 即返 036／FE03；seed 唔使改。
- **Live 已確認（2026-08-14）**：`3aef760a` · health `product=037` · `/box` 200 · `/sealed` 308。Receipt：`data/public/037-fe04-receipt.json`。契約：[docs/HANDOFF_037_FE04.md](docs/HANDOFF_037_FE04.md)。

**其餘一切（演化史、六個食過嘅虧、閘 tier 分級、未完成清單、硬規矩）一律以
[docs/HANDOFF_036_20260812.md](docs/HANDOFF_036_20260812.md) 為準。**
