# Provider API surface freeze — 2026-07-28

Agent-facing freeze of PriceCharting + TCGplayer collection contracts.  
Full ops manuals:

- [PRICECHARTING_API_MANUAL.md](../../PRICECHARTING_API_MANUAL.md)
- [TCGPLAYER_API_MANUAL.md](../../TCGPLAYER_API_MANUAL.md)
- Index: [PROVIDER_API_INDEX.md](../../PROVIDER_API_INDEX.md)

---

## TCGplayer (live 200, no partner key)

| Endpoint | Purpose |
|---|---|
| `POST https://mp-search-api.tcgplayer.com/v1/search/request?q=…&isList=false` | productId, set, `customAttributes.number`, listings sample |
| `GET https://mpapi.tcgplayer.com/v2/product/{id}/pricepoints` | Normal/Foil market + median |
| `POST https://mpapi.tcgplayer.com/v2/product/{id}/latestsales` | recent sales sample |
| `GET https://infinite-api.tcgplayer.com/price/history/{id}/detailed?range=month\|quarter\|annual` | market history buckets |
| `GET https://tcgplayer-cdn.tcgplayer.com/product/{id}_in_1000x1000.jpg` | art candidate |

Headers: `Origin` + `Referer` = `https://www.tcgplayer.com`. Prefer `curl_cffi` chrome impersonate.

**Canary:** `SM190` → stamped vs unstamped; `EB02-010` multi printing — never take first hit.

PoC: [../2026-07-27-image-fix/tcgplayer_lookup.py](../2026-07-27-image-fix/tcgplayer_lookup.py)

---

## PriceCharting

### A. Official (token)

| Path | Notes |
|---|---|
| `GET /api/product?t=&id=` | current grades; **no historic sales** |
| `GET /api/products?t=&q=` | search ≤20 |
| Daily CSV | Legendary; 24h regen; preferred bulk current price |
| Rate | API 1/s; CSV 1/10min |

Cards: `manual-only-price` = PSA10 (US cents); `sales-volume` = yearly product volume.

### B. Product page internal bus (after CF)

Not a separate sales XHR. Frontend reads:

- `VGPC.chart_data.manualonly` → PSA10 history `[[ts_ms, cents], …]`
- DOM `div.completed-auctions-manual-only` → eBay sale rows
- Live product-page XHR only: `/search-autocomplete`, `/consoles-autocomplete/{category}`
- POP: SSR `GET /pop/item/{console}/{slug}` + `VGPC.pop_price_data`

Transport: headed Playwright profile — `pipelines/pricecharting_cf_session.py`  
Parse: `pipelines/pricecharting_page_parse.py`

**Live canary (session cleared):**

| Product | id | PSA10 hist pts | sales rows |
|---|---:|---:|---:|
| Charizard Base #4 | 630417 | 68 | 25 |
| Monkey.D.Luffy EB02-010 | 9361519 | 15 | 7 |

### C. Bulk for 400–1000 cards

- **Daily current:** CSV once (or API 1 rps) — bulk OK  
- **Depth history/sales:** baseline + slow incremental (e.g. 50–100 pages/day), not full HTML crawl every day  
- Details in manual § bulk / identity

Local intercept dump (may be gitignored):  
`data/private/pricecharting_session/internal_net/INTERNAL_SURFACE.json`

---

## Non-goals

- TCGplayer as PSA10 authority  
- PriceCharting replacing SNK  
- Auto-bind by name/number without exact printing review  
