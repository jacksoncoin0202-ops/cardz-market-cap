# Editorial review contract

## Purpose

Each Top 100 detail page answers one question: why does the market care about this exact printing? The answer should give a trader enough context to understand the demand story and give a collector a reason to look more closely at the card itself.

The editorial layer does not explain price movements, predict returns, or repeat price and population figures already shown elsewhere on the page. It is also not a place for generic labels such as “grail”, “iconic” or “highly sought-after” unless the surrounding sentence explains the specific artwork, release, character or collecting lane behind that attention.

## Evidence gate

A story may use only claims supported by the matched asset record and its research summary. The public text must never identify a private provider, source URL, source-native ID or collection path.

A story is `ready` only when all of the following are true:

- TCG, card language and complete collector number agree with the canonical printing.
- The research summary describes the same printing, not merely the same character.
- Every release, collaboration, event, artwork or product claim used in the story appears in the matched evidence.
- English, Traditional Chinese, Simplified Chinese and Japanese versions convey the same facts without being literal machine translations.

If the collector number is incomplete, the summary is empty, the summary contradicts the asset record, or a distinctive claim cannot be tied to this printing, the entry is `review_required`. Missing evidence is never replaced with a plausible story.

## Voice

- Lead with the card-specific reason for attention.
- Prefer one or two concrete facts to a list of market adjectives.
- Keep the focus roughly 80% on demand, market recognition and trading context, and 20% on artwork, character or collecting history.
- Use written Chinese for both Chinese locales. Traditional and Simplified Chinese are edited separately.
- Japanese copy should read as natural editorial Japanese. A file named as Japanese is not evidence that its contents are Japanese.
- Avoid trading advice, price targets, urgency and claims of guaranteed scarcity.

## Data format

`data/editorial/top100-stories.json` is keyed by opaque CARDZ printing ID and repeats the TCG, card language and complete collector number as identity guards. It also pins the canonical snapshot effective time and content hash, so a rank or identity re-freeze cannot silently reuse an older review. `rankAtReview` is audit context only; stories follow the printing when ranks change.

Each entry records an editorial status, four localized stories, provider-neutral evidence tags and an optional review reason. A publication job may join only entries whose opaque ID and all three identity guards match the canonical snapshot. `review_required` entries block a production Top 100 release.

## Review result

The generated pack records its own `readyCount` and `reviewRequiredCount`. Run the verifier after any rank refresh or copy edit:

```powershell
node scripts/verify-editorial-stories.mjs
```

The production command checks the pack shape, identity uniqueness, complete locale coverage, non-template phrasing, forbidden provider terms and summary counts, and fails while any entry remains `review_required`. During copy work, `node scripts/verify-editorial-stories.mjs --allow-review` validates the same contract without pretending the incomplete pack is publishable. The older `--allow-review-required` spelling remains an accepted alias. Editorial review does not substitute for canonical identity validation.

Allowed `reviewReason` values are `summary_printing_conflict`, `summary_empty`,
`collector_number_unresolved`, `language_conflict`, `identity_conflict`, and
`evidence_too_thin`. The reason stays attached to the opaque printing ID until
a human-reviewed evidence update clears it.
