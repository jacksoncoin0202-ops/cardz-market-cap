# CARDZ backend: clean-clone and AWS handoff

This is the repeatable server handoff for the **backend only**. It does not deploy the website, publish a public snapshot, or run any third-party collector in GitHub Actions.

## 🔴 STOP — a clean clone is not deployable today (measured 2026-07-26 18:48, HEAD `57e9bf3`)

**30 of the 51 files this deployment requires are not in `HEAD`.** They exist in the working
tree, so `git clone` on the AWS host does not produce them. Measured with:

```bash
python -X utf8 scripts/verify_clean_clone.py    # exit 1, requiredFiles.missing = 30
```

A commit is being prepared: **20 of the 30 are now staged** (889 files in the index at 18:48,
including both hard requirements `scripts/verify_daily_run.py` and `scripts/notify_alert.py`).
Staged is not committed — `git clone` still produces none of them until that commit lands, so
this section stands until `verify_clean_clone.py` is re-run against the new `HEAD`.

**10 of the 30 are in nobody's index and will be missed by that commit:**
`scripts/verify_clean_clone.py`, `PROJECT_STATE.md`, `.dockerignore`,
`docs/{HANDOFF,AWS_DEPLOY,SERVER_MIGRATION,SOAK_RUNBOOK,PACKAGING_CHECKLIST}.md`,
`deploy/windows/{install_daily_task,freeze-sweep-guard}.ps1`.
Verify with `git diff --cached --name-only` before committing, or the handoff ships without its
own verifier and without every runbook it tells the operator to read.

This is a **packaging bug on the source side, not a host problem**, and it cannot be fixed from
the server. Someone with commit rights must `git add` the remainder before the handoff. The worst
of the 30:

| Missing from `HEAD` | In the pending commit? | Why it stops the deploy |
|---|---|---|
| `scripts/verify_daily_run.py` | staged | The outcome gate. `run-cardz-daily.sh` exits 1 without it → **every scheduled run fails** |
| `deploy/systemd/cardz-market-cap-watchdog.{service,timer}` + `run-cardz-watchdog.sh` | staged | The whole watchdog in the table below |
| `deploy/systemd/cardz-market-cap-alert@.service` + `run-cardz-alert.sh` + `scripts/notify_alert.py` | staged | The entire failure-notification layer |
| `deploy/systemd/cardz-gemrate-freeze.*`, `cardz-image-backfill.*` | staged | Weekly freeze sweep and image backfill |
| `apps/web/Dockerfile`, `apps/web/src/app/api/health/route.ts` | staged | The Docker image and its healthcheck target |
| **`scripts/verify_clean_clone.py`** | **NOT staged** | The check on line 17 **of this document** |
| **`.dockerignore`** | **NOT staged** | Without it the build context ships `data/` and any local `.env` into the image |
| **`docs/SERVER_MIGRATION.md`, `docs/AWS_DEPLOY.md`, `docs/HANDOFF.md`, `docs/SOAK_RUNBOOK.md`, `docs/PACKAGING_CHECKLIST.md`, `PROJECT_STATE.md`** | **NOT staged** | Every runbook this document tells the operator to follow |

### And three different installers exist right now — commit the right one

`deploy/linux/cardz-daily-systemd.sh` exists in three generations simultaneously: 86 lines in
`HEAD`, 230 lines in the index (someone staged an intermediate save), 348 lines in the working
tree. **A commit made right now ships the 230-line middle one.** All three were run `dry-run`
against the same `git clone --depth 1` of `HEAD` `57e9bf3`, WSL Ubuntu 24.04, 2026-07-26 18:5x:

| Generation | Exit | What the operator sees |
|---|---|---|
| `HEAD`, 86 lines | **0** | `schedule=06:30 Asia/Tokyo` — reports success on a clone that cannot run |
| index, 230 lines | 1 | `Missing required file: …/cardz-market-cap-alert@.service` — one line, no remedy |
| working tree, 348 lines | 1 | `FATAL … NOTHING was installed`, 1 core + 3 non-fatal named, each with a reason and a fix |

`HEAD` is the dangerous one: `06:30 Asia/Tokyo` is **the exact trigger value that caused the
2026-07-25 silent failure** (explained in "Two timers are required" below), and it exits 0 while
the clone is missing the outcome gate and the entire notifier layer.

The staged 230-line version fixes the schedule string but stops at the *first* missing file, and
on this clone that first file is `cardz-market-cap-alert@.service` — a notifier component. The
operator supplies it, re-runs, and hits the next one; the actual blocker,
`scripts/verify_daily_run.py`, is never mentioned. The working-tree version reports all four at
once and separates "every run will fail" from "runs work but nobody is watching".

**Before committing, re-`git add deploy/linux/cardz-daily-systemd.sh`** so the 348-line version
is what lands. Confirm with `git show :deploy/linux/cardz-daily-systemd.sh | wc -l` → 348.

**Do not deploy from a clone until `verify_clean_clone.py` exits 0 or 3.** Exit 3 means every file
is present and only the demo snapshot remains — that one is expected and is handled below.

## Release-preparation gate

Run this in the repository that will be pushed. It is intentionally strict: the
approved canonical seed/archive must be both Git-tracked and materialized,
otherwise a clean clone cannot restore the canonical bootstrap. The archive is
canonical data, not a Top 100/300/350 storage lock; those are export views
derived after restore.

```powershell
git lfs install --local
git lfs pull
python -X utf8 scripts/verify_handoff.py --require-archive --require-tracked --verify-archive
python -X utf8 scripts/verify_clean_clone.py
python scripts/backend.py registry --json
```

`git lfs pull` is deliberately unfiltered here. Restricting it to the bootstrap archive leaves the 360 `data/public/market-assets/*.webp` files as 131-byte pointer stubs, which only surface much later as opaque failures inside `sync-snapshot.mjs` or Pillow. `verify_clean_clone.py` clones the repository for real and reports any asset that is still an unresolved pointer, which `verify_handoff.py` alone cannot see.

The command checks only file layout, ignore/LFS rules and the archive's lock/checksums. It never reads `.env`, opens a database connection, or prints credentials. A failure that says the archive is not Git-tracked means it must be included in the approved commit before a server handoff; do not claim clean-clone readiness until that gate passes.

## AWS/Linux installation

Use a private deploy identity and a Linux host with Python 3.10+ and Git LFS. Amazon Linux 2023 may need a versioned Python install because its default `python3` can be older than the required version. Do not replace the system Python symlink.

```bash
git clone <approved-private-repository-url> /opt/cardz-market-cap
cd /opt/cardz-market-cap
git lfs install --local
git lfs pull --include="data/private/cardz-active-bootstrap.tar.gz"
python3 scripts/verify_handoff.py --require-archive --require-tracked --verify-archive
python3 integrations/grade10/run_service.py self-check
python3 scripts/backend.py registry --json
```

For RDS, deliver `CARDZ_DB_*` and `CARDZ_DB_SSL_CA` through the host's approved secret manager into `/etc/cardz-market-cap/backend.env`; keep that file `root:root` and `0600`. The file is not copied from this workstation and never belongs in Git.

`GEMRATE_API_KEY` is delivered the same way but currently lands in **two** different files depending on which chain reads it: the daily chain reads `<repo>/data/runtime/config/gemrate.env` (`scripts/backend.py` `SECRETS_PATH`) while the weekly freeze sweep reads `/etc/cardz-market-cap/gemrate.env` (`deploy/systemd/cardz-gemrate-freeze.service`, via an *optional* `EnvironmentFile=-` that fails silently when absent). Populate both until that split is unified; the two consolidation options and their costs are written up in [the packaging checklist](PACKAGING_CHECKLIST.md#6-gemrateenv-而家有兩個位要-pm-揀一個).

## The committed snapshot is a demo placeholder

`data/public/seed-snapshot.json` in Git is **always** a demo artifact: `generation.mode` is `demo`, `productionEligible` is `false`, and it carries generation blockers. Running a validator against it reports over a thousand errors. **This is expected, not a broken producer** — the repository is not permitted to carry production data, so every clone starts with a stale demo.

A clone is therefore *not publishable* until the producer has run on the target host:

```bash
python3 -X utf8 pipelines/run_daily.py --mode production
```

That builds a candidate, passes it through the image and catalog-shrink gates, and only then promotes it over `data/public/seed-snapshot.json` **on that host**. Never commit the promoted file back.

If you invoke the exporter by hand instead, `--output` is mandatory: its default is `data/public/seed-snapshot.json`, so a bare `python3 pipelines/canonical_public_snapshot.py` silently overwrites the committed demo.

```bash
python3 -X utf8 pipelines/canonical_public_snapshot.py \
  --presentation data/public/seed-snapshot.json \
  --output temp/candidate-snapshot.json \
  --view top300 --production
```

`python3 -X utf8 scripts/verify_clean_clone.py` reports this state explicitly and exits **3** while the snapshot is still the demo placeholder — but only once every required file is committed. Today it exits **1** for the different and larger reason at the top of this document, which masks the snapshot check. The two exit codes are not interchangeable: `1` = files missing, `3` = files all present and only the snapshot is still demo. (Measured 2026-07-26.)

Install the repository's prepared systemd units and perform the one-time bootstrap, then enable both the daily timer and the watchdog timer. The exact commands and ownership layout are in [the systemd runbook](../deploy/systemd/README.md). Both Windows Task Scheduler and Linux systemd execute the same `scripts/backend.py daily` entrypoint; no AI process is required for steady-state collection.

Two timers are required, not one:

| Timer | Trigger | Purpose |
|-------|---------|---------|
| `cardz-market-cap-daily.timer` | 00:30 UTC + up to 30 min jitter = 09:30–10:00 JST | Full collection chain, followed by `scripts/verify_daily_run.py` as an outcome gate |
| `cardz-market-cap-watchdog.timer` | 05:07 UTC = 14:07 JST (no jitter) | Independent read-only re-verification that catches a run which never happened |

The daily timer carries `RandomizedDelaySec=1800`, so `systemctl list-timers` will show a `NEXT`
somewhere inside 00:30–01:00 UTC and a different one after each `daemon-reload`. **That is correct
— an exact `00:30:00` means the jitter was reset to `0`**, which removes the anti-fingerprinting
spread. Why it is capped at 1800s rather than raised: [SERVER_MIGRATION.md §6.3](SERVER_MIGRATION.md#63-cardz-market-cap-dailytimer--watchdog-units). (Values read from the shipped unit files 2026-07-26.)

The daily trigger must stay at 00:30 UTC and must not be moved back to 06:30 JST. `pipelines/run_daily.py` builds `market_run_id` from the UTC date; a 06:30 JST trigger fires at 21:30 UTC on the previous day, so collectors replay the previous run's output, `effective_date` never advances, and the snapshot `INSERT IGNORE` becomes a no-op while the whole chain still exits zero. That is the 2026-07-25 silent failure. The daily service allows 21600 seconds; a measured full chain takes 2 to 2.5 hours, and the earlier 7200-second limit killed the process tree mid-crawl before the outcome gate could run.

## Acceptance sequence

1. Verify the LFS archive, registry, and vendored Grade10 integration on a clean checkout.
2. Bootstrap an **empty** MySQL/RDS database; `seed-restore` never overwrites without its explicit flag.
3. Run `python3 scripts/backend.py status --json` and keep the output as an operator artifact.
4. Confirm `python3 scripts/backend.py explain market_cap` names the same authority, collector, database target, and test documented in the registry.
5. Let the 09:30 JST (00:30 UTC) systemd timer finish two unattended daily runs; verify DB checkpoint progression, complete-rank recalculation, and that an injected source failure leaves last-good unchanged. Each run passes only when `scripts/verify_daily_run.py` exits 0 and `data/runtime/alerts/` stays empty; a zero exit from the collection chain alone is not acceptance evidence.
6. Confirm the watchdog timer fired at 14:07 JST (05:07 UTC) on both days and agreed with the daily run's own gate.
7. Only after these backend gates are met may a separately approved public-snapshot publisher be enabled for its requested presentation view.

---

## Day one, from the zip (offline handoff path)

This section covers the **zip delivered over Google Drive**, not `git clone`. The STOP
section above applies only to the clone path: a clone is missing 30 required files and
every `_200`/`_600` image derivative, so it cannot build the web app today. The zip is
built from the **working tree** and contains all of them. If you were handed a `.zip`,
start here and ignore the STOP section.

Build it with `python -X utf8 scripts/build_handoff_package.py`; it writes to `temp/`.
Exact file count, byte size, git HEAD and the list of files that exist in the package but
not in any commit are recorded in `PACKAGE_PROVENANCE.md` **inside the zip** — read that
first, it is generated at build time and cannot drift from what you actually received.

### 1. Extract and verify before anything else

```bash
unzip cardz-handoff-<date>.zip -d cardz-market-cap
cd cardz-market-cap
python3 -X utf8 scripts/verify_handoff.py
```

This must print `"enforced": true` with `"source": "package-manifest"` and exit `0`.
`enforced: false` means `HANDOFF_MANIFEST.json` is missing and **nothing was actually
checked** — do not proceed. The manifest carries a sha256 per required file, so this
catches a truncated download or a partial unzip, which is the realistic failure here.

### 2. Supply the secrets

The zip ships **templates only — no values**. Copy each and fill it in on the target
machine:

```bash
cp data/runtime/config/backend.env.example  /etc/cardz-market-cap/backend.env
cp data/runtime/config/gemrate.env.example  /etc/cardz-market-cap/gemrate.env
chown root:root /etc/cardz-market-cap/*.env && chmod 0600 /etc/cardz-market-cap/*.env
```

Variables you must provide values for:

| File | Variable | Notes |
|---|---|---|
| `backend.env` | `CARDZ_DB_NAME` | |
| `backend.env` | `CARDZ_DB_USER` | |
| `backend.env` | `CARDZ_DB_PORT` | |
| `backend.env` | `CARDZ_DB_PASSWORD` | |
| `backend.env` | `CARDZ_DB_ROOT_PASSWORD` | bootstrap only |
| `gemrate.env` | `GEMRATE_API_KEY` | **must not** go in `backend.env` — see below |

`GEMRATE_API_KEY` belongs in its own file because `scripts/backend.py` `write_local_config()`
rewrites `backend.env` wholesale, which would copy the key into a repo-visible path.
Origin of both files: [PACKAGING_CHECKLIST.md](PACKAGING_CHECKLIST.md) §3 and §6.

### 3. Backend

```bash
python3 -m pip install -r requirements.txt
python3 scripts/backend.py status --json
python3 -X utf8 -m pytest tests/ -q --no-cov
```

Use the system Python. The zip deliberately excludes `.venv-backend/` — a venv carries
absolute Windows paths and native wheels that will not resolve on the target host.

### 4. Web app

```bash
npm ci
npm run prebuild --workspace apps/web
npm run build   --workspace apps/web
```

`prebuild` runs `sync-snapshot.mjs`, which **hard-throws** on any missing `_200`/`_600`
derivative rather than degrading. Expect it to report roughly 360 raw_front assets plus
720 derivatives. If it throws here, your extraction is incomplete — re-check step 1.

### 5. Acceptance

```bash
python3 -X utf8 scripts/verify_handoff.py     # exit 0, enforced: true
python3 -X utf8 -m pytest tests/ -q --no-cov  # exit 0
npm test      --workspace apps/web            # exit 0
npm run typecheck --workspace apps/web        # exit 0
```

Then continue at [§Acceptance sequence](#acceptance-sequence) above for the backend and
timer gates, which are identical on both delivery paths.

### Two things this package does not fix

- `data/public/seed-snapshot.json` is **demo data**, by design and enforced at build time.
  Promoting real data is a separate, separately-approved step — see
  [§The committed snapshot is a demo placeholder](#the-committed-snapshot-is-a-demo-placeholder).
- POP history (`data/private/gemrate/cards/*/history_full.json`, ~288 MB) ships as a
  **separate optional archive**. Without it, POP windows report `accumulating` instead of
  a 7d/30d delta. Nothing errors, and it is regenerable from the GemRate API.
