# Card print language (`card_language`)

Canonical **physical print** language on `catalog_variant` / `catalog_printing_identity`.

Not UI locale. Not story translation locale.

## Codes (closed set)

| Code | Meaning | Ingest aliases |
|------|---------|----------------|
| `en` | English | eng, english |
| `ja` | Japanese | jp, jpn, japanese |
| `ko` | Korean | kr, korean |
| `zhCN` | Simplified Chinese | zh-cn, zh-hans |
| `zhTW` | Traditional Chinese | zh-tw, zh-hant |

Implemented by `pipelines/g10_ingest.normalize_language` and
`pipelines/g10_public_snapshot.canonical_card_language`.

## Why it exists

Collector numbers collide across languages:

- JP `SV2a` Charizard ex **201/165** vs EN MEW Alakazam ex **201/165**
- Without language, source bind + auto image QC merge distinct printings
- Rank-4 Rare Candy vs “Mega Charizard X 125/132” is the same failure class

Migration **015** removed the column; **018** restores it (nullable first).

## Pipelines

| Step | Tool |
|------|------|
| Schema | `pipelines/migrations/018_restore_card_language.mysql.sql` |
| Backfill | `python -X utf8 pipelines/backfill_card_language.py --write` |
| Public snapshot | `cardLanguage` on each card (optional until full coverage) |
| Image auto-bind | `tools/bind_g10_raw_public_images.py` must not revive hard rejects (`wrong_card_art*`, `identity_mismatch*`) |

## Rules

1. Never invent language — NULL beats a guess.
2. Fail-closed on multi-source conflict.
3. **Language is part of printing identity and `opaque_id`.**
4. Manual `wrong_card_art_*` / `identity_mismatch_*` QC rows must not be overwritten by `source_id_exact` auto-promote.
5. Confirmed identity without language is illegal → status `incomplete`.
6. `market_image_source_pointer` is diagnostic-only: image family/path must
   never vote for or override physical print language.  A route-verified local
   GemRate receipt may vote only when its `canonicalUrl` or identity `set_name`
   carries an explicit language cue; conflicting strong providers leave NULL.
7. **Twin-set repair:** a GemRate/PSA record that explicitly states the physical
   Japanese product is stronger than an image pointer.  Images may support QC,
   but an image URL/pointer must never override canonical `card_language`.
   If a locally evidenced EN marketplace product was attached to its JP twin,
   rebind the source identity **and every source-bound sale/price observation**
   to the verified sibling in one fail-closed transaction.  A JP/EN twin-set
   mix-up is a two-way swap, never an alternate identity: both physical product
   ids and their SNK-family price observations must end on their own language
   variant. Preserve the old evidence as `conflict`. Price rows without a
   product-id lineage (for example generic PC/eBay daily prices) must be
   rematerialized, not guessed or moved by card name. The finite 2026 Pokemon 151 repair is
   `pipelines/repair_pokemon151_twin_language.py` (dry-run first; `--write`
   only after its no-drift preflight passes).

## Identity math (backend)

| Field | Formula |
|-------|---------|
| `opaque_id` | `cmc_` + sha256(tcg ‖ language ‖ set ‖ collector ‖ name)[:24] (`\\x1f` join, casefold) |
| printing hash | sha256 of `tcg\|lang\|set\|collector\|edition\|parallel\|finish` |

Module: `pipelines/card_identity.py`  
Rehash job: `pipelines/rehash_identity_language.py --write`
