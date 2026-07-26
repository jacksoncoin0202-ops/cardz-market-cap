# AWS/Linux systemd deployment

These units assume the private repository is installed at `/opt/cardz-market-cap`, runs as the locked-down `cardz` user, and connects to an external MySQL-compatible database such as Amazon RDS. Secrets come from `/etc/cardz-market-cap/backend.env` with mode `0600`. Change all three values together if the server layout differs.

Install Python, Git, and Git LFS using the package manager for the chosen Linux distribution. Python 3.10 is the hard floor enforced by both wrappers; **3.12 is the target** and is what CI pins, because the interpreter probe below resolves to `python3` (3.12 on Ubuntu 24.04) on a stock server. See `docs/SERVER_MIGRATION.md` §2.3 for the decision record. Clone the private repository through an approved deploy identity, then materialize the private bootstrap LFS object:

```bash
if ! id -u cardz >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir /var/lib/cardz --shell /usr/sbin/nologin cardz
fi
cd /opt/cardz-market-cap
git lfs install --local
git lfs pull
sudo install -d -m 0750 -o cardz -g cardz /etc/cardz-market-cap
sudo install -d -m 0750 -o cardz -g cardz data/runtime data/private/gemrate integrations/grade10/data .venv-backend
```

The service user's home is `/var/lib/cardz`, matching `docs/SERVER_MIGRATION.md` §2.1; it must be a real directory because Playwright's Chromium lands in `$HOME/.cache/ms-playwright` and the daily run launches it from there. Any home outside `/home` works, since `ProtectHome=true` only hides `/home`, `/root` and `/run/user`; a read-only home is sufficient to launch the browser.

`git lfs pull` is deliberately unfiltered. Once `CARDZ_DAILY_PUBLISH` is `local` or `remote` the publish chain reads the real `data/public/market-assets/*.webp` bytes, so restricting the pull to the bootstrap archive would leave ~131-byte LFS pointer files that only fail much later, inside Pillow, with an error that never mentions LFS.

Materialize `/etc/cardz-market-cap/backend.env` from the approved server secret manager. It must contain `CARDZ_DB_MODE=external`, the five `CARDZ_DB_*` connection values, and a readable `CARDZ_DB_SSL_CA` path. Keep it owned by `root:root` with mode `0600`; do not put that file in Git. Both the daily wrapper and the installer refuse to run if that file is group/world-readable, and the installer additionally requires it to be root-owned.

Run the bootstrap once before enabling any timer. The installer described below deliberately does **not** cover `cardz-market-cap-bootstrap.service` or the optional `cardz-grade10-discovery.*` units, so install those the plain way:

```bash
sudo install -m 0644 deploy/systemd/cardz-market-cap-bootstrap.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start cardz-market-cap-bootstrap.service
sudo systemctl status cardz-market-cap-bootstrap.service
```

## Installing the daily and watchdog timers

Use the repo installer — it is the supported path for these four units:

```bash
sudo deploy/linux/cardz-daily-systemd.sh dry-run     # default action; changes nothing
sudo deploy/linux/cardz-daily-systemd.sh install
```

`dry-run` is the default action and only prints what would happen. Check its output before installing; `schedule=00:30 UTC (09:30 Asia/Tokyo)`, `watchdogSchedule=05:07 UTC (14:07 Asia/Tokyo)` and `timeoutSeconds=21600` must all read as shown. It also prints `readWritePaths=` and `readWritePathsMissing=` so the sandbox paths can be checked before they matter.

Flags: `--repo-root` (default `/opt/cardz-market-cap`), `--unit-dir` (default `/etc/systemd/system`), `--env-file` (default `/etc/cardz-market-cap/backend.env`), `--user` (default `cardz`). Other actions are `status` (`systemctl status` on all four units) and `uninstall` (disables both timers, removes the four unit files, reloads). `install` and `uninstall` require root; `install` also requires the service user to already exist.

What `install` does that a plain `install -m 0644` of the unit files does not:

- **Pre-creates every sandbox path.** It reads the `ReadWritePaths=` lines back out of the daily and watchdog service templates (the paths are *extracted* from the templates; the installed units keep their `ReadWritePaths=` intact), rewrites the `/opt/cardz-market-cap` prefix to the real `--repo-root`, then creates each one with `install -d -m 0750` owned by the service user. This is not cosmetic: `ProtectSystem=strict` requires every path listed in `ReadWritePaths=` to exist when systemd builds the mount namespace. If one is missing the unit dies with `status=226/NAMESPACE` **before `ExecStart`**, so the journal shows no Python output at all and the run looks like it never happened. Four of the seven daily paths (`integrations/grade10/data`, `data/runtime`, `data/private/gemrate`, `.venv-backend`) are gitignored and therefore absent from a fresh clone; the three publish paths (`data/public`, `manifests`, `packages/market-data/dist`) are present in a clone except for `dist`, which only exists after a build. Because the list is read from the units, editing `ReadWritePaths=` keeps the installer in sync automatically.
- **Renders the units instead of copying them.** `/opt/cardz-market-cap`, the env-file path, and `User=`/`Group=cardz` are substituted for the values actually passed in. The two `.timer` files carry no paths and are installed verbatim.
- **Refuses to install a broken chain.** Missing `scripts/backend.py` or `scripts/verify_daily_run.py` aborts the install — without the latter there is no outcome gate. The watchdog units are installed only when all three of its files are present.
- Finishes with `daemon-reload`, `enable` + `start` on both timers, and `systemctl list-timers`.

The equivalent manual route, if the installer cannot be used, is `install -m 0644` for the four unit files plus `install -d -m 0750 -o cardz -g cardz` for each `ReadWritePaths=` entry. It only works unmodified when the repository really is at `/opt/cardz-market-cap` and the service user really is `cardz`, since nothing rewrites the paths baked into the units.

## Schedule

| Unit | Trigger | Purpose |
|------|---------|---------|
| `cardz-market-cap-daily.timer` | `OnCalendar=*-*-* 00:30:00 UTC` + `RandomizedDelaySec=1800` — fires 00:30–01:00 UTC = 09:30–10:00 JST | Full collection chain, then the outcome gate |
| `cardz-market-cap-watchdog.timer` | `OnCalendar=*-*-* 05:07:00 UTC` — 05:07 UTC = 14:07 JST | Read-only re-verification of that day's result |
| `cardz-gemrate-freeze.timer` | `OnCalendar=Sun *-*-* 14:23:00 UTC` + `RandomizedDelaySec=3600` | Weekly GemRate roster + POP freeze (heavy; daily API quota is 1000) |
| `cardz-grade10-discovery.timer` (optional) | `OnCalendar=*-*-* 21:17:00 UTC` + `RandomizedDelaySec=1500` — fires 21:17–21:42 UTC = 06:17–06:42 JST | Optional preflight refresh of the broad discovery roster |
| `cardz-image-backfill.service` | none — `systemctl start` only | On-demand image/derivative backfill. The absence of a timer is the design, not an omission. |

Every timer is expressed in UTC; none of them may be rewritten in local time.

**Both outbound timers carry jitter, and the gap between them is deliberately not constant.** The G10 stealth rule (`docs/HANDOFF.md` §8 rule 2) forbids mirroring a fixed schedule. Two to-the-second timers a constant 47 minutes apart — which is what `23:43 UTC` + `00:30 UTC` was — is the same fingerprint as copying a published schedule: the pair itself is the pattern. With `RandomizedDelaySec` on both, the interval now floats between 2h48m and 3h43m, and the off-the-hour minutes (`:17`, `:23`) additionally keep the jobs off on-the-hour scheduling peaks.

The daily jitter is capped at 1800 s for two reasons, both of which break if it is raised: the latest start (01:00 UTC) must stay inside the same UTC day, because `market_run_id` is built from the UTC date (see below); and the latest finish (01:00 + the measured 2–2.5 h chain ≈ 03:30 UTC) must stay clear of the 05:07 UTC watchdog, or the job that exists to catch silent failures starts raising false alarms against a run that is still going. `tests/test_daily_scheduler_contract.py::test_outbound_timers_are_jittered_and_not_a_fixed_offset_apart` reads both bounds back out of the unit files and fails if either is violated.

The discovery timer previously read `05:45:00 Asia/Tokyo`, which was 45 minutes ahead of the old 06:30 JST daily but landed at 20:45 UTC on the *previous* day once the daily moved to 00:30 UTC. It was then moved to 23:43 UTC to restore a 47-minute lead — which is the fixed offset described above. At 21:17 UTC it runs at 06:17 JST, roughly three hours before the daily, restoring the original 05:45 JST placement without the constant spacing. Its worst case (jitter + the service's own 1800 s `TimeoutStartSec`) still finishes before the daily's earliest start, and it is only a pre-warm in any case: `scripts/backend.py daily` calls `run_discovery_tool()` itself, so moving or disabling this timer cannot break the daily run.

The daily trigger is expressed in UTC and must not be moved back to `06:30 Asia/Tokyo`. `pipelines/run_daily.py` builds `market_run_id` from the **UTC** date, so a 06:30 JST trigger fires at 21:30 UTC on the *previous* day. The collectors then see a run ID they have already produced output for, replay it instead of fetching, `effective_date` never advances, and the `INSERT IGNORE INTO market_index_snapshot` in `pipelines/market_alerts.py` becomes a no-op. The whole chain still exits zero with zero new rows and zero alerts; this is exactly the 2026-07-25 silent failure. Keeping the trigger at 00:30 UTC aligns the local day with the UTC day and removes the split structurally.

The daily job first refreshes the broad constituent radar, then collects the frozen active universe, appends/imports canonical observations, and derives rankings and alerts. Any required-stage failure prevents later stages from running and preserves the last-good database/public generation. `cardz-grade10-discovery.service` and its timer remain optional operator preflight tools; they are not required because the parent daily job performs the same discovery gate. Raw acquisition output and database credentials never enter Git.

`TimeoutStartSec` on the daily service is 21600 seconds (6 hours). A measured full chain — FX, 1468 GemRate pages, SNK, import, alerts — takes 2 to 2.5 hours. The former 7200-second (2-hour) limit killed the whole process tree in the middle of the GemRate crawl, so the outcome gate at the end of the wrapper never got a chance to run and the failure was silent. Do not lower it back.

## Publish mode

`cardz-market-cap-daily.service` sets `Environment=CARDZ_DAILY_PUBLISH=local`. The wrapper turns that into the flags it passes to `scripts/backend.py daily`:

| Value | Flags | Effect |
|-------|-------|--------|
| `local` (default) | `--publish --local-only` | Runs the full publish chain but writes only the local public tree. Needs no R2, canary or pointer credentials. |
| `remote` | `--publish` | Adds the R2 upload and conditional pointer promotion. `backend.env` must supply `CARDZ_PRODUCTION_R2_BUCKET`, `CARDZ_GENERATION_CANARY_COMMAND_JSON` and `CARDZ_POINTER_PROMOTE_COMMAND_JSON`; `pipelines/run_daily.py` refuses to start if any is missing. |
| `off` | none | The previous behaviour: `run_daily.py --backend-only`, collection and database sync only. |

The publish chain runs, in order, `canonical_public_snapshot.py` → `ensure_std_card_images.py --write` → `verify_images.py --allow-unreferenced` → the catalog-shrink ratchet → the manifest and snapshot promotion → quarantine of unreferenced assets → a strict `verify_images.py` → `npm run build --workspace @cardz/market-data` → `pipelines/publish-snapshot.mjs`. The shrink ratchet sits ahead of promotion and quarantine because quarantine moves files and is not reversible; its default tolerance is -5 percent and lowering it defeats the gate.

The Windows counterpart, `deploy/windows/run-cardz-daily.ps1`, defaults its `-Publish` parameter to `off`, not `local`. The divergence is deliberate: on Linux the mode is written explicitly into the unit file, so the default in the wrapper is only a manual-invocation fallback, whereas the Windows scheduled task `CARDZ-Market-Cap-Daily` was registered before publish existed and passes no `-Publish` at all. A `local` default there would have turned publishing on for the next 09:30 JST run through a file edit alone, with no scheduling change and no review. Publishing on Windows must be opted into from the task action.

Under `local` or `remote` the wrapper refuses to start unless `npm` is on `PATH`, because the build is the last step of a chain that has already spent two to two and a half hours of GemRate quota. Install Node system-wide: systemd's default `PATH` does not include nvm shims, and `ProtectHome=true` makes them unreachable regardless. The wrapper also redirects `npm_config_cache` into `data/runtime/npm-cache`, since `ProtectSystem=strict` leaves the service user's home read-only. It does not redirect `HOME`, which must keep pointing at the Playwright browser cache.

## Outcome gate and watchdog

`deploy/systemd/run-cardz-daily.sh` runs `scripts/backend.py daily` and then always runs `scripts/verify_daily_run.py`, whether the daily chain succeeded or not. The wrapper deliberately does not end with `exec`; `exec` would replace the shell with Python and the gate would never run. The gate checks price freshness, index-snapshot freshness, and per-source coverage. Exit 0 means the data landed, exit 1 means the run produced no fresh data, exit 2 means verification itself could not run (an infrastructure problem). A non-zero exit writes an alert file under `data/runtime/alerts/` and marks the systemd unit failed, so the unit's status reflects the **result**, not merely the process.

That built-in gate only runs when the daily service actually executed. `cardz-market-cap-watchdog.timer` covers the blind spot: a host that was off, a timer that was disabled, or a run that hung produces no gate invocation and therefore total silence. The watchdog runs `verify_daily_run.py --tag watchdog` independently at 05:07 UTC, records the daily unit's and timer's state first, and leaves an alert plus a failed unit even when nothing at all happened. It has no `Restart=` on purpose: a watchdog failure is the signal to preserve.

## Notification

A failed unit and an alert file are only visible to someone who goes looking. `scripts/notify_alert.py` is the layer that pushes the verdict outwards, and it covers the two death modes separately because no single mechanism sees both:

- **The gate ran and said no.** `verify_daily_run.py` calls `notify_alert.notify_failure()` in-process on exit 1 and exit 2. This is the only path that can report "the chain exited 0 but the data is wrong" — systemd cannot see a verdict, only an exit code, and by the time the exit code exists the gate has already formed the diagnosis.
- **The gate never ran.** `cardz-market-cap-daily.service` and `cardz-market-cap-watchdog.service` both declare `OnFailure=cardz-market-cap-alert@%n.service`. The templated unit runs `run-cardz-alert.sh %i`, which reads `ExecMainStatus`/`Result` off the dead unit and calls the same notifier. This is what covers `TimeoutStartSec` killing the process tree, `226/NAMESPACE`, and a crash before the gate is reached.

Both paths derive the same dedupe key from the unit or tag name (`cardz-market-cap-daily.service` → `daily`, matching `verify_daily_run.py --tag daily`), so one incident that trips both produces one message. A 30-minute quiet window suppresses the second facet.

The template unit must be installed but is never `enable`d — `OnFailure=` activates it by name. `cardz-daily-systemd.sh` installs it and hard-fails if `cardz-market-cap-alert@.service`, `run-cardz-alert.sh`, or `scripts/notify_alert.py` is missing; a missing template would leave systemd logging a single `Unit not found` line and nothing else, which is worse than never having wired notification at all.

Delivery is deliberately unopinionated. With `CARDZ_ALERT_WEBHOOK` unset the notifier writes state, skips the POST, and exits 0 — the behaviour is exactly what it was before (alert file plus failed unit), so no email/Telegram/Slack decision has to be made to deploy this. When the variable is set in the root-owned `0600` env file, the same verdict is POSTed as JSON. The payload carries the date, the stage that failed, the exit code, the log path, and the last few *error-matching* lines from that log — a plain tail would only show crawl progress. Credentials in those lines are redacted before they leave the host, and the webhook URL is never printed, logged, or echoed in an error message.

Repeats are throttled: the same status for the same key is delivered once, then re-sent only every `CARDZ_ALERT_REPEAT_DAYS` days (default 3), with `day N of this failure` appended. A three-day outage produces two messages, not three identical ones. A failed POST does not advance the throttle, so an unreachable receiver is retried on the next run rather than silently counted as delivered. A passing gate clears the key, so the next failure alerts immediately instead of being suppressed as "unchanged".

Send one test message without waiting for a real failure:

```bash
sudo systemd-run --uid=cardz --wait --pipe --property=EnvironmentFile=/etc/cardz-market-cap/backend.env /opt/cardz-market-cap/.venv-backend/bin/python -X utf8 /opt/cardz-market-cap/scripts/notify_alert.py --self-test
```

`systemd-run` is used rather than `sudo -u cardz env $(grep …)` on purpose: the env file is root-owned `0600`, so the service user cannot read it directly, and passing the value on a command line would put the webhook URL into `/proc` and every `ps` listing on the box.

`--self-test` uses its own key, bypasses the throttle, and leaves the real state untouched, so it can be run any number of times without affecting live dedupe. Clear a resolved incident by hand with `--resolved --key daily`.

## Checking status

`deploy/linux/cardz-status.sh` is the single read-only entry point for scheduling state, and the Linux counterpart of `deploy/windows/cardz-status.ps1`. It takes no arguments (`-h`/`--help` prints usage; anything else exits 2) and is overridable through `CARDZ_REPO_ROOT`, `CARDZ_DAILY_UNIT`, `CARDZ_DAILY_TIMER`, `CARDZ_WATCHDOG_UNIT`, `CARDZ_WATCHDOG_TIMER`.

```bash
deploy/linux/cardz-status.sh
```

It prints four sections: both timers' enabled/active state with last and next trigger; the last daily and watchdog service result with exit code and finish time; any uncleared files in `data/runtime/alerts/`; and the most recent outcome-gate verdict. Exit 0 means everything is healthy, exit 1 means a unit is missing, a unit failed, or alerts are outstanding. Exit codes are annotated inline, including `226` for the `ReadWritePaths` namespace failure described above.

It never re-runs `verify_daily_run.py` — it greps the `[verify]` lines out of the newest `daily_*.log` and `watchdog_*.log` under `data/runtime/logs/` instead. It therefore touches no database and writes no alert file, and is safe to run repeatedly during a soak without polluting the record.

After installation, let the timer complete at least two consecutive unattended runs before production cutover. Verify with `deploy/linux/cardz-status.sh`, `systemctl list-timers`, and `journalctl -u cardz-market-cap-daily.service`; inspect status/counts only and never print the environment file. Confirm the NEXT column reads 00:30 and 05:07 UTC — anything else means the trigger was edited and must be corrected before cutover.
