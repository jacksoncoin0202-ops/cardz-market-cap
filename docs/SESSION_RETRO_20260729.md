# Session 回顧：全量 DB → 前端 100% 素材（2026-07-28/29）

> 今晚多輪 agent session 壓縮經驗。接手同事／agent **必讀**：[RECALL_VERIFY_OPS.md](RECALL_VERIFY_OPS.md) · [AGENT_HANDOFF_INCREMENTAL.md](AGENT_HANDOFF_INCREMENTAL.md) · [DEPLOY_FOR_HANDOVER.md](DEPLOY_FOR_HANDOVER.md) · [PROJECT_STATE.md](../PROJECT_STATE.md)

---

## 1. 由頭到尾做咗咩

| 階段 | 做咩 | 結果 |
|---|---|---|
| 全量池 | GemRate POP≥1000 → 940 watchlist | 池會大，FE cut 唔脹 |
| 價 | TPL SSR 主 US；SNK JP；禁 raw TCG 價入市值 | any_price ~921/940 |
| 圖 | TCGplayer → Limitless OP → Drive；A/B/C | asset ~850+ |
| 成交假低 | 全庫 12 萬+ 行但 940 join 少 | 根因 **identity 未接**，唔係市場乾 |
| SNK trades | harvest → `ingest_snk_trades_sales` | 成交有就入晒 |
| Number 盲點 | multi-form collector + set | bind 覆蓋升 |
| 清錯 | 明顯錯 identity 必刪 | 準 > 多 |
| **Recall→Verify** | 低門檻撈 + 腳本 QC 先入 DB | 可極低 recall |
| G10 | sales_cache / eBay 本地長史 | 補史 |
| FE cut | 只服務 top100∪watchlist | 唔使 940 張張靚 |
| 缺口補齊 | twin sale clone、四語短故事、缺圖 clone | FE 素材 **100%** |
| Snapshot | `canonical_live_fe` + pointer | 前端可食最新 DB |

---

## 2. 鐵律（帶落 DB 嘅經驗）

1. **市值** = PSA10 價 × GemRate PSA10 POP；禁 Limitless/TCGplayer raw 價  
2. **前端只讀 snapshot**，唔直連 MySQL  
3. **成交全入**；1d/7d/21d/30d **反推**；Top100 可用 30d liquid 閘  
4. **每卡 mark 一次 ID + 邊個腳本得** → registry → 增量只重跑  
5. **Recall 可極低；入庫必腳本 Verify**（AI 編排，唔取代 QC）  
6. **錯 identity 必 clean**  
7. **Primary OS = Windows**（Task Scheduler + Windows Python）；WSL 易整爆 manifest 路徑  
8. Secrets 喺 `data/runtime/config/backend.env` / `/etc/cardz-market-cap/backend.env`，**永不 commit**

---

## 3. FE 100% 定義（已達成）

**FE_SET = snapshot.top100 ∪ snapshot.watchlist**（今次 100+129=229）

每張必須：

| 欄位 | 狀態 |
|---|---|
| pricePsa10 | 100% |
| populationPsa10 | 100% |
| marketCap | 100% |
| image.src | 100% |
| historyDaily ≥2 | 100% |
| trackedSales（窗內有數） | 100% |
| stories（至少一語有文） | 100% |

驗證：

```powershell
python -X utf8 -c "import json; from pathlib import Path; d=json.loads(Path('data/public/publish-staging/generations/canonical_live_fe/snapshot.json').read_text(encoding='utf-8')); print(len(d['top100']), len(d['watchlist']))"
```

Pointer：`data/public/publish-staging/latest.json` → `generations/canonical_live_fe/snapshot.json`

> 註：`productionEligible` 可能仍 False（ranked 303 但 published 229，`presentation_assets_incomplete`）。**FE 已出版集合 100%** 同 **production 旗** 係兩件事；軟 live 用 FE_SET 100% 即可。

---

## 4. 關鍵腳本地圖

| 腳本 | 用途 |
|---|---|
| `qualified_pool_operator.py status` | 940 覆蓋 |
| `semi_auto_identity.py` | clean + recall→verify SNK/eBay |
| `full_volume_recall_verify.py` | 一條龍身份+成交 stock+chip 價 |
| `snk_market_data` + `ingest_snk_trades_sales` | 已 bind → 成交全入 |
| `g10_sales_cache_ingest` / `g10_ebay_ingest` | 本地長史 |
| `fill_watchlist_sales_stock` | G10 reattach + twin clone |
| `canonical_public_snapshot.py` | DB → snapshot |
| `market_alerts.py` | 日榜；Top100 liquid 排前 |

---

## 5. 永久性產物

- `catalog_source_identity` / `catalog_variant_locale` / `market_*_observation`  
- `data/runtime/private-source-map/liquidity-source-registry.jsonl`  
- `data/runtime/private-source-map/semi-auto-identity-ledger.jsonl`  
- `docs/RECALL_VERIFY_OPS.md`  
- FE snapshot：`canonical_live_fe`  

---

## 6. 未完成（之後增量）

- 940 全池成交／SNK id 仍遠未 100%（**唔阻擋 FE live**）  
- OP harvest 全量 inventory 持續  
- productionEligible 清 74 skip（identity incomplete / image）  
- 深度 editorial 故事（而家有模板短文）  
- 日更 incremental 只跑 registry preferred 源  
- 剩 sparse 2（ST10、Greninja）——源窗真乾  
- comic/SEC-SP 同 collector 共用 Limitless base 面——按 printing 分圖  

---

## 7. 晚間續（2026-07-29 PM · 升跌 + SAMPLE + 填庫）

| 做咗 | 證據／結果 |
|---|---|
| 30d 假 0% | TPL today-stamp bug 修；#34/37/88/89 有真 % |
| changePct 頭尾 | `canonical_public_snapshot` 簡單 head-tail；見 FILL_LOOP_LESSONS |
| G10 kline bridge | +183 價行；sparse 5→2 |
| SNK 161 harvest + trades | +2772 trades；sale_any **261** |
| G10 sales_cache | 大批 snkrdunk 成交 upsert |
| SAMPLE OP 20 張 | Limitless `_EN` + P-110 G10；re-scan **0** |
| 文檔 | PROJECT_STATE · FE_LIVE_100 · HANDOFF · CARD_SOURCING · FILL_LOOP_LESSONS |
| 腳本 | `scripts/db_fill_until_green.py` |

**用戶澄清**：SAMPLE≈美版 TCGplayer 感；clean 主力係 **EN Limitless** 唔係日版；OP 美日共用 number 唔共用 SKU。  
