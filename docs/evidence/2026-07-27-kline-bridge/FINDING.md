# K 線橋接：`g10_kline_daily` ledger → `market_price_observation`

**量度日期**：2026-07-27（UTC 07-26 深夜）
**前提**：`market_source_observation` 有 47,582 行 `g10_analytics` / `g10_kline_daily`（2023-07-20 → 2026-07-25、636 實體）；`catalog_source_identity` 對 ebay/snkrdunk 有 636 實體中 584 個嘅映射；DB session UTC。有平行 agent 改緊圖 manifest，冇掂價格表。

## 結論一句

**ledger 入面 4,357 條真成交日 K 線（carried=0），扣除 identity 對唔到嘅 374 行後，
3,964 行 PSA10 收盤價已寫入 `market_price_observation`（source_code=`g10_kline`、
priority=300 補洞位），ebay 體系卡三年價格歷史正式接通前端。**

## 點量（可重跑）

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"
set -a && . data/runtime/config/backend.env && set +a
# dry-run 自洽
.venv-backend/Scripts/python.exe -X utf8 pipelines/g10_kline_price_bridge.py
# 事後驗證
python -X utf8 scripts/ro_sql.py "SELECT COUNT(*) FROM market_price_observation WHERE source_code='g10_kline'"
```

## 實跑數字（run_id=107，2026-07-27）

| 段 | 數 |
|---|---|
| ledger 讀入 | 47,582 |
| carried=1 跳過（機械延伸，唔係觀測） | 43,225 |
| identity 對唔到（quarantined，52 個實體） | 374 |
| 無效 close | 0 |
| 候選寫入 | 3,983 |
| 實際新插入 | 3,964 |
| INSERT IGNORE 撞 uq | 19（同卡同日雙 entity 映射，正常去重） |

## 四條規則（同 `pipelines/g10_kline_price_bridge.py` docstring 一致）

1. **只認 `carried=0`**：`carried=1 & tx>0` 實測有 1,667 條，證明唔可以用 tx 判斷真成交日。
2. **幣種 USD 已驗**：snkrdunk:100081 K 線 2026-06-08 close=325.0 = 同卡（variant 1592）
   同日 ebay 源 `price_usd=325.000000`，完全吻合。
3. **id 對應唯一合法路徑係 `catalog_source_identity`**（entity 前綴 `ebay:`/`snkrdunk:`
   直接係 identity source_code），唔准靠卡名。374 行 quarantined，identity 擴充後重跑自動撿返。
4. **priority=300 補洞唔搶位**：producer 揀價 `ORDER BY observed_date DESC, source_priority ASC`，
   直採源 100/150/200 永遠贏，K 線只喺該卡該日冇任何直採觀測時先出頭。

## 下游效果（producer 重跑 canonical_20260725_aaca5dd2ee9f 實測）

- snapshot historyDaily 最遠去到 **2023-08-31**（Pikachu wearing a poncho，89 點取樣）；
  橋接前價格歷史最早只到 2026-04。
- 243 張出版卡價格 0 缺。
- 重跑 idempotent：INSERT IGNORE 撞 `uq_market_price_daily` 唔蓋 provenance。
