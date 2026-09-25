# CARDZ 037 / FE04 — 加入 BOX

> **⚠ 歷史檔（2026-08-24 封存）：** 呢份係 037 開代契約，日程／卡數／FE04 全部過時（live 已係 FE05、日更已係 V2 鏈）。現狀睇 [../PROJECT_STATE.md](../PROJECT_STATE.md)。

> **2026-08-15：** live 已係 **1449** 張 · `product=037` · `FE04` · BOX `/box`。
> FE 出街車 = 呢棵 `cardz-market-cap-037-fe04-live`。資料／日更真身 = `../cardz-market-cap-fe-db-20260805`。
> BOX 由 `sealed_daily.py` 產出、唔再人手搬（P6）。下面 1368／`db3308_b0cb6e76228b4a99` 係 08-14 當日數。
>
> **2026-08-16：** FE05（純 `apps/web` 升級）之後要退返呢版 FE04 → 錨點 tag `fe04-live` = `c622d741`（08-17 由 4bed89a7 移上，含手機 hotfix），一句 `pwsh -NoProfile -File scripts\fe05_rollback.ps1`。見 [FE05_ROLLBACK.md](FE05_ROLLBACK.md)。

DADDY 2026-08-14 開代。呢份係 037 契約。036 交接仍睇 docs/HANDOFF_036_20260812.md。

## 一句話

036 已經係靚仔 PSA10 版本，可以隨時 fallback。037 只係喺 036 上面加入 BOX。

唔開 rebuild_037。唔重跑 five-adapter。唔改 Top 100 排位。

- 036 / FE03 = Live PSA10。出街真身。乾淨、用得、可隨時退回。
- 037 / FE04 = 036 嘅 live PSA10 加 BOX sidecar。Fallback = 退回 036 / FE03。
- 037db = 呢棵 cardz-market-cap-fe-db-20260805 上面嘅 037 實作名。

公開路徑係 /box，唔係 /sealed。

## 兩層點合

- PSA10 seed：Live app.cardzmarketcap.com/api/health 同一份 data/public/seed-snapshot.json。唔准寫入 sealed。generation 保持 live bake id。
- BOX sidecar：本機最新 data/public/box-subset.json。overlay 讀完 seed 先掛。盒圖自己嘅 webp。

2026-08-14 對數：

- Live／037 用嘅 PSA10：generation=db3308_b0cb6e76228b4a99 · 1368 張 · 2026-08-13T14:06:55.448Z
- BOX：307 SKU／275 有價／307 有圖
- 舊本機 seed db3308_d62fe3d820af90cc（1322 張）唔係 037 底座，已用 live 份蓋過工作樹

## 點 fallback 返 036

1. 唔 deploy 037；或
2. 已 deploy 之後：還原 FE 去 FE03（冇 /box nav）、image 唔 COPY box-subset.json、loader 唔掛 sidecar。PSA10 seed 唔使改——佢從來都係 036 嗰份。

## FE04 內頁（2026-08-14）

- 圖：跟主站 `.detail-art`。桌面 sticky／手機 relative，唔另加 lightbox（主站已刪 CardImageModal）。
- Metrics：askFloor 用 `wide-metric`，冇 ask 都唔會穿格。
- `/box/[id]` StructuredData：Product + BreadcrumbList。
- 窄屏 `.box-detail-art img` padding 20px，唔俾 `.detail-art img` 16px 冚。
- Docker：`.dockerignore` 要留 `box-subset.json`；盒圖走 `market-assets` volume。

## Live 推送（2026-08-14 DADDY 批准）

內頁 4 項 QA 完成、`tsc --noEmit` 過。正式推上去係 **origin/main**（唔係 `rebuild/036-foundation`、唔係實驗樹）：

1. `git fetch origin`
2. 由 `origin/main` 開 `release/037-fe04-box` worktree
3. Overlay FE + `box-subset.json` + 新盒圖 webp + daily-sync／dockerignore
4. **唔** commit `seed-snapshot.json`（已經係 live `db3308_b0cb6e76228b4a99`）
5. commit `release: CARDZ 037 FE04 add BOX sidecar [deploy]` 再 `git push origin HEAD:main`

每日 PSA10 價鏈自動成功閘：連續兩個 JST 日排程自己對到 live 先算。人手 catch-up 唔計。見 [DAILY_CHAIN_AUTONOMY.md](DAILY_CHAIN_AUTONOMY.md)。2026-08-14 未 proven。

**2026-08-14 live 已確認。** commit `3aef760a`。`https://app.cardzmarketcap.com/api/health`：`product=037`、`presentation=FE04`、`box.path=/box` · 307／275／307、PSA10 `generation` 仍然 `db3308_b0cb6e76228b4a99`、`cards=1368`。`/` 200、`/box` 200、`/box/ptcg-en-lc-booster-box-std` 200、`/sealed` 308 → `/box`。Receipt：`data/public/037-fe04-receipt.json`。

## 不准

- 把 BOX 寫入 seed-snapshot.json 或 pass content hash
- 當 037 係新一輪 identity rebuild
- 用 cardz-market-cap/ 實驗樹 bake 或 [deploy]
- 公開路徑／nav／canonical 寫 Sealed
- 由 `rebuild/036-foundation` 直接 push 當 live（local main 落後 origin/main 205）
