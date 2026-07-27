# 驗收閘 UTC/JST 日期基準診斷

**量度時刻**：2026-07-27 04:03 UTC（= 13:03 JST）
**量度方法**：`docs/evidence/2026-07-27-verify-gate-tz/probe_verify_gate_baseline.py`（唯讀，見下方「點量」）
**倉庫狀態**：HEAD = `e3f5f84278b09301387c19bad4d2ec2afb800140`；`scripts/verify_daily_run.py` 同 `scripts/verify_handoff.py` 兩者 `git status --porcelain` 皆空（無未提交改動）

---

## TLDR

**簡報所講嗰個 bug 唔存在喇 —— 已經修咗，而且從來都唔喺簡報指名嗰個檔案入面。**

一句講晒：日期基準已於 **2026-07-26 19:13:17 +0900**（commit `b0da73a45469b2c4f95ab1a8049e00acdbb3ab4f`，*release: make a clean clone deployable on Linux*）由「UTC 今日」改為 **T-1**，今日實測舊基準會誤殺 4 個 check，新基準當中 3 個即刻轉 PASS。

兩個必須先講清楚嘅前提修正：

| 簡報講 | 實測 |
|---|---|
| bug 喺 `scripts/verify_handoff.py` | 嗰個檔**一行日期邏輯都冇**（連 `datetime` 都冇 import），佢係檔案完整性閘。真正有日期基準嘅係 `scripts/verify_daily_run.py` |
| 本機 UTC+8 | 本機係 **UTC+9（JST）**。`date` = 2026-07-27 13:03 本地 vs `date -u` = 04:03 UTC，git commit 亦全部 `+0900` |

不過**仲有三個殘留問題**，唔係簡報嗰條，但同屬「閘嘅日期基準正唔正確」範圍，全部只寫提案 diff，一個字都冇改落檔。

---

## 一、簡報嗰條：已修

### 修咗嘅嘢

`scripts/verify_daily_run.py`，現行 HEAD：

```python
def default_expected_date(now: datetime | None = None) -> str:
    """排程跑嗰陣，上游最新可用嘅日線係邊一日。

    唔可以用「UTC 今日」做基準。SNKRDUNK 嘅日線要 JST 午夜（= UTC 15:00）
    先埋單，而 daily timer 跑喺 09:30 本地（UTC 01:30）—— run 當日嗰條線
    根本仲未存在。...
    """
    now = now or datetime.now(timezone.utc)
    return (now.date() - timedelta(days=1)).isoformat()
```

有回歸測試守住，`tests/test_verify_daily_run.py:119`：

```python
def test_default_expected_date_is_utc_yesterday():
    assert default_expected_date(datetime(2026, 7, 26, 6, 28, tzinfo=timezone.utc)) == "2026-07-25"
    assert default_expected_date(datetime(2026, 1, 1, 1, 30, tzinfo=timezone.utc)) == "2025-12-31"
```

同檔亦有測試證明放寬咗都仲捉到真斷更（`test_t_minus_one_baseline_still_catches_real_outage`：斷咗兩日照 fail）。

### 排程真係行緊新基準

所有 caller 都**冇傳** `--expected-date`，即係全部食 T-1 default：

- `deploy/systemd/run-cardz-daily.sh`、`run-cardz-watchdog.sh` — 直接調用，無日期參數
- `deploy/windows/cardz-status.ps1` — `$verifyArgs` 只喺人手指定時先加 `--expected-date`

Timer 實際時段（`deploy/systemd/*.timer`）：

| Timer | OnCalendar | Jitter | 換算 JST |
|---|---|---|---|
| `cardz-market-cap-daily.timer` | `00:30:00 UTC` | `RandomizedDelaySec=1800` | 09:30–10:00 JST |
| `cardz-market-cap-watchdog.timer` | `05:07:00 UTC` | — | 14:07 JST |

兩個時段都喺 JST 午夜之後、當日日線埋單之前，正正係舊基準必死嘅窗口。

### 今日實測重現（A/B）

同一批 DB facts，只換 `expected_date`：

```
--- 舊基準 (UTC today): expected_date=2026-07-27 -> FAIL
    [FAIL] price_freshness: max(observed_date)=2026-07-26 expected>=2026-07-27
    [FAIL] snapshot_freshness: stale/missing: ['tcg-combined', 'pokemon', 'one-piece']
           (latest per index: 全部 '2026-07-26')
    [FAIL] source_coverage: counts on 2026-07-27: {'snk_psa10': 21, 'tag': 324};
           require ['gemrate'] and any of ['snk_psa10', 'snkrdunk']
    [PASS] ingest_activity: source observations written today (UTC): 10340
    [FAIL] volume_floor: below 90% of 2026-07-26: gemrate 570->0 (0%); snk_psa10 528->21 (4%)
    [PASS] constituent_sanity: tcg-combined constituents 255 -> 336

--- 新基準 (T-1, 現行 HEAD): expected_date=2026-07-26 -> FAIL
    [PASS] price_freshness: max(observed_date)=2026-07-26 expected>=2026-07-26
    [PASS] snapshot_freshness: all 3 indexes have effective_date>=2026-07-26
    [PASS] source_coverage: counts on 2026-07-26: {'gemrate': 570, 'snk_psa10': 528, 'tag': 324}
    [PASS] ingest_activity: source observations written today (UTC): 10340
    [FAIL] volume_floor: below 90% of 2026-07-25: g10_analytics 1277->0 (0%);
           gemrate 3007->570 (19%); snk_psa10 913->528 (58%)
    [PASS] constituent_sanity: tcg-combined constituents 255 -> 336
```

**四個 FAIL → 三個轉 PASS。** 誤殺確實消除。

（新基準仲有 `volume_floor` FAIL —— 嗰個有真數據成分，屬指數停更診斷嘅範圍，本報告只評估閘本身嘅基準邏輯，唔追停更根因。）

### 時區三角，量度值

```
DB 時區: system_time_zone=UTC  NOW()=2026-07-27 04:03:44  UTC_TIMESTAMP()=2026-07-27 04:03:44
```

`NOW() == UTC_TIMESTAMP()`，所以 `created_at` 存嘅係 UTC。`ingest_activity` 攞 `datetime.now(timezone.utc).date()` 去比 `created_at`，**兩邊同一時區，呢個比較本身係啱嘅**（佢嘅問題喺別處，見殘留 B）。

本機 JST 只影響 `verify_claims.py`（殘留 C），唔影響 `verify_daily_run.py` —— 後者全程 `timezone.utc` explicit。

### 順手記低兩個文檔錯處（唔影響行為）

`default_expected_date` 個 docstring 同 `tests/test_verify_daily_run.py:120` 都寫「daily timer 跑喺 09:30 本地（UTC 01:30）」。本機係 UTC+9，09:30 JST = **00:30 UTC**，唔係 01:30；timer 檔亦寫住 `00:30:00 UTC`。T-1 喺 00:30 定 01:30 都係同一答案，所以邏輯無恙，但個註解會誤導下一手。

---

## 二、殘留問題（唔屬簡報範圍，只提案）

### A. `volume_floor` 攞 T-2 做 baseline，喺回填延遲下結構性偏 FAIL

`market_source_observation` 嘅行係**陸續回填**嘅，一個 observed_date 落地幾日都仲有新行入。今日量度：

```
observed_date | lag0   lag<=1  lag<=2  最終   | 當日佔比
2026-07-27    | 345    345     345     345    | 100.0%
2026-07-26    | 702    1422    1422    1422   | 49.4%
2026-07-25    | 1136   4882    5197    5197   | 21.9%
2026-07-24    | 665    4162    5640    5963   | 11.2%
2026-07-23    | 179    525     526     2113   | 8.5%
```

閘拎 T-1（回填一日）去比 T-2（回填兩日），即係**拎未熟嘅比熟嘅**。最刺眼嗰行：

```
2026-07-25 vs 2026-07-24: gate 睇到 5197/5963=87%  |  同齡 1136/665=171%
```

閘報「跌咗 13%」，同齡對比實際係**升咗 71%**。呢個唔係度緊採集量，係度緊回填進度。

**提案（未應用）**：比較兩邊時對齊「同齡」—— 兩個日期都只數觀察日當日（`lag0`）寫入嘅行，或者將 baseline 由 T-2 推遲到已經回填完成嘅日子。

```diff
--- a/scripts/verify_daily_run.py
+++ b/scripts/verify_daily_run.py
@@ def counts_on(...)
-    source_counts = counts_on(expected_date)
-    previous_source_counts = counts_on(previous_day(expected_date))
+    # 回填延遲令 T-1 永遠「未熟過」T-2：實測 07-25 對 07-24 閘報 87%，
+    # 但兩邊都只數觀察日當日寫入嘅行時係 171%。要比就比同齡。
+    source_counts = counts_on_same_age(expected_date, age_days=1)
+    previous_source_counts = counts_on_same_age(previous_day(expected_date), age_days=1)
```

配套需要一個 `counts_on_same_age(day, age_days)`，條件加 `AND DATEDIFF(DATE(created_at), observed_date) <= age_days`。

**風險**：同齡計數會令每日總量細好多（07-26 得 702 行而唔係 1422），`VOLUME_FLOOR_MIN_BASELINE = 10` 呢個門檻要重新校準，否則低量源會跌落 fail-closed 分支。**呢個改動要主線 PM 決定**，因為佢會改變閘嘅靈敏度，唔係純 bug fix。

### B. `ingest_activity` 實際上永遠 fail 唔到

`collect_facts()` 入面：

```python
cursor.execute(
    """
    SELECT COUNT(*) AS n FROM market_source_observation
    WHERE created_at >= %s
    """,
    (datetime.now(timezone.utc).date().isoformat(),),
)
```

佢嘅設計目的係捉「run 行咗但一行都冇寫」。今日實測：

```
今日(UTC 2026-07-27) 寫入 10340 行
    observed_date=2026-07-27: 345  <- 今日觀察
    observed_date=2026-07-26: 720  (回填)
    ... 一路回填到 observed_date=2026-06-12: 161
=> 新觀察 345 行 / 回填 9995 行（回填佔 97%，全部計入同一個 pass 條件）
```

**97% 係回填流量。** 即使今日採集完全停擺（345 → 0），呢個 check 照樣睇到 9995 行而 PASS。佢而家係度緊「回填 worker 有冇喘氣」，唔係度緊「今日採集有冇發生」。

**提案（未應用）**：

```diff
     cursor.execute(
         """
         SELECT COUNT(*) AS n FROM market_source_observation
-        WHERE created_at >= %s
+        WHERE created_at >= %s AND observed_date >= %s
         """,
-        (datetime.now(timezone.utc).date().isoformat(),),
+        (
+            datetime.now(timezone.utc).date().isoformat(),
+            expected_date,   # 只數「新觀察」，唔數回填
+        ),
     )
```

加咗之後今日個數會由 10340 跌到 345 —— 仍然遠高於零，但終於真係反映當日採集。

### C. `verify_claims.py` 用 naive 本地日期

`scripts/verify_claims.py:466` 一帶：

```python
today = date.today()
runner = Runner(allow_cmd=args.allow_cmd)
try:
    verify(claims, runner, today)
```

同埋 `"generatedAt": datetime.now().isoformat(timespec="seconds")`。

`date.today()` 係 **naive 本地日期**。喺本機（JST）同喺 AWS（UTC）跑，最多會差一日，令 `@verified` 標記嘅 TTL / STALE 判決兩邊唔一致 —— 同一份 claims，本機話仲新鮮，AWS 話過期。

**提案（未應用）**：

```diff
-    today = date.today()
+    today = datetime.now(timezone.utc).date()
```

同 `generatedAt` 一齊轉 `datetime.now(timezone.utc)`，令兩個環境對同一份 claims 得出同一判決。呢個係純一致性修正，唔改任何門檻。

---

## 三、點量（可重跑）

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 docs/evidence/2026-07-27-verify-gate-tz/probe_verify_gate_baseline.py
```

`probe_verify_gate_baseline.py` **完全唯讀**：只 import `verify_daily_run` 嘅 pure function（`collect_facts` / `evaluate_checks`），刻意繞開 `main()` —— `main()` 會寫 volume ledger 同 alert 檔。四段輸出分別係 A/B 基準對比、回填輪廓、同齡對比、`ingest_activity` 拆解。

修復定位：

```bash
git log -S "timedelta(days=1)" --format="%H|%ad|%s" --date=iso -- scripts/verify_daily_run.py
# b0da73a45469b2c4f95ab1a8049e00acdbb3ab4f|2026-07-26 19:13:17 +0900|release: make a clean clone deployable on Linux
```

---

## 四、量度時嘅前提

以下條件當時成立；如果之後有變，上面啲數要重新量：

- HEAD = `e3f5f84278b09301387c19bad4d2ec2afb800140`，`scripts/verify_daily_run.py` 無未提交改動
- DB `system_time_zone=UTC`、`time_zone=SYSTEM`，`NOW() == UTC_TIMESTAMP()`
- 本機 UTC+9（JST）
- 量度時 `gemrate` 同 `g10_analytics` 兩個源正處於低量／零量狀態（07-26 gemrate 570、07-27 g10_analytics 0）—— 所以新基準嘅 `volume_floor` 照 FAIL。呢個係真數據事件，唔係基準邏輯問題，本報告冇追佢根因
- 回填仍然活躍：量度當日寫入 10340 行，覆蓋 observed_date 由 2026-06-12 至 2026-07-27

---

## 五、結論

| 項目 | 判決 |
|---|---|
| 簡報所指 UTC/JST 基準錯位 | **已修**，2026-07-26 19:13 +0900，commit `b0da73a`，有回歸測試守住 |
| `scripts/verify_handoff.py` 有冇日期 bug | **從來冇日期邏輯**，指錯檔 |
| 現行排程有冇行返舊基準 | **冇**，全部 caller 唔傳 `--expected-date`，食 T-1 default |
| 殘留 A：`volume_floor` T-2 回填偏差 | 存在，提案已寫，**需 PM 決定**（會改靈敏度） |
| 殘留 B：`ingest_activity` 被回填流量淹沒 | 存在，提案已寫，純收窄條件 |
| 殘留 C：`verify_claims.py` naive 本地日期 | 存在，提案已寫，純一致性修正 |

三個殘留提案**全部只寫喺呢份報告，一個字都冇改落任何 gate script**。
