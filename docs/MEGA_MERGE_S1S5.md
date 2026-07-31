# MEGA_MERGE S1–S5 + C04 + H3b

**At:** 2026-07-28T22:57Z（status after）  
**專案:** cardz-market-cap · Windows Python · `backend.env` · MySQL `:3308`  
**原則:** 準>多 · 禁弱 bind · verify 先寫

## 一句

SCALE harvest 全量已在 [`snkrdunk_all.jsonl`](../data/private/snkrdunk_brute/snkrdunk_all.jsonl)（本輪 merge **+0** 新 item_id）；exhaust pass 推 **snk_id 397→511**、**sale_any 377→404**。

## 1. 輸入 harvest（ok rows）

| 檔 | ok rows | 備註 |
|---|---:|---|
| [`snk-psa10-SCALE-S1.jsonl`](../data/runtime/private-source-map/snk-psa10-SCALE-S1.jsonl) | 452 | `.partial` 同行數 |
| [`snk-psa10-SCALE-S2.jsonl`](../data/runtime/private-source-map/snk-psa10-SCALE-S2.jsonl) | 470 | |
| [`snk-psa10-SCALE-S3.jsonl`](../data/runtime/private-source-map/snk-psa10-SCALE-S3.jsonl) | 621 | |
| [`snk-psa10-SCALE-S4.jsonl`](../data/runtime/private-source-map/snk-psa10-SCALE-S4.jsonl) | 623 | |
| [`snk-psa10-SCALE-S5.jsonl`](../data/runtime/private-source-map/snk-psa10-SCALE-S5.jsonl) | 619 | complete = partial |
| [`snk-psa10-c04-batch1.jsonl`](../data/runtime/private-source-map/snk-psa10-c04-batch1.jsonl) | 966 | |
| [`snk-psa10-SCALE-H3b.jsonl`](../data/runtime/private-source-map/snk-psa10-SCALE-H3b.jsonl) | 120 | |
| **Σ rows（未 dedupe）** | **3871** | |

路徑根：`data/runtime/private-source-map/`

## 2. Merge → snkrdunk_all（by item_id）

| 項 | 值 |
|---|---|
| Backup | [`snkrdunk_all.pre_mega_s1s5_20260728223706.jsonl`](../data/private/snkrdunk_brute/snkrdunk_all.pre_mega_s1s5_20260728223706.jsonl) |
| Before unique item_id | **7860**（lines 7889） |
| After unique item_id | **7860** |
| **total_added** | **0**（全部已先前 merge；per-file 全 `skip_dup`） |
| Merge audit | [`temp/mega_merge_s1s5_20260728223706.json`](../temp/mega_merge_s1s5_20260728223706.json) |

> S5 用 complete `.jsonl`（= `.partial` 619）；唔再 append partial 免雙寫。

## 3. Exhaust pass（`scripts/db100_exhaust_pass.py`）

順序同腳本；parent 曾 timeout，後段 trades/kline **手動補完**。

| Step | 結果 |
|---|---|
| merge_all_new_jsonl | +0（同 §2） |
| `scale_s11_ptcg_exact.py` | **written 40** · snk 390→430 · verify 硬閘（`verify_failed` 425 唔寫） |
| `scale_s8_op_exact.py` | dry-run only（腳本無 `--write`）· 0 persist |
| `c09_write.py` | dry-run only · 0 persist |
| `semi_auto_identity match-snk --write --recall-min 20` | **accepted/written 11** · harvestCards 7860 · recallRejectedAfterVerify 215 |
| `semi_auto_identity clean --write` | checked 437 · **deleted 37**（`verify_set` 等）· good 400 |
| `build_identity_registry` | rows 932 · withSnk **393**（clean 後快照） |
| trades harvest | bound ids **504** · [`snk-psa10-factory-trades-20260728224624.jsonl`](../data/runtime/private-source-map/snk-psa10-factory-trades-20260728224624.jsonl) · ok 504 failed 0 |
| `ingest_snk_trades_sales` | tradesWritten **4363** · cardsWithSales 247 · watchlist sale any **404** |
| `g10_kline_price_bridge --write` | 新插入 **0**（候選 3903 已 carried） |

**準>多：** clean 刪 37 弱/錯 set bind；match 只 verify 過先寫。

## 4. Status 前後（`qualified_pool_operator status`）

| metric | BEFORE 22:36Z | AFTER 22:57Z | Δ |
|---|---:|---:|---:|
| watch | 940 | 940 | 0 |
| any_price | 940 | 940 | 0 |
| image_asset / image_ptr | 940 | 940 | 0 |
| **snk_id** | **397** | **511** | **+114** |
| ebay_id | 90 | 93 | +3 |
| snk_price | 223 | 223 | 0 |
| snk_family_price | 300 | 300 | 0 |
| **sale_1d** | 99 | **100** | +1 |
| **sale_7d** | 300 | **307** | +7 |
| **sale_21d** | 360 | **378** | +18 |
| **sale_30d** | 362 | **381** | +19 |
| **sale_90d** | 375 | **401** | +26 |
| **sale_any** | **377** | **404** | **+27** |

### 目標

| 目標 | 結果 |
|---|---|
| snk 400+ | ✅ **511** |
| sale 更高 | ✅ sale_any **+27** · sale_30d **+19** · sale_90d **+26** |

> snk_id 終值 511 高過本輪 identity registry 393：含 PTCG exact + match 寫入，以及同時段其他 factory/swarm bind；**以 status 終值為準**。本輪可歸因硬寫：PTCG **+40** + match **+11** − clean **−37** + 外部/並行淨貢獻。

## 5. 產物路徑

| 用途 | Path |
|---|---|
| snkrdunk_all | [`data/private/snkrdunk_brute/snkrdunk_all.jsonl`](../data/private/snkrdunk_brute/snkrdunk_all.jsonl) |
| pre-merge backup | [`…/snkrdunk_all.pre_mega_s1s5_20260728223706.jsonl`](../data/private/snkrdunk_brute/snkrdunk_all.pre_mega_s1s5_20260728223706.jsonl) |
| bound ids (504) | [`factory_bound_snk_ids_20260728224624.txt`](../data/runtime/private-source-map/factory_bound_snk_ids_20260728224624.txt) |
| trades harvest | [`snk-psa10-factory-trades-20260728224624.jsonl`](../data/runtime/private-source-map/snk-psa10-factory-trades-20260728224624.jsonl) |
| trades report | [`snk-psa10-factory-trades-20260728224624_report.json`](../data/runtime/private-source-map/snk-psa10-factory-trades-20260728224624_report.json) |
| ingest report | [`qualified-pool-reports/ingest_snk_trades_20260728T225722Z.json`](../data/runtime/private-source-map/qualified-pool-reports/ingest_snk_trades_20260728T225722Z.json) |
| match report | [`qualified-pool-reports/semi_auto_match_snk_20260728T223722Z.json`](../data/runtime/private-source-map/qualified-pool-reports/semi_auto_match_snk_20260728T223722Z.json) |
| clean report | [`qualified-pool-reports/semi_auto_clean_20260728T223723Z.json`](../data/runtime/private-source-map/qualified-pool-reports/semi_auto_clean_20260728T223723Z.json) |

## 6. 未做 / 限制

- `scale_s8_op_exact` / `c09_write` 仍 dry-run（`db100_exhaust_pass` 未傳 `--write`）— OP comic 殘渣未本輪落庫  
- kline bridge 0 新價行  
- 未 bake FE / 未 git push  
- 未降 verify 門檻抬 snk  

## 7. 下一步（可選）

1. 修 `db100_exhaust_pass.py`：exact/c09 加 `--write` 或分步明確  
2. 對 clean 刪走嘅 37 張：set-aware re-match（唔好弱 bind 回寫）  
3. OP no_snk 殘（S8/C09 dry-run 清單）人工 verify 後寫  
4. sale 仍乾嘅 bound id → 記 documented dry，唔靠重 harvest 抬假數  
