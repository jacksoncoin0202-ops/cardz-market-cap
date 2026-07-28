# SNKRDUNK — 點做

> 只寫命令同 endpoint。地圖：[PROJECT_MAP.md](PROJECT_MAP.md) §3 · 營運：[PROJECT_STATE.md](../PROJECT_STATE.md)

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
$env:CARDZ_DB_HOST = "127.0.0.1"
```

---

## 1. 日常（940 池已 bind）

```powershell
# 拉 PSA10 master + K 線 + trades
python -X utf8 pipelines\snk_market_data.py `
  --ids-file data\runtime\private-source-map\qualified-940-snk-ids.txt `
  --condition trading_card_single_psa10 `
  --out data\runtime\private-source-map\snk-psa10-liquidity-full.jsonl `
  --delay 0.3

# ★ 成交入 DB（唔跑呢步 = 流動性仍假低）
python -X utf8 pipelines\ingest_snk_trades_sales.py `
  --harvest data\runtime\private-source-map\snk-psa10-liquidity-full.jsonl
```

---

## 2. 綁 id（新卡／擴池）

```powershell
# exact bind（多格式 number + set + 名 fail-closed）
# OP01-016 / OP01 016 / 016 / SV2A173 等組合；純數字要 set 確認先 auto
python -X utf8 pipelines\bind_snk_watchlist.py bind --write

# 全圖鑑掃 id（plain requests，免 browser）
python -X utf8 pipelines\snkrdunk_discover.py `
  --keywords ポケモンカードゲーム ワンピースカードゲーム `
  --max-pages 60 `
  --out data\private\snkrdunk_brute\discovered_ids.txt
```

---

## 3. 單 id 批量 dump

```powershell
python -X utf8 pipelines\snkrdunk_bulk.py 116069 115238 --out snkrdunk_dump.jsonl
python -X utf8 pipelines\snkrdunk_bulk.py 116069 --condition trading_card_single_psa10
```

---

## 4. 要 call 嘅 URL（免登入）

Base：`https://snkrdunk.com`

| 做咩 | Method | Path |
|---|---|---|
| master | GET | `/v1/apparels/{itemId}` |
| 16 condition 即時價 | GET | `/v2/products/{itemId}/size-chips?type=apparel` |
| K 線 + 最近 trades | GET | `/v3/products/{productCatalogId}/trading-history?range=all&condition_code=trading_card_single_psa10` |
| 同 set 擴展 | GET | `/v1/apparels/{itemId}/group-items/same-category?page=1&perPage=13` |
| 全圖鑑 id | GET | `/search?keywords=...&page=N` → HTML 抽 `/apparels/(\d+)` |

**PSA10 condition_code**：`trading_card_single_psa10`  
**永久 key**：`snkItemId`（identity / `catalog_source_identity`）  
**x-version**：可選；由 `/apparels/{id}` HTML 抽 `prod-YYYYMMDD-NN` 放 header。

**trades 欄**：`price` 円 · `soldAt` · `title`（要含 PSA10）· `label`（枚數）

---

## 5. 產物路徑

| 檔 | 用途 |
|---|---|
| `data/runtime/private-source-map/qualified-940-snk-ids.txt` | 已 bind id 列表 |
| `…/snk-psa10-liquidity-full.jsonl` | harvest |
| `…/liquidity-source-registry.jsonl` | 每卡邊個源易拎 |
| DB `market_sale_observation` | ingest 後 |
| DB `market_price_observation` source=snkrdunk | 榜價 |

---

## 6. 禁

- first-hit 名搜自動 bind  
- 用 raw 價當 PSA10  
- harvest 完唔跑 `ingest_snk_trades_sales`
