# Sweep same-class provider identity errors from the active 762

## Goal

Remove every active-market source binding that is demonstrably attached to a
different physical printing, without guessing a replacement or starting a
broad collector run.  Preserve the 762-card universe and its accepted history.

## Scope

- Windows MySQL `127.0.0.1:3308`, active 762 only.
- Local GemRate, PriceCharting and SNK evidence already on disk.
- Existing binding application and `operator_control.py db-tidy` entrypoints.
- A durable experience ledger for the rules proven in this session.

No GitHub, staging, AWS, broad PriceCharting sync, Chrome collection, or
candidate-universe change is in scope.

## Progress

- [x] Extract one read-only active-source inventory and identify only physical-identity contradictions.
- [x] Compare each candidate with its locally saved provider evidence.
- [x] Write one decision manifest containing only exact corrections, then apply it once.
- [x] Rebuild the live 3308 projection once when a correction exists. The first rebuild correctly stopped because the four canonical hash changes left their accepted identity freezes stale; the existing hash-bound freeze synchronizer is now part of the same operator pass.  The repaired pass committed with every active-projection completeness count at 762.
- [x] Record the verified wrong/right rules and the completed result.

## Decision log

- A provider product set is not automatically the card's physical collector namespace.
- An identity conflict requires an exact local provider page or raw record; a plausible name alone cannot move a binding.
- Incorrect evidence is quarantined rather than deleted so it cannot become an acceptance candidate again.
- A canonical collector correction changes its canonical and evidence hashes.  The accepted identity freeze must be re-pinned inside the same `db-tidy` transaction before the strict projection gate is evaluated.
- The local sweep found four provable truncated collector numbers (1720 `232/091`, 1721 `234/091`, 1728 `148/142`, 1729 `149/131`).  It found no structured SNK raw contradiction and no duplicate active external ID.  Ambiguous legacy PriceCharting page-map drift was deliberately not treated as an identity change without a complete tuple.
