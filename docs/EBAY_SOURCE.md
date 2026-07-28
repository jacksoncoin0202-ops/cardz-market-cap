# eBay — 點做

> 生產 **唔直爬** sold search（PX 擋）。用 PriceCharting 產品頁。

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap

# 生產
python -X utf8 pipelines\pricecharting_cf_session.py   # 首次 CF
python -X utf8 pipelines\pricecharting_ebay_export.py --map path\to\card-pc-map.jsonl
# → data\runtime\private-source-map\ebay-sold-from-pricecharting.json
# daily / qualified-sync 讀 CARDZ_EBAY_SOLD_INPUT 或呢個預設檔

# 可選：單 item（JSON-LD 唔保證齊）
python -X utf8 pipelines\ebay_brute_harvest.py item --ids 176641588834
```

| 檔 | 用途 |
|---|---|
| `pricecharting_ebay_export.py` | PC HTML → ebay sold input |
| `pricecharting_page_parse.py` | VGPC + completed-sales 表 |
| `ebay_sold_data.py` | PSA10 normalizer → DB |

| 源 | 狀態 |
|---|---|
| PC 產品頁 PSA10 表 | **生產** |
| eBay item 頁 JSON-LD | 可選；cert 有時有 |
| `LH_Sold=1` sold search | **擋** — 唔用 |
| eBay Browse API | 只 active listing，唔係 sold |

詳 parse：[PRICECHARTING_API_MANUAL.md](PRICECHARTING_API_MANUAL.md)
