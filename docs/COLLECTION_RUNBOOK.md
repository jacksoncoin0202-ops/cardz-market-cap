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
python -X utf8 pipelines\pc_cdp_sold_refresh_win.py
```

Flags (argparse in `pipelines/pc_cdp_sold_refresh_win.py`): `--limit` (default **0** = all),
`--offset` (**0**), `--workers` (**`PC_TABS` = 2** concurrent tabs), `--sleep`
(**`PC_SLEEP_SECONDS` = 3.0** s per tab between items), `--challenge-wait` (**120.0** s — waits
on the same 403 page, never reloads), `--cdp-port` (**9333**), `--cdp-already-ensured`,
`--resume-report`, `--variant-ids-file`, `--no-ingest`.

#### Pacing calibration — 2026-08-12 (量返嚟嘅，唔准靠估改)

單 tab + `--sleep 4.0` 嗰陣：5.2 s/頁，其中 **4.0 s 淨係喺度瞓**，全量 993 張 = **95 分鐘**。
嗰 4 秒冇任何量度撐住，本文件當時只寫住 "politeness"。而家改成 N 條 tab 共用同一個
CDP session + 一條共用 backoff。同一批 40 張逐個設定實測：

| 分頁 / sleep | 每頁 | 429 | 推算 993 張 |
|---|---|---|---|
| 4 / 1.0s | 2.78s | 3 | 46 分鐘 |
| 3 / 1.0s | 2.18s | 2 | 36 分鐘 |
| 2 / 1.5s | 1.99s | 1 | 33 分鐘 |
| **2 / 3.0s** | **2.03s** | **0** | **34 分鐘** ← 預設 |
| 6 / 8.0s | 2.19s | 1 | 36 分鐘 |
| 4 / 4.8s | 2.16s | 1 | 36 分鐘 |
| 8 / 0.5s | 2.60s | 4 | 43 分鐘 |

兩個反直覺結論，唔好再試多次：

1. **加分頁唔會快，反而慢。** 4 分頁 2.78 s/頁，慢過 2 分頁嘅 2.03 s/頁 —— 每食一次 429
   就要全部分頁一齊停 30/60/120 秒。乾淨上限大約 **0.5 goto/s**，即係 993 張 ≈ 34 分鐘。
   呢個係 Cloudflare 個閘，唔係腳本慢。
2. **唔准 `page.route` 擋走圖／css 嚟慳額度。** 一版產品頁向 `www.pricecharting.com` 打
   31 個 request（15 圖 / 6 script / 4 css / 2 manifest / 2 xhr / 1 fetch / 1 document），
   睇落擋走 21 個就可以行快三倍。實際 A/B（同一設定、隔 3 分鐘背對背）：唔擋 **40/40
   全清 2.05 s/頁**，擋咗 **38/40、兩次 429、3.69 s/頁**。Cloudflare 見到「瀏覽器」淨係
   攞 HTML 唔攞 css／圖就當你係 bot。要扮足全套。

`collect_control.py` 個 `--pc-sleep` / `--pc-workers` **冇 default**：唔傳就用返上面兩個常數。
（舊版喺嗰邊硬寫 `default=4.0`，所以 refresher 點改都冇用 —— 真正決定 993 張跑幾耐嘅係
CLI 嗰一行。`scripts/test_pc_lane_full_sweep.py` 守住呢件事。）

Behaviour (`pipelines/pc_cdp_sold_refresh_win.py`):

- Reads the map `data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl`; writes its
  report to `data/runtime/operator/collect/pc_cdp_refresh_report.json`.
- `--workers` CDP tabs inside the one session (same cookie jar / CF clearance), sharing one
  backoff: any tab hitting 429/CF pauses **every** tab. `validate_pc_psa10` runs in a thread so
  the ~100 ms HTML parse never blocks the other tabs.
  Verified-only atomic cache writes: temp `.next` → `os.replace` only when status
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

### Gap lane（張卡有 pop 但冇 PC 價）—— canonical 三步

唔使爬全站，唔使搜尋。`pc_identity_discover.py` 係列舉：`/category/one-piece-cards`
→ 137 個 console slug → `/console/<slug>` 一次過攞晒成套嘅產品，
再用 collector number 收窄，最後行返同一套 identity 規矩。console 靠
`set_names_a_card_could_carry(row, code_to_set)[1]` 揀，所以個 reprint 名一錯，
成條 lane 就去錯 console（形狀 25）。

```bash
python -X utf8 pipelines/pc_identity_discover.py --tcg one-piece --language en --generation <gen> --credentials-env data/runtime/config/rebuild.env
```

先淨跑一次（**唔加 `--write`**）睇 `counts`。見到 `alreadyOwned > 0` 而
holder 全部 `manual_review` → 逐個開 `held` 對卡名同 PC title：
正主嗰張要**套 code 同 treatment 兩樣都夾**（例：pid 6235454 係
`Portgas.D.Ace [Manga] OP02-013`，正主係 v188「OP02-Paramount War … Manga
Alternate Art 013」，唔係當時 hold 住嘅 v1427「OP08 … Special Alternate Art」）。
再對 `stamp_red_sheet_quarantine.red_variant_ids()` 確認零重疊，先加 flag：

```bash
python -X utf8 pipelines/pc_identity_discover.py --write --allow-repoint --tcg one-piece --language en --generation <gen> --credentials-env data/runtime/config/rebuild.env
```

`--allow-repoint` 只搶 `manual_review`（守衛喺 SQL：`match_status <> 'exact'` +
無拒絕裁決 + rowcount 必須 1）。呢個 lane 只提案；升格永遠係下一步：

```bash
python -X utf8 pipelines/operator_control.py pc-identity-reverify --write --credentials-env data/runtime/config/rebuild.env
```

`operator_strict_source_identity` 係 **view**，所以升格咗嘅 exact 即刻見到 ——
重跑鏈由 `--invalidate-from pc-replay` 開始就夠，唔使返去 identity-resolve。
`--invalidate-from` 係**獨立一步**，佢清完 checkpoint 就 exit；要再 call 一次
唔帶 flag 嘅 `rebuild-036` 先會真係行 stage。

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

### 一句過（預設用呢個）

```bash
python -X utf8 pipelines/operator_control.py rebuild-036-e2e \
  --generation 036_<YYYYMMDD>T<HHMMSS>Z
```

`cmd_e2e`（`pipelines/rebuild_036.py`）行齊成條鏈：關 cardz scheduled tasks → freeze →
S0..S11 → activate → prune-apply → canary → unfreeze → 開返啱啱關咗嗰幾條 task → bake
snapshot。Unfreeze 同開返 task 喺 `finally`，中途炸都會行。Flags：`--invalidate-from`、
`--credentials-env`、`--freshness-hours`、`--skip-bake`。

改完 code 唔使自己記住 `--invalidate-from`：checkpoint 而家連 stage 行到嘅 code 一齊
hash（`_stage_input_sha` / `_code_sha`），改咗邊條 lane 就邊條 lane 報 INPUT DRIFT，
唔會靜靜 `stage-skip` 出返舊答案。範圍係 transitive 但 scoped —— 改 `_capture_fingerprint`
只郁 `identity-resolve` 同 `bind`，唔會迫你重爬 `discover`。

### Full run (linear S0..S11)

```bash
python -X utf8 pipelines/operator_control.py rebuild-036 \
  --generation 036_<YYYYMMDD>T<HHMMSS>Z
```

Flags (argparse in `pipelines/operator_control.py`): `--generation` (required, must match
`^036_\d{8}T\d{6}Z$`), `--resume` (explicit alias — linear runs always resume from
checkpoints anyway), `--stage`, `--invalidate-from`, `--force-stage`, `--dry-run`,
`--credentials-env`, `--freshness-hours` (default **72.0**).

**A second orchestrator is refused by the code.** `main()` takes `operator_e2e_lease`
(MySQL `GET_LOCK`) for every subcommand outside `READ_ONLY_COMMANDS`, so the second process
dies with `refused: another CARDZ 026 operator run owns cardz-market-cap:operator-e2e:v1`
before it touches a checkpoint. This used to be doctrine only, and doctrine lost: two
orchestrators ran on one generation and the second silently re-ran `discover`, because
`_run_linear` reads a `running` checkpoint as runnable.

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

`rebuild-036-activate` 嘅 `--receipt-sha256` 而家係 optional：唔畀就自己讀返嗰個 generation
最新一張 `passed=1` 嘅 receipt。閘從來都唔係人手抄嗰串 hash，而係 activate 自己 in-process
重跑 validator 同正要 activate 嗰個 DB 對數（`cmd_activate`，`pipelines/rebuild_036.py`）。
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

### 【行 discovery 之前先做】One Piece 印刷 set code —— `pipelines/op_limitless_printed_code.py`

**幾時用**：任何 One Piece 補數據之前，一定行呢步先。唔行嘅話 SNK 同 PC 兩條 lane 都會
搵到正確 listing 再用 `set_code:` 掉咗佢（缺陷形狀 21）。

```bash
# 睇清楚先（唔寫檔；Limitless 頁會 cache 落 data/private/limitless/）
python -X utf8 pipelines/op_limitless_printed_code.py
# 落 policy 檔
python -X utf8 pipelines/op_limitless_printed_code.py --write
```

出 `data/policy/op-printed-codes.json`（contract `op-printed-code-v1`）。
**2026-08-10 實測**：121 張目標 → 證到 116（88 張係復刻）、hold 5。
`provenBy` 三種：`sole_number_in_product` 33、`card_name` 60 入 `codes`（可以入 gate）；
`promo_scan` 23 入 `advisory`（**唔准**入 gate，原因見形狀 21）。

讀佢嘅係 `op_identity_rules.printed_set_code()`，四處 call site 全部經
`rebuild_036.snk_claim_set_agrees()` / `_fingerprint_variant_conflicts()`。
Policy 檔冇咗 = 全部返回 `""` = 行為同未修一樣（唔會爆，但會靜靜咁少一半卡）。

政策檔只填**空白**，永遠唔會推翻 catalog 已經講咗嘅 `set_code`（2026-08-10 實測：
93 條 gate 條目入面 48 條填空白、45 條同 catalog 一致、**0 條矛盾**）。
`scripts/test_op_printed_codes.py` 守住呢條同兩層證據分家。

**5 張 hold（要人手睇）**：v2040 `ambiguous_promo:P-001,ST01-001,ST21-001`、
v2135 `ambiguous_promo:OP07-113,OP10-113,OP15-113`、v2027 `product_not_on_limitless`、
v1445 `ambiguous:OP04-119/OP05-119/OP09-119`、v2235 `language_not_on_limitless:zhCN`。

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

### 已知效能債（全部係欠單，未修）

1. **`stage_validate` ~5 分鐘。**（2026-08-10 重新診斷，**之前寫錯咗**）
   舊版寫「materialize `market_price_daily` 358,164 行做兩次 full table scan」。三處都唔啱：
   - **冇一張叫 `market_price_daily` 嘅 table 或 view。** 真名係 `market_price_observation`
     （345,236 行）。個舊名係佢張 `UNIQUE KEY uq_market_price_daily` —— 睇 index 名當 table 名。
   - 嗰兩個 full scan 實測 **66.2 + 82.3 = 148.5 ms，佔 67,343 ms 嘅 0.22%**。就算刪清都慳唔到。
   - **真兇係 `operator_canonical_current_metric_projection` 入面驗 `canonical_market_rank`
     嗰個 correlated subquery：38,091 ms / 67,343 ms = 56.6%，行咗 44,919 次。**
     `market_canonical_metric_acceptance` 兩個 unique key 都冇帶 `market_cap_usd`，所以每 loop
     都要掃 ~941 個 index entry 再撠 PK。A/B 實測同一個答案（兩邊都 19,675 行）：
     correlated `1 + (SELECT COUNT(0) … market_cap_usd > …)` 20,205 ms，
     `RANK() OVER (PARTITION BY … ORDER BY market_cap_usd DESC, variant_id ASC)` 488 ms（41.4×）。
   **點修（未做，要 maintenance window）**：先試 (a) 加 covering index
   `(ranking_generation_sha256, market_cap_usd, variant_id, canonical_market_rank)` —— 純 DDL，
   零語意面；唔夠先考慮 (b) 改寫 view 做 window function（改 view = 改 activation gate，
   要證明 row set 完全一樣，A/B 相同只係證據唔係證明）。
   **教訓**：`EXPLAIN` 讀到個名之前，先 `information_schema` 對一對佢係 table 定 index 名；
   同埋唔好淨係數 scan 咗幾多行，要睇 `EXPLAIN ANALYZE` 嗰個 **ms**——行多唔等於慢。
2. ~~**`price-materialize` 會將成個 `skipped` array 噴落 stdout。**~~（2026-08-10 修好）
   `stage-complete` 個 counts 而家過 `rebuild_036.printable_counts()`：超過 3 個 item 嘅
   list 變成 `{count, sample, omitted, seeAlso}`，**唔會靜靜咁截短**。
   全份仍然喺 `cardz_rebuild_checkpoint.counts_json`（DB 嗰邊冇改過）。
   實測：5,538 字 → 184 字。
3. **`freshness72h` 嘅 `priceAgeHours` 會係負數**（2026-08-09 `-9.42`；**2026-08-10 重測 `-19.36`，
   仲差咗**）。SNK kline 日 bar 嘅 `effective_at` 蓋章喺**當日 23:59:59**（`snk_market_data.py:1303`
   `datetime.combine(observed, dt_time(23,59,59))`，naive，**冇做過任何時區轉換**；
   `g10_kline_price_bridge.py:102` 同一寫法）。實測 157,429 / 157,610 行 snkrdunk（99.89%）
   `TIME(effective_at)='23:59:59'`；605 條 variant stream 有 17 條蓋喺未來 19.35 小時。
   個 gate（`rebuild_036.py:4843-4844`）係 `<= 72.0`，負數照過。

   **兩個窿，唔止一個：**
   - **負數窿**：feed 死咗都可以扮新鮮多一日。
   - **單一 MAX 窿**（更大）：`rebuild_036.py:4830-4834` 係
     `MAX(effective_at) … WHERE source_code IN ('pricecharting','snkrdunk')` —— **一個 MAX、一個數、
     一次比較**。pricecharting 死足一個月，個 gate 都可以靠 snkrdunk 個章過。今日實測就係咁：
     combined MAX 完全由 snkrdunk 個未來章決定，pricecharting（+0.30h）對個 gate 零貢獻。
     仲有 `snk_psa10`（138,037 行，+136.12h 舊）餵得到 price route，但**唔喺 gate 個 source list 度**。

   **「日 bar 應該蓋幾點」—— 四個選項，實測指住 D：**
   | | 做法 | 代價 |
   |---|---|---|
   | A | 保留 23:59:59，改成 `0 <= age <= 72` | 今日嗰條 bar 永遠喺未來 → **日日全日 false-fail**。單獨用唔得 |
   | B | 改用 fetch 時間蓋 `effective_at` | `effective_at` 係 accepted-price 嘅 tie-break（`operator_accepted_psa10_price_history`）同 join key（`market_metric_history_acceptance.source_effective_at`）→ 會**改當日贏家次序**，仲要 backfill 157k+ 行 |
   | C | 蓋真收市時刻（23:59:59 JST = 14:59:59Z）並且未到唔准寫 | age 唔會負，但**15:00 UTC 之前冇今日價** |
   | **D** | **`effective_at` 唔郁，個 freshness gate 改讀 ingest 鐘 `market_source_observation.observed_at`** | 實測今日 **+0.30** 而唔係 -19.36；四個 price source `observed_at` **零未來行**；價格身份／排序／157k 行全部唔郁 |

   **D 嘅新失效模式（要自己一個 check）**：`observed_at` 答「幾時 fetch」，唔答「fetch 到嘅嘢有幾舊」——
   source 一直派同一條殭屍 bar 都會睇落好新鮮。要另外 alert `MAX(observed_date)` 停咗前進。
   改埋要記住 `priceAgeHours` 喺 `_RECEIPT_VOLATILE_FIELDS`（`rebuild_036.py:6519-6522`），改名要一齊改。

   **次序好緊要**：**先答完個鐘，先至收緊個不等式。** 掉轉做 = 由「靜靜地假 pass」變成「日日假 fail」。
   收緊之後預咗第一次會 fail（snk_psa10 今日 +136.12h）—— 嗰個係 gate 做緊嘢，但係要排期，唔好突然射。
4. **七個「age vs 門檻」比較收得低負數。**（2026-08-10 盤點）
   `rebuild_036.py:4843-4844`、`collect_control.py:278`／`:283`／`:1035`／`:1075`／`:3130`、
   `operator_control.py:1670`／`:1772`。repo 入面已經有三處寫啱咗，照抄就得：
   `gemrate_candidate_backfill.py:803` `0 <= age <= max_age_days`、
   `gemrate_candidate_backfill.py:822`、`market_alerts.py:691` `age < timedelta(0) or …`。
   **但唔准一刀切**，三種 site 意思唔同：
   - `_poll_mode`（`:278`/`:283`）—— 對日 bar 嚟講 age 負數係「今日條 bar 已經喺手」，**當佢新鮮係啱嘅**。
     要改嘅係寫明白，唔係改行為。
   - `slaOk`（`:1035`）—— 呢個係**匯報**，負數即係有未來章，係數據質素信號，唔可以扮 ok。（2026-08-10 已修）
   - `:3130` 比較嘅係 file mtime，負數 = 機器時鐘歪咗，**應該嘈**，同 effective_at 嗰種唔同 response。

---

### Daily gap → 036 universe → FE03 接線

`daily-accept` 只會重排 current universe，刻意唔改 membership。即係新卡就算已經有
GemRate row，如果冇人做 identity discovery 同重新 activation，舊 universe 仍然會日日綠，
但新卡永遠去唔到 FE03。

第一次啟用要由 operator 明確記住**現時** gap 嘅 exact variant ID set（state 放喺 shared
`data/runtime` junction，唔入 git）：

```powershell
python -X utf8 pipelines/operator_control.py daily-discover-activate --initialize
```

首次 seed（2026-08-12）係 generation `036_20260808T084217Z`、**268 個 exact gap ID**。
呢個數細過 committed count baseline 557，因為中間已有身份缺口被證實收窄；兩者唔係互相
取代：runtime cursor 用嚟精準揀新 ID，baseline 仍然係 daily-accept 嘅 count ceiling。

正常排程唔掃歷史 gap；佢只計 `currentGapIds - knownGapIds`：

```text
nightly: HTTP collect → daily-discover-activate --lane http → daily-accept
morning: CDP 9333 → browser collect → daily-discover-activate --lane browser
         → daily-accept → bake/push [deploy]
```

- `card_language='en'` 只送 PC/browser；其他語言只送 SNK/HTTP。
- 兩個 discoverer 同 PC reverify 都收 exact `--variant-id` scope；唔會重審舊 gap 或順手
  promote 另一批 manual-review row。
- 唯一 survivor 證成 exact 後，coordinator 將 ID 先寫入
  `pendingActivationIds`，再沿用現有 `rebuild-036-e2e --invalidate-from identity-resolve
  --skip-bake`。036 validator + activation transaction 仍然係唯一 universe writer。
- process 喺 exact bind 同 activate 中間死咗，下次由 `pendingActivationIds` 接續；唔會將
  半截工作當 complete。
- ambiguous／無 survivor／另一 transport lane 未處理嘅新 gap 一律保持**未 acknowledged**，
  command 非零，排程唔准落 `daily-accept`。唔猜身份、唔靠提高 baseline 開綠燈。
- E2E 自己會 disable／restore `CARDZ-036-*` tasks；外層 operator lease 保證同一時間只有
  一個 DB mutator。FE03/GEO code 完全唔參與呢段，佢只讀 activation 後焗出嚟嘅 snapshot。

狀態：`data/runtime/rebuild-036/daily-discovery-state.json`。刪咗／壞咗會 fail closed；
唔准正常排程自動重建 cursor，因為咁會將一個真正新 gap 靜靜當成歷史已知。

## 6. FE 對數 —— 點解 activation 咗 FE 都唔郁

**行過一次先，唔好靠估。** FE 有兩個 data mode，睇 `CARDZ_DATA_MODE`：

| mode | 讀邊度 | 用途 |
|---|---|---|
| `live-db` | MySQL 3308 直讀，即刻反映最新 accepted ranking generation | 開發 / 對數 |
| `baked-snapshot` | `MARKET_DATA_SNAPSHOT_PATH` 指住嘅 `latest.json` | 貼近 LIVE 嘅預覽 |

2026-08-09 真事：036 activate 咗，1190 張卡入咗 universe，但 `:3800` 一直得 762 張。原因係
`fe03-server.ps1` 個 snapshot path 指住 `publish-staging\latest.json` —— 個 pointer
係 **7 月 28 號**焗出嚟嘅。睇落好似「採集唔夠數據」，其實 FE 根本冇讀個 DB。**任何「FE 數
唔夠」嘅投訴，第一步係分清 mode，唔係去查採集。**

**個 env var 真名係 `MARKET_DATA_SNAPSHOT_PATH`**（`apps/web/src/lib/server-snapshot.ts:58`）。
呢份文同 `fe03-server.ps1` 寫咗一年 `MARKET_DATA_POINTER_PATH`，冇任何 code 讀呢個名 ——
即係「順手清 pointer」呢步一直係空氣，真嗰個變數照留喺 env 度贏（2026-08-10 修正）。

診斷（一句分勝負）：

```bash
curl -s http://127.0.0.1:3800/api/health | python -X utf8 -c "import sys,json; d=json.load(sys.stdin); print(d.get('dataMode'), d.get('generation'), d.get('universeSize'))"
```

`dataMode` 係 `baked-snapshot` 而個 generation hash 對唔上現役 lock → 就係呢個陷阱。改
`live-db` 要順手 `Remove-Item Env:\MARKET_DATA_SNAPSHOT_PATH`（真名，見上）。

要留喺 `baked-snapshot` 就一定要由**現役 generation 重焗** `latest.json`，唔係改 mode 算數。

**改 `fe03-server.ps1` 唔會郁到跑緊嗰個 process。** `server-snapshot.ts` 係
`snapshotPromise ??= readSnapshot()` —— baked snapshot 喺 process 一世只讀一次，所以重焗
`latest.json` 都唔會令跑緊嗰個 `:3800` 變數。要換世界＝要 restart。2026-08-10 實測：
`:3800` 報 `baked-snapshot` / 762，同一時間 `fe03-server.ps1` 檔案裡面已經係 `live-db` ——
個 process 係腳本修好之前開嘅。**睇 health 睇到嘅係 process 嘅過去，唔係個腳本嘅現在。**

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
13. **一條規則有第二個實現 = 你只會修到其中一個。**（2026-08-09，一晚撞兩次）
   (a) `stage_pc_replay` 自己抄咗一份 bracket 比對，所以收緊咗共用嗰個
   `_pc_print_signature_ok` 之後，**真正 stamp `exact` 嗰個 stage 仲用緊鬆嗰個讀法**。
   (b) discovery lane 學識咗一張卡有兩個 set 名之後，promoting gate（`pc-identity-reverify`）
   仲淨係識 catalog 嗰個 → **拒絕咗 lane 頭先啱啱提案嘅 3 張卡**。
   修法：規則只可以有一個定義處，其他人 import 佢，唔准抄。搵新 bug 之前先
   `grep` 個規則個名，數吓有幾多個 call site 同幾多份 copy。
14. **一張卡上面有兩個都啱嘅「set」。**（2026-08-09，22/59 個 EN hold）
   One Piece 會將一張卡再刷入後期產品但**唔換號碼**。GemRate 用「由邊個產品抽出嚟」歸檔
   （`set_name` = Emperors in the New World），卡面印住嘅號碼照舊 `OP08-106`
   （`set_code`/`collector_number`）。PriceCharting 用**號碼嗰個 set** 歸檔。兩邊都冇錯，
   佢哋答緊兩條唔同嘅問題。淨係讀一邊 = 靜靜咁搵錯版，log 只會話「嗰版冇呢個號碼」。
   修法：`rebuild_036.set_names_a_card_could_carry()` 一次過交出兩個讀法，
   **兩個都要交俾判產品嗰個人**（見 shape 13b）。放寬嘅係「去邊度搵」，唔係「收咩」——
   號碼、角色、print signature 三關一關都冇鬆。
15. **只讀咗 catalog 證據嘅一半。**（2026-08-09，928 條 exact PC binding 入面 7 條）
   `_pc_print_signature_ok` 淨係讀 `parallel_code`，冇讀 `printing_code`。一張
   Special Alternate Art（`printing_code='sp'`）因為 `parallel_code` 得個裸 rarity（`sr`），
   就過到一版**完全冇 bracket** 嘅 base print——出咗平嗰張卡嘅價，仲霸住個 product
   令真正嘅 base 卡搵唔到自己嗰版。catalog 由頭到尾都講咗，我哋冇問佢。
   修法：`_PC_BASE_PRINTINGS`（只有 `''` / `'base'` 可以配冇 bracket 嘅頁）。
   量度過先改：919 條唔受影響、7 條拒絕、全部係 parallel 坐喺 base print 上面。

16. **一個 fact 兩個 writer，只有一個有 key。**（2026-08-10，26 張卡 / 朝早 lane 死咗一日）
    `pc_identity_discover` 一邊用 `ON DUPLICATE KEY UPDATE` 寫 DB，一邊用
    `MAP_PATH.open("a")` 直接 append 落 canonical map。**個表有 key 所以乾淨，個檔冇 key
    所以每 run 一次多一行。** 26 張卡有兩行活住，`collect_control._pc_subset_map` 一 raise
    `canonical PC map has duplicate active variants`，成條朝早 browser lane 就死，而
    `daily-accept` 照跑照出 `accept=0` —— 個 chain 尾聲睇落一切正常。
    修法唔係「記得同步」，係**一個檔只可以有一個 writer**：proposal 寫去自己嗰個 shard
    ledger（`c11_pc_ebay_map_full900_shard_identity_discover.jsonl`），
    `consolidate_pc_map.py` 係 canonical map 唯一寫得嘅人，佢會 glob 嗰個 ledger 攞 transport。
    守門人：`scripts/test_pc_identity_discover_rules.py`（proposal path ≠ canonical path
    ＋ 個檔真係一行一 variant）。
17. **reader 用字母序揀證據。**（2026-08-10，5 張卡）`pc-identity-reverify` 用
    `sorted(pages_dir.glob(f"{variant_id}_*.html"))[0]` 揀頁。同一個 variant 底下除咗產品頁，
    仲有 lane 搵嘢時順手存低嘅 search-results 頁 ——
    `2026_search-products-….html` 喺個 `e` 度贏咗 `2026_shanks-magazine-op09-001_r.html`，
    於是五張卡被判 `page_parse_failed: canonical_not_product`，而佢哋嘅產品頁就喺隔籬。
    **證據要按「係咪嗰件事」揀，唔係按檔名排序揀。**修法：`rebuild_036.pc_capture_for_product()`
    —— product id 對得上 **而且** parse 到係產品頁先算數。收嘅條件一格都冇鬆。
18. **receipt 嘅 sha 唔係個檔嘅 sha。**（2026-08-10，`consolidate_pc_map.py`）
    `Path.write_text()` 喺 Windows 會將 `\n` 變 `\r\n`，但 sha256 係喺轉換之前計。
    份 receipt 由頭到尾描述緊一份**從來冇存在過**嘅 bytes，任何人攞去對都會唔啱。
    修法：寫 bytes，寫完再讀返個檔對一次 sha 先出 receipt。
    **凡係「artifact + 佢個 hash」，個 hash 一定要由落咗地嗰份 bytes 計。**
19. **文檔／腳本叫一個 env var 名，code 讀另一個名。**（2026-08-10，FE03）
    `fe03-server.ps1` 同 runbook 寫 `MARKET_DATA_POINTER_PATH`，`server-snapshot.ts:58`
    讀 `MARKET_DATA_SNAPSHOT_PATH`。即係「切 live-db 記得順手清 pointer」呢步**一直係空氣**，
    真嗰個變數留喺 env 度照贏。同 shape 5 一樣：冇 call site 嘅安全步驟＝冇安全步驟。
    改 env var 名之前 `grep` 個名喺 code 入面有冇人讀。
20. **工作清單由寬嗰張表出，執行嗰陣用嚴嗰張表判。**（2026-08-10，86 張卡）
    `collect_control` 由 `catalog_source_identity.match_status='exact'` 砌 `snk_price`
    poll list，但 `snk_market_data` 落庫係認 `operator_strict_source_identity`（037 view，
    仲要驗 036 provider-native evidence + capture receipt）。v1203/v1204 係 pre-036 binding，
    S7 因為 soft parallel mismatch 冇 restamp，status 照舊 `exact` → 入到 poll list →
    ingest 當 `no_exact_identity` skip。
    **真正殺傷力唔係嗰 2 張，係 contract 喺 `record_successful_poll` 之前 return**：
    同一批入咗庫嘅 86 張卡一個 checkpoint 都攞唔到，下次照樣全部重跑，永遠唔會綠。
    由 08-09 開始每晚都係咁，`snk_price:incr 88` 個數一日都冇郁過 —— 睇個數字以為冇進度，
    其實係每晚做完再擦乾淨。
    **形狀**：一個 all-or-nothing gate 架喺 batch 層，而 batch 成員資格由另一張表決定。
    2 個 poison row 就可以永久鎖住成條 lane。
    **點修**：唔好放鬆個 contract（有 kline 但綁唔到 = 真係蝕數據，一定要嘈）。
    要改嘅係 **poll list 同 ingest 認同一張表**。攔起嘅卡要喺 status 出返個數
    （`snkPriceIdentityNotStrict`），唔可以靠「佢唔喺個 list 度」嚟表達。
    **點查**：`SELECT` 對一對兩張表個差；差幾多就係幾多張卡永遠唔會綠。
    Guard 落咗 `scripts/test_snk_identity_discover_rules.py`（poll list 必須係
    `load_exact_snk_item_to_variant()` 嘅子集）。
21. **GemRate 講「喺邊度賣」，卡面印「邊度出世」。**（2026-08-10，123 張 OPTCG 卡）
    123 張 One Piece `qualified_market_pending` 卡，全部同一個死因：兩條 discovery lane
    都**搵到**正確嘅 provider listing，跟住用 `set_code:['op04']!=['op05']` 掉咗佢。
    One Piece 復刻卡入後期產品**唔會重新編號**：OP05 booster 抽到嘅 alt-art Kaido，
    GemRate 記做「OP05-…044」（賣佢嗰個產品），但張卡面印住 `OP04-044`（佢首度登場嗰套）。
    provider 跟卡面，我哋跟 GemRate，於是每次都係**因為佢講真話而拒絕佢**。
    實測：`https://onepiece.limitlesstcg.com/cards/jp/OP05` 出 154 條 card link，
    入面有 `OP04-044` 同 `OP02-120`，而 OP05 原生最大號係 119。
    **修法唔係放鬆閘，係修 input。** `pipelines/op_limitless_printed_code.py` 由
    **GemRate 自己指名嗰個產品**嘅 Limitless 頁證返個印刷 code，寫落
    `data/policy/op-printed-codes.json`；121 張目標證到 116 張（88 張真係復刻）。
    人物／號碼／語言／treatment／tcg／mirror 一格都冇鬆，淨係「邊個 code 算係我哋嘅」多咗一個答案。
    實測差異：SNK discover proposals **0 → 17**；PC reverify promoted **0 → 8**。
    **形狀**：identity gate 冇錯，錯喺入面其中一個 input 答緊另一條問題。
    一條 gate 100% 拒絕率、而且拒絕理由永遠同一個 field —— 查嗰個 field 嘅來源，唔好查 gate。
    **兩層證據唔准撈埋**：`codes`（產品頁 sole-number 或者對到卡名）先可以入 gate；
    `advisory`（走勻 80 個 promo 產品掃出嚟）只係俾人睇嘅線索。
    原因喺 `snk_identity_discover.py:351` —— set code 一夾啱，promo 就會**跳過** `product_agrees`，
    而 product_agrees 係 promo 僅餘嗰道檢查。`printed_set_code()` 只讀 `codes`，唔讀 `advisory`。
    **補完（同日，再測一次先發現）：呢個形狀有兩個方向，第一次只修到一半。**
    復刻卡有兩個都啱嘅答案，而 catalog 一行**淨係載到其中一個**：48 行 `set_code` 係空嘅，
    `printed_set_code` 填返個印刷 code 就通；但另外 21 行 catalog 本身已經載住印刷 code，
    差嗰個係「賣佢嗰個產品」—— GemRate 講 `op10`、catalog 講 `op08`，再加多次 OP08 完全冇用。
    實測 v19 / v1214 / v1228 三張，加完 `printed_set_code` 之後照樣 `STILL CONFLICTS`。
    所以要有 `sold_in_set_code()`：讀 policy 個 `product` 欄（resolver 喺 Limitless 邊一版
    **搵到**張卡），加埋落 `v_codes`。
    **唔准改成讀 catalog 自己個 `set_name`** —— 個 set_name 本身由 GemRate 嚟，
    咁樣等於攞 GemRate 同 GemRate 比，成條 set 檢查會變到永遠唔會拒絕任何嘢。
    教訓：一個 gate 修完之後，要**分開數返兩邊**（幾多張係缺 A、幾多張係缺 B），
    唔好見到總數郁咗就當修完 —— 第一次個修法喺 48 張度啱，喺 21 張度一格都冇郁。
22. **同一條問題，四個地方各有各答法。**（2026-08-10，同上嗰批卡）
    「SNK 講嗰個 set 同我哋夾唔夾？」呢條問題喺 S7、`snk-identity-reverify`、
    `snk-identity-discover` 三處各寫一次，`_fingerprint_variant_conflicts` 再獨立算多一次。
    discover 嗰版學識咗由 claim／set_name／policy 三處讀 One Piece code，
    另外三版仲用緊 Pokemon 嘅「除最後一個 token 之外全部」讀法 —— One Piece 個 code
    黐住個號碼（`OP09-106`），所以嗰個讀法**由頭到尾**回空字串，
    個 supersede escape 為咗佢要服務嗰隻 game 一次都冇 fire 過。
    結果：同一張卡喺 discover 過到，喺 reverify 過唔到，永遠卡喺 `manual_review`。
    修法：`rebuild_036.snk_claim_set_agrees()` 一個執行點，三處 call；
    印刷 code 就直接落 `_fingerprint_variant_conflicts` 嘅 `v_codes`（第四處，亦即真正共用嗰處）。
    **形狀**：「修好咗」嘅規矩只修咗一份 copy。改 identity 規矩之前
    `grep` 個概念（唔係個 function 名）睇下有幾多個地方獨立實現緊。
23. **`--invalidate-from` 要 reset 去「寫嗰個欄位」嗰個 stage，唔係「用嗰個欄位」嗰個。**
    （2026-08-10，白行咗成轉 15 分鐘）修完 `_fingerprint_variant_conflicts` 之後
    `--invalidate-from bind` 重跑全鏈，`productReady` 一格都冇郁。原因：
    `catalog_rebuild_member.detail_json` 入面個 `binding.conflicts` 係
    **`stage_identity_resolve` 寫嘅**（rebuild_036.py:771）；`stage_bind`
    只係 `json.loads` 返舊 detail、換走 `detail["s5"]` 就寫返落去（:2080-2113），
    由頭到尾**冇重算過** conflicts。所以 bind 跑一百次，個欄位都仲係舊嗰版邏輯嘅答案。
    而且 `computed_at` 會更新到最新時間，睇落好似「啱啱重算過」，
    最容易呃到自己。**查法**：改完一條規矩，`grep` 個欄位名睇邊個 stage 真係
    *產生*佢（有 `_fingerprint_...(...)` 嗰句），唔好靠 stage 順序估。
    要 reset 去嗰個 stage 為止。
24. **一大堆 `language:ja!=en` 唔係「拒絕得啱」，係「隻 listing 掛錯咗喺 en 卡度」。**
    （2026-08-10，SNK reverify 80 個 OP hold 入面 73 個係呢個）
    SNKRDUNK 係日本市場，佢啲 listing 由定義上就係**日文卡**。所以一張 en variant
    hold 住個 SNK item、然後年年被 `language:ja!=en` 拒絕，正常結論唔係「呢張卡冇 SNK 價」，
    係「呢個 item 嘅正主係隔離嗰張 ja variant」。實測九對：
    v1225(en, OP07-085) 霸住 item 520534，而 masterName 係
    `Stussy SR-SPC [OP07-085](Booster Pack "A Fist of Divine Speed")` ——
    正主係 v1875(ja, OP11 SPC 085)。九個現任 holder 全部 `manual_review`／`rejected`
    而且 `verdict:false`（冇人手裁決），所以 `snk-identity-discover --allow-repoint`
    正正就係為呢個情況而設，唔係鬆閘。
    **落手之前一定要做嘅兩步**：(a) 打開 `currentlyHeldBy` 睇實 `match_status` 同
    `verdict` —— 有人手 verdict 就唔准搶；(b) 對 `stamp_red_sheet_quarantine.red_variant_ids()`
    確認 target 同 holder 兩邊都唔喺紅名單。兩步都過先加 `--allow-repoint`。
    **形狀**：一個 hold 理由連續大量出現同一個值，多數係「配錯對」而唔係「真係唔啱」。
    睇個 provider 本身係邊個市場，再問「咁邊張卡先係佢嘅正主」。
25. **同一個「套名」問題喺呢個 repo 有第三條軸：PriceCharting 用「印刷來源套」做 console。**
    （2026-08-10）GemRate 講「喺邊個產品賣」、卡面印「邊套出世」（形狀 21 嗰兩條軸），
    而 PC 第三樣：佢將 `ST02-007` 嘅 SP alt art 擺喺
    `/game/one-piece-starter-deck-2-worst-generation/jewelry-bonney-sp-foil-st02-007`，
    即係跟**號碼嘅來源套**開 console，但張卡實際係 OP08 Two Legends 出。
    所以 `product_agrees` 攞我哋個 `set_name`（OP08）對 PC console（ST02）一定唔夾。
    `data/policy/op-printed-codes.json` 得 16 個 code 有 product slug（OP01–OP14、PRB01、PRB02），
    **冇 ST 系**，所以呢批補唔到名。唔准攞 PC 自己個 console 名倒返轉頭做候選 ——
    咁等於攞佢自己對自己，個 check 乜都收。
    **落手前先量**：`pc-identity-reverify` 個 held list 撈出嚟，
    同 `cohort='qualified_market_pending'` 交叉，先知邊啲 hold 真係令張卡跌出 FE。
    2026-08-10 實測：235 個 held 入面得 48 個係 OP pending
    （print_signature 25、product 12、hard_conflict 8、page_product 3），
    其餘 hold 郁咗都唔會多一張卡。
26. **`map_product_mismatch` 係「兩個獨立來源唔同意」，唔准手改個 map 去砌返啱。**
    （2026-08-10）`pc-identity-reverify` 要 page id == bound id == **map id** 三方同意，
    個 map 係 `data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl`，
    由 `consolidate_pc_map.py` 從**現役 exact** row 砌出嚟。
    做完 repoint 之後 5 張卡卡喺呢度（v107/188/1212/1251/1427），
    因為個 map 仲記住舊嗰個 pid。手改個 map = 攞掉個 gate 唯一嘅第二意見，
    而且個 map 同時係 PC sales sweep 嘅入口清單，改錯會影響全部 900+ 張。
    正路係行返一轉 PC sweep 再 consolidate（朝早 browser lane，CDP 9333 headed），
    唔係喺呢度改檔。**形狀**：一個 gate 要「兩個來源同意」嘅時候，
    唔夾嘅正解永遠係去修落後嗰個來源，唔係改個比較。

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
4. **A second `rebuild-036` orchestrator is refused in code** — `main()` takes
   `operator_e2e_lease` (MySQL `GET_LOCK`) for every subcommand outside
   `READ_ONLY_COMMANDS` (`pipelines/operator_control.py`); see section 5.
5. **`prune-apply` / `canary` only via explicit `--stage` after activation is proven** —
   enforced in code by `_assert_activated` + `_run_freeze_proof`
   (`pipelines/rebuild_036.py`).
6. **PriceCharting: port 9333 only, headed Chrome only.** Every PC entry point now defaults to
   9333 and `cf_session` refuses to fall back to other ports. Never attempt headless — it is
   the known Cloudflare-blocked mode (`pipelines/pricecharting_cf_session.py`).
7. **Never print or commit env secret values.** `.gitignore` excludes `backend.env`,
   `gemrate.env`, `rebuild.env`, `.env*`, and `data/runtime/` — keep it that way.
