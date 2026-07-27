# OP 價格主線（選項 D）目標名單 — 132 張 POP 合格零價卡

## 結論一句

roster 內 One Piece 卡有 **132 張 PSA POP≥1000 但 `market_price_observation` 一行價都冇**，
其中 **131 張連一條價源 identity mapping 都冇**（`catalog_source_identity` 只有 `gemrate` 行），
1 張有 `ebay,gemrate`；**132 張全部 `card_language='en'`** —— 所以本主線第一子步係
identity mapping 補洞（eBay 主場、SNKRDUNK 補充），唔係直接開採集器。

## 量度日期

2026-07-27（agent：pm-live-3800）

## 點量（可直接重跑）

```bash
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 scripts/ro_sql.py "SELECT COUNT(*) FROM catalog_variant v JOIN (SELECT variant_id, MAX(top_grade_population) mp FROM market_grader_population_observation WHERE grader_code='PSA' GROUP BY variant_id) p ON p.variant_id=v.id AND p.mp>=1000 LEFT JOIN (SELECT DISTINCT variant_id FROM market_price_observation) pr ON pr.variant_id=v.id WHERE v.tcg_code='one-piece' AND pr.variant_id IS NULL"
```

<!-- @verified 2026-07-27 id=op_mainline.target_132 expect=132 ttl=3 sql=SELECT COUNT(*) FROM catalog_variant v JOIN (SELECT variant_id, MAX(top_grade_population) mp FROM market_grader_population_observation WHERE grader_code='PSA' GROUP BY variant_id) p ON p.variant_id=v.id AND p.mp>=1000 LEFT JOIN (SELECT DISTINCT variant_id FROM market_price_observation) pr ON pr.variant_id=v.id WHERE v.tcg_code='one-piece' AND pr.variant_id IS NULL -->

identity 覆蓋分佈（同日同一條 join 加 `GROUP_CONCAT(source_code)`）：
`gemrate` only = 131 張、`ebay,gemrate` = 1 張。

## 量度時嘅前提

- 「roster 內」以 `catalog_variant` 現有行為準（tcg_code='one-piece' 喺 catalog 有 311 張，
  但 POP gate + 零價條件自然收斂到呢 132 張）。**價一入庫呢個名單就會縮** ——
  132 係 D 開工前嘅 baseline，唔係恆量。
- POP gate 用 `MAX(top_grade_population) WHERE grader_code='PSA'`，同 07-27 E agent 口徑一致
  （E 報 132，本次重量 132，對上）。
- `card_language='en'` 全 132 張 —— 採集策略含義：**eBay（英文卡 PSA10 成交主場）行先，
  SNKRDUNK（日本平台，英語版流動性低）做補充**。
- 55 張 out-of-roster 有價 OP 卡**唔喺**呢個名單（roster lock 9：入唔入圍等用戶決定）。

## 檔案

- [target_list_132.jsonl](target_list_132.jsonl) —— 132 行，欄：variant_id / opaque_id /
  canonical_name / collector_number / set_name / card_language / psa10_pop / existing_sources，
  按 psa10_pop 降序。
