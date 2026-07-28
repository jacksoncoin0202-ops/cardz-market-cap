# PriceCharting — 點做

> eBay **逐筆 sold** 主 transport（直爬 eBay sold = 擋）。地圖：[PROJECT_MAP.md](PROJECT_MAP.md) §4

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
$env:CARDZ_DB_HOST = "127.0.0.1"
```

---

## 1. 生產流程（無付費 token）

```powershell
# 1) 首次：headed browser 過 CF → 存 cookies
python -X utf8 pipelines\pricecharting_cf_session.py

# 2) 有 card→PC map 先 export sold
python -X utf8 pipelines\pricecharting_ebay_export.py --map path\to\card-pc-map.jsonl
# 預設 out: data\runtime\private-source-map\ebay-sold-from-pricecharting.json

# 3) normalizer → market_sale_observation
# daily / qualified-sync 讀 CARDZ_EBAY_SOLD_INPUT 或上面預設檔
```

Parse 腳本：`pipelines/pricecharting_page_parse.py`  
Normalizer：`pipelines/ebay_sold_data.py`

---

## 2. 產品頁要拎咩

過 CF 後 GET 產品 HTML：

| 數據 | 喺邊 |
|---|---|
| PSA10 guide / 月頻 K | 頁內 `VGPC.chart_data` · `manualonly: [[ts_ms, cents], …]` |
| 現價 cents | `manualonly` 最後非零點 或 API `manual-only-price` |
| 逐筆 eBay sold ~30/grade | `div.completed-auctions-manual-only` 表：date · eBay 連結 · $price |

**冇獨立 sales XHR** — 全部 SSR 嵌 HTML。

---

## 3. 付費 API（可選 · 本 repo 通常無 token）

```powershell
# demo token 只 identity；真價要訂閱 t=
$env:PC_TOKEN = "<你嘅 token>"
curl "https://www.pricecharting.com/api/products?t=$env:PC_TOKEN&q=EB02-010"
curl "https://www.pricecharting.com/api/product?t=$env:PC_TOKEN&id=9361519"
```

| 路線 | 拎到 | 拎唔到 |
|---|---|---|
| `/api/product` · 每日 CSV | 現價 · 年度 sales-volume | 歷史 K · 逐筆 sold |
| 產品頁 HTML | 歷史 + 逐筆 sold | 要 `cf_clearance` |

Token：https://www.pricecharting.com/subscriptions → API/Download · query `t=`

---

## 4. 禁

- `LH_Sold=1` 直爬 eBay（PerimeterX）  
- 用 PC 取代 SNK 做 JP 榜價  
- 未 map product id 就 first-hit overwrite identity
