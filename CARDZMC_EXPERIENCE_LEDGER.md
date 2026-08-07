# CARDZMC Experience Ledger

This file records only practices that have been proven correct or incorrect in
the local CARDZ Market Cap runtime.  It is an engineering reference, not a
release gate or a second runtime.

## Canonical identity: correct rules

- One card has one public, PSA-facing and file identity: the exact GemRate/PSA
  full title plus the physical collector number from
  `catalog_printing_identity`.
- The physical printing tuple is authoritative: TCG, language, complete
  collector number, set, edition, printing, parallel and finish.
- Keep full collector evidence intact.  `15/82`, `15/102` and `15/132` are
  separate values; reducing each to `15` destroys identity.
- When the exact GemRate/PSA name supplies a full collector number, preserve it
  literally: `232/091`, `234/091`, `148/142` and `149/131` are not shorthand
  forms.  Conversely, do not invent a denominator where that exact title has
  not supplied one.
- GemRate is the authority for the PSA/GemRate full title and PSA10 POP, but a
  GemRate One Piece product set describes the product/release.  It is not a
  licence to manufacture a card-number prefix.  A card sold in an OP11 product
  can physically remain `OP05-119`.
- SNK EN is used only after exact identity confirmation.  Its international
  primary/default image is the preferred product image when it matches the
  canonical printing.
- PriceCharting history can supplement history only for the same exact
  physical printing and PSA10 evidence.  It never proves a card by title
  similarity alone.

## Canonical identity: proven mistakes to avoid

- Do not derive a One Piece collector prefix from GemRate `set_name`.  This
  previously turned a correct physical identity into a false `OP11-*` identity
  and made correct providers look conflicting.
- Do not treat an external source as exact because its character name matches.
  Promo, parallel, finish, collector number and product set can change the
  printing and market price completely.
- Do not accept empty set, printing, parallel or finish as a wildcard when the
  canonical printing contains that value.
- Do not allow `catalog_variant` legacy display fields to overwrite
  `catalog_printing_identity` in a market projection.
- Do not replace a source ID merely because a page is available.  A replacement
  needs a locally stored exact page/record; otherwise leave the binding alone.

## Proven local correction: 2026-08-06

- Variant 958, `OPCD-093` Gold DON!!, was linked to PriceCharting `13256449`.
  Its local title was a Dodgers promo, therefore it was a different printing.
- The local exact PriceCharting Gold Alternate Art page identified product
  `11018417`.  The old evidence was quarantined, the exact product was bound,
  and eight historical PSA10 points were materialized.
- The repair did not require Chrome, a broad PriceCharting run or a new
  collector.  It used the existing binding manifest, local history materializer
  and the existing `operator_control.py db-tidy` rebuild.

## Complete-number repair and sweep: 2026-08-06

- A complete local active-762 source sweep found four actual identity defects:
  Variant 1720 Mew ex needed `232/091`; Variant 1721 Charizard ex needed
  `234/091`; Variant 1728 Squirtle needed `148/142`; and Variant 1729
  Vaporeon ex needed `149/131`.  The exact accepted GemRate/PSA titles already
  contained those values; the canonical physical rows had incorrectly retained
  only the numerator.
- Existing exact provider claims for those four variants were re-pinned to the
  corrected complete number.  No provider ID, price authority, POP source or
  accepted observation was guessed or swapped during this correction.
- The first rebuild exposed a real runtime ordering mistake: changing a
  canonical identity invalidated the hash stored by the accepted identity
  freeze, and the strict projection stopped at 758/762.  The correction is now
  in the existing `db-tidy` transaction: refresh accepted identity freezes
  first, then accept names/metrics and build the projection.  The completed
  run restored all completeness counts to 762/762.
- The local SNK raw archive produced no structured physical-identity conflict.
  PriceCharting had legacy saved-page map drift for some old artefacts, but
  those artefacts did not expose enough set/finish/parallel evidence to prove a
  different printing.  They were not mass-rebound, rejected or used to change
  market facts.

## Frontend and release operating knowledge

- `fe02` is the local frontend baseline.  Port 3800 is an engineering review
  surface, not a baked release snapshot: it reports `dataMode=windows-db-3308`
  and reads the current 3308 projection.  Seeing a new generation there does
  not by itself publish GitHub, staging or AWS.
- The production frontend must continue to read a deliberately approved
  snapshot only.  Local engineering direct-DB and production snapshot modes
  must never be confused when deciding whether a visual change is current.
- The frontend must display the canonical public/PSA full name and complete
  collector number from the projection.  It must not rebuild a shorter title
  from legacy variant fields or silently supply a denominator.

## Runtime and data-flow rules

- Windows MySQL `127.0.0.1:3308` is the only CARDZ Market Cap business DB.
- The local engineering frontend on port 3800 runs in `live-db` mode and reads
  the current 3308 projection server-side.  It is the approval view before a
  product release.
- AWS/public mode reads only the baked snapshot and referenced market assets;
  it never reads the business DB.
- The fast daily market lane is exact SNK IDs -> `snk_market_data.py` ->
  ingest to 3308 -> `operator_control.py db-tidy --project-ingested-history`.
  It is single-process HTTP work, not Chrome or a broad adapter batch.
- Historical price dates must use their source `observed_date`/effective date,
  never the importer runtime timestamp.  Empty kline responses never erase an
  existing real historical point.
- Current price authority is not silently changed by backfilling history.  A
  history point is added only where the external identity is exact.

## What made earlier runs unreliable

- Multiple Chrome/collector workers on the same external IDs caused duplicate,
  slow and non-deterministic work.  One exact-ID owner/lease is required.
- A source page, a build artifact, a snapshot, or an HTTP 200 by itself is not
  proof that a particular market value belongs to the card.  Physical identity
  must be checked first.
- A 30-day blank can be caused by using importer time as the price date or by
  discarding valid exact historical evidence from a non-current provider.  It
  does not justify borrowing a price from a similarly named printing.
- Baked frontend data and direct-DB local data are intentionally different
  modes.  Port 3800 must be checked in `live-db` mode when reviewing the latest
  database state.

## Boundaries that remain deliberate

- Never turn unproven local data, a partial product name, JP image search
  output, eBay sales, RAW prices or G10 kline into PSA10 market facts.
- Do not shrink the active 762-card universe to conceal missing evidence.
- Do not use this ledger as a new QC/finalizer/SOP layer.  The existing
  operator entrypoints remain the only runtime paths.
- A canonical identity correction changes both stored identity hashes.  The
  normal `db-tidy` transaction must re-pin the accepted identity freeze before
  it evaluates the live projection; otherwise a correct correction is wrongly
  reported as incomplete.
- A legacy saved PriceCharting page whose local product ID drifts from a map is
  not by itself proof of a different printing.  Rebind or quarantine only when
  the complete physical tuple is proven, as it was for Variant 958.
