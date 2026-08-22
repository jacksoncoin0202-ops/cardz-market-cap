# CardZ Marketcap Daily Chain V2

This ExecPlan is a living document. Keep `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` current while implementation proceeds.

## Purpose / Big Picture

Replace the four loosely coupled Windows scheduled paths with one resumable WSL state machine. The new daily chain must start from a real Task Scheduler trigger, collect registered sources concurrently without holding the legacy global operator lease, checkpoint work durably in WSL SQLite, gate publication on a strict current-run contract, and write a single `live.confirmed` outbox event only after immutable generation readback succeeds.

The implementation is intentionally scoped to the existing GemRate, SNK, PriceCharting, FX, identity, accept, BOX, bake, and release capabilities. It creates the adapter and outbox interfaces for later sources and social consumers, but it does not invent a new supplier or connect X, Facebook, or WhatsApp in this change.

Public push/deploy and Windows Task Scheduler cutover were authorization-gated. DADDY subsequently authorized the complete E2E through Bake/Live and the daily cutover; both were performed on 2026-08-21 while every promotion consumer remained disabled.

## Progress

- [x] (2026-08-20 19:15 JST) Identified the authoritative engineering tree, public/release tree, WSL release worktree, MySQL compose owner, and PriceCharting CDP boundary.
- [x] (2026-08-20 19:15 JST) Read the project instructions, state, handoffs, collection runbook, manifest, and prior autonomy evidence.
- [x] (2026-08-20 19:15 JST) Recorded dirty-tree ownership and selected an additive-first implementation strategy.
- [x] (2026-08-20) Created the WSL SQLite journal, registry-driven adapter contract, resumable task/attempt model, retry classifier, and single-tick orchestrator.
- [x] (2026-08-20) Added the generic MySQL registry, route-policy, source-state, outbox, and delivery migration without deleting legacy fields.
- [x] (2026-08-20) Migrated GemRate, SNK, PriceCharting, FX, current-catalog discovery, accept, BOX, and release execution into V2 stages.
- [x] (2026-08-20) Made canonical quote eligibility and priority registry/policy-driven while preserving current PC/SNK language behavior.
- [x] (2026-08-20) Added the Windows launcher, 107/110 provenance receipt, headed Chrome 9333 ownership, and apply-gated Task Scheduler cutover installer.
- [x] (2026-08-20) Added one consolidated V2 test module, discovered by the existing single test entrypoint.
- [x] (2026-08-20) Ran the only validation command, `python -X utf8 scripts/run_all_tests.py`, once. It exited 1 with `58/60 passed, 2 failed, 1 skipped`.
- [x] (2026-08-20) Recorded the output and blockers. The two failures were traced statically to fixture assertions: the new interrupted-worker fixture mixed a fixed claim clock with wall-clock interruption time, and the legacy discovery test still asserted the superseded HTTP-before-PC morning order. Both assertions were corrected after the one permitted run and were not rerun.
- [x] (2026-08-21 04:20 JST) Resumed only the failed `daily-accept` checkpoint after replacing the legacy per-row quote resolver with set-based candidate loading and chunked writes. Source collection and DB ingest were not rerun.
- [x] (2026-08-21 04:20 JST) Completed DB accept for all 1,604 active variants and completed the current-run BOX projection.
- [x] (2026-08-21 04:33 JST) Baked and published immutable generation `db3308_ded6da0314fec782`, generated at `2026-08-20T19:26:52.797Z`, snapshot SHA-256 `777ac73e510de1eb9576c47277cfd9b38efb5c5bac2e1efac0027d2705eb5cd4`.
- [x] (2026-08-21 04:33 JST) Release-worktree gate completed `62/62 passed, 0 failed, 7 skipped`; this is distinct from, and does not rewrite, the earlier one permitted consolidated authority-tree result.
- [x] (2026-08-21 04:33 JST) Confirmed public `/api/health` exactly matched the immutable generation and `generatedAt`; the journal recorded `PUBLISHED`, 24/24 tasks completed, zero leases, and one `live.confirmed:2026-08-20` outbox event.
- [x] (2026-08-21 04:36 JST) Exported the four legacy Task Scheduler definitions, disabled all four, and installed the single `CARDZ-Marketcap-Daily-V2` task. Its first natural start is 2026-08-22 03:30 JST, then PT10M repetition for PT13H30M, `IgnoreNew`, PT55M, interactive logon.
- [x] (2026-08-21 04:38 JST) Removed the zero-attempt Telegram delivery placeholder and changed the contract so consumers are registered only when their channel is explicitly enabled. Final state: outbox 1, delivery 0, no V2/promotion process, promotion STOP sentinel present, scheduled action has no `-Notify`.

## Current State and Ownership

The authoritative implementation tree is:

    C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805

The public frontend handoff tree is:

    C:\Users\jackson0202\Documents\Playground\cardz-market-cap-037-fe04-live

The WSL release worktree is:

    /home/jackson0202/cardz-market-cap-release-daily

The experimental `cardz-market-cap` tree remains application-code read-only. It owns only the active MySQL compose service used by this project.

The authoritative tree started this work with extensive pre-existing modifications and untracked artifacts. They belong to the user or prior work. This change will not reset, clean, stage broadly, or overwrite them. Planned V2 ownership is limited to:

- this ExecPlan;
- a new migration numbered after the current `050` migration;
- new `pipelines/daily_chain_v2*` modules;
- new V2 launcher/install/run scripts;
- a new consolidated V2 test module;
- narrowly necessary patches to collection state writes, canonical quote policy SQL, and release V2 mode.

Before every overlapping edit, inspect the exact diff and preserve existing content. No `git add .`, `git add -A`, reset, clean, or checkout rollback is permitted.

## Surprises & Discoveries

- [KNOWN] Existing source tables are substantially generic, but current eligible-quote SQL and collection scheduling still hardcode PriceCharting and SNK.
- [KNOWN] `collect_control.py` can already acquire per-adapter MySQL leases, but its public CLI is wrapped by one global operator lease and its shared JSON checkpoint/quarantine files are vulnerable to cross-process read-modify-write races.
- [KNOWN] The current release path removes run-stamp-only changes, so an unchanged market day does not necessarily create a distinct daily generation. V2 needs an explicit mode rather than relying on the old no-change behavior.
- [KNOWN] Current `-Scheduled` provenance is caller-supplied and therefore cannot prove natural Task Scheduler execution.
- [KNOWN] Windows event 107 and 110 provenance must be captured by the Windows launcher; WSL cannot infer it from a command-line flag.
- [INFERRED] The safest source fan-out unit is one task per source family: GemRate, SNK family, PriceCharting family, and FX. This preserves source-native batching while allowing true cross-source overlap.
- [KNOWN] The existing daily discovery ledger can activate exact new identities and series only after their variants already exist in the canonical catalog/gap ledger. Provider-wide GemRate `all-sets` discovery and the shadow-catalog rebuild are a separate, long 036 rebuild path; this V2 daily tick does not pretend that an unseen external ID has already landed in the catalog.
- [KNOWN] Candidate activation is optional to the already-active universe. All identity/candidate/activation work is cut off at 10:15, recorded degraded when unfinished, and retried on the next business date so it cannot hold the current active universe past the publication SLA.
- [KNOWN] The only consolidated test invocation reached all 61 registered entries: 58 passed, 2 failed, and 1 skipped because `integrations\\grade10\\data` is absent. The first failure used wall-clock time inside an otherwise fixed-time retry fixture. The second was a stale clean-tree assertion against an already modified, intentionally PC-first legacy morning script. The narrow fixture corrections made after that run remain unverified because the one-run rule forbids a rerun.
- [KNOWN] Legacy accept contained two independent O(N) paths: historical quote reconstruction and canonical ranking/source-state writes. Set-based quote reconstruction reduced the first; the 1,604-row ranking loop remains bounded at roughly 80 seconds and did not block this acceptance.
- [KNOWN] Existing active cards included 159 provider bindings at `manual_review`; requiring only strict source identity incorrectly dropped them. V2 now accepts an exact run-scoped source-state-to-quote bind for existing current-universe members while keeping strict identity mandatory before a new card can enter that universe.
- [KNOWN] The release worktree received two concurrent upstream FE commits during publication. The immutable manifest kept the already-baked artifact fixed while the release commit was rebased and pushed without rebaking.
- [KNOWN] This Windows ScheduledTasks module exposes a null repetition instance and rejects direct `$trigger.Repetition.Interval` mutation. The installer now creates `MSFT_TaskRepetitionPattern` as a client-only CIM instance. The Operational log was already enabled and must not be re-enabled without elevation.
- [KNOWN] Registering a repeating daily task after 03:30 initially schedules the next repetition on the same day. The installer now places the first StartBoundary at the next natural 03:30 so cutover cannot launch a duplicate business date immediately.

## Decision Log

- Decision: Use one SQLite file on WSL ext4 for orchestration metadata only.
  Rationale: It survives process termination, supports atomic task claiming, and does not duplicate business prices, identities, secrets, or MySQL authority.
  Date: 2026-08-20.

- Decision: Use source-family adapters and concurrency groups, not one process per capability.
  Rationale: SNK and PriceCharting capabilities share expensive source-native sessions; splitting them would duplicate requests and create conflicts.
  Date: 2026-08-20.

- Decision: Preserve legacy command behavior by default and introduce guarded V2 entrypoints/options.
  Rationale: The dirty tree and existing scheduled paths must remain intact until cutover is authorized.
  Date: 2026-08-20.

- Decision: Require a journal-issued task claim before any V2 worker may bypass the legacy global operator lease.
  Rationale: This prevents the new concurrency path from becoming an unrestricted ad hoc bypass.
  Date: 2026-08-20.

- Decision: Build an apply-gated scheduler installer and do not execute it in this implementation turn.
  Rationale: Disabling old tasks, installing the V2 task, push, and deploy are explicitly gated by `上`.
  Date: 2026-08-20.

- Decision update: DADDY authorized E2E publication and cutover; the installer was applied on 2026-08-21 after the successful Live readback.
  Rationale: The explicit authorization boundary was satisfied. XML backups were preserved and the old tasks were disabled, not deleted.
  Date: 2026-08-21.

- Decision: Run the V2 release shell directly inside the orchestrator's WSL process group.
  Rationale: It preserves one killable PID/lease lineage and avoids WSL -> PowerShell -> WSL nesting that could leave an orphan release after a tick is terminated.
  Date: 2026-08-20.

- Decision: Publication outbox insertion does not imply any delivery consumer.
  Rationale: Bake/Live is core state; Telegram and future social delivery are separate opt-in consumers. With `-Notify` absent, no `publication_delivery` row is created or claimable.
  Date: 2026-08-21.

## Design

### Journal and state machine

`daily_chain_v2` owns a SQLite schema with `chain_run`, `chain_task`, `chain_attempt`, and `chain_event`. A run is keyed by business date. A source task idempotency key is composed from business date, source code, capability, variant or shard, and input revision. Claims are leases with heartbeat timestamps. Expired running attempts become interrupted and only unfinished task keys return to ready state.

One orchestrator invocation is a bounded tick. Task Scheduler `IgnoreNew` prevents duplicate natural ticks, while SQLite `BEGIN IMMEDIATE` task claims also make overlapping manual invocations safe at individual task/concurrency-group level. A tick recovers expired attempts, plans missing tasks idempotently, starts only currently eligible tasks, records subprocess receipts, evaluates barriers, emits SLA state changes, and exits before the scheduler execution limit. The next ten-minute trigger resumes from SQLite.

Run origin is accepted only from a JSON provenance receipt created by the Windows launcher. Event 107 is `scheduled`; event 110, direct CLI, malformed receipt, or absent 107 is `manual`. Manual resumes increment `manual_intervention_count`. Autonomous proof is derived from two consecutive naturally scheduled successful business dates with a zero manual count.

### Source registry and adapters

Each adapter exposes `spec()`, `plan(context)`, `execute(task, context)`, and `ingest(result, context)`. `SourceSpec` declares capabilities, transport, concurrency group, maximum concurrency, cadence, freshness SLA, and required class. The orchestrator knows only this contract and registry; it has no SNK/PC branching.

Initial registrations are:

- GemRate POP/identity: four resumable source-native shards, required core;
- SNK price/trades/image: one source-family task, source-native worker cap retained, quote source;
- PriceCharting sales/reference: one source-family task, exclusive `cdp:9333` group, quote source;
- FX: one task, required core;
- a fixture-only dummy adapter proving third-source registration without core changes.

Network collection holds no global DB/operator lock. Ingest uses existing short MySQL transactions. Shared legacy runtime JSON read-modify-write operations gain a dedicated cross-process lease.

### Barriers and publication

After core collection completes, optional quote/extra sources may settle before the cutoff or continue independently after it. At 10:15 the candidate snapshot freezes; unfinished candidate work becomes visibly degraded and the orchestrator continues with current-run accept/ranking, BOX projection, bake/release, and live readback for the already-active universe. Existing strict identity and POP >= 1000 rules remain authoritative.

Core publication requires current-run GemRate coverage, a current-day eligible canonical quote for every active variant, FX and DB accept success, current-run BOX, one immutable generation across snapshot/assets/metadata, and exact live generation plus generated-at readback. Extra source failure permits `PUBLISHED_DEGRADED`; it never becomes full success and remains independently retryable until 17:00.

The outbox key is `live.confirmed:<business_date>`. It is inserted only after live readback and is unique per business date. Consumer delivery state is separate by `(event_id, consumer_code)`. This change creates the interface and Telegram topic split only; social consumers are not connected.

### Retry and recovery

Retry classification is data, not exception-string branching in the orchestrator:

- transient HTTP: 1, 2, 4, 8, 16, and 30 minute delays;
- MySQL 3308: start only the known compose `db` service, wait at most 120 seconds, retry up to three attempts;
- CDP 9333: use the existing headed Chrome bootstrap and retry up to three attempts;
- expired heartbeat: mark interrupted, release the exact lease/PID receipt, and requeue only unfinished task keys;
- auth/schema/illegal numeric values: terminal;
- identity ambiguity: quarantine only that variant/source;
- publication: retain the same immutable generation and retry after 2, 5, 10, 20, and 30 minutes.

At 11:00 JST a non-live run records and sends one deduplicated `SLA_MISSED` event, then continues recovery. At 17:00 it becomes `FAILED_FINAL`; no success receipt is fabricated.

## Implementation Sequence

1. Add the SQLite schema, state transitions, retry policy, adapter registry, and subprocess runner.
2. Add the MySQL generic-source and outbox migration.
3. Register existing source-family commands and add guarded collection concurrency support.
4. Make canonical route SQL policy-driven and preserve current priorities by migration seed data.
5. Add identity/accept/BOX/release barriers and live readback/outbox insertion.
6. Add Windows provenance launcher and apply-gated scheduler installer/export/disable logic.
7. Add all V2 fixtures and assertions to one test module discovered by `scripts/run_all_tests.py`.
8. Execute the consolidated test command once.
9. Update this plan with exact results. Stop before public push/deploy or scheduler mutation pending `上`.

## Validation and Acceptance

The only validation invocation is:

    python -X utf8 scripts/run_all_tests.py

The V2 test module must cover source independence in both directions, dummy third-source registration, real overlap timestamps with exclusive CDP ownership, interrupted worker resume without duplicate observations/checkpoints/generations, required versus extra-source publication behavior, exact identity activation versus quarantine, 107/110/manual provenance, current language route priorities/source switch history, unchanged-content daily generation, and outbox idempotency/live mismatch rejection. All external effects use fixtures: no Telegram, push, deploy, browser login, or production mutation.

The authorized manual E2E acceptance is complete through DB accept, BOX, Bake, push/deploy, and exact Live readback. It proves the executable chain and durable checkpoint recovery, but it is deliberately recorded as `origin=manual`, `scheduled_event_107_count=0`, `manual_intervention_count=20`, and `proven_autonomous=0`. Natural autonomy proof remains pending: two consecutive naturally scheduled business dates must succeed with event 107 and zero manual intervention. Full provider-wide discovery of previously unseen GemRate IDs/new sets also remains outside this daily V2 implementation; only exact candidates already represented in the canonical catalog/gap ledger can auto-activate here.

### Observation classification

- [KNOWN] A defect that was reproduced and then passed the production E2E is `FIXED`; it is not carried forward as an open blocker.
- [KNOWN] A capability that is implemented and passed the authorized manual E2E, but has not yet accumulated its required natural-schedule evidence, is `OBSERVATION_PENDING`; it is not an unresolved defect or incomplete implementation.
- [KNOWN] For V2, `proven_autonomous=0` currently means observation is pending. The evidence can only be accumulated by two consecutive natural Task Scheduler event-107 business dates with `manual_intervention_count=0`.
- [KNOWN] A capability that is not implemented, such as provider-wide creation of a canonical catalog identity for a completely unseen external ID or set, remains a declared `SCOPE_GAP`. A scope gap must not be relabelled as observation pending.
- [KNOWN] Measured but non-blocking behaviour, such as the approximately 80-second 1,604-card ranking loop, is `KNOWN_PERFORMANCE`; it becomes remediation work only if natural runs show that it threatens the SLA.

Therefore the post-cutover operating statement is: V2 implementation and one complete production E2E are accepted; natural autonomy is in observation, with no known unresolved E2E blocker.

## Safety, Rollback, and Idempotence

Migration DDL is additive and idempotent. Legacy source columns remain present. The installer first exports the exact four legacy tasks, and only an explicit apply action disables them and installs V2. Re-running a tick or worker is safe because every task, attempt receipt, observation ingest, generation, and outbox event has an idempotency key.

Before cutover, rollback is simply non-activation: legacy tasks remain untouched. After an authorized cutover, rollback means disabling the V2 task and re-enabling the exported legacy tasks; no data deletion or schema reversal is required.

## Outcomes & Retrospective

V2 is now live and cut over for daily active-universe refresh, registered parallel sources, current-catalog candidate activation, deterministic canonical selection, checkpoint-only recovery, DB accept, BOX, immutable publication, provenance, and consumer-separated outbox delivery. The accepted E2E published 1,604 cards as `db3308_ded6da0314fec782`; Live readback matched, 24/24 journal tasks completed, and no worker/lease remained. The sole consolidated authority-tree test invocation remains honestly recorded as `58/60 passed, 2 failed, 1 skipped`; the separate release-worktree publication gate later completed `62/62 passed, 0 failed, 7 skipped`. The four legacy tasks are disabled and backed up; the single V2 task begins naturally on 2026-08-22 at 03:30 JST. No Telegram, X, Facebook, WhatsApp, or other promotion delivery is registered. `proven_autonomous` remains false until two natural event-107 days satisfy the zero-manual contract. Full discovery of provider-side IDs absent from the canonical catalog remains a declared next-scope gap, not a V2 success claim.
