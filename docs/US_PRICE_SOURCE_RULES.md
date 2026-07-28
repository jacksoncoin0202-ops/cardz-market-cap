# US 價 — 點做

> 無 API key。地圖：[PROJECT_MAP.md](PROJECT_MAP.md) §2 · §6

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
$env:CARDZ_DB_HOST = "127.0.0.1"
```

---

## 鏈

```text
TPL SSR（主） → TCGFish SSR → Collectr(product_id) → PriceCharting HTML（sold）
```

腳本：`pipelines/tcgpricelookup_ssr.py` · `pipelines/us_price_fallback.py`

---

## TPL

```powershell
# 綁 slug
python -X utf8 pipelines\qualified_pool_operator.py map-tpl --workers 10

# 全量（首次：窗內 ~211 日 + 現價，一次 card 頁就夠）
python -X utf8 pipelines\qualified_pool_operator.py harvest-tpl --mode full --workers 8
# 或
python -X utf8 pipelines\tcgpricelookup_ssr.py harvest --mode full --slugs-file data\runtime\private-source-map\tpl-slugs.txt

# 之後每日 incremental（同一接口 upsert 日點，唔加長窗）
python -X utf8 pipelines\qualified_pool_operator.py harvest-tpl --mode incremental --workers 8

# 寫 DB
python -X utf8 pipelines\qualified_pool_operator.py ingest-prices

# 單卡
python -X utf8 pipelines\tcgpricelookup_ssr.py fetch --slug pokemon-base-set-charizard-004-102-holofoil
```

```text
GET catalog?game=pokemon|onepiece|pokemon-jp&q=...
GET /card/{slug} + Header RSC: 1
只寫 PSA10 ebay 價 > 0；raw NM 唔入市值
```

---

## TCGFish / Collectr

```text
TCGFish: search HTML → 卡頁 → "PSA 10" 格
Collectr: 有 product_id 先有用（search 幾乎廢）
  GET api-v2.getcollectr.com/catalog/products/{id}
```

---

## 禁

- `api.tcgpricelookup.com` + key  
- eBay sold 直爬  
- 130point  
- raw TCGplayer market 頂 PSA10
