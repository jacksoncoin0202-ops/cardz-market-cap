# CARDZ Market Cap V2 日更鏈 — 結構審計

- 日期：2026-08-23（審計期間 08-24 run 仍然 live 行緊）
- 審計對象（live tree）：`C:/Users/jackson0202/Documents/Playground/cardz-market-cap-fe-db-20260805`，branch `rebuild/036-foundation`
- 方法：READ-ONLY。冇改檔、冇行 chain / pipeline / ps1、冇 query MySQL 3308、冇掂 git。所有 finding 由 3 個 finder 提出，再由獨立 verifier 逐條對返 code 同 run artifact；verifier 推翻咗嘅照樣寫低（第 4 節），唔准再翻叮。
- 證據來源：`data/runtime/daily-chain-v2/2026-08-23`、`/2026-08-24` 嘅 `logs/*.log` + `receipts/*.json`；`data/private/gemrate/runs/daily_*/manifest.json`；timing profile（下面第 2 節）。

---

## 1. 一句總結：腳本係咪根本上錯？

**唔係。架構係啱嘅，錯嘅係「分類器」同「調度器」兩層。** [KNOWN]

再講白啲：durable journal、per-task lease/heartbeat、fail-closed 嘅 core contract barrier、immutable publication manifest、per-source adapter contract — 呢啲骨架冇問題，而且真係有做嘢（08-24 個 window bug 就係俾 barrier 攔住咗，冇出過錯數據）。三日 run 全部**冇一次出過錯 data**：要麼 publish 成功，要麼被 gate 攔住停喺度。

真正壞嘅係兩類：

1. **分類器用 substring 判生死** — `classify_error()` 攞住一舊自由文字（最多 6000 字 provider stderr）做 `token in value`，`"nan"` 會中 `mainte**nan**ce`／`gover**nan**ce`，`stage == "publish"` 直接短路晒所有 transient 判斷。一個 transient 上游故障可以變成零重試 TERMINAL；一個一定唔會過嘅 unit test failure 可以食足 67 分鐘 backoff。
2. **調度器係批次屏障，唔係 pump** — `execute_ready()` 一次 claim 一批，然後 join 晒成批先返；`run_tick` 要等佢返先再 `plan()`。所以一個 18 秒就死咗嘅 task，要等最慢嗰個 sibling（gemrate shard 15 分鐘）行完先可以重試。

加上一個 governance 漏洞（`clamp_manual_window` 個 17:00 JST cap 過咗 17:00 就唔再 bind，45 分鐘 floor 贏，而每次續期都會把 `source_cutoff` 重設成 `span*0.5` = 22.5 分鐘，直接砍死行緊嘅 non-core source）——呢個就係 08-22 個 1218 分鐘 run 嘅真身。

冇一條係要重寫成個 chain。最貴嗰幾條係 S effort。

---

## 2. 量度：時間去咗邊

| run | status | wall | sum(attempt durations) | 空轉 |
|---|---|---|---|---|
| 2026-08-22 | PUBLISHED | **1217.8 min** | 333.9 min | ~884 min (73%) |
| 2026-08-23 | PUBLISHED | **123.2 min** | 74.7 min | ~48 min (39%) |
| 2026-08-24 | RUNNING（審計時） | 24.2 min（+15.3m 時） | 59.4 min（並行） | 14 min 死等 |

### 2026-08-23（123.2 min wall，真正做嘢 ~22 min）

- `+0.2m` 全部 source 同時起：gemrate 4 shard **14.4 / 14.6 / 14.7 / 14.8 min**，pricecharting 5.1 min，snkrdunk 3.3 min，fx 0.0 min。→ **gemrate 係 source phase 嘅 critical path，比第二名長 2.7 倍**。
- `+15.1m` core-contract-pre a1 RETRY → `+15.2m` snkrdunk contract-repair COMPLETED → `+16.2m` a2 COMPLETED（呢段健康，只蝕 60 秒）。
- `+18.1m` box COMPLETED。**真正嘅工作喺 +18.2m 已經做完。**
- `+18.2m → +87.0m` release 連續 6 次 PUBLISH_FAILED：a1 +18.2 / a2 +20.6 / a3 +25.9 / a4 +36.2 / a5 +56.6 / a6 +87.0（TERMINAL）。ladder = 120/300/600/1200/1800 秒。**68.8 分鐘純瞓覺**，6 個 log 全部 5940 bytes 一模一樣，同一個 `scripts/test-fe-share-file.mjs` FAIL（`65/66 passed, 1 failed, 7 skipped`）。
- `+120.0m` a7 COMPLETED — 因為有人手推咗一行 fix 落嗰個 test file（attempt-7 log 頭幾行就係 `19a580b4..1c3c0372 main -> origin/main`，`scripts/test-fe-share-file.mjs | 2 +-`）。
- **拆帳：22 min 真工作 + 69 min backoff + 33 min 等人 = 123 min。**

### 2026-08-24（審計時 live）

- `+0.3m` fx TERMINAL/TERMINAL_CONTRACT（`FX schema contract: stale last-good ...`）→ 唔會自動重開，等到 `01:07:50Z` 有人 unpark 先行，**28.9 分鐘 blocked**。
- `+0.3m` gemrate shard 3-of-4 RETRY/SOURCE_FAILED（0.1 min 就死，實際 ~4.9 秒）；shard 0/1/2 分別 14.9 / 14.9 / 14.8 min COMPLETED。
- shard 3 backoff 只係 60 秒（`TRANSIENT_RETRY_SECONDS[0]`），照計 `+1.3m` 就應該重試，但 **a2 `+15.3m` 先開始** —— 啱啱好等到 shard 0-2 行完。**14.0 分鐘死等**，source phase critical path 由 ~15 min 變 ~30 min。
- shard 3 重跑用咗 13m48s（`00:53:56Z → 01:07:44Z`），全部 399 張由零再爬。

### 2026-08-22（1217.8 min）

- pricecharting `quote+price+sales+identity:all` 由 `+578.6m` 到 `+986.2m` 行咗 **16 次 attempt**，其中 **12 次 WORKER_INTERRUPTED**，attempt 長度 6.4 / 22.6 / 20.4 / 6.2 / 10.2 / 8.1 / 5.6 / 10.0 / 20.0 / 10.0 / 20.0 min — 而 20.4 / 22.6 / 20.0 / 20.0 呢啲數字唔係巧合，係 `source_cutoff = started + span*0.5` = **22.5 分鐘** 呢個 deadline 斬出嚟嘅。
- events：`MANUAL_WINDOW_RENEWED=8`、`TASK_UNPARKED=12`、`SOURCE_CONTRACT_REPAIR_PLANNED=8`、`SLA_MISSED=1`、`TICK_CRASHED=1`。
- 最後 `PUBLISHED` 喺 `2026-08-22T14:47:53Z` = **23:47 JST**，比合約嘅 17:00 JST final 遲咗 6 個 47 分鐘，而個 business_date 仍然係 2026-08-22。

---

## 3. 已確認問題

排序：P0（令 run 失敗／卡死／出錯數據）→ P1（幾個鐘延誤或要人手介入）→ P2（脆弱／醜，但有具體 failure path）。

---

### P0

#### P0-1 · core source 一 TERMINAL 就永遠卡死 barrier，仲要零 alert

- **file:line**：`pipelines/daily_chain_v2.py:484`（`source_barrier_ready`）；失敗事件寫喺 `:1749`
- **evidence**：

```python
core = [row for row in tasks if str(row["required_class"]) == "core"]
if not core or any(str(row["status"]) not in {"COMPLETED", "SKIPPED"} for row in core):
    return False
# ...
if now >= cutoff:
    return True          # <- cutoff 逃生門只服務 non-core
```

- **點解錯**：每個 phase 都有 deterministic degrade path（`degrade_unfinished_phase`），只有 core source TERMINAL 冇。`now >= cutoff` 條逃生門喺 core 檢查之後，永遠去唔到。而且自我修唔返：`recover_expired() → reopen_retryable_terminal()` 一睇 `decision.terminal` 就 `return False`（`daily_chain_v2_journal.py:740`）。最後，失敗係用 `self.journal.add_event("TASK_ERROR", ...)`（`:1749`）寫，唔係 `self.journal_event()`，所以唔喺 `ALWAYS_ALERT_EVENTS`（`:81-88`），**零 alert**。第一個會叫嘅事件係 17:00 JST 嘅 `FAILED_FINAL`。
- **實例**：08-24 `fx:rates:all`（`required_class="core"`，`daily_chain_v2_adapters.py:362`）喺 `+0.3m` TERMINAL，因為佢自己個訊息含 `schema contract` 呢個 TERMINAL_CONTRACT token。成條鏈靜靜地卡咗 28.9 分鐘，等人 unpark。
- **最小修法**：喺 `plan()` 入面（`:1012` 之後）加：core row 全部喺 `TERMINAL_TASK_STATES` 但唔係 COMPLETED 時，行 `self.journal_event("CORE_TASK_PARKED", ...)`（已經接咗 error alert + 30 min cooldown）。**唔好**自動 degrade core source——問題係「靜」同「無限期」，唔係「太嚴」。
- **safe_during_run**：✅ 得
- **effort**：S
- **verifier**：4 個子機制全部對返 code 逐個確認；timing 由 raw log 對過（`00:38:54Z` → `01:07:50Z`）。

#### P0-2 · gemrate 每個 shard 嘅 Cloudflare warm-up navigation 冇 retry，一炸就拖冧全部 399 張

- **file:line**：`pipelines/gemrate_source.py:1737-1744`（`_gemrate_public_page`）
- **evidence**：

```python
with sync_playwright() as pw:
    browser = _launch_chromium(pw)
    ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900})
    ctx.add_init_script(_STEALTH)
    ctx.route("**/*", _abort_heavy_resources)
    page = ctx.new_page()
    page.goto(WEB + "/universal-pop-report", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
```

- **點解錯**：成個 website transport 入面，**得呢一個 network call 冇 retry**。逐張卡嘅 `page.goto` 有 RATE_LIMIT_LADDER（30/60/120/300/600 秒）加埋第二 pass slow retry；但 gate 住全部 399 張嘅呢一次 navigation 得一次機會。而且 `_fetch_card_once` 有 blanket `except Exception` 返 `browser_evaluation_failed` receipt，所以 per-card 故障**根本去唔到** `_run_shard` 個 handler（`:1945`）——即係話 08-23 觀察到嘅 shard 級 `browser_collection_failed` 一定係喺 session setup 呢七行入面爆。4 個 shard process 喺同一秒開 Chrome（run id `...674651Z / ...703790Z / ...811100Z / ...907796Z`），正正係最易 transient 失敗嗰刻。
- **實例**：08-20 同 08-23 兩次全 399 張陣亡（`daily_20260823T003856907796Z_3-of-4/manifest.json`：attempted=399 succeeded=0 failed=399，session 大約 1.5 秒就死）。08-24 因此 blocked ~29 分鐘 + 13m48s 重爬。
- **最小修法**：`_launch_chromium` + `new_context` + warm-up `goto` 包一個 3-attempt loop，backoff 5/15/30 秒，每次之間 close 咗半生半死嘅 browser，第三次先 raise。
- **safe_during_run**：✅ 得
- **effort**：S
- **verifier**：由 `_fetch_card_once` 嘅 blanket except 反推 + `_launch_chromium` raise RuntimeError（→ `browser_unavailable`）反推，鎖定失敗只可能喺 `new_context`/`new_page`/`goto`，正正係呢個 wrapper 覆蓋嘅範圍。**由 P1 升做 P0**：兩次全 399 失敗 + 一次 29 分鐘 blocked run。

---

### P1

#### P1-1 · `execute_ready()` 係批次屏障，唔係 pump — 死咗嘅 task 要等最慢 sibling 行完先可以重試（實測 14.0 分鐘）

- **file:line**：`pipelines/daily_chain_v2.py:1994-2009`
- **evidence**：

```python
rows = self.journal.claim_ready(
    self.run_id, phases=self.eligible_phases(),
    lease_seconds=TASK_LEASE_SECONDS, limit=16,
)
if not rows:
    return 0
# Every durable claim must start heartbeating immediately; do not claim
# sixteen tasks and leave half queued behind an eight-thread executor.
with ThreadPoolExecutor(max_workers=len(rows)) as pool:
    futures = [pool.submit(self.execute_claim, row) for row in rows]
    for future in as_completed(futures):
        future.result()
return len(rows)
```

- **點解錯**：`claim_ready()` 係一個時間點嘅 snapshot，而 `with ThreadPoolExecutor` 會 join 晒所有 future 先返。`run_tick`（`:2256-2257`）要等佢返先再 `plan()`／`claim_ready()`。10 分鐘一次嘅 scheduled tick 都幫唔到手：`acquire_tick_lock()`（`:346`）係 exclusive flock，整批行緊期間每個 scheduled tick 都只會印 `TICK_SKIPPED_LOCKED`。**任何早死嘅 task，實際重試延遲 = 同批最慢嗰個嘅時長，唔係佢自己個 backoff。**
- **排除咗嘅解釋**：(a) backoff — SOURCE_FAILED 用 `TRANSIENT_RETRY_SECONDS[0]=60`，08-24 shard 3 應該 `+1.3m` 就到期；(b) concurrency — gemrate `max_concurrency=4`（`daily_chain_v2_adapters.py:297`），shard 0-2 行緊仍然有第 4 個位。
- **實例**：08-24 shard 3 `+0.3m` 死，`+15.3m` 先重試 = **14.0 分鐘死等**。08-22 pricecharting 16 次 attempt 全部要等批次邊界。
- **最小修法**：改成 refill scheduler：keep 一個長命 `ThreadPoolExecutor` + 一個 live future set，用 `wait(pending, return_when=FIRST_COMPLETED)`；每次有 future 完成就再 `claim_ready(limit = 16 - len(in_flight))` 再 submit，直到 in-flight 空 + claim_ready 返空。每次 refill 都要保留 `deadline_monotonic` guard 同 per-group `max_concurrency` 檢查。~30 行，唔使改 schema。
- **safe_during_run**：⚠️ 呢個 tick 已經 load 咗 module，改動下個 tick 先生效；建議 run 完先落（見第 5 節）。
- **effort**：M
- **verifier**：三個 finder 各自獨立中同一條，證據一致；14.0 分鐘實測。**由 P0 降做 P1**：純延誤，冇 failed run、冇錯 data。

#### P1-2 · publish 階段一律 PUBLISH_FAILED + 2/5/10/20/30 分鐘 ladder，連 deterministic code/test failure 都照瞓（實測 68.8 分鐘）

- **file:line**：`pipelines/daily_chain_v2_contract.py:273-274`；ladder 喺 `:40`；orchestrator 呼叫喺 `daily_chain_v2.py:1738`
- **evidence**：

```python
if stage == "publish":
    return RetryDecision("PUBLISH_FAILED", False, PUBLISH_RETRY_SECONDS)
# PUBLISH_RETRY_SECONDS = (120, 300, 600, 1200, 1800)
```

- **點解錯**：publish branch 喺 CDP（`:275`）、MySQL（`:280`）、transient-HTTP（`:285`）**之前**短路，所以 publish 階段嘅失敗永遠唔會被認出係第二樣嘢——release 期間真係 MySQL 死咗都只會食 2/5/10/20/30，而且永遠唔會觸發 `recover_mysql()`（`daily_chain_v2.py:1782` 只認 `MYSQL_UNAVAILABLE`）。同時，一個死實嘅 test failure 一樣行足成條 ladder。
- **實例**：08-23 attempt 2-6 六個 log 全部 5940 bytes、同一個 FAIL、同一句 `65/66 passed, 1 failed, 7 skipped`；68.8 分鐘純 sleep，operator 到 `+87m` 先見到第一個 terminal verdict（本來 `+18.6m` 就可以）。
- **最小修法**：喺 `classify_error` 嘅 **publish branch 入面**（唔好放喺佢上面）加 deterministic 判斷：test-suite verdict regex `\d+/\d+ passed, [1-9]\d* failed`、或者 `NameError|AttributeError|TypeError|ImportError|ModuleNotFoundError|SyntaxError|ReferenceError`、或者 `exit=127`／`command not found` → `RetryDecision("PUBLISH_DETERMINISTIC", True, ())`，再加入 `ALWAYS_ALERT_EVENTS`。
- **⚠️ 陷阱（verifier 標紅）**：
  1. **唔准**放喺 `if stage == "publish"` 上面 —— `' failed,'`／`typeerror`／`attributeerror` 喺 source worker traceback 好常見，咁做會把 transient source failure 變成 terminal。
  2. **唔准**淨用 `"passed," in value and "failed" in value` 呢種鬆 heuristic —— 分類文字係 `_tail(limit=4000)`（`daily_chain_v2.py:1684`），一個 test 全過但 `git push` 失敗嘅 release 一樣會中，變成唔可重試嘅 terminal，比原 bug 更差。要用有 capture group 嘅 regex 確認 failed 數 ≥ 1。
- **safe_during_run**：✅ 得（改 classifier，下個 attempt 生效）
- **effort**：S
- **verifier**：三條 log 證據（byte size、FAIL 行、attempt-7 嘅 git fast-forward）全部落實；regex 可達性亦確認（log 只有 95 行，一定喺 4000 字 tail 入面）。

#### P1-3 · `clamp_manual_window` 個 17:00 JST cap 過咗 17:00 就唔再 bind，而每次續期都會重設 `source_cutoff` 斬死行緊嘅 source

- **file:line**：`pipelines/daily_chain_v2.py:175`（cap）同 `:183`（`source_cutoff`）
- **evidence**：

```python
floor = started + timedelta(seconds=MANUAL_WINDOW_MIN_SECONDS)
cap = max(floor, last_scheduled_tick_utc(business_date))     # <- floor 贏
ceiling = next_scheduled_tick_utc(started) - timedelta(seconds=NEXT_TICK_GUARD_SECONDS)
cap = min(cap, ceiling)
cap = max(cap, started + timedelta(seconds=MANUAL_WINDOW_ABSOLUTE_MIN_SECONDS))
# ...
values["source_cutoff"] = started + timedelta(seconds=span * 0.5)
# docstring :162 話 "No manual window may outlive the day's last scheduled tick."
```

- **點解錯**：`started` 一過 17:00 JST，2700 秒 floor 永遠大過 `last_scheduled_tick_utc`，個 cap 直接被丟棄。**實測執行**（business_date 2026-08-22）：16:00 JST 開 → final 17:00（cap 生效）；18:00 → 18:45；21:00 → 21:45；23:00 → 23:45。配合 `Journal.renew_manual_window`（會把 `FAILED_FINAL` 復活成 `RUNNING`），operator 可以 45 分鐘一格無限開落去，`FAILED_FINAL` 呢個 gate 變裝飾。
- **更貴嘅一半**：每次續期 `:183` 把 `source_cutoff` 重設成 `started + span*0.5` = **22.5 分鐘**，即係每次續期都重新武裝一個 22.5 分鐘 deadline 落每一個 non-core source 身上。**呢個先係 08-22 pricecharting 12 次 WORKER_INTERRUPTED、~533 分鐘蒸發嘅真身**（attempt 長度 22.6 / 20.4 / 20.0 / 20.0 完全對得上），唔係 tick deadline。
- **實例**：08-22 `MANUAL_WINDOW_RENEWED=8`，PUBLISHED 喺 23:47 JST，遲咗 6h47m，data 仍然標 business_date 2026-08-22。
- **最小修法**：`cap = min(last_scheduled_tick_utc(business_date), next_scheduled_tick_utc(started) - NEXT_TICK_GUARD_SECONDS)`，再 `cap = max(cap, started + MANUAL_WINDOW_ABSOLUTE_MIN_SECONDS)`。另外 `renew_manual_window` 加每 run_id 續期次數上限。
- **⚠️ 陷阱（verifier 標紅）**：淨係咁改**會令情況更差**。17:00 JST 之後 `last_scheduled_tick_utc` 已經過咗身，`max(cap, started+600)` 會俾一個 10 分鐘 window，而 `:183` 就會計出一個 **5 分鐘 `source_cutoff`** —— 每個 non-core source 5 分鐘就俾 SIGTERM。要同一次改埋 `span*0.5` 條規則（例如 `source_cutoff` 下限鎖喺 `final`，或者豁免已經行緊嘅 non-core source）。
- **safe_during_run**：❌ 唔得（縮短 `final` 可能即刻觸發 `FAILED_FINAL`）
- **effort**：S（改 cap）／M（連 `source_cutoff` 一齊改）
- **verifier**：直接執行 `clamp_manual_window` 驗過四個時間點。**由 P2 升做 P1**：呢個係最大單一 measured time cost。

#### P1-4 · `classify_error` 用 substring 夾 `"nan"` / `"forbidden"`，transient 上游故障變零重試 TERMINAL

- **file:line**：`pipelines/daily_chain_v2_contract.py:265-272`
- **evidence**：

```python
if any(token in value for token in (
    "unauthorized", "forbidden", "invalid api key", "authentication",
    "schema contract", "contract mismatch", "illegal numeric", "nan", "infinity",
    "migration content changed", "migration hash", "migration checksum",
)):
    return RetryDecision("TERMINAL_CONTRACT", True, ())
```

- **實測（import 落去行）**：
  - `'SNKRDUNK is under maintenance, retry later'` → 中 `nan`（mai-nte-**nan**-ce）
  - `'HTTP 503 Service Unavailable: scheduled maintenance window'` → 中 `nan`
  - `'governance check failed'` / `'finance rate feed timeout'` / `'nanjing card lookup failed'` → 全部中 `nan`
  - `'HTTP 403 Forbidden (cloudflare)'` → 中 `forbidden`
- **點解錯**：呢啲正正係呢條鏈最常見嘅 **transient** 狀況（memory：PC headless 定期食 CF-403；SNK/GemRate 有維護時段），而佢哋被判做永久 contract violation，delay tuple 係空嘅，attempt 1 就 TERMINAL；再加 `reopen_retryable_terminal` 一見 `decision.terminal` 就 `return False`（`daily_chain_v2_journal.py:740`），**永遠唔會自己再行**，只可以人手 `unpark`。同 FX bug 一樣嘅形狀：一條為咗某個意思寫嘅規則，夾到意思完全唔同嘅文字。
- **未 fire 過**：掃過 08-22/23/24 全部 receipt，TERMINAL_CONTRACT 只有正當命中（`migration content changed` ×4、`FX schema contract` ×1），零誤判。所以係 latent，唔係已發生。
- **最小修法**：數字 token 改 word-boundary：`re.search(r'(?<![a-z])(nan|infinity)(?![a-z])', value)`。`forbidden`/`unauthorized`/`authentication` 搬出去做一個新 `AUTH_OR_BLOCKED` decision 行 `INFRA_RETRY_SECONDS`（re-auth 或者 CF cooldown 之後係修得返嘅），只留 `invalid api key` terminal。
- **safe_during_run**：✅ 得
- **effort**：S
- **verifier**：由 P0 降做 P1（未有實例）。注意：`FX schema contract` 嗰個命中係**故意**嘅，唔好為咗遷就呢條 finding 而改 `daily_chain_v2_worker.py:336` 條訊息。

#### P1-5 · gemrate shard 寫死 `--workers 1`，令 gemrate 成為比第二名長 2.7 倍嘅 critical path

- **file:line**：`pipelines/collect_control.py:1956-1959`
- **evidence**：

```python
"--website-budget-seconds",
str(GEMRATE_WEBSITE_BUDGET_SECONDS),
"--workers",
"1",
]
```

- **點解錯**：`gemrate_source.py:1930-1990` **已經實作咗** workers>1：一個 thread 一個 browser、1.5 秒 stagger、per-thread 429 ladder、`pending[offset::worker_count]` round-robin、逐張卡即時 persist。而佢要防嘅 rate ceiling 喺量度期內**從未 fire**（grep `'429 on'` / `'rate_limited_429_exhausted'` 喺 `data/runtime/daily-chain-v2/2026-08-2*/logs` 全部零命中）。實測 399 張用 858 秒（08-22）同 828 秒（08-24）= 2.08-2.15 秒/張，單 browser。
- **最小修法**：payload-driven，唔好寫死：`worker_payload={"adapters": ["gemrate_pop"], "ensureBrowser": False, "gemrateWorkers": 2}`（`daily_chain_v2_adapters.py:304`），穿過 `run_gemrate_pop`，出 `"--workers", str(gemrate_workers)`。**由 2 開始**（預期 ~7.5 min/shard），落之後睇 manifest 嘅 `transports.gemrate_public_card_page.failed` 同有冇 `rate_limited_429_exhausted` receipt 先好加。
- **⚠️ 風險**：adapter 本身已經 `max_concurrency=4`（`host:gemrate`），workers=2 即係同一部機 **8 個 Chrome**。`gemrate_source.py:1724-1731` 自己個 comment 已經講 Chrome relaunch 喺呢部機會 hang。「4 個冇 429」呢個證據唔會自動 transfer 去 8 個。要同 P0-2 個 warm-up retry 一齊落。
- **safe_during_run**：✅ 得（下個 shard 生效）
- **effort**：S
- **verifier**：line-exact 確認；並行 code path 存在且有 stagger/ladder；零 429 確認。

#### P1-6 · 一個 browser session exception 拖冧成個 shard，仲會 skip 埋設計好嘅 slow-retry pass

- **file:line**：`pipelines/gemrate_source.py:1244-1249`
- **evidence**：

```python
outcome = collect_public_card_details(
    pending, cards_dir=run_cards, delay=pass_delay, resume=False,
    chunk_size=WEBSITE_CHUNK, workers=pass_workers,
    deadline=deadline, payload_sink=website_payloads,
)
if outcome["error"]:
    print(f"[daily] public card page unavailable: {outcome['error']}", file=sys.stderr)
    break
```

- **點解錯**：website transport 其他地方全部係 card-scoped（per-card 429 ladder、per-card failure receipt、設計好嘅第二 pass slow retry `(1, SPEEDS["slow"], "slow retry")` 喺 `:1225`），但 shared browser session 入面任何一個 exception 會升去 shard scope（`shard_error`，`:1946`），再俾呢個 `break` 升去 run scope。**個 `break` 正正禁用咗唯一為咗救返呢班卡而寫嘅機制。**
- **實例**：08-24 shard 3 全 399 張。成本：13m48s 重爬 + 15 分鐘等重派。
- **最小修法**：第一 pass 改成 `if outcome["error"] and not outcome["succeeded"]: continue`（用一個 fresh session 喺同一 budget 入面再試），第二 pass 先 `break`；連續 N 次 session 級錯誤而且零卡收成先放棄。
- **⚠️ 註**：單獨落呢條唔夠力——08-23 個 session 係開波 1.5 秒就死（同時 3 個 sibling Chrome 喺起緊），即刻 retry 好可能一樣死。**要連 P0-2 個 5/15/30 秒 backoff 一齊落。**
- **safe_during_run**：✅ 得
- **effort**：S
- **verifier**：line-exact。⚠️ 原 finding 寫 `elapsed 18 s` 係**錯**嘅 —— manifest 根本冇 `elapsed` key，實際 worker-start `00:38:54.117Z` → receipt `00:38:59.002Z` = ~4.9 秒。**由 P0 降做 P1**（冇錯 data，當日已經 recover）。

#### P1-7 · 一次全 shard browser outage 會把 399 張卡逐張推高 quarantine streak，而 quarantine 冇時間衰減 = 永久鎖死

- **file:line**：`pipelines/collect_control.py:2039`（`_record_item_outcomes`）；streak 邏輯 `:676`；`QUARANTINE_THRESHOLD = 3` 喺 `:92`；`_quarantined_streams` `:641`；`_partition_quarantined` `:651`
- **evidence**：

```python
if failed_items:
    # Per-item source failures advance the quarantine streak immediately so
    # they persist even if the ingest step below fails for other reasons.
    _record_item_outcomes("gemrate_pop", succeeded=[], failed=failed_items)
```

- **點解錯**：quarantine 係用嚟隔離**個別**壞 ID 嘅。一個由頭到尾一次都冇成功過嘅 transport 係 lane failure，唔係 399 個獨立 item failure，而 manifest 本身已經有判別器：`transports.gemrate_public_card_page.succeeded == 0` 而 `attempted == 399`。
- **⚠️ 真正嚴重嘅點（同原 finding 講嘅唔同）**：「悄悄跌走 399 張卡」係**錯**嘅 —— gemrate 係 `required_class='core'`，`daily_chain_v2_db.py:158-167` 會算到 `complete=False`，`daily_chain_v2_stage.py:127-145` 會 `raise RuntimeError("V2 core contract incomplete at ...")`，大聲攔住 publish。真正嘅 defect 係：**quarantine 冇時間衰減，而 quarantined item 永遠唔會再被嘗試**，所以唯一可以清 streak 嘅嘢（喺 `ok_items` 出現一次成功）**永遠唔會發生** —— 一入三次就永久自我延續，要人手改 quarantine state file 先解得返。
- **最小修法**：叫 `_record_item_outcomes` 之前先睇 manifest 嘅 transport counter：如果每個 attempted transport 都係 `succeeded == 0` 而且 `attempted == len(selected)`，就報 `ok=False, error="gemrate_transport_unavailable"`，**完全唔好**推 per-item streak。另外加 streak 時間衰減（例如 N 日冇再失敗就 reset）。
- **safe_during_run**：✅ 得
- **effort**：M
- **verifier**：機制逐行確認；harm 描述由 verifier 改寫（原本嘅「silent drop」係錯）。

#### P1-8 · `PARKED` 喺 journal 定義為「已了結」，但六個手寫 state literal 漏咗佢 → 一個 PARKED 嘅 identity stage 令 `plan()` 死鎖成日

- **file:line**：`pipelines/daily_chain_v2.py:1088`、`:1156`、`:1181`、`:1218`（另外 `:489`、`:502` 有 cutoff 逃生門所以冇事）；定義喺 `pipelines/daily_chain_v2_journal.py:33`
- **evidence**：

```python
# journal.py:30-33
# PARKED is settled work ... Downstream barriers must treat it as finished,
# exactly like TERMINAL, or one exhausted task would hold the whole business date open.
TERMINAL_TASK_STATES = frozenset({"COMPLETED", "DEGRADED", "TERMINAL", "SKIPPED", "PARKED"})

# daily_chain_v2.py:1088
if str(pending_stage.get("status") or "") not in SUCCESS_TASK_STATES | {"TERMINAL"}:
    return
```

- **點解錯**：`:1088`（同 `:1156` / `:1181` / `:1218`）冇 cutoff 逃生門。一個 PARKED 嘅 `pending-identities` 會令 `plan()` 喺每個 tick 嘅每個 loop iteration 都 `return`：冇 accept、冇 box、冇 release、冇 publish。`degrade_unfinished_phase` 救唔到（`journal.py:1127` 只覆蓋 PENDING/READY/RETRY/INTERRUPTED），`claim_ready` 亦唔會再派 PARKED row。而 PARKED 喺日常操作可達：`interrupt_claim` 喺 `attempts >= max_attempts` 就 park（`journal.py:953`），而 `candidate-source-plan` 嘅 `max_attempts=1` —— 一次 deadline interrupt 就即刻 park。
- **最小修法**：四個地方全部 import `TERMINAL_TASK_STATES` 嚟用，唔好再手寫個 set。再喺 durability test 加一句 assert：`daily_chain_v2.py` 入面每個 settled-state literal 都要等於 `TERMINAL_TASK_STATES`，防止再飄。
- **⚠️ 陷阱**：原 finding 叫改 `scripts/test_daily_chain_v2_durability.py:284` 係**錯**嘅 line —— PARKED 嗰個 assertion 喺 `:307-310`，而且佢 assert 嘅係 cutoff **之前** `source_barrier_ready` 應該係 False（cutoff 之後 `:489` 本來就會 return True）。照改會連 `source_barrier_ready` 語意都改埋，唔係純 literal swap。
- **safe_during_run**：✅ 得
- **effort**：S
- **verifier**：三日 run 未出現過，但可達路徑確認；有 `CORE_TASK_PARKED` alert + health.json 記錄，所以係「要人手介入」P1，唔係「不可恢復」P0。

---

### P2

> 以下全部有具體 failure path，但未量到時間成本，或者已經被下游 gate fail-closed 接住。

| # | file:line | 問題 | 最小修法 | safe | effort |
|---|---|---|---|---|---|
| P2-1 | `daily_chain_v2.py:2514` | `TICK_SKIPPED_LOCKED` 個 branch 都會寫 `health.json`，`written_at_utc` 每 10 分鐘被刷新 → watchdog 個 25 分鐘 staleness 規則（`scripts/watchdog_live_release.ps1:32,158`）永遠見唔到「tick 卡死但仲揸住 flock」。跑緊嘅 tick 只喺 `started`/`ended` 寫 health（`:2551`/`:2587`），50 分鐘動都唔動。 | (a) `run_tick` while-loop 每 iteration 寫一次 `write_health(tick_phase='working')`；(b) skip branch 唔好寫 health，或者寫去另一個 marker file | ✅ | S |
| P2-2 | `daily_chain_v2.py:764` | contract-repair task identity 把 `variantIds` 落 hash，shortfall 一縮就鑄一個全新 task + 全新 7-attempt ladder，每日冇上限。08-22 見到 4 個 pricecharting key + 3 個 snkrdunk key。新鑄嘅 PENDING non-core row 仲會令已經開咗嘅 `source_barrier_ready` 再閂返。 | revision hash 只用 `(businessDate, sourceCode, capability, adapterVersion)`；targets 放去 checkpoint。**⚠️ 淨改 hash 唔夠**：`add_task` 係 `ON CONFLICT DO NOTHING`，會靜靜變 no-op，要加 explicit reopen + checkpoint update path | ❌ | M |
| P2-3 | `daily_chain_v2.py:2271` | `time.sleep(max(0.05, wait_seconds + 0.05))` 可以喺 process 入面瞓足 1800 秒，期間揸住 flock、唔再行 `recover_expired()`、唔寫 health。另一端係 spin：`next_retry_wait` 俾 0.0 但 `claim_ready` 因為 concurrency group 滿而唔派，變成每秒 ~20 次 plan+claim scan。 | 拆成 ≤60 秒一片，每片寫 health + 重行 `recover_expired()`；下限由 0.05 改做 1.0。**⚠️ 唔好**淨係 cap 個 sleep：`wait_seconds > remaining → break` 條件係用未 cap 嘅值計，只 cap sleep 會變成同一段 wall clock 空轉 30 次 | ✅ | S |
| P2-4 | `daily_chain_v2_worker.py:228` + `daily_chain_v2_adapters.py:258` | worker 已經有 structured verdict（`errorCode`、`failedAdapters`、per-command exit code），但 adapter `raise RuntimeError(str(receipt.get("error") or ...))` 掉晒，只剩 prose；worker 再把最多 6000 字 stderr tail 塞入去。retry policy 由「邊張卡啱啱落喺最後 6000 bytes」決定，同一種失敗跨 run 唔 deterministic。 | worker raise typed error 只帶 error code；6000 字 blob 放 receipt `detail`；`ingest` 把 `errorCode` 傳去 classifier 短路 substring scan | ✅ | M |
| P2-5 | `daily_chain_v2_worker.py:159-169` | shard 夾唔到任何 registry row 就返 `status:"completed"` + 一個假 `payloadSha256`（`sha256({"empty": True})`）、冇 `evidenceRef`。`adapters.py:265-270` 照收唔驗 counts。`"gemrate_pop"` 呢個字串喺 worker payload（`adapters.py:304`）同 registry builder（`collect_control.py:1278`）各寫一次，一飄就四個 shard 全部毫秒返 completed。 | 改返 `status='degraded'` + `detail.reason='shard_matched_no_registry_rows'`，唔好偽造 sha；整個 adapter 零 registry row 就 raise（空 shard 正常，空 adapter 唔正常） | ✅ | S |
| P2-6 | `daily_chain_v2_worker.py:350` | `"currencies": len(snapshot.get("quotes") or {})` —— snapshot 根本冇 `quotes` 呢個 key（`fx_rates.py:86-93` 係 `rates`）。live receipt `0419b3c9e68c852f-attempt-2.json` 寫住 `{"currencies":0,"written":30}`。`.get(...) or {}` 食咗個 KeyError，receipt 睇落好正常。將來任何 `counts['currencies'] >= 30` 嘅 gate 都會永遠唔滿足。 | `len(snapshot.get("rates") or {})`，再加 assert 等於 `len(load_result['currencies'])` 先准返 completed | ✅ | S |
| P2-7 | `daily_chain_v2_worker.py:233-234` | `status = "degraded" if quarantined else "completed"` —— `counts.failed` 寫咗落 receipt 但冇任何決策讀。SNK adapter 可達：`collect_control.py:2246-2256` init `ok=True`，`:2341` 設 `failed`，`:2347` 只喺**全部** item 失敗先 flip `ok=False`。三日內未出現過（latent）。 | `status = "degraded" if (quarantined or int(report.get("failed") or 0)) else "completed"`，detail 帶埋失敗 adapter list | ❌ | S |
| P2-8 | `daily_chain_v2_contract.py:69` | `freshness_sla_minutes` 四個 adapter 全部宣告 405、有 validate、有寫入 `market_source_registry`、出現喺每個 contract dump —— **全 repo 零個地方拿觀察年齡同佢比較**，`current_run_contract` 連 column 都冇 SELECT。operator 睇 registry 會以為有 6h45m staleness 保證。典型「有檢查零 call site」。 | 二揀一：喺 `CommandSourceAdapter.ingest` 真係比 `now - observed_at` 並 degrade + alert；或者索性刪走個 field。**刪走係真正最小嘅選項**（enforce 係行為改變，可能中途開始 degrade source） | ❌ | M |
| P2-9 | `gemrate_source.py:860` | 一個死咗嘅 browser 會令 399 張卡喺**永久** manifest 入面寫成 `reason: "no_current_population"` —— 即係一句關於 GemRate 數據嘅斷言，但真相係「我哋個 browser 未出過 request 就死咗」。`collect_control.py:2032` 再原文抄落 per-item failure 同 quarantine entry。`failureReceipts`（`:2007`）有 399 條真原因，但 `cmd_daily` 由頭到尾冇讀過（grep 證實只有一個 occurrence）。 | `cmd_daily` 收好 receipts，傳一個 `website_failure_reasons` map 入 `build_population_transport_run`，`:860` 改 `reason=website_failure_reasons.get(gid, "no_current_population")`，manifest 加 `websiteFailureReceipts` | ✅ | S |
| P2-10 | `gemrate_source.py:1854` | `_safe_browser_error` 把所有 browser 故障壓成一個字串，exception object 完全掉走 —— 冇 type name、冇 message、run dir 冇任何嘢。兩次全 399 outage 之後，唯一線索係一句 `browser_collection_failed`。（本次審計要靠反推先鎖到範圍，就係實證成本。）exception class name 唔含 secret。 | `return f"browser_collection_failed:{type(error).__name__}"`，同埋把 `{gemrateId: reason}` receipts 連 class name 寫落 run dir `website_transport_receipts.json` | ✅ | S |
| P2-11 | `gemrate_source.py:1224` | `(workers, max(0.3, delay), "first")` —— caller 明明傳 `--speed fast`（0.15，`collect_control.py:1954-1955`），被靜靜加倍做 0.3 秒；再加 `:1577` 個 `page.wait_for_timeout(200)` JSON poll（平均 100ms overshoot）。可回收 ~0.25-0.30 秒/張 = **~100-120 秒/shard**。呢個 floor 冇任何量度支持（對比 PC 個 sleep 有 `pc_cdp_sold_refresh_win.py:20-30` calibration table），而且量度期內零 429。 | 直接用 `delay`（拆走 `max(0.3, ...)`，保留 slow-retry pass），poll 改 50ms。同 P1-5 一齊落，落完睇 `transports.*.failed` 同 429 receipt | ✅ | S |
| P2-12 | `collect_control.py:1887` | manifest reuse 條件 `fetched_at.astimezone(JST).date().isoformat() == business_date` —— 同 FX midnight bug 一模一樣嘅形狀。operator 傳一個同 fetch 時 JST 日期唔同嘅 `--business-date`（08-24 run 正是：fetchedAt `2026-08-23T00:38:56Z`，business_date `2026-08-24`），reuse path 直接死，MySQL ingest 一 blip 就要重爬 828 秒/shard。docstring 自己寫住「retrying the provider network work is both slow and semantically wrong」。 | 改成 run-anchored window：`fetched_at >= min(JST-midnight(business_date), run_created_at)` 且 `<= now`，shard suffix 同 resolved-ID set 檢查唔變 | ✅ | M（`collect_control` 只見到 `CARDZ_V2_BUSINESS_DATE`，要新開一個 env 傳 `run_created_at`） |
| P2-13 | `collect_control.py:3818-3824` | PC local SLA replay 嘅 `html_path.stat()` 同 `read_bytes()` 都冇 guard，而 `ROOT / ""` 會 resolve 返 ROOT 本身（Python 3.10.11 實測），即係空 map field 會得到一個 directory：`stat()` 成功、`read_bytes()` 爆 `IsADirectoryError`。`_collect_mode_impl`（`:4387-4700`）零個 `except Exception`，OSError 一路走出去炸冧成個 pricecharting task → SOURCE_FAILED → 62 分鐘 backoff。同一個 loop 仲有 `:3812` 個裸 dict lookup `map_by_variant[variant_id]`，同樣 blast radius。 | stat 之前加 `if not html_path.is_file(): network.append(item); network_reasons[...] = "local_exact_html_missing"; continue`（上面兩行 `local_exact_html_exceeds_36h_sla` 已經係正確示範）；空 `html_path` 當 map contract error | ✅ | S |
| P2-14 | `daily_chain_v2_journal.py:998`（unpark）／`:1048`（retire） | 兩個都係 `SELECT * FROM chain_task WHERE task_key=?`，UPDATE 亦冇 `AND run_id=?`。CLI（`daily_chain_v2.py:2358`/`:2397`）只用 run_id 嚟標籤 event。而 `claim_ready` 係 scope 喺 run_id（`:523`）。結果：貼錯前一日個 key → row 真係變 READY、印 `TASK_UNPARKED ... ->READY`、但今日冇任何 tick 會 claim 佢。operator 以為條 lane 復活咗。08-22 要 12 次 unpark，呢個係真 foot-gun。 | `unpark`/`retire` 加 `run_id` 參數，SELECT 同 UPDATE 都加 `AND run_id=?`，唔 match 就 return None（exit 2，訊息講明「task belongs to another business date」） | ✅ | S |
| P2-15 | `scripts/daily_public_release.sh:49` | 仲有第二條非-V2 publish path 生存緊（`scripts/refresh_publish.ps1:89`、`scripts/morning_browser_lanes.ps1:123` 都係唔帶 `--v2-*` 咁叫 `daily_public_release.ps1`，即 `V2_MODE=0`，冇 immutable manifest、冇 journal claim）。佢同 V2 之間唯一嘅 interlock 係 `flock -n 9`，喺 `set -euo pipefail` 之下靜靜 exit 1、零輸出 → V2 讀成 `release exit=1: `（空 tail）→ PUBLISH_FAILED → 白燒 67 分鐘 ladder 等一個**人手**publish 放鎖，receipt 完全冇講。**註**：呢個 flock 本身係啱嘅（兩條路都經同一個 WSL `/tmp` lock），錯嘅係佢靜同被誤分類。 | `flock -n 9 \|\| { printf 'daily release: another publisher holds %s\n' "$LOCK_FILE" >&2; exit 75; }`，`daily_chain_v2.py:1636` 把 exit 75 認做獨立 error class 行 `INFRA_RETRY_SECONDS`。另外 V2 已經接管 publication，`refresh_publish.ps1` / `morning_browser_lanes.ps1` 嗰兩個 call 應該拆走 | ❌ | S |
| P2-16 | `scripts/test_daily_chain_v2.py`（`:584-605` 一帶）／`scripts/test_daily_chain_v2_durability.py` | 兩個 test file 合共 2218 行，grep `run_tick`／`execute_ready`／`execute_claim`／`eligible_phases`／`next_retry_wait`／`_work_deadline_monotonic`／`_run_stage_process` = **零命中**。取而代之係 16 個 `read_text()` + ~20 條 substring assert（例如 `assert "03:30" in installer and "PT10M" in installer`、`:602` 釘死 `'"identity", "candidate-source", "activation"'` 呢串字面）。上面每一條 orchestration defect 都住喺 suite 從來冇 execute 過嘅 code 度。 | 加**一個** fixture test 真係驅動個 loop：temp journal + FakeAdapter registry + `deadline_monotonic = now+5s`，assert (a) 快死嘅 task 喺慢 sibling 完成之前就被重新 claim、(b) deadline 觸發嘅 `WorkerInterrupted` 唔會改變 `interruptions`。跟住刪走被 fixture 覆蓋咗嘅 source-text assert | ✅ | M |

---

### 已修 / 已診斷（2026-08-23 當日）

#### ✅ 已修 · FX staleness 只夾 JST 午夜（commit `c1ddf4cb`，`fx_freshness_floor`）

`daily_chain_v2_worker.py:289-308`。原本 FX 新鮮度只同 business_date 嘅 JST 午夜比，一個喺午夜之前開嘅 manual window 拎到嘅 rate 一律當 stale。修法係按 run 建立時間放寬 floor。

#### ✅ 已修（審計期間）· 同一個 bug 嘅另一半：coverage barrier 個 business window

- **file:line**：`pipelines/daily_chain_v2_db.py:35`（`business_window_utc`）
- **審計開始時**：`start = datetime.combine(day, time.min, tzinfo=JST)...`，冇按 run 建立時間放寬。三個 coverage section 全部照用：pop `:116`、quote `:168`、fx `:207`。
- **live 證據**：run `cardz-v2:2026-08-24` 喺 `2026-08-23T00:38:37Z`（09:38 JST）建立，business_date 08-24 → window start `2026-08-23T15:00:00+00:00`。4 個 gemrate shard 入咗 398+407+400+399 = 1604 行，fx 喺 `01:07:51Z` 寫咗 30 隻貨幣；**一秒之後** barrier receipt `d9a7b0ee3552a175-attempt-1.json` 寫住 `fx=0/30 gemrateMissing=1604 quotesMissing=1604` —— 成個 run 收嘅每一行都喺自己個 window 開之前約 14 個鐘。無論點重試都滿足唔到，結構性唔可能 publish。
- **修咗**：檔案 mtime `2026-08-23 10:37:31 +0900`，`business_window_utc` 已經讀 `CARDZ_V2_RUN_STARTED_AT` 並 `start = min(start, started)`。`scripts/test_daily_chain_v2_durability.py:940-958` 有覆蓋。
- **⚠️ 仲欠兩單（未修）**：
  1. **orchestrator 自己個 process 冇呢個 env**。`daily_chain_v2.py:835` 喺 orchestrator 入面直接叫 `current_run_contract(self.day_text)`（`plan_contract_repair_tasks`），但 `CARDZ_V2_RUN_STARTED_AT` 只喺 `:1601` 注入 **stage subprocess** 個 env。即係 repair planner 用緊窄 window，stage 側 barrier 用緊闊 window —— 兩邊會算出唔同嘅 shortfall。
  2. `pipelines/rebuild_036.py:7842` 係第三個 process，同樣暴露。
  3. `min()` 亦都靜靜放鬆咗正確性（off-cycle run 會數埋前一個 business day 嘅 observation）；原修法建議嘅「plan 時 assert `utc_now() >= window_start`」**冇落**。

#### ✅ 已診斷 · gemrate shard 3-of-4 全 399 張即時 `no_current_population`

根因 = 上面 **P0-2**（warm-up navigation 冇 retry）+ **P1-6**（`break` skip 咗 slow-retry pass）+ **P2-9**（transport 故障被寫成 data verdict）三重疊。

推理鏈：`_fetch_card_once` 有 blanket `except Exception` 返 per-card receipt，所以 per-card 故障**去唔到** `_run_shard:1945` 個 handler；`_launch_chromium` raise `RuntimeError` 會出 `browser_unavailable`，但實際 receipt 係 `browser_collection_failed` —— 兩邊夾埋，失敗只可能喺 `new_context` / `new_page` / warm-up `goto` 呢三個 call。時間對得上：worker start `00:38:54.117Z`，run dir `...T003856907796Z`，receipt `00:38:59.002Z` —— session 大約 1.5 秒就死，正正係 4 個 shard process 喺同一秒開 Chrome 嗰刻。同型 outage 08-20 亦發生過一次（`daily_20260820T152605432801Z_3-of-4/manifest.json`）。

---

## 4. 被推翻嘅指控（唔好再翻叮）

| 指控 | 推翻理由 |
|---|---|
| core-contract-pre 盲 timer，08-22 因此空轉 ~345 分鐘 | `TRANSIENT_RETRY_SECONDS` 封頂 1800 秒，解釋唔到 133.6 / 149.4 分鐘嘅空隙。嗰兩段係 core-contract-pre 用晒 `max_attempts=7` 入咗 TERMINAL 等 operator（`TASK_UNPARKED=12`）。ladder 實際只佔 ~39 分鐘；健康日（08-23）成本係 60 秒。 |
| 任務可以喺 tick 剩 31 秒時被 claim，然後被 tick deadline 斬死 | `_work_deadline_monotonic`（`:1533-1547`）唔係俾 tick 剩餘時間：pricecharting 係 `required_class="quote"`（non-core），佢個 work deadline 係 `source_cutoff`。08-22 嗰 12 次斬係 `source_cutoff` 斬嘅（見 P1-3），提議嘅 `MIN_WORK_SLICE_SECONDS` 一分鐘都救唔返。 |
| `plan()` raise 會炸冧 tick，而 crash path 唔會釋放 in-flight claim | `own_claims` 只喺 `execute_claim` 入面填，`finally` pop，而 `execute_ready` 個 context manager `shutdown(wait=True)`。`plan()` 行嗰陣 `own_claims` 一定係空 —— 冇嘢可以釋放。而且 `TICK_CRASHED` 喺 `ALWAYS_ALERT_EVENTS`，唔係靜。08-22 冇 `ORCHESTRATOR_ERROR` event，所以嗰次 crash 唔係由 plan() 嚟。 |
| `eligible_phases()` 餓死 contract-repair task，令 tick 空轉到 17:00 | 自相矛盾：令 `critical` 成立嗰啲 row（candidate-collection-registry / consolidate / activation / daily-accept）本身就喺回傳嘅 phase tuple 入面。而且前置條件互斥（repair 只喺 core-contract RETRY/TERMINAL 時鑄，而 `plan()` 喺 `core-contract-post` 未 complete 就已經 return，daily-accept 根本未 add）。08-22 時序亦唔重疊：最後一個 repair `+1174.0m`，daily-accept 第一次 `+1190.1m`。 |
| TERMINAL 係唯一嚴重但零 alert 嘅 state，08-24 fx TERMINAL 冇 page 過任何人 | `ALWAYS_ALERT_EVENTS` 唔係唯一 channel。`deliver_events()`（`:2118-2146`）喺有 `--notify` 時把**每一個**未派 event（除 `origin.%`）推去 Telegram topic 2925。`install_cardz_daily_v2_task.ps1:57` 有 `-Notify`；launcher log `launcher-20260823.log 09:38:37` 亦見到 `--notify`。所以嗰個 TERMINAL 有出過 Telegram。剩返嘅只係「靠 `--notify` 偶然開住」呢個窄 gap。 |
| tick deadline interrupt 食 interruption budget，令長 stage 自己 park（08-22 12 次 unpark 嘅原因） | `unpark` 會把 `interruptions` 清零並 grant `max_attempts = attempts+1`。pricecharting 由 `max_attempts=7` 起步但行到 a16，即係 a7 之後每一次都要人手 unpark；a8 六次斬之後仲喺度，證明 counter 已經 reset 過。binding constraint 由頭到尾係 attempts ceiling，唔係 `DEFAULT_MAX_INTERRUPTIONS=6`。`spend_budget=False` 一次 unpark 都慳唔到。 |
| worker 失去 journal claim 之後仲繼續寫 production MySQL（兩個 writer 一條 lane） | orchestrator 係**先殺後收**：`recover_expired`（`:1874-1913`）算 `alive`，disposition `terminate` 就 `terminate_worker_group(pid)`（SIGTERM → grace → SIGKILL）**先**，之後先 `_interrupt` 放返個 task。`recovery_disposition`（`:360-387`）仲會 adopt 一個 lease 剛過但仲生嘅 worker 而唔係重開。要出現雙寫，要 `terminate_worker_group` 失敗，呢點冇證據。（同三行入面嗰個 `except Exception` 食咗 sqlite `OperationalError` 係真嘅，但係 P2 脆弱性。） |
| tick-deadline interrupt 掉晒工作，PC resume 機制寫死 `None` | 傳 `None` **唔係**關掉 resume，係**開啟** auto-resume：`collect_control.py:3945-3957` `if resume_report is None and PC_REFRESH_REPORT.is_file()` 就會 load 返上次個 report。08-22 receipt 自己證明有 carry：`pcRefresh.networkVariantIds` 跨 attempt 係 1028 → 1028 → 1028 → 810 → 716 → 498 → 0 → 0 → 0。「零 carried progress」同「可以無限 livelock」兩句都唔成立。 |
| 每次 gemrate chain retry 都由零重爬 399 張（`resume=False` + per-run cache dir） | `_run_shard` 個 except 保住 `shard_payloads`，已經捉到嘅卡照樣入 `website_payloads` → manifest 標 partial → collect_control 逐 item 分流 ingest → checkpoint 前進，所以 chain retry 個 due set 會縮，唔係重播 399。08-24 嗰次慳唔到係因為佢真係捉到 0 張。而且兩個提議修法都**危險**：`cards_dir=CARDS_DIR, resume=True` 會令 cached card 唔入 `payload_sink`，全部變 `no_current_population`；`sorted(glob('daily_*_{suffix}'))[-1]` 冇 business-day scoping，新一日第一次會揀到**尋日**個 dir，而 `gemrate_source.py:552` 用當前 run 蓋 `effectiveDate` —— 即係把尋日 population 靜靜標成今日。呢個正正係要防嘅缺陷。 |
| 10:15 JST 之後每個 identity/candidate-source/activation worker 一 Popen 就即死，`pending-identities` 三次即 park、`plan()` 永遠死鎖 | 缺 guard 係真（`:1543`），但後果推唔出。`plan()` 過咗 cutoff 每一 pass 都會叫 `degrade_unfinished_phase(...,'identity',...)`（`:1051-1070`），而 `run_tick` 係 plan → execute_ready 交替，所以最多即殺**一次**：INTERRUPTED → 下一 pass DEGRADED → DEGRADED 屬 `SUCCESS_TASK_STATES` → gate 過。真正未被救嘅係 candidate-source（佢個 degrade call 喺 `if pending_ids:` 入面），但佢個 barrier 都喺同一個 `if` 入面，所以只係嘥 spawn，唔 block。降做 P2。 |
| `plan()` 423 行 early-return chain 靜靜卡死，仲每 pass 讀 41 次全表 | 量度大致啱（424 行、33 個裸 return、~39 次 `SELECT *`），但兩個後果都唔過關。成本：`chain_task` 一日得 ~15-30 行，本地 WSL ext4 sqlite，冇任何量度支持會超過幾毫秒。可診斷性：`build_health_document` 把每個 task 嘅 state 同 `last_error_code` 寫落 `health.json`（durability test `:497` 釘住），`status_brief` 印同樣嘢，而佢引用嘅 PARKED 情境仲會額外出 `CORE_TASK_PARKED`。降做 P2 衛生項。 |
| `TASK_ERROR` dedupe key 漏咗 attempt 號，第 2..N 次同樣失敗完全冇 event | 三行之後 `:1765-1781` 就有 `TASK_RETRY_STATE`，dedupe key **有** attempt 號，payload 個 `logPath` 亦係當次 attempt（`_task_paths` 用 `row['attempts']`，而 `claim_ready` 已經 increment 過）。08-22 roll-up：`TASK_RETRY_STATE=32` vs `TASK_ERROR=11`，正正係呢個。「完全冇 event」同「永遠指住 attempt 1 個 log」兩句都推翻。 |

---

## 5. 修理次序

### 桶 A · 而家即刻可以落（run 進行中安全，S effort）

| 次序 | 項目 | 每 run 慳／收益 |
|---|---|---|
| A1 | **P0-2** gemrate warm-up 3-attempt 5/15/30s retry | 避免 08-20/08-23 型全 399 outage：~29 min blocked + 13m48s 重爬 |
| A2 | **P0-1** core TERMINAL 出 `CORE_TASK_PARKED` alert | 把 28.9 min 靜默 blocked 縮成即時可見 |
| A3 | **P1-2** publish deterministic classifier（**放喺 publish branch 入面**，用有 capture group 嘅 regex） | 08-23 型 run 慳 **68.8 min**；alert 由 +87m 提早到 +18.6m |
| A4 | **P1-6** 第一 pass `continue` 唔好 `break`（同 A1 一齊落） | 慳 shard 級全滅嘅重跑 |
| A5 | **P1-4** `nan`/`infinity` 改 word-boundary regex；`forbidden`/`unauthorized` 搬去 `INFRA_RETRY_SECONDS` | latent；避免一次 CF-403 或者維護頁令 core source 永久 TERMINAL |
| A6 | **P1-5** gemrate `--workers` payload-driven，設 2 | shard 14.3 → ~7.5 min：**critical path 慳 ~7 min** |
| A7 | **P2-11** 拆走 `max(0.3, delay)` floor、poll 200→50ms（同 A6 一齊落先分得清效果） | **~1.7-2.0 min/shard**（並行，critical path 慳 ~2 min） |
| A8 | **P2-6** FX receipt `quotes` → `rates` + assert | receipt 由講大話變講真話 |
| A9 | **P2-13** PC local replay `is_file()` guard（+ `:3812` dict lookup guard） | latent；避免一個檔案唔見就燒 62 min backoff |
| A10 | **P2-10** `_safe_browser_error` 帶 exception class name + 寫 transport receipts | 下次 outage 唔使再靠反推 |
| A11 | **P2-14** `unpark`/`retire` 加 `AND run_id=?` | 除掉 operator foot-gun（08-22 用咗 12 次） |
| A12 | **P2-1** health liveness：跑緊嘅 tick 寫 `working`；skip branch 唔好偽造 | watchdog 個 25 min 規則先至有 call site |
| A13 | **P2-5** 空 shard 改 `degraded` + 唔好偽造 sha | 「冇嘢到期」同「我收齊晒」分得開 |

**桶 A 合計：一個乾淨 run 慳 ~9 分鐘（A6+A7）；一個 publish 出 bug 嘅 run 慳 ~69 分鐘；一個 shard 爆咗嘅 run 慳 ~29-43 分鐘。**

### 桶 B · 今次 run 完先落

| 次序 | 項目 | 每 run 慳 |
|---|---|---|
| B1 | **P1-1** `execute_ready` 改 refill pump（保留 deadline guard + per-group `max_concurrency`） | 有 task 早死時慳到 **14-15 min**；乾淨 run 慳 0 |
| B2 | **P1-3** `clamp_manual_window` cap 改 `min()`，**同時**處理 `source_cutoff = span*0.5`，另加續期次數上限 | 直接封住 08-22 型 ~533 min 蒸發 |
| B3 | **P1-8** 四個 gate 改用 `TERMINAL_TASK_STATES` + drift assert（留意 durability test 係 `:307-310` 唔係 `:284`） | 避免整日死鎖 |
| B4 | **P1-7** transport 全滅時唔好推 per-item quarantine streak；加 streak 時間衰減 | 避免永久自我延續嘅鎖死 |
| B5 | **FX window 殘留兩單**：把 `CARDZ_V2_RUN_STARTED_AT` 放入 orchestrator 自己個 `os.environ`（`daily_chain_v2.py:835` 之前）同 `rebuild_036.py:7842` 個 process；補返 plan-time assert | 避免 repair planner 同 barrier 用兩個唔同 window |
| B6 | **P2-3** tick sleep 拆片 + 下限 1.0s（**連 `wait_seconds > remaining` 條件一齊改**） | 釋放 flock，spin 消失 |
| B7 | **P2-7 / P2-12 / P2-15 / P2-8** | 見上表 |

### 桶 C · 要重新設計（L）

| 項目 | 點解係 L |
|---|---|
| **P2-2** contract-repair task identity + 生命週期 | 淨改 hash 唔夠：`add_task` 係 `ON CONFLICT DO NOTHING`，要新開 reopen + checkpoint-update path，仲要諗清楚一個 repair row 一日應該有幾多 attempt budget、同 `source_barrier_ready` 點互動（新鑄 PENDING row 會把已開嘅 barrier 閂返） |
| **P2-4** structured error contract（worker → adapter → classifier） | 要同時改 worker raise、adapter ingest、`classify_error` 簽名，等 retry policy 由 error code 決定而唔係 6000 字 prose。呢個係 P1-2 同 P1-4 嘅根本解 —— 兩條 S fix 只係止血 |
| **P2-16** 一個真正驅動 loop 嘅 fixture test | 冇呢樣，桶 A/B 每一條改動都係盲改。做完之後刪走被覆蓋嘅 source-text assert |
| （新）source phase 調度模型 | B1 個 pump 只係救 head-of-line。長遠 gemrate 4 shard × 15 min 應該由 shard 數／workers／budget 三者一齊 tune，而唔係喺 `collect_control.py` 寫死 |

---

## 6. 唔准做

### 硬規矩

1. **唔准放鬆任何 gate。** core contract barrier、`source_barrier_ready`、`daily_chain_v2_stage.py:127-145` 個 `V2 core contract incomplete` raise、publication manifest —— 呢啲三日以來全部**做啱咗嘢**（08-24 個 window bug 就係俾佢哋攔住，冇出過錯 data）。上面每一條 finding 嘅修法都係「令佢講嘢／令佢快返」，冇一條係「令佢鬆啲」。見到自己想改 `!=` 做 `not in {...}` 去塞多個 state 入去，停手先諗。
2. **唔准改 `backend.env`。**
3. **唔准用一大堆 id 去 query `operator_card_product_projection`。**

### 呢次審計新增（verifier 標紅嘅陷阱）

4. **唔准**把 test-verdict / Python-error token 檢查放喺 `if stage == "publish"` **上面** —— `' failed,'`／`typeerror`／`attributeerror` 喺 source worker traceback 好常見，咁做會把 transient source failure 變成 terminal。（P1-2）
5. **唔准**用鬆 heuristic（`"passed," in value and "failed" in value`）判 publish terminal —— 一個 test 全過但 `git push` 失敗嘅 release 一樣會中，會令 publish 完全唔可重試，比原 bug 更差。（P1-2）
6. **唔准**淨係改 `clamp_manual_window` 個 cap 而唔掂 `source_cutoff = span*0.5` —— 17:00 之後會變成 5 分鐘 source deadline，每個 non-core source 5 分鐘就死。（P1-3）
7. **唔准**淨係喺 contract-repair revision hash 拆走 `variantIds` —— `add_task` 個 `ON CONFLICT DO NOTHING` 會令佢靜靜變 no-op，新 targets 永遠去唔到 checkpoint。（P2-2）
8. **唔准**用 `cards_dir=CARDS_DIR, resume=True` 去做 gemrate cross-attempt resume —— cached card 唔會入 `payload_sink`，全部會變 `no_current_population`，成個 manifest 變 unpromotable。（refuted list）
9. **唔准**用 `sorted(glob('daily_*_{suffix}'))[-1]` 揀上一個 run dir 做 resume 來源 —— 冇 business-day scoping，新一日第一次會揀到尋日，而 `gemrate_source.py:552` 會用當前 run 蓋 `effectiveDate`，即係把尋日 population 靜靜標成今日。（refuted list）
10. **唔准**改 `daily_chain_v2_worker.py:336` 條 FX 訊息去避開 `schema contract` token —— 嗰個係**故意**嘅 contract fault 標記，要修嘅係 `nan`/`forbidden` 呢啲誤中 token。（P1-4）
11. **唔准**淨係 cap `run_tick` 個 `time.sleep` —— `wait_seconds > remaining → break` 條件用緊未 cap 嘅值，只 cap sleep 會變成揸住同一個 flock 空轉 30 次。（P2-3）
12. **唔准**喺 gemrate workers 由 1 跳到 4 或以上 —— adapter 本身已經 `max_concurrency=4`，workers=N 即係 4N 個 Chrome，而「4 個冇 429」呢個證據唔 transfer。由 2 開始，睇 manifest counter 再講。（P1-5）

---

## 7. 宣傳鏈（Hermes cron `bc4615fdd701`）— 2026-08-23 實測 + 已落嘅修法

> 證據：`~/.hermes/cron/output/bc4615fdd701/2026-08-23_{12-22-58,13-17-32,13-41-29}.md`、`~/.hermes/state.db` `messages`（tool output 全文）、receipts dir `~/.hermes/workspace/deliverables/cardz-marketcap-daily/`。

### 7.1 結果 [KNOWN]
六步全部出咗（gate 04:40Z 轉 `DONE`），尺寸啱：X 2160×2700、IG 2160×2160、Threads 2160×2700。但由 12:00 READY 到六張 receipt 齊用咗 **~99 分鐘**（12:00→13:39 JST），真正做嘢時間 [COMPUTED] ~25 分鐘。

### 7.2 時間去咗邊 [KNOWN]

| 時段 | 發生咩 | 損失 |
|---|---|---|
| 12:15–12:22 | x-en：`x-chrome-mcp-post.js:317 composeAndPost` 三次 attempt（226 s）都係 media 未 attach 到（`selectedFiles:0`），router exit 非 0，冇 receipt | transient；唔係 gate 問題 |
| 12:22–13:00 | gate `slot=HH` 每個鐘頭先變一次 → monitor hash 唔變 → agent 唔醒 | **~38 分鐘純等** |
| 13:01–13:05 | x-en、x-zh 成功 | — |
| 13:17 | ig-en：關「分享到 Threads」掣之後 IG 彈確認 overlay，舊 dialog locator 讀到 0 個 switch → 腳本誤判 `ig_threads_switch_still_on`（exit 4） | — |
| 13:17–13:30 | 同上 hourly slot 問題；grok 喺 13:2x **直接改咗 `cardz_marketcap_meta_post.py`**（加 confirm overlay 處理 + retry），冇 backup、冇 git、冇人 review，之後 13:33–13:39 四步出齊 | ~15 分鐘；治理缺口 |
| 每次 READY | 步驟 0 兩張 heatmap 每張 110 s render（CLI 行 OG endpoint）；舊 prompt 靠「檔已存在就跳過」 | 重試時多 ~4 分鐘；而且「檔存在」≠「係 live generation」——00:48 JST 嗰張 square 係舊 generation |

### 7.3 根因歸類
- **調度（主因）**：retry 節奏綁死喺 gate 輸出 hash，而 hash 只有每個鐘頭變一次。失敗之後嘅等待時間＝到下個整點。[KNOWN]
- **證據消失**：失敗 JSON 只印落 agent，Hermes `agent.log` 截到 ~330 字；下一次 tick 冇任何檔講得出上次點解衰。[KNOWN]
- **冪等缺失**：步驟 0 嘅 skip 規則喺 prompt（LLM）入面而唔係喺腳本入面；腳本本身冇 generation 概念。[KNOWN]
- **治理**：cron 入面嘅 agent 有 write 權去改自己嘅 posting script；今次改得啱（係收緊，要 confirm 證據先當已關），但同一權限下次可以改錯。[KNOWN]
- X composer attach flakiness 本身係 `x-chrome-mcp-post.js` 嘅問題，**未修**（只係令 retry 快咗）。[KNOWN]

### 7.4 已落（2026-08-23 05:03Z，test 47/47 綠，live probe 過）
備份：`~/.hermes/workspace/scripts/bak-promo-20260823T050315Z/`（五個腳本 + SKILL.md + cron job JSON）。冇 git commit（daddy 未叫）。

| 檔 | 改咗咩 |
|---|---|
| `~/.hermes/scripts/cardz_marketcap_promo_gate.py` | `slot=HHMM`（15 分鐘 bucket）；missing 步入面任何一步今日已失敗 ≥2 次 → 退返 `slot=HH`。最壞情況一日 ≈11 次 agent run 而唔係 36 |
| `cardz_marketcap_receipts.py`（新） | `record_failure` → `fail-<step>-<day>.json`（attempts[] 帶時間、exit、error、output 尾 2500 字）；`assert_media_generation`（讀圖旁邊 `.json` sidecar 嘅 `generation`） |
| `cardz_marketcap_heatmap_export.py` | sidecar 寫 `generation`/`rendered_at`/`skipped`；同一 generation + 尺寸過 gate → 0 秒回 `skipped:true`；`--force` |
| `cardz_marketcap_x_post.py` | 每個非零 exit 寫 fail record；`--yes-public` 前驗圖 generation == live（exit 8） |
| `cardz_marketcap_meta_post.py`（anchored patch，+27 行） | `fail()` 寫 fail record（step key 由 `--platform/--handle` 推）；`assert_live_media` 喺任何 browser 動作之前（exit 5 health / exit 8 stale）；receipt `generation` 用同一次 health 讀數 |
| cron prompt | READY `slot=HHMM`；步驟 0 每次都行（腳本自己 skip）；exit 8 → 重行步驟 0 再跑該步一次；加「唔准改腳本」 |
| SKILL.md | 閘門一段同步 |

Live probe：square 第一次 111 s render（舊 sidecar 冇 generation → 正確重出）、第二次 0 s `skipped:true`；post 同樣；x_post dry-run 過；gate 輸出字串不變 → monitor hash 不變，冇嘈醒 agent。

### 7.5 未修（欠單）
- `x-chrome-mcp-post.js` media attach 偶發失敗嘅根因（`selectedFiles:0`）。
- grok 改 script 嘅權限：cron prompt 已加「唔准改腳本」，但係 prompt 級唔係 code 級；真正修法係 posting scripts 對 cron agent 只讀（chmod / 另一個 user）。
- captions 腳本嘅 skip 仍然喺 prompt 入面（「三個 txt 已有就跳過」），未做 generation-aware。
- 明日 12:00 JST 先有真 e2e；今日 DONE 冇得再試。

## 8. 結構修法（workflow `w9xt8sf9k`，2026-08-23）— 落咗咩、落地、欠單 ledger

### 8.1 落咗咩 [KNOWN]
四個 package 各自一條 branch，再合成 `dev/20260823-v2-structure`（b1f3664c，直接喺 live 67085f16 之上）：

| package | branch head | 內容 |
|---|---|---|
| sched | 66b1c6ae | P1-1 refill pump（`_claim_batch`、`claiming_closed`、`EXECUTE_MAX_IN_FLIGHT=16`、`EXECUTE_POLL_SECONDS=2.0`）；drain（`drain_deadline_for`、`TICK_DRAINING` 非紅 event、`tick_hard_limit_seconds()=3300-60-60-180=3000`）；P2-1 skip-locked tick 寫 `tick-skipped.json`、唔再刷 health.json |
| gov | 7cf88f16 | DEGRADED core rows 會講嘢；`unpark`/`retire` 以 `run_id` scope；`MANUAL_WINDOW_MAX_RENEWALS=4`；`export_run_started_at` |
| classify | e150b9d5 | `PUBLISH_LOCK_HELD` exit 75 + `INFRA_RETRY_SECONDS`；`PUBLISH_ERROR_CLASS_RE`；P1-4 word-boundary；P2-5/P2-7 receipts |
| gemrate | e84eb961 | workers=2（8 個 Chrome）；SIGTERM grace 5→60 s；`run_started_at(state_db, run_id)`；`report['rateLimited429']`；`mapContractErrors` |

證明：`scripts/test_daily_chain_v2_loop.py`（6 checks，每條先重種 bug 證明會 fire）、`scripts/test_v2s_integ.py`、`run_all_tests.py --no-db` 71/71。

### 8.2 落地 [KNOWN]

**Merge**：workflow `land-v2-merge` 將 `dev/20260823-v2-structure`（b1f3664c）同 `dev/20260823-price-identity` 合落 `land/20260823-v2`：merge commit 9b3b5d68，三個 verify 鏡頭（correctness / gates-not-loosened / tests-fire）→ 一輪 fix → mustFix=0，final **c140db3c**。之後 `feat/20260823-run-label`（8b1b7d00，rehearsal label）merge 上去 → d447a852，零衝突。

**Live FF 鏈（`rebuild/036-foundation`，全部 `merge --ff-only`，落喺 tick 之間）**：
67085f16 → d447a852（07:04:26Z）→ ecb2eeaa（07:24:45Z）→ 08a33598（07:47:02Z）→ 97ea438c（08:09:06Z，profiling clocks）→ **da10b4f2（A04 三修，見 §8.3）**。
每次 FF 之後 live 跑 `scripts/run_all_tests.py --no-db`：80/82 → 80/83 → **81/83**。兩條長期 fail 係 Windows-only、非 regression：`test_daily_chain_v2_durability.py`（WSL guard，WSL 行 rc=0）、`test_pc_lane_durability.py` 101/106（SIGTERM 語義）。ecb2eeaa 嗰次第 3 條 `test_cdp_jammed_targets` 係 suite 內 port race flake，單跑 PASS。
WSL 側（鏈真正行嘅 runtime）：`test_daily_chain_v2_durability.py` + `test_daily_chain_v2.py` 喺 08a33598 都 rc=0。

**Rehearsal label（8b1b7d00）**：`daily_chain_v2.py --run-label A0n` → `run_id = cardz-v2:<day>#<label>`，journal `~/.local/state/cardz-marketcap/daily-chain-v2-<label>.sqlite3`、runtime `data/runtime/daily-chain-v2/<day>-<label>/`、`health-<label>.json`；**永遠唔 publish，喺 box 之後停喺 READY_FOR_CUTOVER**。用途：同一個 business date 可以重跑 N 次而唔燒 calendar date（daddy 2026-08-23 指示：「唔好叫 08-25，叫返 A01，慢慢加上去」）。
結構事實 [KNOWN]：publication 係 **DB-keyed**，唔係 journal-keyed——rehearsal 嘅 `daily-accept` 照寫 3308（`cardz_rebuild_generation` / `cardz_rebuild_checkpoint` / `catalog_rebuild_member` / `market_canonical_metric_acceptance` / `market_metric_history_acceptance` / `market_canonical_image_acceptance` / `catalog_population_identity_incident`），generation id 由內容 hash 推（A01、A02 同出 `db3308_ae47e5f0bc4747ef`，accepted 1604 == ranked 1604），所以重跑係 idempotent；冇 release / live-confirm 就冇任何公開面改動。

**Rehearsal 搵到並落咗嘅三個 bug（全部有 seeded-bug 證明會 fire，全部已 FF 入 live）**：

| # | commit | 現象 | 根因 | 修法 |
|---|---|---|---|---|
| R1 | 014efd9f | A01 手動窗只得 55 分鐘（final 08:00Z = 17:00 JST） | `clamp_manual_window` 對所有 manual window 都 cap 喺 business date 嘅 17:00 JST final | `rehearsal=True` 跳過 17:00 cap，只剩「下一個 03:30 JST − 300 s」ceiling；A02 證實 +4h/+5h/+8h |
| R2 | ecb2eeaa | A01 PC lane attempt 1 `pc_ebay_contract:OperationalError 3024` | `_db_writer_lease()` 用 `qualified_pool_operator.db()`，session cap `CARDZ_MAX_EXEC_MS=120000` 斬咗 `SELECT GET_LOCK(…,120)` 個 **wait** 本身（SNK 真 harvest 4.9 min 揸住 writer lease）；MySQL 8.4.10 hint `MAX_EXECUTION_TIME(0)` 唔豁免 | 兩條 lease 連線先 `SET SESSION max_execution_time=0` 再 GET_LOCK；writer lease 120→900 s；新 `scripts/test_collect_lease_uncapped.py`；A02 SNK+PC 重疊零 3024 |
| R3 | 08a33598 | PC CDP child 每次 call 重 fetch 同一 57 頁（A01 lane 58、checkpoint-repair 61×2 含一次 429、A02 lane 57；每次 ~100 s、bytes 一樣、`[bind] 0 proposals`） | parent 送 428 個 bind-missing id 俾 child，child 將佢哋 merge 入 `requested_ids`，凡 canonical MAP 有 row 嘅（rejected / manual_review 殘留）都被當 sold refresh 目標；bind step 只寫 proposal 唔寫 MAP，所以「bind 完有 MAP row 就順手 fetch」係 dead-by-design | child `select_refresh_rows()` 只 refresh exact id；`sweep_complete()` 容許 bind-only run 空 batch 完成；parent `pc_bind_missing_ids()` 對 explicit variant scope（checkpoint-repair）回 []；`scripts/test_pc_bind_unresolved.py` §3/§4 |

**量度 [COMPUTED]**（同一 business date 2026-08-23，gemrate 4 shards 都係 SLA replay）：

| run | live HEAD | wall | WORK | 備註 |
|---|---|---|---|---|
| 08-24 真跑（baseline） | 67085f16 | 125.0 min | 105.4 | 含 gemrate 真 harvest 14.9 min ×4 並行 + 19.6 min IDLE |
| A01 | d447a852 | 19.3 min | 16.9 | 1 TASK_ERROR（R2）、PC retry backoff 1.0 min |
| A02 | ecb2eeaa | 7.5 min | 7.6 | 零 TASK_ERROR；critical path PC lane 4.1 min（其中 108.7 s 係 R3） |
| A03 | 08a33598 | 5.7 min | 5.6 | 零 TASK_ERROR；PC lane 2.3 min（child batch 0 / fetched 0 / exit 0 = bind-only run 空 batch 完成，R3 證實）；同一 generation db3308_ae47e5f0bc4747ef |

Caveat [KNOWN]：rehearsal 日 gemrate shards 係 SLA replay（<1 min）；真 harvest 日會加返 ~15 min（4 shards 並行）。呢部分未被 rehearsal 量度過。

**未掂嘅 pre-existing**：live tree ` M scripts/daily_public_release.ps1`（LF/CRLF-only diff，mtime 08-20）同 untracked `data/public/*`——唔係今次嘢，冇郁。

### 8.3 欠單 ledger（逐條有證據；標籤遺失嘅誠實標明）

| # | 項目 | 狀態 | 證據／點做 |
|---|---|---|---|
| L1 | **drain 落咗 code 但係 inert** | 欠單，要 operator 決定 | `tick_hard_limit_seconds()=3000` == launcher `-MaxRuntimeSeconds 3000` → drain 得 0 秒。(1) task 改 `-MaxRuntimeSeconds 2400`（claim 40 分鐘收、drain 600 s，唔使改 PT）；(2) installer `ExecutionTimeLimit` PT55M→PT70M **同時** launcher env `CARDZ_V2_TICK_EXTERNAL_LIMIT_SECONDS=4200`（drain 900 s，先真係冚得住實測 55–60 min 嘅 gemrate contract-repair worker）。**建議**：先睇 08-24 第一次 workers=2 run 嘅 gemrate worker 時長同 `TICK_DRAINING.truncatedByExternalLimit`，再揀——兩個都係改 Task Scheduler，唔喺 repo 自動落 |
| L2 | P1-3 `source_cutoff` forward re-arm | 未做（declared） | 見 §3 P1-3；cap 改咗但 cutoff 重設未處理 |
| L3 | P2-2 repair due-set frozen | 未做 | regression test 釘住現狀，改之前先解釘 |
| L4 | `_work_deadline_monotonic` cutoff clamp 對 cutoff 之後先 claim 嘅 worker 唔會 fire | 另開 ticket | sched package 冇掂 |
| L5 | 8 個 Chrome（workers=2）host 負載未觀察 | 08-24 run 觀察 | 睇 run 期間 CPU/RAM + `rateLimited429` |
| L6 | gov package 實作期間 worktree `daily_chain_v2.py` 被 WSL `git show … >` redirect 截斷，靠 anchored patch 重建（test 過、diff 核過） | 已復原 | 教訓：`wsl.exe` inline redirect 會俾外層 shell 食咗，大檔一律 Write tool 落檔再跑 |
| L7 | journal `runs.business_date TEXT NOT NULL UNIQUE`（`daily_chain_v2_journal.py:99`）= 一個 business date 只可以有一個 run | 結構事實 | 同日要重來只能 `unpark`/`retire` 現有 run，冇第二個 run_id；文件化，唔係 bug |
| L8 | `_trim_mean_prices`（chart-price composer）喺 price-identity d557dbf0 刪咗；live 67085f16 `operator_control.py` 仲有 3 處 | 隨今次落地消失 | `scripts/test_live_ebay_sold_authority.py` 釘住「唔准返嚟」 |
| L9 | `pb_live_` key 寫死喺 `~/.hermes/skills/openclaw-imports/post-bridge-manager/{config.json,pb-manager.sh}` | 欠單 | 任何 Hermes log 輸出先 `sed 's/pb_live_[A-Za-z0-9_-]*/pb_live_<redacted>/g'`；正路係搬去 env／secret store |
| L10 | Task Scheduler 真相：`CARDZ-Marketcap-Daily-V2` 03:30 JST 起 **每 10 分鐘 tick** 到 17:00（PT55M、IgnoreNew、wscript //B 隱藏）；唔係「一日一個 18:30Z tick」 | 已更正 memory／plan | cadence 永遠睇 `scripts/install_cardz_daily_v2_task.ps1` + `Get-ScheduledTask` |
| L11 | 宣傳鏈欠單（§7.5）：X attach 根因、「唔准改腳本」只係 prompt 級、captions skip 未 generation-aware | 欠單 | 08-24 12:00 JST 第一次真 e2e |
| L12 | 標籤遺失（context 壓縮）：「tick granularity」「stale PC test fixture」「`market_universe_lock` 83 probe」「script slowness 量化」 | **未重驗** | 詳情只喺 session transcript；未重驗之前唔好當已知、唔好據此改嘢 |

### 8.4 A04–A10 rehearsal 循環（2026-08-23 08:09Z–11:10Z）— 每 run 嘅根因、落咗嘅修法、operator 決定

**Live FF 鏈（續 §8.2，全部 ff-only、落喺 run 之間、每次 `run_all_tests.py --no-db`）**：
da10b4f2 → acef1f35（A05 profile）→ c7d88799（A06 profile）→ ed4e9a53（同日 idempotent sale-quote mint）→ 3c06d17f（single-writer）→ b02ffb3e（WSL self-heal）→ **7f1952bf**（A10：PC lane resolve() + rankAndAccept 批次）。Live suite 最後 91/93（2 fail = §8.2 嗰兩條 Windows-only baseline）。

**量度 [KNOWN, journal + receipt]**（同一 business date 2026-08-23，gemrate 4 shards 全部 SLA replay，永遠唔 publish）：

| run | live HEAD | wall | 錯誤 | 關鍵 clock | 結論 |
|---|---|---|---|---|---|
| A04 | 97ea438c | 6.8 min | 1 TASK_ERROR `CDP_9333_UNAVAILABLE`（Chrome 其實生存，一次 2 s probe miss） | acceptHistory 68.6 s；pcPartition 32.9 s；pcAdapters 114.1 s | `ensure_chrome_cdp.ps1 -ProbeAttempts 3`；partition 重用 validator sha（少一次 718 MB 讀） |
| A05 | acef1f35 | 5.7 min | 0 | — | PC page parse cache、batched quote-revision door、receipt `statementTotals` |
| A06 | acef1f35（profiled） | 10.5 min | 2 個 attempt-1 連線失敗由 chain retry 救返（schema-052 `2013 reset`、PC lane `Packet sequence number wrong`），代價 3.9 min | acceptHistory 69.3 s；psa10_sale ×119 = 32.1 s | **churn 根因 1**：run-clock `asOf` 落咗入 hashed `pc_psa10_current_price_v1` payload → 1,238 PC row 每 run 重寫、35,949 revision 重鑄；**根因 2**：history ON DUP `accepted_at=VALUES(accepted_at)` 每 run 重 stamp 626,038 + 117,782 行 → c7d88799：同日 evidence idempotency、`connect_with_retry` 單一執行點、psa10_sale temp-table 一次過 join、conditional re-stamp |
| A07 | c7d88799 | 5.0 min | 0 | acceptHistory 27.3 s；psa10_sale 1 stmt 2.36 s；rowcount 0/0 | 仲 churn：sale-quote mint 每 run +1,753 NULL-kind + 1,618 bootstrap revision（`materialize` 用 as_of 做 checked_at）；**首次見 `Wsl/Service/0x8007274c` ×2** |
| A08 | ed4e9a53 | 4.2 min | 0 | acceptHistory 28.1 s；minted 0 / standing 1176+577 | sale-quote 同日 idempotent 證實（視窗內 0 新 revision）；**仲有 52,138 acceptance row 每 run 重 stamp**（pricecharting 22,500 / snkrdunk 29,638） |
| A09 | 3c06d17f | ~4.5 min（冷 distro 重啟，load 9） | 0（launch 第一次死於 0x8007274c，`wsl -t Ubuntu` 5 s 復活） | acceptHistory 20.3 s；視窗內 acceptance row **0** | **雙寫根因**：registry schema 上 043 legacy block 同 registry block 對同一 52,138 行計唔同 `lineage_sha256`，每 run 互相改寫 → 3c06d17f 一個 schema 一個 writer（registry 存在就跳過 legacy block；registry block 寫嘅行同最終狀態不變） |
| A10 | 7f1952bf | **3.5 min** | 0 | PC lane 1.3 min（A09 2.2）；rankAndAccept 9.9 s（A09 15.9）；statementCount 4,903（A09 8,109） | 見下 |

**A10 嘅兩個修法 [KNOWN, WSL live tree 實測]**：
- PC lane：`pc_page_cache.load_page` 用 `Path.resolve()` 做 cache key，喺 `/mnt/c` 每 call 11.2 ms（逐 component lstat），每 run ~4,548 頁；`failure_ledger.normalize_script` / `_events_root` 同樣每 call resolve（1,176 call ≈ 10 s／lane）。改 `os.path.abspath`（0.00 ms，1,098 map page A/B 零 miss）+ `lru_cache`。全 hit pass 25.6 → 2.73 ms/page。test：`scripts/test_pc_page_cache.py` step 8（monkeypatch `Path.resolve` 拋 exception，HEAD 4 fail）、新 `scripts/test_failure_ledger_memo.py`（HEAD 1 fail）。
- rankAndAccept：每 variant 一條「上一次 selected source」SELECT + 一條 `is_selected=0` UPDATE（1,604 × 2）→ 一條 window-function SELECT（EXPLAIN 0.011 s，derived 18,438 行）+ 一條 IN-list 批次 UPDATE；等價 harness（loop vs window，business_date < 08-23 / < 08-24）DIFF 0。`totals(limit=8→24)` 令 receipt 見齊 5 種 statement。Gate 不變：A10 DB `market_variant_source_state` 0823 canonical_quote `is_selected=1` = 1,604（rows 1,618），accepted=ranked=members=1,604、awaiting 0。

**WSL self-heal（b02ffb3e）[KNOWN]**：`scripts/wsl_ubuntu_selfheal.ps1` — probe `wsl.exe -d Ubuntu -- true`（cap 30 s）；靜默就用 `docker.exe info` 判 VM 生死；VM 生存就 **只** `wsl -t Ubuntu` 再 probe（exit 0 `WSL_REVIVED`）；VM 死 / 復活失敗 exit 3（`wsl --shutdown` 會拉低 MySQL 3308，係人嘅決定，script 永遠唔做）。`cardz_daily_v2_launcher.ps1` 喺交俾 WSL 之前跑佢，log `CARDZ_V2_WSL_PREFLIGHT exit=…`，exit 3 就 tick 以 3 退出。A10 第一次真身行：`exit=0 WSL_OK probeMs=367`。今日 0x8007274c 共 3 次，全部 VM 生存（docker 照答）。

**Operator 決定（daddy 2026-08-23 10:4x：「全部你決定，唔好等我」）[KNOWN]**：

| 項目 | 決定 | 理由／證據 |
|---|---|---|
| 6 個殭屍 discover pid（6235506 / 8091540 / 6235271 / 8091627 / 6235277 / 6235262） | `operator-rule --action reject --write` ×6，receipt `data/runtime/operator/rulings/ruling-*-v{1876,1915,1962,1974,2146,2153}-pricecharting.json` | 每個 variant 已 exact bind 另一個 pid（operator_adjudication_20260822）；呢啲係同號 sibling 頁；row 留 `manual_review` 但 `$.action='reject'` → `NOT_A_REJECTION_VERDICT_SQL` 唔再數。A10 reverify：reviewBindings 128→122、mapProductMismatch 55→49，held diff 剛好呢 6 對，added 0 |
| 3 條 SNK `operator-zero-20260814` supersede | **唔做** | `operator_ruling()` 對任何 `operator-` reason 都係 lane hold，supersede 成 accept 功能上零分別；v1203/v1448 已 exact、v1760 已有 PC exact 8091680 |
| v1813 reverify、v2033 fetch+bind、intake 16 張 | 已由 A08 in-chain stage（identity-intake / reverify-browser）做咗 | receipt 喺 `data/runtime/daily-chain-v2/identity-intake-2026-08-23.json` 同 reverify artifact |
| `consolidate_pc_map.py --write` | 唔使 | map 8/8 同 DB 一致 |
| ChromeCdpWatchdog PT5M FULL mode 踢走 9333 [GUESS] | 留原狀 | A04 一次 miss 未證實係 watchdog；`-ProbeAttempts 3` 已落；real run 先觀察 |
| drain 2400 s vs PT70M（§8.3 L1） | 留原狀 | 等 08-24 第一次 workers=2 真跑嘅 gemrate worker 時長先揀 |
| **MySQL 3308 runaway 第 3 次**（id 34297，`catalog_rebuild_member LEFT JOIN operator_card_product_projection … GROUP BY cohort`，10:28Z 起 2,694 s，tick preflight `DB_LONG_SESSIONS_OBSERVED maxMinutes 37` 有報） | root `KILL 34297` @ 11:13:57Z；**`SET PERSIST max_execution_time=1800000`**（30 min，寫入 volume `mysqld-auto.cnf`，重啟仍生效） | 同類 ×3（08-22 6,272 s、08-23 01:18 3,760 s、今次 2,694 s）→ 升級做「冇得做錯」：只影響冇設 session cap 嘅 read-only SELECT；chain 嘅 lease 連線本身 `SET SESSION max_execution_time=0`（`collect_control.py:127`）、operator read path 60–120 s，都唔受影響；INSERT…SELECT / UPDATE 唔喺 `max_execution_time` 範圍。證明會 fire：fresh `cardz` session `@@session.max_execution_time`=1800000；session cap 1,000 ms vs `SLEEP(5)` 1.002 s 後被斬。還原：`RESET PERSIST max_execution_time; SET GLOBAL max_execution_time=0;` |

**事故（誠實記低）[KNOWN]**：A10 準備期間喺 live tree 誤跑 `git stash pop`（cwd 由上一個 `cd` 殘留；`stash push -- <file>` 零內容後 `pop` 彈咗 common `.git` 入面 `stash@{0}`（release/037-fe04-box 嘅 `[deploy]` WIP）落 live tree：13 個 tracked 檔 M/UU + 2 個 untracked）。修復：`git restore --source=HEAD --staged --worktree -- <剛好嗰 13 個路徑>`，2 個 untracked 搬去 scratchpad；之後 `git diff HEAD` 空、`stash list` 三條原封不動、tick 之間零影響。規矩：**所有 worktree 共用一個 stash list，唔准喺任何 cardz worktree 用 `git stash`**；要證「test 喺 HEAD 會 fire」就 `cp` 檔案旁邊 → `git show HEAD:<file> > <file>` → 跑 → `cp` 返；每條 shell 命令必須明寫 `cd <tree> &&`。

**未做（有提案、冇落）[KNOWN]**：rankAndAccept P1/P2/P5/P6（workflow `wqnuapkwi`，各 ~2 s，要 rollback harness diff test）；PC lane P4–P8（`record_resolution` 只讀最後一個 event file、console-rows cache、重複 stat、c11 1,176 restamp UPDATE、derive 跳 eBay median）；pcNetworkRefresh 22 s bind sweep；rehearsal 每次都送一份 `identity.brief` 落 Hermes topic（`--notify`），未加 rehearsal 靜音。

## 附錄：判斷標籤

- [KNOWN] 三個 run 嘅 wall / attempt / event 數字、所有 file:line 同 code quote、08-23 六個 release log 嘅 byte size 同 verdict、08-24 barrier receipt 內容、gemrate manifest 嘅 399 unresolved rows、`classify_error` 對 `maintenance`/`governance`/`403 Forbidden` 嘅實測結果、`clamp_manual_window` 四個時間點嘅實測輸出。
- [COMPUTED] 「乾淨 run 慳 ~9 分鐘」= gemrate workers 2（~7 min）+ delay floor/poll（~2 min）；「68.8 min backoff」= 120+300+600+1200+1800 秒；「~533 min 蒸發」= 08-22 pricecharting 12 次 interrupted attempt 之間嘅 wall。
- [INFERRED] 08-23 shard 3 個 session 死喺 `new_context`/`new_page`/`goto` 三個 call 之一（由 `_fetch_card_once` 嘅 blanket except 同 `_launch_chromium` 嘅 RuntimeError branch 反推，唔係直接觀察 —— 因為 `_safe_browser_error` 掉咗 exception，見 P2-10）。
- [FRAME] 「架構啱、分類器同調度器錯」呢個總結，係基於三日 run 全部冇出過錯 data 而每次延誤都追得返去 `classify_error` 或者 `execute_ready`。樣本得 3 個 run。
