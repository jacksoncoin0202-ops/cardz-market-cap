# TCGplayer — 點做

> **卡圖主線**。raw market **唔入** PSA10 市值。地圖：[PROJECT_MAP.md](PROJECT_MAP.md) §5

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
$env:CARDZ_DB_HOST = "127.0.0.1"
```

---

## 1. 940 池補圖

```powershell
python -X utf8 pipelines\qualified_pool_operator.py fill-images --write
python -X utf8 pipelines\ensure_image_abc.py --write --refetch-missing
```

有 `tcgplayerId`（多數 TPL harvest 已帶）→ 直接 CDN，唔使 search。

---

## 2. 單卡 lookup（PoC）

```powershell
.\.venv-backend\Scripts\python.exe -X utf8 docs\evidence\2026-07-27-image-fix\tcgplayer_lookup.py
```

---

## 3. Endpoint（免 partner key · curl_cffi chrome）

Header（search / mpapi / infinite 要）：

```http
Origin: https://www.tcgplayer.com
Referer: https://www.tcgplayer.com/
Accept: application/json
```

| 做咩 | Method | URL |
|---|---|---|
| 搜尋 productId | POST | `https://mp-search-api.tcgplayer.com/v1/search/request?q={collector}&isList=false` |
| 即時 market | GET | `https://mpapi.tcgplayer.com/v2/product/{id}/pricepoints` |
| 最近 raw 成交 | POST | `https://mpapi.tcgplayer.com/v2/product/{id}/latestsales` |
| 市價歷史桶 | GET | `https://infinite-api.tcgplayer.com/price/history/{id}/detailed?range=month` |
| **卡面圖 CDN** | GET | `https://tcgplayer-cdn.tcgplayer.com/product/{id}_in_1000x1000.jpg` |
| 原圖 host | GET | `https://product-images.tcgplayer.com/{id}.jpg` |

**Search body 形狀**（簡）：

```json
{
  "algorithm": "sales_dismax",
  "from": 0,
  "size": 24,
  "filters": { "term": { "productLineName": ["One Piece Card Game"] }, "range": {}, "match": {} },
  "listingSearch": { "context": { "cart": {} }, "filters": { "term": { "sellerStatus": "Live", "channelId": 0 } } }
}
```

入庫：`store_face_art_image` → `market_image_asset` + webp 檔 + `market_image_qc`。

---

## 4. OP 圖死角（TCGplayer 唔中）

```powershell
python -X utf8 pipelines\op_limitless_images.py --write --only-missing
```

CDN pattern：

```text
https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/one-piece/{SET}/{SET}-{NUM}_EN.webp
```

再：Drive ×3 / OP.gg → `data/private/op-image-inbox/` → `store_face_art_image`  
link 見 STATE §F。

---

## 5. 禁

- raw market / latestsales 當 PSA10 榜價  
- search first-hit 自動 bind printing  
- 入庫 seller 場景相、slab、SAMPLE、sealed 盒圖
