# 召回 → 二次 QC → 入庫（營運鐵律）

> **2026-07-29** 深度協作後凍結。  
> 營運總覽：[PROJECT_STATE.md](../PROJECT_STATE.md) · 點做地圖：[PROJECT_MAP.md](PROJECT_MAP.md)  
> **入庫前工序 checklist（短）：** [INGEST_VERIFY_GATE.md](INGEST_VERIFY_GATE.md)

---

## 0. 一句

**門檻可以極低去撈；入 DB 前必須過可重跑嘅 verify／QC。通過先 mark 一次；之後增量只跟已記 ID + 腳本。**

**人類點 search（變種 × 日英名 × 編號多寫法）：** 必讀 [IDENTITY_HUMAN_SEARCH_PLAYBOOK.md](IDENTITY_HUMAN_SEARCH_PLAYBOOK.md)——agent 唔可以淨跑腳本名而無 query ladder。

---

## 1. 點解要咁（經驗）

| 教訓 | 細節 |
|---|---|
| 覆蓋假低 | 全庫 sale／價很多，940 池 join 少 → 根因係 **identity 未接**，唔係市場冇貨 |
| Number 盲點 | 只試一種 collector 寫法會 miss；要 **多格式**（`OP01-016` / `OP01 016` / `OP01016` / 純數字+set） |
| 低門檻 alone 好危險 | 純數字 first-hit 會綁錯（Sharpedo→Rayquaza、mew⊂mewtwo） |
| 二次 check 救場 | Recall 可以海量；**Verify 打回頭 100+ 次／輪** 係正常，唔係失敗 |
| 記一次就夠 | `catalog_source_identity` + `liquidity-source-registry.jsonl` + `semi-auto-identity-ledger.jsonl` |
| 成交唔限 30d | 有就入晒；1d/7d/21d/30d **反推**；Top100 閘用 30d 衍生 |
| 錯一定清 | clean 掃現有 identity，verify 唔過就刪 |
| 手冊要點做 | 方法論長文無人用；命令 + endpoint 先有用 |

---

## 2. 標準流程（所有源同一套路）

```text
任一腳本 / 源
    │
    ▼
RECALL（門檻可極低）
  · collector 多格式 hit
  · 名有任何物種／角色信號
  · 可以「只 collector」先撈（尤其 OP 完整卡號）
    │
    ▼
VERIFY / QC（硬閘 · 可重跑 · 無 AI 主觀）
  · species = token 全字（mew ≠ mewtwo）
  · collector 必須中
  · 純數字 → 必須 set hint 中
  · VMAX/VSTAR/GX 唔好錯 stage
  · 歧義近分 → 拒
  · sleeve/playmat 非單卡 → 拒
    │
    ├── 唔過 → 唔寫 DB（可記 reject 報告）
    │
    └── 過 → WRITE
          · catalog_source_identity（exact）
          · market_*_observation（價／成交）
          · liquidity-source-registry.jsonl（邊個腳本得）
          · semi-auto-identity-ledger.jsonl（永久帳）
```

**門檻可以極低**，因為：

1. Recall 只喺記憶體／候補表  
2. 入庫路徑唯一入口 = Verify  
3. Verify 係程式，唔係「AI 覺得 OK」  

---

## 3. QC：腳本定 AI agent？

| 層 | 負責 | 點解 |
|---|---|---|
| **Verify 硬閘** | **腳本（必寫）** | 可重跑、可測、可 CI、同一規則日日一樣；海量候補要毫秒級 |
| **Clean 掃錯** | **腳本** | 同上；現有 identity 定期重跑 verify |
| **編排全源** | **AI agent** | 決定而家跑邊個腳本、讀 status、串 full_volume、harvest 完再 bind |
| **擴規則** | **AI agent + 寫入腳本** | 新 set alias、新 number 格式 → 改 `semi_auto_identity.py`，唔好每次人手判斷 |
| **needsReview 殘渣** | **AI agent 半自動批次** 或人手 | 近分／雙 printing／真歧義；通過仍然 **寫入同一 DB mark** |
| **品味／版權／法律** | **人** | 唔入自動 verify |

### 結論（硬）

- **QC 主體 = 劇本（deterministic verify）**，唔好靠 AI 逐張「睇下啱唔啱」先入庫。  
- **AI agent = 司機**：低門檻開多個源、收齊候補、call verify、過先 commit、更新 STATE。  
- 若要「AI QC」：只用於 **verify 拒咗但高價值** 嘅殘渣（例如 Top 市值無 sale），結果仍要落同一套 identity 表。

---

## 4. 腳本入口

| 腳本 | 角色 |
|---|---|
| `pipelines/semi_auto_identity.py` | clean · match-snk · match-ebay（recall→verify） |
| `pipelines/full_volume_recall_verify.py` | 一條龍：identity + sales stock + chip 價（+可選 trades） |
| `pipelines/bind_snk_watchlist.py` | multi-form index；**入庫前仍應 clean/verify** |
| `pipelines/fill_watchlist_sales_stock.py` | G10 reattach + twin clone（名+collector） |
| `pipelines/snk_market_data.py` + `ingest_snk_trades_sales.py` | 已 bind id → 成交全入 |
| `pipelines/g10_sales_cache_ingest.py` / `g10_ebay_ingest.py` | 本地長史成交 |
| `pipelines/market_alerts.py` | Top100：**有 30d 成交排前**（liquid gate） |

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
$env:CARDZ_DB_HOST = "127.0.0.1"

# 全源低召回 + 二次 QC + 入庫
python -X utf8 pipelines\full_volume_recall_verify.py --write --recall-min 20

# 或只身份
python -X utf8 pipelines\semi_auto_identity.py run --write --recall-min 20

# 已 bind → 成交（有就入晒）
python -X utf8 pipelines\snk_market_data.py --ids-file data\runtime\private-source-map\qualified-940-snk-ids.txt `
  --condition trading_card_single_psa10 --out data\runtime\private-source-map\snk-psa10-bound.jsonl --delay 0.25
python -X utf8 pipelines\ingest_snk_trades_sales.py --harvest data\runtime\private-source-map\snk-psa10-bound.jsonl
```

---

## 5. 永久產物（增量靠呢啲）

| 路徑 | 內容 |
|---|---|
| `catalog_source_identity` | snkrdunk / ebay / gemrate / tpl… |
| `data/runtime/private-source-map/liquidity-source-registry.jsonl` | 每卡 preferred 源 + script |
| `data/runtime/private-source-map/semi-auto-identity-ledger.jsonl` | 清／配對帳 |
| `data/runtime/private-source-map/qualified-940-identity.jsonl` | 人／agent 查 ID 總表 |
| `data/runtime/private-source-map/qualified-pool-reports/semi_auto_*.json` | 每輪 accept/reject 樣本 |

---

## 6. 反模式（禁止）

1. 低門檻直接 `INSERT` identity／sale  
2. AI 口頭「呢張應該係」就 commit，無 verify 程式  
3. 只用 30d 過濾採集  
4. first-hit 名搜 bind  
5. 知錯 identity 唔 clean  
6. 方法論長文當每日入口  

---

## 7. 變更

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 首版：深度協作經驗 + QC 責任分工 + 極低 recall 政策 |
