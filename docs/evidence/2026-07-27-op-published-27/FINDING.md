# FINDING — 27 張已出版 OP 卡「價唔得」診斷（op-published-27）

- **量度日期**：2026-07-27（DB 逐卡查詢 + production snapshot `canonical_20260726_e88c81289ac3` 掃描即日執行）
- **結論一句**：27 張已出版 OP 卡**全部有價格歷史（90/90 點，status=ready），冇一張缺價**；用戶見到嘅怪數字係三種病：**單日源 spike**（rank 5 +544%）、**snk 日更源死咗/從來冇**（rank 95/96/Law/Zoro → stale forward-fill + 窗口借數）、**稀疏源 forward-fill 令 24/27 張 Δ1=0**。
- **前提**：production snapshot 258 published（231 pokemon + 27 one-piece）；價源生死以 `market_price_observation` 最後寫入日計（量度時 snk_psa10/ebay 都去到 07-25）；窗口借數 cascade（`borrowWindowMetric`）已上線，見 [2026-07-27-launch-fallback/DATA_CONCERNS.md](../2026-07-27-launch-fallback/DATA_CONCERNS.md)。⚠ 量度當時 donor 順序係 30d→7d→1d；同日晚間用戶糾正為 **1d→7d→30d（最新鮮優先）**——下面病類 B 引嘅「rank 95 借 7d、rank 96 借 1d」係反轉前嘅顯示值，反轉後同一批卡會優先借 1d，實際顯示值要重量。

## 點量（可重跑）

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"
set -a && . data/runtime/config/backend.env && set +a

# 27 張卡逐張價源分佈（opaque id 清單喺 temp/op27_ids.txt；catalog_variant 欄係 tcg_code 唔係 tcg）
python -X utf8 scripts/ro_sql.py "SELECT cv.opaque_id, mpo.source_code, COUNT(*), MIN(mpo.observed_date), MAX(mpo.observed_date) FROM catalog_variant cv JOIN market_price_observation mpo ON mpo.variant_id = cv.id WHERE cv.opaque_id IN (<27 ids>) GROUP BY cv.opaque_id, mpo.source_code"

# rank 5 spike 對照（兩張 ST21-014 近期原始行；欄係 native_price / price_usd，冇 price_amount）
python -X utf8 scripts/ro_sql.py "SELECT variant_id, observed_date, native_price, native_currency, price_usd FROM market_price_observation WHERE variant_id IN (54,240) AND source_code='snk_psa10' AND observed_date >= '2026-07-18' ORDER BY variant_id, observed_date"

# spike 上游定案（直接睇原始 payload SNK 講咩價；external id 格式係 snkrdunk:<asset>）
python -X utf8 scripts/ro_sql.py "SELECT observed_date, observation_kind, LEFT(payload_json, 600) FROM market_source_observation WHERE external_entity_id='snkrdunk:706813' AND observed_date IN ('2026-07-25','2026-07-26') ORDER BY observed_date"
```

Snapshot 側：node script 掃 `data/runtime/local-serve/snapshot.json` 27 張 OP 卡嘅 `historyDaily`（90 點 priced 計數）同 `windows` 三窗口 status。

## 三個病類（實測證據）

| 病類 | 卡 | 證據 |
|---|---|---|
| A. 單日源 spike | rank 5 ST21-014 Luffy（+544%） | snk_psa10 07-26 ¥294,500 vs 07-25 ¥45,700，一日 ×6.4；另 g10_kline 07-24 有 $610.90 單點 blip。**已驗定案（07-27）：唔係身份污染** —— 兩個 ST21-014 係兩張唔同卡（variant 54 = 週刊少年 Jump 雜誌 promo ↔ snk asset 706813；variant 240 = Flagship Battle 優勝紀念 ↔ 605546），`catalog_source_identity` 綁定 1:1 乾淨；¥294,500 亦唔等於 240 嘅價位（¥398,800–450,000）。原始 payload（`referenceMethod: snk_daily_history`）07-26 SNK 自己就係報 ¥294,500，而同卡 07-25 G10 metrics latestPrice $295.53、當日 kline high $325 → **上游 SNKRDUNK 單日異常**，我哋 pipeline 冇抄錯 |
| B. snk 日更源死/冇 → stale forward-fill | rank 95 Boa OP07-051（零 snk 行，ebay-only 13 行）；rank 96 ST10-006（snk 死於 06-15）；Law ST10-010、Zoro OP01-025（snk 死於 07-11） | 前端 30d 窗口砌唔出 → 借 7d/1d：rank 95 嘅 +6.41% 係借 7d、rank 96 嘅 0.00% 係借 1d（有 `fallbackWindow` 標記） |
| C. 稀疏源 forward-fill | 24/27 張 Δ1=0.00% | snk 每日只掃部分卡，冇新觀測嗰日 forward-fill 舊價 → 日變動恆 0 |

## 一個要記住嘅反面教訓

初查時 27 張源分佈 output 經 `tail -80` 截咗頭 5 行，先後推出「5 張卡冇價源」「id 漂移」兩個**錯**結論。逐卡專查證實 5 張全部有價（287/192/151/387/221 行，去到 07-25）。**唔准由 tail 截斷咗嘅 output 推「冇」。**

## 修復方向（連 roster 級 FINDING 一齊睇）

- 病類 A：已定案係上游單日異常（唔係身份污染）→ 唯一修法係 source-level 日環比 sanity gate（一日 ×N 倍隔離候審，N≈3 起步）。就算上游聽日自我修正，冇 gate 下次照中。
- 病類 B/C：根在 SNKRDUNK 日更覆蓋——27 張只係表徵，roster 級 132 張零價卡嘅身份 gate（見 [2026-07-27-op-price/FINDING.md](../2026-07-27-op-price/FINDING.md)、[2026-07-27-op-ebay-mapping/FINDING.md](../2026-07-27-op-ebay-mapping/FINDING.md)：catalog 冇 parallel 欄 + 兩批孿生 variant 行）先係正題。
