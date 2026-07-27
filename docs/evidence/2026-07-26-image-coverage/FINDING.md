# Card image coverage: what is actually missing, and why QC looked far worse

**Measured** 2026-07-26 16:35–16:43 local, re-confirmed 17:22 local (identical output).
**Branch** `ui-experiments-20260725`, working tree uncommitted.
**Access** read-only. Every producer here is `SELECT`-only and writes nothing to the DB.

## Conclusion

Three sentences, in the order they matter:

1. **12 ranked cards genuinely have no publishable image** — not 4. An earlier
   report circulated the number 4; that was a false alarm in the *optimistic*
   direction, and the 4 cards it named are publishing normally.
2. **The catastrophic-looking QC coverage gap is an artifact of `set_name`
   drift, not of missing files.** 331 of 419 QC-passing images (79%) fail to
   join the current catalog because `opaque_id` is derived from `set_name`, and
   207 cards have a different `set_name` in the presentation pack than in the
   DB. The bytes are on disk; the key moved out from under them.
3. **Nothing in the ranking is asset-less.** All 181 ranked cards without a QC
   image do have a `market_image_asset` row — and *zero* of those private SHAs
   are a QC-passing image. So the two populations are disjoint by construction,
   which is the signature of a keying failure, not a coverage failure.

## How to reproduce

From repo root. All three read the same DB and the committed snapshot; they
write fresh copies into `temp/`, leaving the frozen ones here untouched.

```bash
cd "C:/Users/jackson0202/Documents/Playground/cardz-market-cap"
set -a && . data/runtime/config/backend.env && set +a

python -X utf8 docs/evidence/2026-07-26-image-coverage/img_gap_audit.py
python -X utf8 docs/evidence/2026-07-26-image-coverage/img_skip_diagnose.py
python -X utf8 docs/evidence/2026-07-26-image-coverage/img_evidence2.py
```

> These three scripts were promoted out of `temp/` on 2026-07-26. The only edit
> made to them was `ROOT = ...parents[1]` → `parents[3]`, forced by the change
> in directory depth. Logic is untouched.

## The numbers

Measured by `img_gap_audit.py`:

| | count | meaning |
|---|---:|---|
| QC records total | 623 | rows in `manifests/image-qc.json` |
| QC passing the public gate | 419 | eligible to be published |
| Cards in the ranking | 255 | snapshot 19, all distinct `opaque_id` |
| QC ∩ ranking | **74** | ranked cards served by a QC image |
| Ranked, no QC image | **181** | looks like a coverage hole |
| QC orphaned from catalog | **331** (79% of QC) | publicId matches no current `opaque_id` |
| QC in catalog but unranked | 14 | |
| QC alive in catalog | 88 | |
| Ranked-miss **with** a private asset | **181** | i.e. all of them |
| Ranked-miss **without** any asset | **0** | ← the tell |
| Ranked-miss whose private SHA is a QC image | **0** | ← the other tell |

Catalog and asset premises at measurement time — these rot, so they are stamped:

- `catalog_variant` held 1,705 rows.
  <!--@verified 2026-07-26 id=evidence.img.catalog_variant expect>=1705
      sql=SELECT COUNT(*) FROM catalog_variant-->
- `market_image_asset` held 456 rows across 453 distinct variants.
  <!--@verified 2026-07-26 id=evidence.img.asset_rows expect>=456
      sql=SELECT COUNT(*) FROM market_image_asset-->
  <!--@verified 2026-07-26 id=evidence.img.asset_variants expect>=453
      sql=SELECT COUNT(DISTINCT variant_id) FROM market_image_asset-->

`expect>=` is deliberate: these are monotonic counters and an exact match would
report DRIFT the next time a card is added, which trains people to ignore the
report. Use `expect=` only for values that genuinely should not move.

## The 12 genuinely missing

`img_skip_report.json` — every entry is `reason: "image_unavailable"`.

- 11 One Piece, 1 Pokémon (`Pikachu`, `153/SV-P`, ja).
- **All 12 have `has_private_asset: true`.** The local bytes exist; each entry
  carries its `assets[].sha` and the `g10/full/...` source key.
- All 12 have `collector_complete: true` and a well-formed `printing_key`, and
  all 12 have `pack_key_n: 0` — no presentation-pack entry keyed to them.

So this is a wiring gap, not a sourcing gap. Nobody needs to go find these
images; they need to be keyed correctly.

## The 207 drift pairs

`img_drift_pairs2.json` — side-by-side `pack` vs `db` for every drifted card.

- 207 pairs, and in **all 207** `pack_set != db_set`.
- **`repro_ok` is `true` for all 207**: the QC `publicId` reproduces exactly
  from the pack's own field values. The hash function is not broken and the QC
  file is not corrupt — the *input* changed.
- Typical shape: pack says `2018 Sun and Moon Tag Bolt Japanese`, DB says
  `SM9: Tag Bolt`. Same card, different naming convention.
- `collector` and `tcg`/`lang` are unchanged in the sampled pairs; `set_name`
  is carrying the drift on its own.

### Caveat that will bite you

`img_evidence2.py` **does not emit rows in a stable order.** Two consecutive
runs produced byte-different files containing the identical 207 pairs. Do not
diff this file raw to check freshness — sort by `qc_publicId` first:

```bash
python -X utf8 -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(len(d), sorted(x['qc_publicId'] for x in d)[:3])" temp/img_drift_pairs2.json
```

## Premises — what was true when this was measured

- Snapshot 19 was the ranking source; 255 cards, top-100-per-scope shape.
- `catalog_printing_identity` was **0 rows**, so no identity convergence layer
  was collapsing the drifted `set_name` values.
  <!--@verified 2026-07-26 id=evidence.img.printing_identity ttl=14 expect=0
      sql=SELECT COUNT(*) FROM catalog_printing_identity-->
- `market_identity_review_queue` held 203 pending rows — the drift these
  producers measured is very likely the same population sitting unreviewed in
  that queue. **Not verified as the same set; do not restate it as fact.**
  <!--@verified 2026-07-26 id=evidence.img.review_queue expect>=203
      sql=SELECT COUNT(*) FROM market_identity_review_queue-->
- `manifests/image-qc.json` was mid-churn that day (a backup exists at
  `temp/image-qc.backup-20260726-074624.json`). The 623/419 split is as of the
  16:35 read.

## What this finding does not establish

- It does not prove the 207 drifted cards are the *cause* of the 331 orphans,
  only that both are consistent with `set_name` drift and the arithmetic lines
  up. Nobody has joined the two sets row-by-row.
- It says nothing about whether the DB `set_name` or the pack `set_name` is the
  correct one. That is a product decision, not a measurement.
- It measures the ranked 255 only. Coverage across the full 1,705-row catalog
  was not assessed.
