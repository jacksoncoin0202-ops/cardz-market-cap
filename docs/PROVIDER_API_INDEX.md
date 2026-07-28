# Provider 索引 — 點做入口

**2026-07-29**：日常只睇  
→ **[PROJECT_MAP.md](PROJECT_MAP.md)**（分支 + 點做）  
→ **[PROJECT_STATE.md](../PROJECT_STATE.md)**（數字 + 營運）

| 源 | 做咩 | 手冊（純點做） | 腳本 |
|---|---|---|---|
| GemRate | POP 入池 | MAP §1 | `gemrate_source.py` |
| TPL SSR | US PSA10 價 | [US_PRICE_SOURCE_RULES.md](US_PRICE_SOURCE_RULES.md) | `tcgpricelookup_ssr.py` · operator |
| SNK | JP 價 + 成交 | [SNKRDUNK_API_MANUAL.md](SNKRDUNK_API_MANUAL.md) | `snk_market_data` · `ingest_snk_trades_sales` |
| PC / eBay | 逐筆 sold | [PRICECHARTING_API_MANUAL.md](PRICECHARTING_API_MANUAL.md) · [EBAY_SOURCE.md](EBAY_SOURCE.md) | `pricecharting_*` |
| TCGplayer | 卡圖 | [TCGPLAYER_API_MANUAL.md](TCGPLAYER_API_MANUAL.md) | `tcgplayer_images` · fill-images |
| TCGFish/Collectr | 價尾巴 | US_PRICE § | `us_price_fallback.py` |
| 各源 key 表 | 點搵一張 | [SOURCE_LOOKUP_METHODS.md](SOURCE_LOOKUP_METHODS.md) | identity jsonl |

## 硬禁（一行）

exact bind fail-closed · SNK=JP 榜 · TCG 圖≠PSA10 價 · 無 PC token 用 HTML · secrets 唔 commit · Limitless `$` 唔入市值
