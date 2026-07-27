# board-gaps — missing images and missing stories across the three boards

**Measured** 2026-07-27, against the live database and the working tree.
**Agent** `opus-board-gaps`. **Read-only**: nothing was written to
`data/public/market-assets`, `apps/web/public/market-assets`, the database, or
`boards.json`. Everything produced by this task lives in this directory.

## Conclusion, one sentence

Of the 265 cards on the three boards, **22 have no card image on disk at all**
and **28 have no story in any language** — and those two gaps are the same
cohort, 19 cards deep in overlap, concentrated on `op100`, which is split
exactly in half: 20 rows complete, 20 rows never ingested.

Two further defects sit behind that headline and are larger: **169 of 265 have
an image that is not the standard 429×600 canvas**, and **70 of 265 have a
public snapshot pointing at an image file that no longer exists on disk**.

## The numbers

| Board | rows | std 429×600 OK | non-std canvas | no image file | no story in any language | snapshot points at a deleted file |
|---|---|---|---|---|---|---|
| `op100` | 40 | 20 (50%) | 0 | **20 (50%)** | **20 (50%)** | 25 |
| `ptcg100` | 100 | 20 (20%) | **78** | 2 (2%) | 8 (8%) | 20 |
| `tcg300` (universe) | 265 | 74 (28%) | **169** | **22 (8%)** | **28 (11%)** | **70** |

`op100` and `ptcg100` fail in opposite directions. `op100` has no middle tier at
all — a card is either fully normalized or entirely absent. `ptcg100` is almost
fully populated but 78% of it is stuck on the pre-normalization canvas.

<!--@verified 2026-07-27 id=evidence.boardgaps.universe expect=265 ttl=14
    cmd=python -X utf8 docs/evidence/2026-07-27-board-gaps/produce_board_gaps.py --counter universeSize-->
<!--@verified 2026-07-27 id=evidence.boardgaps.missing_image expect=22 ttl=14
    cmd=python -X utf8 docs/evidence/2026-07-27-board-gaps/produce_board_gaps.py --counter missingImageCount-->
<!--@verified 2026-07-27 id=evidence.boardgaps.nonstd_canvas expect=169 ttl=14
    cmd=python -X utf8 docs/evidence/2026-07-27-board-gaps/produce_board_gaps.py --counter nonStdCanvasCount-->
<!--@verified 2026-07-27 id=evidence.boardgaps.no_english_story expect=28 ttl=14
    cmd=python -X utf8 docs/evidence/2026-07-27-board-gaps/produce_board_gaps.py --counter missingEnglishStoryCount-->
<!--@verified 2026-07-27 id=evidence.boardgaps.translation_only expect=1 ttl=14
    cmd=python -X utf8 docs/evidence/2026-07-27-board-gaps/produce_board_gaps.py --counter missingTranslationOnlyCount-->
<!--@verified 2026-07-27 id=evidence.boardgaps.current_key_reachable expect=74 ttl=14
    cmd=python -X utf8 docs/evidence/2026-07-27-board-gaps/produce_board_gaps.py --counter imageReachableViaCurrentOpaqueIdCount-->
<!--@verified 2026-07-27 id=evidence.boardgaps.ko_locale_rows expect=0
    sql=SELECT COUNT(*) FROM catalog_variant_locale WHERE locale_code='ko'-->
<!--@verified 2026-07-27 id=evidence.boardgaps.asset_rows expect>=456
    sql=SELECT COUNT(*) FROM market_image_asset-->

## Images

Every card was classified into one tier. The tiers exist because "has an image"
turns out to be four different questions.

| Tier | n | Meaning |
|---|---|---|
| `A_current_std` | 74 | Reachable from the card's **current** `opaque_id`, std 429×600, file on disk. Healthy. |
| `B_drift_std` | 0 | std canvas on disk but only reachable by content sha. |
| `C_drift_nonstd` | 169 | File on disk, `raw-front-v3` era, **not** 429×600, and only reachable by content sha. |
| `D_no_file` | 22 | A `market_image_asset` row exists, but no public `.webp` on disk. **This is the missing-image list.** |
| `E_no_asset_row` | 0 | Neither a row nor a file. |

### Only 74 of 265 are reachable through the card's current identity

`manifests/image-qc.json` keys its records on `publicId`, which is
`catalog_variant.opaque_id` — a hash over tcg, language, set name, normalized
collector number and name. Change a set name and the card silently re-keys.
191 of the 265 board cards no longer have any QC record whose `publicId` equals
their current `opaque_id`. Their images were located here only by joining
`resolverEvidence.sourceContentSha256` against `market_image_asset.content_sha256`,
which is content-addressed and cannot drift.

Worked example, board rank 1 Pikachu (`variantId` 1): its current `opaque_id` is
`cmc_fc229f7ae1b256b2119fa79b`, but the QC record supplying its image carries
`publicId` `cmc_4104320a31c4d989742c067d`. The image is fine; the key is stale.
Anything that resolves images purely through `images.by_public_id` — the third
tier in `resolve_presentation_entries()` in
[canonical_public_snapshot.py](../../../pipelines/canonical_public_snapshot.py) —
will drop those 191 cards. They survive today only because the published
snapshot was frozen before the drift, or because the `relinked_printing_key`
tier rescued them.

### 70 cards point at a file that has been deleted

For 70 of 265, the `image.sha256` in
[presentation-pack.json](../../../data/public/presentation-pack.json) is absent
from **both** `data/public/market-assets` and `apps/web/public/market-assets`.
That set of 70 is *exactly* the set of 70 whose recorded `image` dimensions are
landscape — wider than tall, 60 of them at 1000×730, which is the raw
background-removed SNK canvas rather than a card. Set equality was checked, not
assumed: the two sets are identical, with zero cards on either side only.

So the mechanism is single and clean: the snapshot froze pre-normalization
landscape shas; the normalization run replaced those files; the snapshot now
references 70 deleted objects. Of the 70, **48 already have a good std 429×600
file waiting on disk** and need nothing but a snapshot regeneration; **21 have
no file at all**; 1 has only a non-std file.

### Four cards are published without a database row

`variantId` 83, 123, 235 and 353 have a std 429×600 file on disk and a QC record
matching their current `opaque_id`, but **zero** rows in `market_image_asset`.
Independent SQL over the universe counts 261 variants with an asset row against
265 in the universe; 261 + 4 reconciles. This is database under-recording, not a
missing image, so it is counted separately from the tiers.

## Stories

Real locale codes in `catalog_variant_locale` are `en`, `ja`, `zhCN`, `zhTW`.

| Gap class | n | What it means |
|---|---|---|
| No story in any language | **28** | Zero rows in `catalog_variant_locale`. Not "row present, story empty" — the row does not exist. This is an ingest gap. |
| English present, translation missing | **1** | `variantId` 168, Mew EX `024/020`, missing all of `ja` / `zhCN` / `zhTW`. This is the translation queue. |

Independent SQL over the 265 confirms: 237 with any locale row, 237 with an
English story, 236 each for `ja` / `zhTW` / `zhCN`. So the translation queue for
these boards is one card long; the real work is the 28 that were never ingested.

**`ko` does not exist anywhere.** Not one row in `catalog_variant_locale` carries
`locale_code='ko'`, and neither [card-names.json](../../../data/editorial/card-names.json)
nor [set-names.json](../../../data/editorial/set-names.json) has a `ko` key —
both only carry `zhTW`, `zhCN`, `ja`. Korean is a structural gap in the schema
and the editorial files, not a per-card gap, and is reported as such rather than
inflating the per-card count by 265.

### The published snapshot carries no stories at all

All 300 entries in `presentation-pack.json` have `stories` null in every
language, while the database holds 237 English stories for these same cards.
Zero of the 237 reached the snapshot. Combined with the 70 dead image
references, the published snapshot is materially behind the database.

### Editorial name coverage

246 of 265 board cards have a `card-names.json` entry; only 124 of 265 have a
`set-names.json` entry. Set-name translation is the weaker of the two by a wide
margin.

## EB02-010 — the wrong image is not a normalization bug

`op100` rank 1, Monkey.D.Luffy L `EB02-010`, the LA Dodgers promo, market cap
$45.06M. Full candidate record with every probe result:
[eb02-010-candidates.json](eb02-010-candidates.json). Visual proof:
[eb02-010-current-vs-candidate.jpg](eb02-010-current-vs-candidate.jpg).

Three layered problems, and only the third is the one that matters:

1. The published snapshot serves sha `cadb6890…` at **700×512 landscape**, and
   that file is not in either asset directory. Dead reference.
2. The current pipeline *has* produced a correct-looking asset for this card:
   sha `398cebb1…`, **429×600 RGBA, corner alpha 0, std canvas, tier A**. The
   canvas contract is satisfied exactly.
3. That asset is a photograph of the card **sealed inside a plastic sleeve** —
   plastic sheen, wrinkles and a crimped top seal are visible. Its framed
   content measures 380×610 = ratio 0.621; a One Piece card is 0.716. The ratio
   mismatch is the numeric fingerprint of the wrong subject.

So normalization is working and the **source photograph** is the defect. The QC
record's `semanticMatchStatus` is `metadata_exact_unreviewed`: collector number,
language and tcg all matched, so it passed every automated gate, and no human
ever looked at the picture. That is the hole.

### Source research

| Source | Result |
|---|---|
| SNK harvest cache | **Exhausted.** Scanning `snkrdunk_all.jsonl` for `EB02-010` / `Dodgers` returns 3 hits: item 607986 (this same sealed photo) and two Official Playmat vol.5 goods. No second card image exists, so `get_master(607986)` was not spent. |
| Official card list, base art | 200, `image/png`, 600×838, mode P — but a **different illustration** (red Gear-3 Leader) and a large diagonal SAMPLE watermark. |
| Official card list, `_p2` | 200, `image/png`, 600×838, mode P — **confirmed the Dodgers baseball art**, and therefore the identification win: the board needs the *parallel* `_p2` print, not base `EB02-010`. Still SAMPLE-watermarked, so disqualified for production. |
| Official card list, `asia-en` host | **404.** Only the `en.` host carries this card. Recorded so nobody retries the pattern. |
| Kado dump | Not applicable. Pokémon-only: pokémon-shaped columns, Pokémon set directories, and a structured scan of name / number / set for `EB02` or `one piece` returns 0 rows. |
| TCGdex | Not applicable; scoped to English Pokémon in the resolver's source chain. Not probed. |
| **TCGplayer CDN, product 641620** | **200, `image/jpeg`, 143154 bytes, 625×873, ratio 0.716, clean Dodgers card face, no watermark, no sleeve.** |

**Best candidate:** `https://tcgplayer-cdn.tcgplayer.com/product/641620_in_1000x1000.jpg`.

It is *not* native rounded RGBA — it is an RGB JPEG on white with square corners
(top-left pixel 251,251,251) — so unlike every SNK or Kado source the resolver
normally handles, it needs `apply_rounded_corners()` before
`normalize_card_canvas()`. 625×873 → 429×600 is a downscale by 0.686, so no
upscaling is involved. Residual risk: it is a marketplace scan, not a publisher
asset, and the white background must be keyed out rather than assumed
transparent. It needs the one human look the in-use asset never got.

Nothing was downloaded into either asset directory and no database row was
touched; the candidate is recorded for the mainline to execute.

## Premises

- **Join key.** `boards.json` `variantId` is `catalog_variant.id`, the surrogate
  key, not `opaque_id`. Verified on ids 1 and 8 before running in bulk. All 265
  resolve in `catalog_variant`.
- **Universe.** The union of `variantId` across all three boards, computed rather
  than assumed. It is 265, identical to `tcg300` alone, so `op100` (40 rows, not
  100) and `ptcg100` (100) are strict subsets.
- **"Missing image" means no file on disk** in `data/public/market-assets` —
  tiers D and E. A card with a non-std or drifted image is *not* counted as
  missing; those are reported as separate defects, which is why the headline is
  22 and not 191.
- **Canvas standard.** 429×600 with alpha-0 corners, per `CANVAS_W` / `CANVAS_H`
  and `NORMALIZED_MARKER` in
  [native_image_resolver.py](../../../pipelines/native_image_resolver.py). This
  script hard-codes those values rather than importing them, so that it stays
  runnable standalone; if the contract changes, this file is wrong until updated.
- **Dimensions are probed, not trusted.** `market_image_asset.width_px/height_px`
  hold *raw source* dimensions, never the normalized output, so every on-disk
  file was opened with Pillow for its real size, mode and corner alpha.
- **Locale codes.** The brief asked for `en` / `zh-TW` / `zh-CN` / `ja` / `ko`;
  the table actually uses `en` / `ja` / `zhCN` / `zhTW` and has no `ko` at all.
- **No GemRate quota was spent**, no secrets were printed, and
  `data/public/seed-snapshot.json` was not touched.

## Known limits

- **`boards.json` was deleted mid-audit.** Agent `claude-boards` held a live
  claim on it and removed `apps/web/src/data/` entirely while folding the boards
  in behind the watchlist control. The file was untracked, so the deletion left
  no git trace. The universe measured before the deletion is frozen in
  [board-universe.json](board-universe.json), and the producer prefers the live
  file whenever it reappears — `summary.universeSourceIsFrozenCopy` tells you
  which was used. If the boards are rebuilt with different membership, re-run and
  every count in this document must be re-derived.
- The 169 drifted cards were **not** traced back to the specific set-name edit
  that re-keyed them. That is the identity-convergence workstream's territory,
  not this audit's.
- `market_image_qc` was ignored. It joins through `image_asset_id`, and all 456
  of its rows carry `public_allowed=0` while the producer gates on the JSON
  manifest instead, so it contributes nothing to publishability.
- `manifests/image-qc.json` has `generatedAt` 2026-07-22 but contains
  `raw-front-v4` records stamped as late as 2026-07-25. The header was not
  bumped when records were appended, so do not date the manifest by its header.
- Whether the live site currently renders these images was not checked in a
  browser. The `/boards` page carries no image markup at all, and the page was
  being removed during this audit.

## Reproduce

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap

# everything: writes board-gaps.json and the three CSVs, prints the summary
python -X utf8 docs\evidence\2026-07-27-board-gaps\produce_board_gaps.py

# a single headline figure, as the @verified stamps above call it
python -X utf8 docs\evidence\2026-07-27-board-gaps\produce_board_gaps.py --counter missingImageCount

# story coverage straight from SQL, independent of the script
python -X utf8 scripts\ro_sql.py "SELECT locale_code, COUNT(*) AS rows_with_story FROM catalog_variant_locale WHERE market_story IS NOT NULL AND TRIM(market_story)<>'' GROUP BY locale_code"

# confirm Korean is absent
python -X utf8 scripts\ro_sql.py "SELECT COUNT(*) FROM catalog_variant_locale WHERE locale_code='ko'"

# re-probe the EB02-010 candidate
curl -s -o nul -w "%{http_code} %{content_type} %{size_download}\n" -A "Mozilla/5.0" "https://tcgplayer-cdn.tcgplayer.com/product/641620_in_1000x1000.jpg"
```

## Files in this pack

| File | What it is |
|---|---|
| [produce_board_gaps.py](produce_board_gaps.py) | The producer. Read-only; re-runnable; `--counter <key>` for a single figure. |
| [board-gaps.json](board-gaps.json) | Full machine-readable output: summary plus all 265 per-card records. |
| [missing-images.csv](missing-images.csv) | The 22 cards with no image file. |
| [image-defects.csv](image-defects.csv) | All 191 cards with any image defect — missing, non-std, or drift-unreachable. |
| [missing-stories.csv](missing-stories.csv) | 29 rows: the 28 with no story in any language, plus the 1 translation-only gap. |
| [eb02-010-candidates.json](eb02-010-candidates.json) | Every EB02-010 source probed, with HTTP status, content type, dimensions and verdict. |
| [eb02-010-current-vs-candidate.jpg](eb02-010-current-vs-candidate.jpg) | Side by side: the sealed-pack image in use now, and the clean candidate. |
| [board-universe.json](board-universe.json) | The frozen 265-card universe, captured before `boards.json` was deleted. |
