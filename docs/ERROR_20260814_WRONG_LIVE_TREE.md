# 2026-08-14 事故：認錯 live 樹、認錯公開路徑

DADDY 更正。寫低，之後唔好再犯。呢份係錯誤紀錄，唔係授權再 bake／deploy。


## 錯咗咩（嚴重）

1. **認錯樹當 live。** 喺 `cardz-market-cap/`（new-era／operator 實驗樹）跑 `snk-image-priority-status`、presentation pass、materialize、bake。呢棵樹只擁有 MySQL 3308 compose／volume，**唔係**出街真身。
2. **認錯舊 bake 當出街權威。** 當 8/7 762 張 `pass_receipt` 同 `cardz-market-cap-release-20260804` 係而家 live。錯。Live 已經係 1368 張、`db3308_*`、homepage 用得。
3. **差啲污染 PSA10 口徑。** 試過把 BOX 寫入 PSA10 product snapshot／bake hash／`seed-snapshot.json`。咁會改 pass content hash、Top 100 物料清單、deploy 讀嘅唯一 seed。**未寫入 live seed；呢條路永久封死。**
4. **公開路徑用錯名。** 用咗 `/sealed`（內部 pipeline／table 名）。DADDY 公開名／路徑係 **BOX／`/box`**。唔係 subdomain、唔係 `sealed.cardzmarketcap.com`、唔係 nav 寫 Sealed。

## 事實（2026-08-14 實測）

| 項目 | 正解 |
|---|---|
| Live site | `https://app.cardzmarketcap.com` |
| Health | `generation=db3308_b0cb6e76228b4a99` · 1368 張 · `2026-08-13T14:06:55.448Z` · `dataMode=baked-snapshot` · `status=ok` |
| 真身樹 | `cardz-market-cap-fe-db-20260805` |
| 037 推 live | 由 `origin/main` 開 `release/037-fe04-box` overlay；**唔**由呢棵樹、亦唔由 `rebuild/036-foundation` 直接 push |
| 呢棵 `cardz-market-cap/` | 只准讀 compose／3308。**唔准** pass／bake／`[deploy]`／webhook |
| `release-20260804` | 舊 762 bake，唔係 live |
| 公開 BOX | `/box` · `/box/{id}`。canonical／nav／sitemap／share 一律呢條 |
| `/sealed` | 只准 308 redirect 去 `/box`。唔入 sitemap |
| PSA10 seed | `fe-db` 嘅 `data/public/seed-snapshot.json`。BOX **唔准**寫入 |

## 污染邊界（hard）

准：

- 獨立 sidecar：`data/public/box-subset.json`（或 runtime sealed export 嘅複本）
- FE overlay：讀完 PSA10 seed 之後先掛 box block；`generation`／Top 100／watchlist 不變
- 盒圖自己嘅 `/market-assets/{sha}.webp`，唔改卡圖 triplet 清單
- 公開路由 `/box`；舊 `/sealed` 只做轉向

不准：

- 改 `data/public/seed-snapshot.json` 加 `sealed[]`
- 改 036 pass／five-adapter／Top 100 排位
- 用 new-era 樹 `daily --refresh --pass`、bake、`[deploy]`
- 公開路徑／nav／canonical 寫 Sealed
- 出街 HTML 印供應商名
- 未 DADDY visual 就 `[deploy]`

## GEO／SEO

Live PSA10（首頁 + 卡頁）2026-08-14 GEOHub diagnose 六維 100/100。跟 `provenance.tsx`、`METHOD & DATA`、ISO 日期同一個 text node、英文 byline。BOX 頁要抄同一套 token 規則，但文案講 BOX 參考價，唔好抄 PSA10 市值那段（事實會錯）。

## 之後點擺新改進

BOX 喺實驗樹養。入 fe-db 真身只准 sidecar。契約：`cardz-market-cap-fe-db-20260805/docs/BOX_SIDECAR_PORT.md`。
