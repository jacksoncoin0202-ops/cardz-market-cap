# `catalog_variant.identity_status` (backend)

Closed set. Ranking / public export only trusts **`confirmed`**.

| Status | Meaning | How it is set | May enter TopN? |
|--------|---------|---------------|-----------------|
| **confirmed** | Complete print identity: `tcg` + **`card_language`** + set + collector + name; not merged away | Backfill language + rehash; normal seed | **Yes** |
| **incomplete** | Missing required identity field (today: almost always **no `card_language`**) | Rehash demotes confirmed→incomplete when language null | No |
| **alias** | Row is a **non-canonical duplicate** of another variant. Facts should live on canonical. Link in `catalog_variant_alias` | Printing convergence / status processor | No |
| **duplicate** | Printing-layer duplicate shell (often empty of sources); sibling owns facts | Convergence / processor | No |
| **review** | **Cannot auto-resolve**: identity conflict (e.g. name vs collector art), or salted opaque collision still needing merge decision | Manual demote / rehash salt / processor leave | No |

## Sub-reasons (operational, not separate DB enum yet)

Recorded in processor reports and (when useful) alias `reason_code`:

| reason_code | Bucket | Notes |
|-------------|--------|-------|
| `missing_card_language` | incomplete | Fill via G10 / TPL / set evidence then rehash |
| `confirmed_printing_duplicate` | alias | Row is secondary print of `canonical_variant_id` |
| `opaque_salt_duplicate` | alias | Same 5-tuple as confirmed primary after language rehash |
| `identity_mismatch_name_vs_source` | review | e.g. v794 Mega Charizard label vs Rare Candy 125/132 |
| `awaiting_merge` | review | Temporary; processor should promote to alias when primary clear |

## Invariants

1. Every `alias` row **must** have `catalog_variant_alias.duplicate_variant_id = id`.
2. Alias target (`canonical_variant_id`) should be `confirmed` (or `review` only while mismatch pending).
3. `duplicate` shells should either become `alias`→sibling or stay empty until deleted.
4. Never invent `card_language`. TPL EN-catalog slugs without region markers may resolve to `en` (TPL is EN market catalog).
5. Public opaque_id always includes language once status is `confirmed`.
6. **Human language fill is ranking work only.** Require **exact GemRate PSA10 POP ≥ 1000**.
   - POP &lt; 1000, no GemRate POP, or non-GemRate (eBay / G10 mirror) POP → **park** as `incomplete` / `below_pop1000_no_human`.
   - Do **not** put those rows on a human checklist. Machine may still auto-resolve language later.
   - Formal index / GemRate watchlist already fail-closed at POP ≥ 1000; human queue must match that gate.

## Processor

```text
python -X utf8 pipelines/process_identity_status.py          # dry-run
python -X utf8 pipelines/process_identity_status.py --write
```
