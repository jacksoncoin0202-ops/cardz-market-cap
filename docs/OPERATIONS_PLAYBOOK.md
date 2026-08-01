# CARDZ 數據營運手冊:經驗盤點 + 硬性規矩 + 軟性旋鈕

**版本**:2026-07-31(WSL cutover 版)
**環境**:所有嘢只准喺 WSL(Linux）行，見 `AGENTS.md` 第一句。Windows 已退役。
**地位**:呢份係經驗總結同閱讀指南，**唔係**第二份架構合約。路由/authority 嘅唯一來源係 `config/data-routing.json`；數據語義嘅唯一來源係 `docs/DATA_CONTRACT.md`。

---

## Part 1 — 盤點：邊啲入圍、邊啲唔入圍、出過咩情況

### 1.1 入圍（驗證過、喺每日鏈上面）

| 做法 | 點解入圍 |
|---|---|
| Canonical MySQL(42 表，append-only 觀測） | 每格數有日期、來源、hash，可重建、可追溯；第二次 import 係零新觀測 |
| 每個 metric 一個 authority(`data-routing.json` routes) | 冇「A 冇問 B 借」嘅灰色地帶 |
| GemRate 做 POP 唯一 authority,**keyless 逆向 transport**(public card page + G10 mirror) | direct API 已停（2026-07，全部 403)；逆向照樣攞到同一個 authority 嘅數 |
| SNK exact 做 PSA 10 價唯一 authority | 排名公平性嘅根基 |
| **G10 apparel feed = SNK 多 grade 成交**(13 個 grade 檔，由 PSA10 到 raw) | 多年歷史 + 精確 grade + txAmount;`g10_snkrdunk_grades_ingest.py` 已入庫 |
| 三源成交 union(SNK + G10 eBay + PriceCharting)，逐單落 `market_sale_observation`,export 先合併 | provenance 分開，同一單物理成交跨 feed 只計一次（`dedupe_cross_feed_sales`,2026-07-31 修） |
| Exact crosswalk 綁身份，唔准 name-search | 身份錯冇得返轉頭 |
| Universe 兩層：**名單 lock（凍結）vs 每日資格（新鮮 POP ≥ 1000)** | radar(971–999）升穿 1000 即日自動入榜，唔使等 lock refresh |
| **Liquidity 閘**:Top100 = 近 30 日 ≥10 筆合格 PSA10 成交先行（`POLICY_FE_TOP100_LIQUIDITY.md`) | 高 cap 死水卡唔佔公開座位 |
| Fail-closed 發佈 + 5% 縮水閘 | 壞數據永遠唔出街；單向棘輪有閘 |
| 圖：**G10 最高優先；SNK 淨收官方掃描版（cdn.snkrdunk.com/upload_bg_removed)，唔收拍賣網/玩家相機相**（用戶規矩 2026-07-31) | 圖質統一 |

### 1.2 唔入圍（試過或者評估過，否決）

| 做法 | 點解唔得 |
|---|---|
| GemRate direct API | **已停**(2026-07 全部 403)；以 keyless transport 取代 |
| eBay Browse API | 叫價唔係成交 |
| eBay 直爬(PerimeterX JS fingerprinting) | 過唔到；用 Grade10 feed + PriceCharting |
| eBay 關鍵字 grade 搜尋當精確數 | 混裸卡；一定要結構化 grade + 噪音閘 |
| 「A 冇問 B 借」式跨來源 fallback | 違反 missing-stays-missing |
| G10 做 authority | bootstrap/last-good 證據，永遠唔入排名輸入 |
| 蠟燭圖 OHLC、1 小時指標、語言分榜、泰文版、writable SQLite 做 production | 全部否決或 out of scope |
| **喺 Windows 起任何嘢** | 2026-07-31 起永久禁止（AGENTS.md) |

### 1.3 出過嘅情況同教訓

| 情況 | 教訓 |
|---|---|
| 360→192 縮水事件（冇閘嗰陣一次 run 跌 46.7% 卡仲 exit 0) | 縮水閘要喺 promote/quarantine **之前** |
| 寫 `'PSA 10'` 查 `'10'`、單行 JSONL 被 `json.loads` 誤食、4 個 QC module 从未存在 | 冇 error 嘅靜默壞數最危險；每類都要有測試 |
| **56 張 lock 成員 printing identity 係 `candidate` 未 promote** → universe hash mismatch → publish 全停(2026-07-31) | 身份 promote 係 lock 嘅前置；`promote_printing_candidates.py` 係標準工具 |
| **兩個 Docker daemon + localhost forwarding** 令「兩個 DB」假象持續成日 | 先查 daemon/forward 先好信「邊個 DB 先係 live」 |
| **q940 名單文件喺 identity renumber 後 37/932 有效** | 名單永遠以 `market_universe_lock` 為準，文件要 regenerate |
| **跨 feed 成交重複計**(fingerprint 嵌 provider id,1,235 組同日同價重複） | 物理成交要跨 feed dedupe;`dedupe_cross_feed_sales` 以單 feed 最大行數做上限 |
| GemRate direct API 全停 | 來源可以死，authority 唔可以亂 — transport 層吸收咗佢 |

---

## Part 2 — 硬性規矩（唔改得）

1. **每個 metric 只有一個 authority**:POP = GemRate；價 = SNK exact；market cap/升跌 = CARDZ 自己計。
2. **Missing stays missing**:冇數 = `null` + status，永遠唔係 0、唔係借數、唔係平線。
3. **eBay/PriceCharting 永不入價格序列**；淨係 trackedSales 展示。
4. **Exact identity，唔准 name-search 綁卡**；模糊一律入 review queue。
5. **Public boundary**：公開 snapshot 唔可以有 provider ID/名/URL/secret/未遮罩 slab 圖。
6. **Append-only + idempotent**;DB 可以由 seed/archive 重建。
7. **Fail-closed 發佈**;last-good 指針喺任何閘 fail 時唔郁。
8. **排名資格**:POP ≥ 1000 非估計 + 身份 canonical + collector number 齊 + 價 ≤48h + 圖 QC 過 + liquidity ≥10/30d（公開座位）。
9. **Market cap = PSA 10 價 × POP**，三個榜同一條式。
10. **Tracked sales 係 partial 樣本**;bundle 總值唔可以當單卡價。
11. **G10 圖最高優先;SNK 淨收官方掃描版**（用戶規矩）。
12. **WSL 係唯一 runtime**（用戶規矩 2026-07-31)。

---

## Part 3 — 軟性數字（可以改，話你知改邊度）

| 數字 | 而家值 | 位置 |
|---|---|---|
| 正式排名 POP 門檻 / radar | 1000 / 971–999 | `config/data-routing.json` populationBands + `market_alerts.py` |
| 價 ready/stale/出局 | ≤30h / ≤48h / >48h | `data-routing.json` + `DATA_CONTRACT.md` |
| POP 新鮮度 | ready 48h / stale 168h | `data-routing.json` psa10_population |
| FX / TAG last-good | 72h | `run_daily.py` |
| 縮水閘 | 5% | `run_daily.py DEFAULT_MAX_CATALOG_SHRINK_PCT` |
| PC export 鮮度閘 | 48h | `run_daily.py`(2026-07-31 加） |
| Liquidity 閘 | ≥10 筆 / 30 日 | `market_alerts.py variants_with_sale_30d` + `POLICY_FE_TOP100_LIQUIDITY.md` |
| 歷史點上限 / normalize 窗 | 90 日 / 45 日 | `canonical_public_snapshot.py` / `market_source_sync.py` |
| 每日觸發 | 09:30 JST + jitter | `deploy/systemd/` |
| Weekly candidate refresh | 預設 DISABLED | `deploy/systemd/cardz-market-cap-candidate-refresh.timer`（上線後先開） |
| Coverage audit 價 fallback 鏈 | SNK chart → DB snk_psa10 → ebay → pricecharting → last sale | `data_coverage_audit.py`(2026-08-01 加，老細 A→B 政策） |
| 價新鮮度（audit) | ≤2 日 verified / ≤30 日 lastKnown / >30 日出局 | `data_coverage_audit.py` fallback 段 |
| Refill 容忍（thin tail) | 1.5% of cohort | `data_coverage_audit.py refill_tolerance` |

## 2026-08-01 深夜決策（cutover 順手記低）

- **Coverage audit 改為稽核 canonical lock 成員**:audit 本來行 crosswalk(600 行，snkrdunk/ebay key)，但 identity convergence 之後 universe 係 gemrate-keyed,crosswalk 同 lock 嘅 source id 重疊 <25%,audit 變相度緊錯嘅 cohort。而家 audit 直接由 `active-source-identities.json`（每個 lock 成員嘅全 source 綁定，`scripts/export_active_source_identities.py` 出）合成稽核行。**lock 一變（re-seal）就要重行個 exporter。**
- **Refill 淨係放可重試嘅 fetch gap**:冇成交/冇 chart 係市場現實，唔係 fetch 失敗，唔會再入 refill 阻塞閘口。
- **missing SNK binding 唔再阻塞 release**:lock 成員全部有 canonical(gemrate）身份，SNK 綁定屬 identity backlog，照樣出喺報告俾 ops 跟。
- **Bare collector number 算 complete**:canonical DB 唔存 set size,SNK matching 係 parts-based，強制要有 `/` 係舊 crosswalk 時代嘅假設。
- **GEMRATE_API_KEY 係死 key(403)**:daily 嘅 GemRate 步驟一定要 `unset GEMRATE_API_KEY` 行 keyless，唔係會逐張 retry 死 API 零進度。server 嘅 `gemrate.env` 到咗 AWS 要清走個 key。
- **Worklist 要由 canonical DB 出**:`tracked-gemrate-ids.txt` / `tracked-snk-ids.txt` 本來係 7-28 舊貨（73/71 張），已改由 lock 37 成員綁定出（585/564 張）。每日鏈如果唔經 `tracked_universe.py` 重生，就要用 exporter 嗰套邏輯。
- **G10 數據根喺 WSL 係由 Windows 抄過嚟**:`integrations/grade10/data` 而家係 7-31 嘅 copy。上咗 AWS 之後 G10 scraper 自己會生，唔使再抄。

改任何嘢之前：`backend.py explain <metric>` → 分硬軟 → 改完 `generate-docs --check`。
