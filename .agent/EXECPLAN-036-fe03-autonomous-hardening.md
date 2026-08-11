# 036 / FE03 autonomous hardening

## Goal

Close the nine unattended-operation gaps in `docs/HANDOFF_036_20260812.md` without
opening generation 037 or weakening any acceptance gate. The finished chain must
refuse a silent ranking collapse, carry its decision counts in receipts, run its
guards automatically before bake, and keep the 036 / FE03 public contract.

## Progress

- [x] Read `AGENTS.md`, the handoff, collection runbook, and `CLAUDE.md` in order.
- [x] Measure the 2026-08-31 failure against the live read-only DB state.
- [ ] #9 Run the no-DB guard suite automatically before every public bake.
- [ ] #3 Persist ranked and awaiting-fresh-price counts in daily receipts.
- [ ] #1, #2, #4 Repair source-cycle freshness and add ranking/awaiting hard stops.
- [ ] #6 Assert the runtime junction target and writability at process startup.
- [ ] #8 Use one checkpoint-adapter authority in collector and acceptance code.
- [ ] #5 Derive release membership from the current universe and allow growth.
- [ ] #7 Put discovery ahead of nightly acceptance.
- [ ] Run the required negative/positive guard evidence and the complete suite.
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

## Surprises & discoveries

- The old handoff estimate of 925 affected cards is stale. Current live evidence
  is worse: 914 PriceCharting winners and 31 SNKRDUNK winners would lose rank.
- The latest morning log ends `collect=1 accept=0 publish=0`; the new 5xx retry code
  still has no unattended clean-run proof.
- The workspace does not contain `.agent/PLANS.md`; this file follows the existing
  ExecPlan layout used in `.agent/` and records the missing implementation work.

## Validation

- Guard changes must include an in-process negative fixture and the real positive
  source in one deterministic test entry, so the check is shown to fire without a
  second network or database run.
- Project suite: `python -X utf8 scripts/run_all_tests.py`.
- Final E2E: `scripts/morning_browser_lanes.ps1` exactly once after all code is in
  place, followed by the existing release readback.

