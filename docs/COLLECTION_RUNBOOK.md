# COLLECTION RUNBOOK — cardz-market-cap-fe-db-20260805

> **The code in this repo is the authority.** Every command, flag, default, and number below was
> verified against the argparse definitions and constants in the scripts of this checkout
> (branch `rebuild/036-foundation`). Where older runbooks in other checkouts disagree, this
> document (and the code it cites) wins. Each number cites its source file in parentheses.

Repo root (Windows): `C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805`

---

## Ports & environments

### CDP Chrome ports

| Port | Owner | Rule |
|------|-------|------|
| **9333** | CARDZ dedicated headed-Chrome CDP profile (`%LOCALAPPDATA%\cardz-chrome-cdp-9333`) | The only port CARDZ collection should use. Started/revived by `scripts/ensure_chrome_cdp.ps1` (default `-Port 9333`). |
| **9222** | Other tooling (the code comment in `pipelines/pricecharting_cf_session.py` says it "may be owned by the Codex browser profile" with a different Cloudflare clearance state) | **Do not use for CARDZ.** |

Caveats you must know (code behaviour, not doctrine):

- `pipelines/pricecharting_cf_session.py` `fetch` connects to **9333 only**
  (`_try_cdp_ports()` returns `[9333]`); if 9333 is down it fails fast (exit **2**) instead of
  falling back to a foreign Chrome profile. Revive with `ensure_chrome_cdp.ps1 -Port 9333`.
- `scripts/pc_full900_supervisor.ps1` defaults to `-Port 9333`, matching the shard runner's
  hardcoded `ensure_cdp_or_raise(9333)` (`pipelines/pc_full_shard_runner.py`).
- `pipelines/collect_control.py` reads `CARDZ_CDP_PORT` from the environment, default `9333`.

### Python / OS split

| Task | Interpreter |
|------|-------------|
| PriceCharting CDP fetching (headed Chrome lives on Windows) | Windows Python: `C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe` (used by `scripts/pc_full900_supervisor.ps1`); `pipelines/collect_control.py` also references `.venv-backend-windows/Scripts/python.exe` |
| DB ingest (C11, SNK kline, rebuild orchestrator) | WSL Ubuntu venv: `/home/jackson0202/cardz-market-cap/.venv-backend/bin/python` (the exact interpreter `pipelines/pc_cdp_sold_refresh_win.py` invokes for its auto-ingest leg) |
| Canonical MySQL | Windows host, `127.0.0.1:3308`, credentials in `backend.env` (`PROJECT_STATE.md`) |

Start/verify the CARDZ Chrome before any PriceCharting work:

```powershell
powershell -NoProfile -File scripts\ensure_chrome_cdp.ps1 -Port 9333
```

Outputs `CDP_OK` or `CDP_REVIVED` (exit 0) when `http://127.0.0.1:9333/json/version` answers;
`CHROME_NOT_FOUND` / `CDP_REVIVE_FAILED` (exit 1) otherwise (`scripts/ensure_chrome_cdp.ps1`).

---

## 1. GemRate public card pages — `pipelines/gemrate_source.py`

### Purpose

Population (grading pop) collection per GemRate card id. Three legs, in preference order:
direct API (needs `GEMRATE_API_KEY`), public exact card page via Playwright (keyless), and a
Grade10 mirror fallback. Produces per-card capture directories with raw receipts
(sha256-verified) so downstream stages can prove provenance.

### Full run (bulk page dump)

```bash
python -X utf8 pipelines/gemrate_source.py public-card-dump \
  --ids-file <ids.txt> \
  --resume \
  --workers 3 \
  --delay 0.3
```

Flags (argparse in `pipelines/gemrate_source.py`): `--ids-file`, `--out`, `--resume`, `--limit`,
`--delay` (default **0.3** s), `--chunk-size` (default **25** = `WEBSITE_CHUNK`), `--workers`
(default **1**), `--manifest-out`.

### Incremental (daily)

```bash
python -X utf8 pipelines/gemrate_source.py daily \
  --ids-file <roster.txt> \
  --speed medium \
  --workers 4
```

Flags (argparse in `pipelines/gemrate_source.py`): `--ids-file`, `--speed` `{slow,medium,fast}`
(default `medium`; delays `slow=3.0`, `medium=1.0`, `fast=0.15` s — `SPEEDS` dict),
`--with-history`, `--limit`, `--identity-file`, `--mirror-root`, `--website-budget-seconds`
(default **3600** = `DEFAULT_WEBSITE_BUDGET_SECONDS`), `--workers` (default **4**),
`--volume-slug`.

Daily leg order: direct API until the quota dies (`POP_HTTP_429` breaks the loop — a code
comment records a measured clean cut at request ~1000 of a 1468-card roster on 2026-07-26,
`pipelines/gemrate_source.py`), then the remaining cards fall to the public card page. The
website leg runs **two passes**: `(workers, max(0.3, delay))` first, then a single-worker
"slow retry" pass at `SPEEDS["slow"]=3.0` s, both under one monotonic budget deadline.

### Resume / checkpoint

- `--resume` (public-card-dump) and daily both use `_has_complete_public_card_capture`: a card
  is skipped only if `card_details.json` exists **and** its raw receipt matches **and** the raw
  payload sha256 verifies. Captures marked `dom_evidence_only` are refetched
  (`pipelines/gemrate_source.py`).
- Captures persist per chunk (`--chunk-size`, default 25), so killing the process loses at most
  the current chunk.
- Multi-worker mode shards ids round-robin; each worker gets its own browser and its own 429
  ladder, staggered by `offset * 1.5` s (`pipelines/gemrate_source.py`).

### Failure modes / receipts / exit codes

- 429 backoff ladder: `RATE_LIMIT_LADDER = (30, 60, 120, 300, 600)` s
  (`pipelines/gemrate_source.py:88`); exhausted → failure receipt reason
  `rate_limited_429_exhausted`.
- 404/410 pages **fast-fail** with reason `page_http_404` / `page_http_410` (waiting the JSON +
  DOM windows used to burn ~25 s per dead id — comment in `pipelines/gemrate_source.py`).
- Budget exhaustion → `budget_exhausted` receipts for the untouched remainder.
- Other receipt reasons: `missing_response`, `browser_unavailable`,
  `browser_collection_failed`, plus payload-validation reasons.
- Exit codes — `public-card-dump`: **2** no ids, **0** promotable manifest, **1** partial.
  `daily`: **2** no ids, **1** any rejected/partial leg, **0** clean. Internal harvest legs
  return **3** (`KEY_DEAD`) / **4** (direct quota spent) — surfaced in the daily report, not as
  the process exit (`pipelines/gemrate_source.py`).

---

## 2. GemRate brute catalog — `pipelines/gemrate_brute_harvest.py`

### Purpose

Full catalog sweep of GemRate sets (TCG sets, year ≥ 2020 filter) via `curl_cffi` Chrome
TLS-fingerprint impersonation — no browser needed. Builds the id universe used by rebuild S2.

### Full run

```bash
python -X utf8 pipelines/gemrate_brute_harvest.py --all-sets --resume
```

Flags (argparse in `pipelines/gemrate_brute_harvest.py`): `--all-sets`, `--set-id`, `--query`,
`--limit`, `--resume`. Pacing: `SET_DELAY = 1.0` s between sets, one retry per failed set after
`RETRY_SLEEP = 10.0` s (`pipelines/gemrate_brute_harvest.py`).

Outputs (all atomic temp + `os.replace`) under `data/private/gemrate_brute/`:
`set_{set_id}_{safe}.jsonl` per set, `all_cards.jsonl`, `failed_sets.jsonl`, and
`psa10_1000_plus.jsonl` (cards with `psa_10 >= 1000`).

### Incremental / retry

```bash
python -X utf8 pipelines/gemrate_resume_failed.py
```

`pipelines/gemrate_resume_failed.py` takes **no CLI flags**: it reads `failed_sets.jsonl`,
retries each set, rebuilds `all_cards.jsonl` + `psa10_1000_plus.jsonl` from all `set_*.jsonl`
files, and rewrites `failed_sets.jsonl` with only the still-failing sets (atomic writes).

### Resume / checkpoint

`--resume` skips any per-set file that already exists non-empty and reuses its rows when
rebuilding the aggregates (`pipelines/gemrate_brute_harvest.py`).

### Freshness rule used by the 036 rebuild

`stage_discover` (S2) in `pipelines/rebuild_036.py` reuses `data/private/gemrate_brute/all_cards.jsonl`
**only if its mtime is < 7 days old** (`(time.time() - mtime) < 7 * 24 * 3600`); otherwise it
re-runs `gemrate_brute_harvest.py --all-sets`. The merged worklist must be ≥ **3000** ids or S2
aborts; S2 then calls `collect_public_card_details(..., delay=0.2, resume=True, chunk_size=200,
workers=6)` (`pipelines/rebuild_036.py`).

### Failure modes

Failed sets land in `failed_sets.jsonl` with the error; the harvest keeps going. Rerun via
`gemrate_resume_failed.py` until `failed_sets.jsonl` is empty.

---

## 3. PriceCharting — headed Windows Chrome CDP (port 9333)

### Purpose

PSA10 sold-price observations from pricecharting.com. **Cloudflare doctrine: headless is
blocked.** The only working path is a real headed Chrome with remote debugging, attached over
CDP. `pipelines/pricecharting_cf_session.py` deliberately has **no launch fallback** in `fetch`
("Chrome launched here (headless especially) is the known Cloudflare-blocked mode").

### Precondition (always)

```powershell
powershell -NoProfile -File scripts\ensure_chrome_cdp.ps1 -Port 9333
```

Optional session check (exit **0** only when the page cleared CF **and** a `cf_clearance`
cookie exists, else **2**):

```powershell
python -X utf8 pipelines\pricecharting_cf_session.py connect --port 9333 --timeout 120
```

### Incremental refresh (Windows Python)

```powershell
python -X utf8 pipelines\pc_cdp_sold_refresh_win.py --sleep 4.0
```

Flags (argparse in `pipelines/pc_cdp_sold_refresh_win.py`): `--limit` (default **0** = all),
`--offset` (**0**), `--sleep` (**4.0** s between items), `--challenge-wait` (**120.0** s — waits
on the same 403 page, never reloads), `--cdp-port` (**9333**), `--cdp-already-ensured`,
`--resume-report`, `--variant-ids-file`, `--no-ingest`.

Behaviour (`pipelines/pc_cdp_sold_refresh_win.py`):

- Reads the map `data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl`; writes its
  report to `data/runtime/operator/collect/pc_cdp_refresh_report.json`.
- One CDP tab. Verified-only atomic cache writes: temp `.next` → `os.replace` only when status
  is 200/`challenge_resolved` **and** HTML length > **5000** **and** not a CF page **and**
  product-id + canonical-URL identity match **and** an explicit PSA10 figure is present.
- Rate-limit backoff ladder `BACKOFF_LADDER = (30.0, 60.0, 120.0)` s.
- Per-item statuses: `ok`, `missing`, `navigation_error`, `rate_limited`, `cf_or_fail`,
  `explicit_psa10_missing`, `product_id_mismatch`, `http_or_content_fail`.
- Auto-ingest fires only when every item is `ok` (no failures, no CF): it shells into WSL —
  `wsl.exe -d Ubuntu -- bash -lc "cd '<root_wsl>' && /home/jackson0202/cardz-market-cap/.venv-backend/bin/python -X utf8 pipelines/c11_pc_sold_ingest.py --map data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl --write"`.
- Exit **0** only if fully complete including ingest exit 0; otherwise **1**.

Manual ingest (WSL) when you ran with `--no-ingest` or need a dry run first:

```bash
python -X utf8 pipelines/c11_pc_sold_ingest.py \
  --map data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl --dry-run
python -X utf8 pipelines/c11_pc_sold_ingest.py \
  --map data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl --write
```

Flags (argparse in `pipelines/c11_pc_sold_ingest.py`): `--map` (default
`data/runtime/private-source-map/c11_pc_ebay_map.jsonl` — **not** the full900 map; always pass
it explicitly), `--write`, `--dry-run`, `--limit`, `--report`. Effective write =
`--write and not --dry-run`. Summary JSON goes to
`qualified-pool-reports/c11_pc_sold_{write|dry}.json`; the process returns **0** and raises
(after rollback + failure-ledger record) on DB write failure.

### Full run (900-card rebuild of the map)

```powershell
powershell -NoProfile -File scripts\pc_full900_supervisor.ps1 -Port 9333
```

Chain: `pc_full900_supervisor.ps1` → `pc_full_serial_driver.py` → `pc_full_shard_runner.py`
(fetches via CDP, cache-fill assisted by `pricecharting_cf_session.py fetch` semantics).

- Supervisor (`scripts/pc_full900_supervisor.ps1`): `param([switch]$Once, [int]$PollSeconds = 45,
  [int]$Port = 9333)`. Lock file
  `data\runtime\private-reports\fill\PC-FULL-900\cdp.lock`, auto-cleared if the PID is dead or
  the lock is older than **3 min**. Runs exactly one serial worker
  (`pc_full_serial_driver.py --shards 6`) under
  `C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe`. Exit **0** when all
  6 `summary_shard_*.json` exist; `-Once` with CDP down → exit **2**.
- Serial driver (`pipelines/pc_full_serial_driver.py`): flags `--shards` (default **6**),
  `--runner`, `--consolidate-only`. Runs shards serially; a nonzero shard rc is recorded in the
  failure ledger and returned. Then `consolidate_maps` builds
  `c11_pc_ebay_map_full900.jsonl` gated by the **current exact DB identity**
  (`catalog_source_identity` with `source_code='pricecharting'`, `match_status='exact'`); fails
  closed on conflicting products, skips stale/non-exact rows, writes atomically.
- Shard runner (`pipelines/pc_full_shard_runner.py`): flags `--shard`, `--shards` (**6**),
  `--shard-file`, `--force`, `--limit` (**0**), `--offset` (**0**), `--variant-id`,
  `--candidate-url` (repeatable), `--import-existing-results`. Hardcodes
  `ensure_cdp_or_raise(9333)` before every fetch. FileLock timeout **600** s, stale **90** s.
  036 Gate 1: collector is **DB read-only**; it emits `evidence_manifest_shard_*.jsonl` for
  `attached`/`attached_no_sold` and map rows only for `attached`. Sleeps **0.35** s between
  items. Supported languages: `pokemon: {en, ja}`, `one-piece: {en, ja}`
  (`PRICECHARTING_SUPPORTED_LANGUAGES`); others get `language_blocked` statuses.

### Resume / checkpoint

- Shard runner resume: **"only a current exact DB binding makes a card complete"** — prior
  JSONL results are diagnostic only and are re-verified against the DB
  (`pipelines/pc_full_shard_runner.py`).
- Incremental refresh: `--resume-report` continues from the previous
  `pc_cdp_refresh_report.json`.
- Cache writes are verified-only and atomic everywhere; blocked/CF HTML is snapshotted beside
  the target as `*.rejected` and never lands on the cache path
  (`pipelines/pricecharting_cf_session.py`).

### Failure modes / exit codes

- Shard runner: CDP fail streak ≥ **3** → summary with `"fatal": "CDP fail ×3"`, exit **2**;
  normal completion exit **0** (`pipelines/pc_full_shard_runner.py`).
- `pricecharting_cf_session.py fetch`: **0** verified non-CF HTML, **4** terminal tiny 404
  (status 404 and < **4096** bytes — a stale candidate URL; try the next candidate), **2** CDP
  unavailable or still-CF. `connect`: **0** cleared + `cf_clearance` cookie, else **2**.
  `launch`: **0**/**2**. Every outcome is recorded in the failure ledger
  (`failure_ledger.record_failure` / `record_resolution`).
- All PC collectors record into the failure ledger; check it plus the shard summaries and
  `pc_cdp_refresh_report.json` before rerunning anything.

---

## 4. SNKRDUNK — `pipelines/snkrdunk_bulk.py` + `pipelines/snk_market_data.py`

### Purpose

JPY market data (listings, size-chips, trading history / kline) from SNKRDUNK's internal JSON
API (CloudFront, no auth): `/v1/apparels`, `/v2/size-chips`, `/v3/trading-history`. The
`x-version` header is optional — fetch failure is non-fatal ("x-version fetch failed,
continuing without header", `pipelines/snkrdunk_bulk.py`).

### Full run (bulk dump)

```bash
python -X utf8 pipelines/snkrdunk_bulk.py \
  --discover-from <seed> --max-ids 2000 --out snkrdunk_dump.jsonl
```

Flags (argparse in `pipelines/snkrdunk_bulk.py`): positional `ids`, `--discover-from`,
`--max-ids` (default **2000**), `--delay` (**1.5** s), `--condition`, `--out` (default
`snkrdunk_dump.jsonl`), `--discover-only`, `--price-refill-candidates` +
`--price-refill-out` (must be paired) + `--price-refill-seeds`.

Client defaults: `SnkrdunkApi(delay=1.5, timeout=30, retries=4)`; on 429/5xx it sleeps
`max(retry_wait, 2.0 * (attempt + 1))` where `retry_wait` honours `Retry-After`
(`pipelines/snkrdunk_bulk.py:78`). Threaded harvests use
`SnkrdunkApiPool(workers=16, delay=0.0)` with thread-local sessions.

### Incremental (033-style fast daily lane)

```bash
# harvest (16 workers, zero delay, no Chrome involved)
python -X utf8 pipelines/snk_market_data.py \
  --ids-file <roster> --workers 16 --delay 0.0 --run-id <RUN_ID> --out <out.jsonl>

# ingest the harvest JSONL into MySQL (WSL backend venv)
python -X utf8 pipelines/snk_market_data.py --ingest-jsonl <out.jsonl>
```

Flags (argparse in `pipelines/snk_market_data.py`): positional `ids`, `--ids-file`,
`--discover-from`, `--max-ids` (default **600**), `--delay` (**0.0**), `--workers` (**16**),
`--condition` (default `trading_card_single_psa10`), `--run-id`, `--discover-only-out`,
`--out`, `--ingest-jsonl`, `--ingest-archive-dir`, `--recover-local-history`,
`--recovery-source`, `--recovery-archive-dir`, `--recovery-report`, `--variant-allowlist`,
`--dry-run`.

After ingest, `PROJECT_STATE.md` prescribes
`python pipelines/operator_control.py db-tidy --project-ingested-history`.

### Resume / checkpoint

Harvest `run()` (`pipelines/snk_market_data.py`):

- A request sha256 is computed over the sorted ids + condition.
- A **complete** existing out file with matching sha → replayed as success (no refetch).
- An **incomplete** existing out file → `RuntimeError` (refusing to silently mix runs).
- Progress goes to `<out>.partial` + `<out>.state.json` (validated on `runId` and
  `requestSha256`); promotion to the final path is a single `os.replace` and happens **only**
  when the run is complete with zero failures.

`snkrdunk_bulk.py pull_all` resumes by re-reading the out JSONL and skipping ids that already
have a non-error row.

### Kline ingest — fail-closed rules (`pipelines/snk_market_data.py`)

- PSA10 condition only.
- Exact `snkrdunk` `catalog_source_identity` ownership is re-asserted **per row**; loss →
  `RuntimeError("SNK identity lost exact ownership")`.
- FX USD/JPY must come from the DB, else `RuntimeError` — empty klines are skipped
  ("never invent prices").
- `effective_at` = observed-day 23:59:59; quarantined variants stay quarantined.
- `--ingest-archive-dir` mode ingests files matching `snk(_price)?_harvest*.jsonl` in timestamp
  order with `ingest_mode="backfill"`.

### Failure modes / exit codes

Harvest exits `0 if report["failed"] == 0 else 1` (`pipelines/snk_market_data.py`). Ingest
raises on any integrity violation rather than writing partial rows. Failures are recorded in
the failure ledger.

---

## 5. Orchestrated rebuild — `pipelines/rebuild_036.py` via `operator_control.py rebuild-036`

### Purpose

The full 036 rebuild pipeline. `rebuild_036.py` is **not an entry point** — it is invoked only
through `operator_control.py` (module docstring, `pipelines/rebuild_036.py`).

### Stage map (`LINEAR_STAGES` / `POST_ACTIVATION_STAGES`, `pipelines/rebuild_036.py`)

| Stage | Name | Notes |
|-------|------|-------|
| S0 | `preflight` | always_run |
| S1 | `migrate` | |
| S2 | `discover` | GemRate universe (see line 2 above; needs old checkout `C:/Users/jackson0202/Documents/Playground/cardz-market-cap` present) |
| S3 | `pop-land` | |
| S4 | `identity-resolve` | |
| S5 | `bind` | |
| S6 | `pc-replay` | |
| S7 | `snk-refresh` | |
| S8 | `price-materialize` | |
| S9 | `image-bind` | |
| S10 | `prune-plan` | |
| S11 | `validate` | always_run |
| S12 | activation | **separate subcommand** `rebuild-036-activate`, not a linear stage |
| S13 | `prune-apply` | post-activation, explicit `--stage` only |
| S14 | `canary` | post-activation, explicit `--stage` only, always_run |

### Full run (linear S0..S11)

```bash
python -X utf8 pipelines/operator_control.py rebuild-036 \
  --generation 036_<YYYYMMDD>T<HHMMSS>Z
```

Flags (argparse in `pipelines/operator_control.py`): `--generation` (required, must match
`^036_\d{8}T\d{6}Z$`), `--resume` (explicit alias — linear runs always resume from
checkpoints anyway), `--stage`, `--invalidate-from`, `--force-stage`, `--dry-run`,
`--credentials-env`, `--freshness-hours` (default **72.0**).

**Never start a second orchestrator while one is running.** Note this is doctrine: there is no
process-level mutex in the code — only per-stage checkpoint rows (`running` status + attempt
counter) and S0's stale-run abort stand between you and a corrupted run.

### Incremental / single stage

```bash
python -X utf8 pipelines/operator_control.py rebuild-036 \
  --generation 036_<...>Z --stage <stage-name>
```

Activation (S12) and post-activation:

```bash
python -X utf8 pipelines/operator_control.py rebuild-036-activate \
  --generation 036_<...>Z --receipt-sha256 <sha256>

python -X utf8 pipelines/operator_control.py rebuild-036 \
  --generation 036_<...>Z --stage prune-apply
python -X utf8 pipelines/operator_control.py rebuild-036 \
  --generation 036_<...>Z --stage canary
```

`rebuild-036-activate` requires `--receipt-sha256` (`pipelines/operator_control.py`).
`prune-apply` and `canary` run **only** via an explicit `--stage`, and `_run_single_stage`
first calls `_assert_activated` and `_run_freeze_proof` (`pipelines/rebuild_036.py`) — they can
never fire from a linear run. `rebuild-036-freeze` and `rebuild-036-unfreeze --confirm` exist for
the deliberate freeze / unfreeze steps.

**Freeze:** `python -X utf8 pipelines/operator_control.py rebuild-036-freeze`. It reads the
rebuild account out of `rebuild.env`, pipes the SQL into the container client on stdin (the
password never reaches a command line or a log), and runs the 1142 proof before returning — a
freeze that did not actually apply cannot report success. Do **not** hand-write this SQL: MySQL
reads `_` in a grant db name as a wildcard, so `REVOKE ... ON \`cardz_market_cap\`.*` fails with
error 1141, mysql stops at the first error, the `GRANT SELECT` after it never runs, and the
freeze silently does not happen. The command spells the escaped name once so this cannot recur.

### Resume / checkpoint semantics (`pipelines/rebuild_036.py`)

- Checkpoints live in the MySQL table `cardz_rebuild_checkpoint`. Linear runs always resume:
  completed stages are skipped unless marked `always_run` (`preflight`, `validate`, `canary`).
- Each stage records an input sha; **drift** between the recorded and current inputs aborts the
  run with advice to use `--invalidate-from <stage>`.
- Rerunning a stage that previously **failed** requires `--force-stage`.
- A stage whose function is not implemented (`None`) makes the linear runner return **3**.
- Credentials: DDL/DML during the rebuild go through `--credentials-env`, default
  `data/runtime/config/rebuild.env` (`DEFAULT_CREDENTIALS_ENV`, `pipelines/rebuild_036.py`).

### S0 preflight gates (`stage_preflight`, `pipelines/rebuild_036.py`)

- Writer-freeze proof: the frozen backend account must fail DDL/DML with MySQL error **1142**
  (`scripts/prove_writer_freeze.py`, run with the **unmodified** `backend.env`).
- Prune allowlist checks pass.
- ≥ **14** cardz scheduled tasks exist and all are Disabled.
- Backup dump sha256 verifies.
- Zero `market_ingest_run` rows with `status='running'`.

### Freeze / unfreeze 生命週期（每次補跑 stage 都會撞到）

`rebuild-036-unfreeze --confirm` 會 **DROP 咗 `cardz_rebuild` 呢個 user**。所以「跑完一次 036
之後想補跑一兩個 stage」嘅時候，`--credentials-env rebuild.env` 一定連唔到 —— 唔係 env 壞咗，
係個 user 真係唔喺度。次序永遠係：

```bash
# 1. 兩條現役 task 先 Disable（S0 gate 要求；就算行 --stage 跳過 S0 都要做，
#    因為 freeze 期間 backend 只剩 SELECT，夜鏈 03:30 撞正就靜靜失敗）
powershell -NoProfile -Command "Disable-ScheduledTask -TaskName 'CARDZ-036-Nightly-Collect-Accept'; Disable-ScheduledTask -TaskName 'CARDZ-036-Morning-Browser-Lanes'"

# 2. 重新 freeze（會重建 cardz_rebuild + 即場行 1142 proof）
python -X utf8 pipelines/operator_control.py rebuild-036-freeze

# 3. …跑 stage / activate…

# 4. unfreeze + Enable 返兩條 task —— 呢步唔做 = 夜鏈死
python -X utf8 pipelines/operator_control.py rebuild-036-unfreeze --confirm
powershell -NoProfile -Command "Enable-ScheduledTask -TaskName 'CARDZ-036-Nightly-Collect-Accept'; Enable-ScheduledTask -TaskName 'CARDZ-036-Morning-Browser-Lanes'"
```

`--stage <name> --force-stage` **唔行 S0 preflight**（`_run_single_stage` 只對
`prune-apply` / `canary` 叫 `_assert_activated` + `_run_freeze_proof`）。方便，但代價係
上面第 1、4 步冇人幫你做，要自己記。

### 補數據入現役 generation（gap intake，唔開新 generation）

新綁定嘅卡（SNK 或 PC）落咗 `catalog_source_identity` 之後**唔會自己上 FE**。cohort 係喺 S11
`stage_validate` 每次重算嘅，所以要行足下游鏈：

```bash
G=036_20260808T084217Z
for S in snk-refresh price-materialize image-bind; do
  python -X utf8 -u pipelines/operator_control.py rebuild-036 --generation $G --stage $S --force-stage
done
python -X utf8 -u pipelines/operator_control.py rebuild-036 --generation $G --stage validate
python -X utf8 -u pipelines/operator_control.py rebuild-036-activate --generation $G --receipt-sha256 <新 receipt>
```

查新綁定嘅卡而家喺邊個 cohort（`qualified_market_pending` = 未夠料上 FE）：

```sql
SELECT crm.cohort, COUNT(DISTINCT si.variant_id)
  FROM catalog_source_identity si
  JOIN catalog_rebuild_member crm ON crm.variant_id = si.variant_id
 WHERE si.source_code='snkrdunk' AND si.match_status='exact'
   AND si.updated_at >= NOW() - INTERVAL 3 HOUR
 GROUP BY crm.cohort;
```

**PriceCharting gap intake 係四步，唔係一步**：seed →
`pc-identity-reverify` → `pc_cache_replay` → 由 `pc-replay` 起 linear 重跑。S8 讀嘅係 **replay
目錄**，唔係 capture 目錄 —— 抄漏咗就係「爬咗嘢但入唔到庫」。

### 缺價卡搵返正確 PC 產品 —— `pipelines/pc_identity_discover.py`

**幾時用**：張卡有 pop、有 GemRate 身份，但 `catalog_source_identity` 冇任何 `exact` 價源。
`pc-identity-reverify` **幫唔到手** —— 佢只係重新審張卡**已經有**嘅 binding，而呢批卡嘅
binding 本身就指錯產品（One Piece 復刻卡保留原始編號，舊 binding 就係照個號綁落原本嗰套）。

**點解唔用搜尋**：逐張搜 PriceCharting 實測 35 張只有 2 張唯一解
（[POSTMORTEM](POSTMORTEM_OP_GAP_20260809.md)）。呢條線唔搜尋，佢**枚舉**：
`/category/one-piece-cards` → 137 個 console slug；`/console/<slug>` → 成套卡連 product id
（每頁 150 行，之後跟佢自己個 `cursor`）。「PriceCharting 幾百萬件貨邊件係佢」變成
「呢 233 行邊行係佢」。

```bash
# 睇清楚先（唔寫 DB；console 頁會 cache，第二次行好快）
python -X utf8 -u pipelines/pc_identity_discover.py --tcg one-piece --language en --delay 1.2
# 落提案
python -X utf8 -u pipelines/pc_identity_discover.py --tcg one-piece --language en --delay 1.2 --write
```

**佢只提案，唔提升。** 生還者寫 `manual_review` + 抓產品頁 + 補 map 行，之後照行
`pc-identity-reverify` 由現有 fail-closed 合約判 `exact`。一個判官 = 由呢條線入嘅卡同世界上
其他卡係同一把尺；呢度有 bug 最多令我哋少一個 binding，唔會多一個錯 binding。

**點睇 hold 記錄**（artifact：`data/runtime/rebuild-036/pc-identity-discover-*.json`）：

| 欄 | 意思 |
|---|---|
| `askedNumber` | 我哋要嘅編號 |
| `pageCarries` | 嗰版真係載住咩前綴（`OP06x224` = 224 行 OP06） |
| `reachedIdentityChecks` | 幾多行過到號碼呢關、再入身份檢查 |
| `refusedBy` | 拒絕理由分佈（`number` / `character_mismatch` / `product_mismatch` / `print_signature`） |

`reachedIdentityChecks: 0` = 成版都冇我哋個號 → 多數係**我哋 catalog 個號錯**（見下）。
`>0` = 有行同號但身份對唔上 → 睇 `rejections`。

**2026-08-09 實測（One Piece en，pop≥1000，100 張）**：proposed 30、no_survivor 51、
no_console 11、ambiguous 8。三種 hold 各自嘅意思：

1. **`no_survivor` 且 `reachedIdentityChecks: 0`（23 張）** —— catalog 身份缺陷，唔係比對缺陷。
   例：v1300 `set_code='ST01'`、`collector_number='ST01-007'`，但 `set_name` 係 Wings of the
   Captain。實測嗰版 224/224 行全部 OP06 前綴，而 OP06-007 係 **Shanks 唔係 Nami**。
   **唔准為咗夾到而放鬆前綴比較** —— 放鬆咗就會將 Nami 綁落 Shanks。
2. **`no_console`（11 張）** —— GemRate 個 set name 喺 PriceCharting 根本唔係一套
   （例：`3rd Anniversary Brothers Tournament`）。
3. **`ambiguous_survivors`（8 張）** —— 幾行同時過晒閘，照 hold，唔猜。

**促銷卡（promo）嘅號碼寫法**：GemRate 掉咗前綴（`062`），PriceCharting 保留（`OP05-062`）。
`numbers_agree()` 因此容許「我哋個 bare number = 佢個尾號」，**只限 One Piece**。
呢個唔係放鬆閘：同一版真係有一行 `P-062` 而佢係 Hody & Hyouzou —— 尾號自己從來唔決定任何嘢，
角色/產品/印刷簽名三關照跑。Pokémon 促銷寫法係 `085/SVP`，唔准套呢條規矩。

### 已知效能債

`stage_validate` 而家要行 ~5 分鐘。`EXPLAIN` 顯示佢會 materialize
`market_price_daily`（358,164 行）做兩次 full table scan（`derived15`、`derived40`）。
未修；唔好因為佢慢就以為 hang 咗。

---

## 6. FE 對數 —— 點解 activation 咗 FE 都唔郁

**行過一次先，唔好靠估。** FE 有兩個 data mode，睇 `CARDZ_DATA_MODE`：

| mode | 讀邊度 | 用途 |
|---|---|---|
| `live-db` | MySQL 3308 直讀，即刻反映最新 accepted ranking generation | 開發 / 對數 |
| `baked-snapshot` | `MARKET_DATA_POINTER_PATH` 指住嘅 `latest.json` | 貼近 LIVE 嘅預覽 |

2026-08-09 真事：036 activate 咗，1190 張卡入咗 universe，但 `:3800` 一直得 762 張。原因係
`fe03-server.ps1` 設緊 `MARKET_DATA_POINTER_PATH=publish-staging\latest.json` —— 個 pointer
係 **7 月 28 號**焗出嚟嘅。睇落好似「採集唔夠數據」，其實 FE 根本冇讀個 DB。**任何「FE 數
唔夠」嘅投訴，第一步係分清 mode，唔係去查採集。**

診斷（一句分勝負）：

```bash
curl -s http://127.0.0.1:3800/api/health | python -X utf8 -c "import sys,json; d=json.load(sys.stdin); print(d.get('dataMode'), d.get('generation'), d.get('universeSize'))"
```

`dataMode` 係 `baked-snapshot` 而個 generation hash 對唔上現役 lock → 就係呢個陷阱。改
`live-db` 要順手 `Remove-Item Env:\MARKET_DATA_POINTER_PATH`：個 pointer 留喺 env 度會贏。

要留喺 `baked-snapshot` 就一定要由**現役 generation 重焗** `latest.json`，唔係改 mode 算數。

另：`npm run build` 會 `rmSync` 掉 `apps/web` 底下啲卡圖（`sync-snapshot.mjs` 只認
seed-snapshot）。手抄落去嘅 generation 圖每次 build 完要再抄一次，而且要連 `_200` / `_600`。

---

## 缺陷形狀清單（每次事故沉澱一條；查新 bug 之前先對呢張單）

呢度唔係 bug 列表，係**形狀**列表。每條都係喺呢個 repo 真係炸過一次，而且大機會有第二個
未搵到嘅實例。查一個「數據明明有但用唔到」嘅問題時，由上到下逐條試。

1. **一個欄位擔起兩個意思。** 例：`market_grader_population_observation.top_grade_label`
   有陣時係 `'top'`（未拆解），有陣時係 `'10'`。acceptance lane 認 label 唔認數字，所以
   同一個數字喺唔同 label 下面，一個收一個唔收。
2. **檢查窄過佢守嗰個寫入。** 寫入用 `(a,b,c,d)` 做 unique key，檢查只睇 `(a,b)` →
   個檢查以為自己攔到嘢，實情永遠 pass。
3. **upsert 淨係喺 INSERT 講清楚意思，UPDATE 唔講。**（2026-08-09，131 張卡）
   `ON DUPLICATE KEY UPDATE` 冇 restate `top_grade_label`，所以早一條 lane 用 `'top'`
   佔咗嗰行之後，新 lane 寫入正確嘅 PSA-10 數字但個 label 冇變 → acceptance 按 label 拒收。
   **規矩：唔喺 unique key 入面、但決定行意義嘅欄，upsert 一定要喺 update list restate。**
   守門人：`scripts/test_pop_upsert_restates_label.py`。
4. **為一款遊戲寫嘅規則，靜靜咁套落第二款。**（2026-08-09，SNK matcher）
   `op_identity_rules.py` 成套字彙係為 One Piece 寫，之後直接拎去判 pokemon：GemRate 每個
   pokemon set name 開頭都有 "Pokemon" 呢個字，SNKRDUNK 日文標題唔會重複佢 → 大批卡被一個
   「唔指向任何產品」嘅字拒絕。同一個 25 張樣本：4 → 15 ACCEPT，原本嗰 4 張冇一張變拒絕。
5. **有檢查但零 call site = 冇檢查。** 加完 assert / hook / test 要**即場證明佢會 fire**：
   臨時將個 bug 種返落去，睇住佢紅，再還原。冇做過呢步就唔准講「已修」。
6. **搵唔到嘢驗嘅 checker 會永遠 pass。** static scan 類 test 一定要 assert 一個最低命中數
   （`if found < 4: FAIL`），否則有日 refactor 改咗 SQL 寫法，個 test 靜靜咁變咗綠色壁紙。
7. **參數靜靜咁減半個結果集。** `daily.py` 漏 `--view all_eligible` 會少收一半卡，而 QC
   report 一模一樣 —— 兩次 report 一樣就去對參數，唔好再查 QC。（已落 hook 擋住）
8. **provider 標題唔係按你嘅 schema 砌。** GemRate 將 treatment 焊死喺卡名度
   （`Full Art/Pikachu Vmax`），攞成串去搵 SNKRDUNK = 搵一張冇人賣嘅卡。搵之前用
   `rebuild_036.card_name_without_treatment()` 剝走，treatment 交返俾 print-signature 規則證。

9. **同一個號碼，兩邊兩種寫法。**（2026-08-09，PC promo）GemRate 促銷卡掉咗前綴寫 `062`，
   PriceCharting 寫 `OP05-062`。淨係字串比 = 靜靜 refuse 晒成批促銷卡，而 log 只會話
   「number 對唔上」，睇落好合理。修法：將兩種寫法嘅關係寫成一個有名有姓嘅函數
   （`pc_identity_discover.numbers_agree()`）+ test + **限死邊款遊戲**，唔好散喺比對邏輯度。
   注意呢個唔等於放鬆：同一版真係有一行 `P-062` 但佢係另一張卡，所以尾號永遠唔單獨決定。
10. **HTML entity 當咗名嘅一部分。** `<a>Hody &amp; Hyouzou</a>` 唔 unescape 就會多咗個
   `amp` token，之後所有名字比對都同佢比。parse 完即刻 `html.unescape`。
11. **人手裁決冇寫落佢管轄嗰行 = 下一條 lane 一定繞過佢。**（2026-08-09，紅名單 13 張）
   034 audit sheet 上面 13 張人手拒絕嘅卡，個裁決淨係活喺某幾條 lane 嘅記憶入面。新開嘅
   discovery lane 從來冇聽過佢，照樣提案 3 張，最後 activation 前一關（validator034
   `red13`）先攔到，而且係同月第二次。修法唔係叫 lane 記得，係將裁決**推導**成一個
   set 俾每條 lane 用（`scripts/stamp_red_sheet_quarantine.py: red_variant_ids()`），
   而且 fail-closed：睇唔到裁決就唔准提案。復原行同一個 script `--write`（idempotent）。
12. **統計拋棄咗 = 個 hold 講唔出自己點解 hold。** 為咗唔想 log 太長而靜靜 drop 大多數
   rejection，結果 artifact 出 `"rejections": []` —— operator 分唔到「冇一行接近」同
   「個 filter 根本冇行過」。要 drop 就留低分佈（要嗰個號碼、嗰版實際載住咩、幾多行過到關）。

### 相關嘅 MySQL / shell 陷阱

- 一條 statement 入面 reference 同一張 TEMPORARY table 兩次 → `ERROR 1137 Can't reopen table`。
  拆做兩條。
- `JSON_EXTRACT(..., "$.x")` 經 `docker exec` 傳會俾 shell 食咗個 `$`。寫落 `.sql` 檔再
  `docker exec -i ... < file.sql`。
- `catalog_rebuild_member` 嘅 PK 係 `(generation_id, gemrate_id)`，**唔係 variant_id**。
  數卡永遠 `COUNT(DISTINCT variant_id)`，唔係 `COUNT(*)`。
- 密碼永遠留喺容器入面：`docker exec cardz-market-cap-db-1 sh -c 'mysql -u root -p"$MYSQL_ROOT_PASSWORD" ...'`。

---

## Hard rules

1. **No `git add -A` in this repo.** `data/private/` 同 `data/runtime/` 由 2026-08-08 起已經喺
   `.gitignore`（`PLAN_036_FE02.md:521` 講「未 ignore」係舊嘢，已過時），但呢條規矩照守：
   working tree 隨時有唔應該入 repo 嘅嘢。逐個檔 add。
2. **`backend.env` is read-only** — "一個 byte 都唔准改" (`PLAN_036_FE02.md` §0.4). The writer
   freeze is proven by `scripts/prove_writer_freeze.py` against the unmodified `backend.env`
   (expects MySQL error 1142).
3. **Writer freeze:** all rebuild DDL/DML goes through the credentials in
   `data/runtime/config/rebuild.env` via `--credentials-env`
   (`pipelines/rebuild_036.py` `DEFAULT_CREDENTIALS_ENV`). Never widen the backend account.
4. **Never start a second `rebuild-036` orchestrator while one is running** (doctrine — not
   enforced by a process mutex; see section 5).
5. **`prune-apply` / `canary` only via explicit `--stage` after activation is proven** —
   enforced in code by `_assert_activated` + `_run_freeze_proof`
   (`pipelines/rebuild_036.py`).
6. **PriceCharting: port 9333 only, headed Chrome only.** Every PC entry point now defaults to
   9333 and `cf_session` refuses to fall back to other ports. Never attempt headless — it is
   the known Cloudflare-blocked mode (`pipelines/pricecharting_cf_session.py`).
7. **Never print or commit env secret values.** `.gitignore` excludes `backend.env`,
   `gemrate.env`, `rebuild.env`, `.env*`, and `data/runtime/` — keep it that way.
