# CardZ Marketcap Daily Chain V2 Cutover

## Current authority state

- V2 code and the additive migration live in `cardz-market-cap-fe-db-20260805`.
- The MySQL runtime remains the existing `cardz-market-cap` Compose `db` service on `127.0.0.1:3308`.
- Public bake/release remains `/home/jackson0202/cardz-market-cap-release-daily`.
- PriceCharting remains exclusively on headed Windows Chrome CDP `9333`.
- The legacy scheduled tasks remain untouched until DADDY explicitly authorizes `上`.

## Prepared cutover

The installer has a read-only default. This prints the exact intended scheduler change and mutates nothing:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_cardz_daily_v2_task.ps1
```

Only after an explicit `上`, the authorized cutover command is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_cardz_daily_v2_task.ps1 -Apply
```

The apply path refuses to proceed if any expected legacy task is missing or any cutover target is still running. It then:

1. exports XML for the four legacy tasks and any existing V2 definition;
2. enables the Task Scheduler Operational log;
3. disables the four legacy tasks without deleting them;
4. installs one interactive-logon task named `CARDZ-Marketcap-Daily-V2`;
5. schedules 03:30 local time with ten-minute repetition through 17:00;
6. sets `StartWhenAvailable`, `IgnoreNew`, and a 55-minute execution limit;
7. launches the WSL tick with publication enabled while Telegram and all promotion consumers remain disconnected.

The first natural tick installs migration `051` idempotently, synchronizes the Python source registry into MySQL, and resumes solely from the WSL ext4 SQLite journal. Bake/push/live retry runs directly in the same WSL process group, so an interrupted claim can reclaim the exact release PID lineage.

Current-catalog exact candidates can fan out GemRate/SNK/PriceCharting work and auto-activate before the 10:15 barrier. A previously unseen GemRate external ID or entirely new provider set still requires the existing full 036 catalog-discovery/rebuild path; V2 does not infer or insert an unknown canonical identity from a POP row alone.

## Evidence boundary

Do not manually run `tick` to simulate autonomy. Direct CLI, Task Scheduler event `110`, a missing event `107`, or an invalid launcher parent is permanently recorded as manual intervention for that business date.

The first real acceptance point is the next natural 03:30 event `107`. The system may report the run as published after exact live readback, but it cannot set `proven_autonomous=true` until two consecutive business dates are both fully `PUBLISHED`, both contain event `107`, and both have `manual_intervention_count=0`. `PUBLISHED_DEGRADED` does not qualify as a proven day.

Until those two natural days exist, the autonomy state is **observation pending**. It does not mean the V2 implementation is incomplete, does not reopen defects already fixed and accepted by the production E2E, and is not a release blocker. A genuinely unimplemented capability must be listed separately as a scope gap rather than hidden under observation pending.

Read-only status after a natural tick is:

```bash
python3 -X utf8 pipelines/daily_chain_v2.py status --business-date YYYY-MM-DD
```

No X, Facebook, or WhatsApp consumer is connected in V2. Their future consumers claim independent `(event_id, consumer_code)` delivery rows from `publication_delivery`; they do not compete for the Telegram delivery record.

An explicitly authorized after-hours E2E may use the same durable run without
installing the recurring task:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\cardz_daily_v2_launcher.ps1 -AllowPublish -ManualE2E
```

This keeps the current JST business date, records manual provenance, sends no
Telegram unless `-Notify` is separately supplied, and persists one fixed
four-hour source cutoff / eight-hour final deadline in the WSL journal. Every
resume uses those original deadlines; it cannot extend its own recovery window.

## Authorization boundary

Preparing or testing these files does not authorize any scheduler mutation, commit, push, deployment, Telegram send, or public-site change. Those actions remain blocked until DADDY says `上`.
