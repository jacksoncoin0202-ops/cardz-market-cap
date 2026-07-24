# CARDZ Market Cap page-driven data scope

The canonical database stores each resolved printing and observation once. Rankings are complete ordered views over those facts, while Top 100, Top 300 and Top 350 are presentation cuts rather than storage limits. Language is printing identity metadata and does not create a separate leaderboard.

## Pages and required data

| Surface | Required canonical data | Not required |
| --- | --- | --- |
| Home heatmap | rank, safe raw-front image, localized name, complete collector number, current PSA 10 price, exact PSA 10 population, market cap, 1d/7d/30d price change | full set catalogue, slab image, individual listings |
| Top 100 ranking | the same current metrics plus selected-window tracked-sales aggregate | Set column, 1h data, unverified transaction volume |
| Watchlist | current market-cap shadow rank, population progress, selection signals, 1d/7d/30d momentum | unresolved identities or cards outside the POP 971–999 pre-entry range |
| Card detail | canonical identity, localized story, daily reference-price history, daily market-cap history, daily tracked-sales aggregate, grader population history, current image | synthetic OHLC, provider identifiers, raw provider payload |
| TCG index | current presentation view for combined TCG, Pokémon, or One Piece | separate language leaderboards |
| Grader page | current top-grade/total population and daily population history for PSA/BGS/CGC/SGC/TAG | non-PSA market cap where no reliable same-grade price exists |

## Retention contract

- Canonical identity and ranking membership are permanent audit records.
- Reference prices are stored as one validated close per printing, source and UTC date.
- Grader population is stored as at most one validated observation per printing, grader, source and UTC date.
- Tracked sales are stored as daily partial-coverage aggregates. Individual upstream transactions remain in the immutable private run only when required for replay; they are not copied into public snapshots.
- Resolved discovery candidates may retain current observations. Full history is collected for the deduplicated Top 350 target union; daily pre-entry monitoring additionally covers POP 971–999.
- Images are limited to a QC-passed current raw front plus version/hash metadata. Discovery and slab images are not bulk-loaded.

## Selection order

1. Build the exact canonical discovery catalogue.
2. Obtain GemRate-authoritative current PSA 10 POP for the broad candidate catalogue.
3. Resolve exact SNK identities and current PSA 10 reference prices for the DB candidates.
4. Calculate complete combined TCG, Pokémon, and One Piece rankings; formal membership requires PSA 10 POP >=1000.
5. Select presentation views from the same ordered generation and backfill full history for the deduplicated Top 350 target union.
6. Keep POP 971–999 in the pre-entry pool so daily refresh can promote a card automatically when it reaches POP 1000.
