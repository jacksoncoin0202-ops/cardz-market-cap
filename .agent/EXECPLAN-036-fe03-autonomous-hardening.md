# 036 / FE03 autonomous hardening

## Goal

Close the nine unattended-operation gaps in `docs/HANDOFF_036_20260812.md` without
opening generation 037 or weakening any acceptance gate. The finished chain must
refuse a silent ranking collapse, carry its decision counts in receipts, run its
guards automatically before bake, and keep the 036 / FE03 public contract.

## Progress

- [x] Read `AGENTS.md`, the handoff, collection runbook, and `CLAUDE.md` in order.
- [x] Measure the 2026-08-31 failure against the live read-only DB state.
- [x] #9 Run the no-DB guard suite automatically before every public bake.
- [x] #3 Persist ranked and awaiting-fresh-price counts in daily receipts.
- [x] #1, #2, #4 Repair source-cycle freshness and add ranking/awaiting hard stops.
- [x] #6 Assert the runtime junction target and writability at process startup.
- [x] #8 Use one checkpoint-adapter authority in collector and acceptance code.
- [x] #5 Derive release membership from the current universe and allow growth.
- [x] #7 Put exact-ID discovery and existing 036 atomic activation ahead of
  nightly/morning acceptance; explicit runtime cursor seeded with 268 current gaps.
- [x] Run the required negative/positive guard evidence and the complete suite.
- [ ] Run one full morning E2E, publish through the existing `[deploy]` path, and
  read back the resulting generation.

## Decision log

- 2026-08-12: the active implementation is 036 / FE03. `FE103` in the request is
  treated as the current FE03 presentation because no FE103 generation exists in
  the checkout.
- 2026-08-12: a read-only simulation at the first 2026-08-31 publish slot found
  1,314 currently ranked cards, 369 future-ranked cards, 945 rank losses, and no
  source switches. With the eight already awaiting price, the failed policy would
  leave 953 of 1,322 members unranked.
- Keep policy in a committed, secret-free contract read by both activation and the
  baked-release validator; do not duplicate thresholds in separate executables.
- 2026-08-12: owner clarified that FE03's GEO presentation is already finished.
  `apps/web/**` is read-only for this work. "Connect it back" means closing the
  data edge from a newly qualified catalog card through discovery, the existing
  036 product gates, atomic universe activation, daily bake, and the unchanged
  GEO FE03.
- 2026-08-12: do not call discovery over the historic 557-card gap every slot.
  Persist the exact acknowledged gap IDs under the shared runtime junction,
  target only set difference IDs by transport lane, and keep ambiguous IDs
  unacknowledged so acceptance fails closed.
- 2026-08-12: do not add an incremental universe writer. When discovery proves
  an exact binding, resume the sole existing 036 E2E at `identity-resolve`; its
  validator and activation transaction remain the only membership authority.

## Surprises & discoveries

- The old handoff estimate of 925 affected cards is stale. Current live evidence
  is worse: 914 PriceCharting winners and 31 SNKRDUNK winners would lose rank.
- The latest morning log ends `collect=1 accept=0 publish=0`; the new 5xx retry code
  still has no unattended clean-run proof.
- The workspace does not contain `.agent/PLANS.md`; this file follows the existing
  ExecPlan layout used in `.agent/` and records the missing implementation work.
- GEO commit `7731b8ad` is already an ancestor of `origin/main`, production SSR
  contains the provenance panel, and production reads the 036 baked snapshot.
  The missing connection is incremental membership, not frontend rendering.
- The count-only baseline still says 557 historical gaps; the exact live cursor
  seeded on 2026-08-12 contains 268. The cursor is intentionally runtime state:
  it tracks exact IDs for delta routing, while the committed count baseline stays
  a separate acceptance ceiling and is not raised by automation.
- The first real morning run exposed two unrelated release blockers after
  discovery and acceptance had passed: Playwright can reject `page.content()`
  during a same-tab redirect, and a clean WSL release checkout cannot run tests
  whose fixtures intentionally live under ignored machine-private roots. The
  collector now waits for the same page to settle; the runner reports only the
  three named fixture tests as SKIP when their exact fixtures are absent.
- The completed second morning run collected all 993 PriceCharting pages and
  accepted all 1,322 universe members (1,314 ranked, 8 awaiting fresh price).
  Its publish-only tail exposed one narrower fixture declaration: the identity
  discovery test derives the red quarantine from ignored
  `psa-identity-repair-034/audit.json`, so that exact file—not a broader HTML
  directory—is its clean-release prerequisite.

## Validation

- Guard changes must include an in-process negative fixture and the real positive
  source in one deterministic test entry, so the check is shown to fire without a
  second network or database run.
- Project suite: `python -X utf8 scripts/run_all_tests.py`.
- Final E2E: `scripts/morning_browser_lanes.ps1` exactly once after all code is in
  place, followed by the existing release readback.
- 2026-08-12 deterministic evidence: `34/34 passed, 0 failed, 1 skipped`; the
  skip is the absent legacy `integrations/grade10/data` tree. PowerShell AST
  parsing passed and the production Next build compiled, type-checked and emitted
  all 12 static pages.
- After the real-run repairs, the release-equivalent local command
  `scripts/run_all_tests.py --no-db` reports `33/33 passed, 0 failed, 2 skipped`.
  The same runner owns the redirect-content race fixture and the portable/private
  fixture classification used by the WSL bake gate.
