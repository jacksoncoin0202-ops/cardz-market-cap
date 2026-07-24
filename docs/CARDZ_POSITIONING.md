# CARDZ Positioning

## Position

CARDZ Market Cap is an art market before it is a dashboard. It presents collectible cards as cultural objects, then supplies the market evidence needed to judge liquidity, scarcity, and lasting value.

The audience balance is deliberate:

- 80% traders who need comparable price, population, market cap, tracked sales, and change data.
- 20% collectors who care about artwork, release context, cultural relevance, and why a specific printing is desired.

The interface must satisfy both groups without becoming visually noisy. The first impression is a calm gallery. Deeper interaction reveals the evidence.

## Product promise

CARDZ helps people answer five questions:

1. Which eligible cards carry the largest PSA 10 market cap?
2. Which cards are gaining or losing attention over 1 day, 7 days, or 30 days?
3. Is a quoted price supported by population and tracked sales?
4. Which cards are approaching Top 100 eligibility?
5. Why does the market care about this exact printing?

## Curated market universe

CARDZ is not attempting to catalogue every collectible card. It deliberately
tracks a small market universe that can support reliable daily prices,
population history, change windows, images, and editorial context.

- Rankings are scoped to combined TCG, Pokémon, and One Piece. Language and
  release market remain canonical printing metadata, not separate leaderboards.
- Top 100, Top 300, and Top 350 are presentation cuts over the same complete
  eligible ranking; they do not cap canonical storage.
- Formal ranking eligibility requires GemRate-authoritative PSA 10 population
  of at least 1,000. The pre-entry monitoring pool is narrowly defined as
  population 971–999.
- A printing must first have a confirmed Pokédex/canonical identity, complete
  collector number, TCG, card language, set, and native-language name.
- Pokémon supports Japanese, English, Korean, Traditional Chinese, and
  Simplified Chinese printing metadata. Thai printings are explicitly out of
  scope.
- Korean printings require Korean card/set text and KRW is a supported display
  currency. English fallback is not accepted as Korean catalogue content.
- Cards leaving a presentation view retain their historical observations for
  audit. Cards in POP 971–999 stay in daily pre-entry monitoring and can enter
  the formal ranking automatically after reaching POP 1,000.

The broad source catalogue is a discovery input, not the production tracking
database. Growth in source catalogues must not silently expand the active
time-series universe.

## Market language

Use precise, neutral language. Explain what the data covers and what it does not cover.

- Say "tracked sales value" with its 1-day, 7-day, or 30-day window, never "total market volume".
- Say "data accumulating" when the observation window is incomplete.
- Say "stale" or "unavailable" when freshness is outside contract.
- Do not turn a missing metric into `0`, `0.00%`, or a flat line.
- Do not name private providers or collection paths in public copy.

## Card stories

Every Top 100 detail page needs an original story for English, Traditional Chinese, Simplified Chinese, and Japanese. The story explains why the exact printing receives market attention. Useful reasons include artwork, character significance, release circumstances, print distribution, promotion history, set importance, condition sensitivity, and collector crossover.

Stories are not generic trading advice. Do not use a repeated heading such as "Trader market view" or a template that merely repeats population and price. Do not invent provenance, scarcity, or market events. When a historical claim is not verified, omit it.

## Decision hierarchy

When product goals compete, use this order:

1. Identity and data truth.
2. Image safety and legal publication boundaries.
3. Legibility and accessibility.
4. Visual quality and emotional clarity.
5. Information density.

An attractive presentation never overrides a failed identity, freshness, or image gate. A valid dataset should still be presented with the care expected from an art marketplace.

## Non-goals

- CARDZ is not a listing marketplace in this release.
- CARDZ does not claim full-market transaction coverage.
- CARDZ does not expose provider-native identifiers, links, or provenance.
- CARDZ does not rank estimated population records.
- CARDZ does not publish a 1-hour metric. The V1 heatmap and ranking use daily close-to-close 1-day, 7-day, and 30-day windows.
