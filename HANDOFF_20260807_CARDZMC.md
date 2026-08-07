# CARDZ Market Cap release report — 2026-08-07

## Final state

- Windows MySQL authority: `127.0.0.1:3308`.
- WSL operator runtime completed the canonical end-to-end refresh.
- Local engineering frontend: FE02 standalone, direct Windows-3308 reads on `127.0.0.1:3800`.
- Public/AWS artifact: baked `data/public/seed-snapshot.json` plus referenced `data/public/market-assets/*.webp`.
- Active universe: 762 cards. Candidate backlog: 776. Active gaps: 0.
- Database projection generation exposed by FE02: `db3308_ab0b51aa013eb50b`.
- Product snapshot generation: `product_subset_20260807T094818Z`.
- Public snapshot content SHA-256: `8e800a2ac69a03a4de6e8635075e37e75b3c2f42a6095d890af02471839e7ce8`.

## Completed E2E

The authorized canonical command completed successfully in WSL:

```bash
python3 -X utf8 pipelines/operator_control.py daily --pass --refresh
```

Result:

- Exit code: 0.
- Six required adapters completed.
- PriceCharting full worklist: 306/306 through one browser session with 4-second pacing.
- Collector totals: 1,356 processed, 744 inserted, 1,356 checkpointed, 0 failed.
- Product-ready: 762/762.
- Top 100: 100. Watchlist: 662.
- Pass receipt SHA-256: `ea2d2a549a18009d5c246caed35c9819febf95b2d996a575bd7d326a406d64f0`.

The passed snapshot was promoted through the existing `promote-product-subset`
entrypoint and materialized through `scripts/materialize_snapshot_assets.py`.
The public artifact contains 762 image identities and 2,286 ready asset files.

## Source-identity corrections retained

- Variant 958 PriceCharting binding is the exact product `11018417` for the Gold
  DON!! card. The unrelated Dodgers promo `13256449` remains rejected.
- Variant 426 evidence was revalidated by the repository's exact PriceCharting
  validator as accepted for product `3216328`, collector `TG29`, explicit PSA10
  field and observed date. Only its refreshed artifact SHA changed; the provider
  identity and price fact did not change.
- PSA/GemRate canonical names retain the full physical collector number.

## Migration and frontend level

- Repository migrations are implemented through `033_canonical_projection_binding_lineage.mysql.sql`.
- The successful E2E rebuilt the live canonical projection on that 033 path.
- Frontend remains FE02. No FE03 fork or static Vue replacement was introduced.
- FE02 production build completed with Next.js 16.2.11 and TypeScript success.
- The only build warning is Next.js' middleware-to-proxy deprecation notice.

## Live readback

The single post-restart health readback returned:

```json
{
  "status": "ok",
  "generation": "db3308_ab0b51aa013eb50b",
  "cards": 762,
  "surfaces": {
    "tcgTop100": 100,
    "pokemonTop100": 100,
    "onePieceTop100": 100,
    "tcg101To300": 200
  },
  "build": "fe02",
  "dataMode": "windows-db-3308",
  "databasePort": 3308
}
```

## Manual review sheet

- https://docs.google.com/spreadsheets/d/1a8cKz1fm-Ns7u3JrwnWqpWHqXd4HRn1qUVNeSypad9k/edit

## Release boundary

The release commit intentionally includes the new source/frontend/public artifact
and the approved large deletion set. Private provider pages, runtime receipts,
collector caches, Python caches and secrets are not release content and remain
outside Git.
