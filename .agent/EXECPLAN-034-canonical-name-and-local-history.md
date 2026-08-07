# Correct canonical PSA names and reuse local price history

## Progress

- [x] Read the Cardz Market Cap runtime contract and local source inventory.
- [x] Build the active name set from the local GemRate full-name map and saved card pages.
- [x] Apply canonical-name acceptance through the existing operator path.
- [x] Rebuild the live 3308 projection once; the local 3800 health and card route both responded during the single read.
- [x] Separate GemRate product-set labels from physical collector numbers for One Piece names.
- [x] Replace the one locally proven conflicting PriceCharting product and rebuild the live projection.

## Decision Log

- Canonical display, PSA-facing and file identity use the same full GemRate/PSA name plus the canonical collector number.
- A source title which conflicts with canonical set or collector identity is corrected from exact local evidence before any bulk name write.
- Local PriceCharting history is used only after its existing binding matches the same printing; no Chrome or broad collector run is used.
- The name write uses the full collector encoded by the saved GemRate page for One Piece, rather than the stale collector column in the historical name map.
- GemRate's One Piece product set is not a collector-number namespace.  The canonical physical collector number remains the value in `catalog_printing_identity`.
- `OPCD-093 Gold` has one locally proven wrong PriceCharting binding (`13256449`, Dodgers promo) and one locally stored exact replacement (`11018417`, Gold Alternate Art).

## Scope

The target is the active 762-card Windows MySQL 3308 cohort.  The only frontend result is the existing direct-DB runtime on port 3800.  No GitHub, staging, AWS deployment, candidate promotion, or new runtime is in scope.

## Acceptance

The one mutation rebuild must leave every active card with one canonical full name, preserve exact physical identity, and expose the new 3308 generation on port 3800.  History is only added where a locally stored exact source record exists.

## Outcome

The 2026-08-06 one-time repair quarantined variant 958's unrelated PriceCharting
Dodgers promo product (`13256449`), bound the locally evidenced Gold Alternate
Art product (`11018417`), materialized its eight historical price points, and
rebuilt the Windows 3308 live projection as `db3308_c4bec9a5a068e58b`.

`operator_control.py db-tidy --project-ingested-history` committed at 2026-08-06T10:04:04Z.  Its receipt records 762 accepted GemRate official names and a 762-card live projection.  The separate source-binding repair remains deliberately out of this name-only write: local evidence identifies mixed GemRate/PriceCharting/SNK physical identities on some One Piece rows, so it must not be silently reattached by name synchronization.
