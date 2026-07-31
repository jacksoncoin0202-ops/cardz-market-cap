# Archived CARDZ Market Cap frontend handshake (2026-07-29)

The frontend reads only a sanitized, versioned generation. It never reads
MySQL, provider landing files, provider identifiers, or source URLs.

## Index contract

The backend owns three complete eligibility rankings. A display request selects a
presentation view from one generation; it does not select a different database
or trigger a different collection job.

| code | canonical ranking | public default | optional private view |
| --- | ---: | ---: | ---: |
| `tcg-combined` | all eligible printings | `top300` | `reserve50` |
| `pokemon` | all eligible printings | `top300` | `reserve50` |
| `one-piece` | all eligible printings | `top300` | `reserve50` |

View IDs are `top100` (ranks 1–100), `top300` (ranks 1–300), `top350`
(ranks 1–350), `top100_plus_200` (an alias of `top300`), and private
`reserve50` (ranks 301–350). The frontend sends a view ID; rank and market-cap
values remain identical when the same card occurs in more than one view.

The public snapshot must never contain reserve members. A canonical printing
may occur in more than one index, but its card/detail record is emitted once
and index membership contains only its CARDZ opaque ID and rank.

## Required fields

```text
schemaVersion
generation.id
generation.effectiveAt
generation.contentSha256
generation.status
generation.presentationView
indexes.<code>.members[].rank
indexes.<code>.members[].cardId
cards.<cardId>.identity
cards.<cardId>.pricePsa10
cards.<cardId>.populationPsa10
cards.<cardId>.marketCap
cards.<cardId>.windows.1d|7d|30d
cards.<cardId>.trackedSales.1d|7d|30d
cards.<cardId>.historyDaily
cards.<cardId>.populationHistory
cards.<cardId>.image
```

Every metric is an object with `value`, `status`, and `asOf`. Supported status
values are `ready`, `accumulating`, `stale`, and `unavailable`. Missing data is
`null`; it must not be converted to zero.

All timestamps are UTC ISO-8601. Canonical ranking values are USD. Display FX
is a separate dated block. Index arrays are ordered by ascending rank and
pagination must preserve that order.

## Failure contract

A generation is publishable for a requested view only when every declared scope
has enough eligible members for that view. The database can still ingest and
re-rank a partial market while a larger requested view is unavailable. Partial
collection, schema drift, stale source data, identity conflicts, or a database
transaction failure preserve the previous last-good generation for that view.

Frontend field requests start with a fixture change in this contract. The
canonical exporter is then changed and tested before UI code depends on it.
