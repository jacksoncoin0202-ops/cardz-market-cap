# G10 歷史 POP 入庫盤點 — 有缺口，但冇現成 script，只出提案

**量度日期**：2026-07-27（由「D」執行，全程唯讀，零外部 API 調用）

---

## TLDR

1. **DB 得 7 日，磁碟有 3 年。** 兩張 population 表都係 `2026-07-21 → 2026-07-27` 共 7 個日期點；磁碟 `data/private/gemrate/cards/*/history_full.json` 有 833 個檔、393 個日期、`2023-07-25 → 2026-07-26`。
2. **缺口實數：317,421 個 grade 點喺 DB 完全冇對應日期**（覆蓋到 roster 嘅 730 張卡 × 2,761 條 (variant,grader) 序列）。呢個唔係四捨五入誤差，係兩個數量級。
3. **但係冇現成 script 入得到庫。** 兩個 `grader_population_*` 生產者（`g10_ingest.build_grader_population_observations` / `market_source_sync.gemrate_current_observations`）**結構上都只出一個日期**，唔係歷史路徑。按 brief「冇現成 script 就只出提案，唔准手寫 INSERT」→ **本次零 DB 寫入**。
4. **入庫前有兩個硬阻塞要先拍板**：唯一寫者用 `ON DUPLICATE KEY UPDATE`（會 mutate 2,761 行現有數據，違反 append-only）；來源數據本身有 264 個負 delta step（違反 population 只升唔跌）。兩個都唔係寫 code 解得到，係政策決定。
5. **⚠ 唔好誤讀成「前端冇 POP delta」。** `canonical_public_snapshot.population_series()` 已經**直接由磁碟讀**呢批歷史，繞過 DB。入庫係為咗持久化同一致性，唔係為咗解鎖 UI。

---

## 量度時嘅前提

- DB = `backend.env` 指向嗰個 MySQL，`market_grader_population_observation` 當時 10,751 行。
- 磁碟 = 工作區 `data/private/gemrate/cards/`。實測 `git check-ignore -v data/private/gemrate/cards/` → `.gitignore:46:data/private/*`（⚠ 唔係 CLAUDE.md 寫嗰個「`data/private/gemrate/` 條目」，係上一層嘅 `data/private/*` 通配）。呢個目錄由 `gemrate_source.py` 嘅 `fetch_card()` 寫，內容跟 GemRate key 嘅採集進度變，**缺檔唔係錯**。
- 相對之下 `docs/evidence/2026-07-27-g10-history/` **唔受 gitignore 管**（同一條 `git check-ignore` 對 `FINDING.md` 出 exit=1）—— 所以下面「唔准將 gemrate id 放入呢個目錄」嗰條唔係潔癖，係真會 commit 落 git。
- 同期有其他 agent 喺跑（POP 表當日仲有新行寫入：07-27 已經有 324 行）。下面所有「只有 N 處」類全域否定，重量方法都寫咗喺旁邊。
- Roster 定義 = `market_grader_population_observation` 入面出現過嘅 1,590 個 variant，唔係 `tracked-gemrate-ids.txt` 嗰 1,468 個。

---

## 證據鏈

### ① DB 側：兩張表都得 7 日

```
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 scripts/ro_sql.py --file temp/g10_history_probe.sql --limit 60
```
→ 完整輸出 [db-coverage.txt](db-coverage.txt)

```
[1] market_grader_population_observation
    rows_total=10751  variants=1590  2026-07-21..2026-07-27  date_points=7
[3] market_population_transport_observation
    rows_total=11170  variants=1590  2026-07-21..2026-07-27  date_points=7
```
<!-- @verified 2026-07-27 id=g10hist.pop_obs.datepoints expect<=14 ttl=14 sql=SELECT COUNT(DISTINCT observed_date) FROM market_grader_population_observation -->

逐日量差好大，唔好當「7 日都有真量」：07-23 得 **1 行 / 1 張卡**（空跑），07-27 得 324 行（單一 grader）。

### ② 對照組：價格／K 線史**已經**入過庫，POP 史從來冇

同一張原始觀測表 `market_source_observation` 逐 `observation_kind`（`db-coverage.txt` 第 [5] 段）：

| observation_kind | 行數 | 日期數 | 最早 |
|---|---:|---:|---|
| `index_constituent` | 278,770 | **1,134** | 2023-06-19 |
| `g10_kline_daily` | 47,582 | **1,102** | 2023-07-20 |
| `tracked_sales_daily` | 19,047 | 218 | 2023-07-20 |
| `grader_population_psa` | 3,992 | **6** | 2026-07-21 |
| `grader_population_cgc` | 2,308 | **5** | 2026-07-21 |
| `grader_population_bgs` | 2,082 | **5** | 2026-07-21 |
| `grader_population_sgc` | 2,021 | **5** | 2026-07-21 |
| `grader_population_tag` | 1,161 | **3** | 2026-07-22 |

**結論一句**：同一個庫、同一張表、同一批卡，價格線有三年歷史，POP 線得一星期 —— 差異唔係數據攞唔到，係**歷史 backfill 只做過價格嗰邊**。

### ③ 磁碟側：833 個檔、3 年、43 萬點

```
python -X utf8 docs/evidence/2026-07-27-g10-history/g10_history_inventory.py
```
→ 完整輸出 [disk-inventory.txt](disk-inventory.txt)，producer script [g10_history_inventory.py](g10_history_inventory.py)

```
card_dirs_total=2978
dirs_with_history_full=833
parse_fail=0
window_intervals={'week': 632, 'two_week': 201}
total_grader_date_points=433936
distinct_dates=393   date_min=2023-07-25  date_max=2026-07-26
per_card_datepoint_hist={79: 201, 157: 632}
```

每張卡嘅點數**只有兩個值**（157 或 79），對應週線／雙週線兩種 window —— 即係 GemRate 每張卡俾一個固定長度嘅 3 年窗口，唔係逐張卡長短不一。

### ④ 兩個磁碟陷阱（brief 已警告，實測確認）

```
find . -name "history_full.json" -not -path "*/node_modules/*"
ls integrations/grade10/data/
find data/runtime/private-landing/g10 -name "populations.json" | wc -l
```
→ 完整輸出 [code-path.txt](code-path.txt)

- `history_full.json` **全 repo 只喺 `data/private/gemrate/cards/<sha1>/` 一個位**（重量方法：上面條 `find`；⚠ 全域否定，同期有 agent 可能落新檔）。
- **`integrations/grade10/data/` 根本冇 `cards/` 子目錄**，只有 `_state/` 同 `index/`。由呢度診斷「冇數據」係錯嘅。
- **G10 landing zone 唔係歷史源**：`data/runtime/private-landing/g10/` 有 1,200 個 `populations.json`，top-level key 得 `['population', 'source']`，全份 JSON 冇 `history` 字。佢係 point-in-time 快照。

### ⑤ Roster 覆蓋：1,590 → 730 真正入到庫

`disk-inventory.txt` 第 [B] 段：

```
roster_variants_with_gemrate_identity=1495   (1590 個 pop variant 入面)
roster_covered_by_history=730
roster_dir_exists_but_no_history=735
roster_no_disk_dir_at_all=30
history_files_not_in_roster=103
```
<!-- @verified 2026-07-27 id=g10hist.roster.gemrate_identity expect>=1400 ttl=14 sql=SELECT COUNT(DISTINCT csi.variant_id) FROM market_grader_population_observation p JOIN catalog_source_identity csi ON csi.variant_id=p.variant_id AND csi.source_code='gemrate' -->

拆開睇：95 張 pop variant 冇 gemrate identity（join 唔到，唔係採集問題係身分問題）；735 張有卡目錄但**冇歷史檔**（採集缺口，唔係入庫缺口）；30 張連目錄都冇。**即使入庫做到 100%，都只係 730/1590 = 45.9%。**

### ⑥ 可載入量同兩個 append-only 阻塞

`disk-inventory.txt` 第 [C] 段：

```
series_(variant,grader)=2761
series_per_grader={'PSA': 708, 'BGS': 713, 'CGC': 706, 'SGC': 634}
usable_grade_points=320182
points_overlapping_db_dates=2761
points_on_new_dates=317421
points_dropped_missing_gradekey={'psa_10': 21096, 'beckett_10_pristine': 22094,
                                 'cgc_10_perfect': 21846, 'sgc_10_pristine': 18619}
series_with_negative_delta=155
negative_delta_steps=264
negative_delta_magnitude_buckets={'-1': 239, '-2..-9': 22, '-10..-99': 3}
worst_negative_step=(-67, variant 336, 'BGS', ('2025-12-21', 68), ('2025-12-28', 1))
```

**阻塞 A — 唯一寫者係 upsert 唔係 append。**
```
grep -rn "INSERT INTO market_grader_population_observation" --include=*.py .
```
→ 全 repo 得 `pipelines/db_runtime.py` 一處（`temp/cardz-handoff-20260726/` 嗰份係 handoff 副本，唔算）。佢兩張表都用 `INSERT ... ON DUPLICATE KEY UPDATE`。UNIQUE KEY 分別係 `(variant_id, grader_code, source_code, observed_date)` 同 `(variant_id, authority_code, transport_code, grader_code, grade_label, effective_date)`。
→ **直接餵歷史會 mutate 嗰 2,761 個重疊點**（現有 7 日 × 覆蓋序列），違反 brief 嘅 append-only。

**阻塞 B — 來源數據本身有負 delta。**
264 步負向，分佈：`-1` 佔 239 步（大機會係 GemRate 重算／去重），但有 3 步跌超過 10，最誇張 variant 336 BGS 由 68 跌到 1。CLAUDE.md「數據語義」硬規矩寫明 population 係存量型、**永遠唔准出負 delta**。呢 264 步要有處理政策先入得。

**阻塞 C（非阻塞但要知）— 約 8.4 萬點會被丟。**
`points_dropped_missing_gradekey` 加埋 83,655，係啲點冇對應 grade key（例如某日 BGS 冇 `beckett_10_pristine`）。用 `canonical_public_snapshot.POPULATION_HISTORY_KEYS` 同一份對照量出嚟，唔係我另立標準。

### ⑦ 冇現成 script：兩個生產者結構上都只出一個日期

```
grep -rln "grader_population_" --include=*.py pipelines/ scripts/
```
→ 9 個檔（`code-path.txt` 第 [5] 段），但真正**產生**觀測嘅只有兩個：

| 生產者 | 日期行為 | 判定 |
|---|---|---|
| `g10_ingest.build_grader_population_observations()` | `observed_date = effective_at.date().isoformat()` —— 讀 `cards/<src>/<id>/populations.json`（point-in-time），全批一個日期 | 唔係歷史路徑 |
| `market_source_sync.gemrate_current_observations()` | 讀 `population.json` + `current.json`，`observedDate` 每張卡一個 | 唔係歷史路徑 |

`db_runtime.py` 嗰個 gate 收 `grader_population_*` 時**要求 `observedDate` 係明確 `YYYY-MM-DD`**，否則當市場點 skip —— 即係入庫接口本身**支援**逐點日期，缺嘅純粹係一個會 emit 多個日期嘅 producer。

**結論一句**：接口通嘅，缺一個 producer。

---

## 提案（未執行，零 DB 寫入）

按 brief「冇現成 script 就只出提案」。以下係接線工作項，唔係已完成事項。

### P0 — 兩個政策決定（要 PM 拍板，唔係工程問題）

1. **重疊點點處理？** 建議：producer 開跑前先 `SELECT (variant_id, grader_code, source_code, observed_date)` 現有 key 做 skip-list，**磁碟點撞到就跳過**，DB 當日觀測永遠贏（同 `population_series()` 現有「同日撞到 DB 贏」嘅語義一致）。咁樣先做到真 append-only，唔靠 `ON DUPLICATE KEY UPDATE` 嘅副作用。
2. **264 個負 delta 點做咩？** 三個選項，要揀一個：
   - (a) **單調化**：per-series running max，跌嗰點抬平。數據好睇但係篡改來源。
   - (b) **隔離**：整條有負 delta 嘅序列（155 條）唔入庫，寫入 quarantine 計數。乾淨但少 5.6% 序列。
   - (c) **原樣入 + consumer clamp**：DB 保留真相，`population_series()` 出街前 clamp。分層清楚但要改 consumer（而 `canonical_public_snapshot.py` 主線 claim 緊）。
   —— 「D」唔幫呢個揀，呢條係 taste 決定。

### P1 — 新 producer（政策定咗先寫）

- 位置：`pipelines/gemrate_history_ingest.py`（新檔，唔改現有兩個 producer）
- 契約：出 `canonical-batch.json`，行 `db_runtime.py` 呢個唯一寫者，**唔准手寫 INSERT**
- 每個 history 點出一行，`observedDate` = 該點 `date`，`kind` = `grader_population_<grader>`
- **新 `transportCode`**（例如 `gemrate_history_full`）同日線嘅 `grade10_gemrate_mirror` 分開，否則兩條線嘅 UNIQUE KEY 會撞埋一齊，之後分唔返邊個點係邊度嚟
- 先 `--dry-run` 出 batch 檔驗行數／日期分佈，再 write

### P2 — 規模要先諗清楚

317,421 新行 vs 現有 10,751 行 = **表大 30 倍**。`market_population_transport_observation` 同步膨脹。入庫前要確認 retention 政策（`db_retention.py` 有冇覆蓋呢張表）同索引影響。

### 唔喺呢個提案入面嘅嘢

- **735 張有目錄冇歷史檔** 係 GemRate 採集缺口（`gemrate_source.py` `api-dump` 未掃到），唔係入庫工作。
- **95 張冇 gemrate identity** 係身分問題，行 `catalog_source_identity` / review queue 嗰條線。
- **TAG 冇歷史** 係結構性：TAG 唔喺 per-card population API 入面，磁碟 `by_grader` 只有 psa/beckett/cgc/sgc 四個 key，冇任何歷史來源可以入庫。

---

## 檔案地圖（呢個證據包）

| 檔 | 內容 | 點重跑 |
|---|---|---|
| [FINDING.md](FINDING.md) | 本文 | — |
| [g10_history_inventory.py](g10_history_inventory.py) | 磁碟盤點 + roster join + 序列質量，一個 script 出齊三段 | `python -X utf8 docs/evidence/2026-07-27-g10-history/g10_history_inventory.py`（要先 source `backend.env`） |
| [disk-inventory.txt](disk-inventory.txt) | 上面 script 嘅原始輸出 | 同上 |
| [db-coverage.txt](db-coverage.txt) | 6 條 DB 探針原始輸出 | `python -X utf8 scripts/ro_sql.py --file <本目錄>/db-probe.sql --limit 60` |
| [db-probe.sql](db-probe.sql) | 嗰 6 條探針 | — |
| [code-path.txt](code-path.txt) | 磁碟陷阱確認 + 唯一寫者 + 生產者日期行為 | 見文件頭嘅 `find`／`grep` |

⚠ `g10_history_inventory.py` 會將 roster identity map 落 `temp/g10_roster_gemrate_ids.json`（**唔係**呢個目錄）—— gemrate id 屬 gitignored 嘅 `data/runtime/` 範疇，唔應該進入入 git 嘅 `docs/`。刪咗 cache 佢會自動經 `scripts/ro_sql.py` 重攞。
