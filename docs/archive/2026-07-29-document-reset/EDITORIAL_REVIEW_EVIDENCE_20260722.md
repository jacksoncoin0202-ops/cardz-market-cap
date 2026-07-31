# Historical editorial-review evidence — 2026-07-22

> Archived volatile material. It records one former generation and cannot
> certify the current editorial pack or direct publication.

The pack pinned to canonical generation `894737a06f5947229af177c166b9a40d938896d0b2509d97cc336748ced7215f` (effective at `2026-07-22T09:48:26.496874Z`) contains 100 exact Top 100 identities:

- 45 entries are `ready`, each with independently edited English, Traditional Chinese, Simplified Chinese and Japanese copy.
- 55 entries are `review_required`: 51 have evidence that is too thin for a printing-specific story, two have a research summary for the wrong printing, and two have an unresolved identity conflict.
- The final generation was rebound by exact rank, TCG and language agreement with `data/runtime/editorial/top100-mapping.json`; changed opaque IDs, collector-number guards and number references in ready copy were updated without changing evidence status.
- The structure and content gate passes with `--allow-review`; the production gate deliberately fails until all 55 entries are cleared by reviewed evidence.
- A provider, source identifier, upstream URL and private-path scan of the public pack returns no matches.

## Pre-freeze evidence findings

The first evidence pass found recurring defects that must be handled as a class, not fixed on a single visible card:

- The research text for Birthday Pikachu `7/25` describes a different Pikachu collaboration. That summary is rejected for this printing.
- The research summary for the Japanese CD promo Blastoise is empty.
- Several vintage records expose only a bare card number or no card number in the asset record. They need an exact canonical set match before editorial copy can be attached.
- Some Chinese-language Pikachu assets are labelled as English in the ranking feed. The language conflict sends those printings back to identity review.
- The first frozen v2 candidate also labelled four Japanese-only promos as English: `227/S-P`, `207/XY-P`, `208/XY-P`, and `226/S-P`. Their canonical set names and research evidence both say Japanese, so editorial review rejected the language field and triggered a data-layer re-freeze.
- Several summaries inherit the wrong year from an upstream title even when their own release notes disagree. This affects, among others, `227/S-P`, `207/XY-P`, `208/XY-P`, `231/XY-P`, `295/XY-P`, and `094/150`; stories may use only the corroborated event or product fact until the year is normalized.
- The summary attached to `400/SM-P` describes an early Sun & Moon promo rather than the Limited Collection Master Battle Set printing. It is rejected as a printing conflict.
- The `ST13-003` BVB match-day evidence describes a 2025 event while the provisional canonical set label begins with 2022. That printing remains under identity review until the event edition is represented consistently.
- Several One Piece numbers arrive in compact form. Editorial data accepts only the canonical hyphenated form, such as `OP05-119` or `ST21-014`.
- Files carrying a Japanese filename currently repeat English prose. They are never treated as Japanese source material or copied into the Japanese locale.

These cases map to the following `reviewReason` values in the story pack: `summary_printing_conflict`, `summary_empty`, `collector_number_unresolved`, `language_conflict`, `identity_conflict`, and `evidence_too_thin`. The reason stays attached to the opaque printing ID until a human-reviewed evidence update clears it.
