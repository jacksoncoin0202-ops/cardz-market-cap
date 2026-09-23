# Sealed（原盒）Ops Runbook

> **2026-08-25 已接返現行 authority：** 下面所有命令由 `cardz-market-cap-fe-db-20260805` 執行；BOX 產出已入 V2 鏈（`sealed_daily.py` in-chain 產 `/box` sidecar）。現狀睇 [../PROJECT_STATE.md](../PROJECT_STATE.md) §5。
> **2026-09-23：** 呢份文件以前寫嘅 `operator_control.py sealed-*` 命令喺呢棵樹從來冇接過；V2 切換又封存咗 P6 嘅 collect lane，冇嘢頂上，所以 BOX 價由 08-20 企到 09-23。而家操作命令統一行 `pipelines/sealed_daily.py`（測試：`scripts/test_sealed_daily_cli.py`）。
> 2026-08-14 起。Sealed 係 PSA10 單卡以外嘅平行線：自己嘅 identity／freeze／observations／composer／snapshot block，唔掂 GemRate gate、universe lock、`latest_prices()`、Top100 pass contract。
> **2026-08-15：** 公開路徑 `/box`。Live BOX overlay 喺 fe-db／037 sidecar。呢棵樹只養 sidecar，**唔准** merge 入 PSA10 seed／pass／`[deploy]`。

## 日常命令（Windows）

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805
$PY = 'C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe'   # P6 一向用 Windows Python 行 sealed collect（PC 經 9333）

& $PY -X utf8 pipelines\sealed_daily.py status
& $PY -X utf8 pipelines\sealed_daily.py gaps --limit 50
& $PY -X utf8 -u pipelines\sealed_daily.py refresh   # 每日：incr PC → SNK → Yahoo（只刷 accepted bind）+ compose
& $PY -X utf8 -u pipelines\sealed_daily.py scan      # 每週：release 到期 / upcoming / 未 bind + SNK/PC discovery
```

前置：PriceCharting 需要 CDP Chrome —— `powershell -NoProfile -File scripts/ensure_chrome_cdp.ps1 -Port 9333 -UserDataDir $env:LOCALAPPDATA\cardz-chrome-cdp-9333`（Windows 側）。

- `refresh`／`stock`／`accept-binding`／`scan`／`release`／`add-product`／`set-product` 攞 operator e2e lease；V2 行緊會被拒，唔好夾硬。`collect`／`compose`／`export`／`status`／`gaps` 唔攞 lease，因為 V2 box stage 攞住 lease 行 compose + export 做 child。
- V2 box stage 每日自己 compose + export `/box`，但唔會 collect：`refresh` 要喺 V2（11:00 JST）之前或者 17:00 之後跑，下一轉 V2 先會出街。
- `sealed_collect` 全部 fetch 失敗都 exit 0，所以 `refresh` 逐個 adapter 睇今次寫嘅 report：exit≠0、冇今次嘅 report、attempted>0 但 ok=0 都算紅。收據 `refresh-receipt.json` 嘅 `red` 唔係空就 exit 2。PC 紅唔擋 SNK／Yahoo／compose。
- `refresh` 只刷 accepted bind（同 P6 一樣）；candidate 要先 accept。product export 只出 accepted source freeze。唔好塞入 PSA10 `daily --refresh --pass`。

## 新貨上架 SOP

新盒由官方公佈到上 `/box`，全部喺呢棵樹用 `sealed_daily.py` 做；catalog 每次改動（連 actor／`--note` 來源）喺 commit 之前寫入 `catalog-changes.jsonl`。

1. **搵新貨**：`scan`（每週）列 release 到期／60 日內 upcoming／active 但未 bind，同時行 SNK／PC discover。官方清單：[onepiece-cardgame.com/products](https://www.onepiece-cardgame.com/products/)、[pokemon-card.com/products](https://www.pokemon-card.com/products/)、pokemon.com news。
2. **入 catalog**：`add-product --game optcg --lang jp --set OP-18 --name-en "..." --name-jp "..." --release 2026-11 --packs 24 --official-url https://... --note "<官方來源 URL>"`。一律入 `unreleased`；group 同 kind 要已經存在，打錯字會被拒；`--official-url` 同時變 official hint，俾 image harvest 用。未有 bind 嘅 SKU 唔會出 product export，所以加咗都未上 FE。
3. **事實錯就改**：`set-product --sku <slug> --release 2025-10 --note "<官方來源>"`，只寫有分別嘅欄。JP 盒改 `--name-jp` 會順手將由舊名砌出嚟嘅 Yahoo query hint 搬去新名（Yahoo QC 要標題有自己個名；OP-17 JP 多咗個「達」，Yahoo 成交 0 條入到）。`status` 唔係事實欄，要改狀態用 `release`。
4. **到月上架**：`release --sku <slug> --note "..."`，只准 unreleased 而且發售月已到；之後 Yahoo 自動 query 同 price triage 先會包埋佢。
5. **搵 source**：`sealed_bind_resolve.py --source snkrdunk`、`--source pricecharting`（9333），再 `sealed_fullname_backfill.py`、`sealed_image_harvest.py --refresh`、`sealed_price_triage.py`（見下面 Bind → Freeze）。
6. **人手 accept**：新盒逐隻對證據 accept（`accept-binding --sku ... --kind source/image`）。**唔准 bulk accept 新貨；`ptcg-jp` 一律逐隻**：SNK 舊 JP 盒 candidate 試過黐到 DIESEL T 恤同第二個系列。
7. **第一次全量**：`stock`（唔准用 incr 頂第一次）。收據 blocked＝件 source 貨同另一隻 SKU 共用，要人手裁決先再 stock。
8. **之後日常**：V2 11:00 box stage compose + export `/box`；每日 `refresh`（V2 前或 17:00 後）刷價。

## 架構一覽

- Identity：`catalog_sealed_product`（sku = `{game}:{lang}:{set}:{product}:{print}`，slug 做 FE id）＋ `catalog_sealed_source_identity`（exact binds）＋ `catalog_sealed_source_hint`（search/official/image bootstrap URLs）。
- Freeze：`operator_sealed_binding_freeze`（identity/source/image；accepted 唔 batch reopen）。
- Observations：`market_sealed_price_observation`（kind = market/ask/buyback）、`market_sealed_sale_observation`（`(source, lot_id)` dedupe）、`market_sealed_daily_aggregate`（composer 輸出，FE historyDaily 讀呢度）、`market_sealed_source_warehouse`（pull-first 原始）。
- 圖：`market_sealed_image_asset`＋ webp 落 `data/public/market-assets/{sha}.webp`（+`_200`/`_600`）。
- Adapters（checkpoint 喺 `market_ingest_checkpoint`，SLA 36h）：`sealed_pc`（PC Ungraded 史＋eBay sold rows）、`sealed_snk`（ask＋日線＋成交）、`sealed_yahoo`（落札）、`sealed_ebay`（import-only）、`sealed_mercari`（暫無 transport，fail closed）。

## 價格紀律（sealed Polaris）

- EN：eBay sold 30d median（min 3、trim >2x / <1/2.5x）→ PC market（≤45d）→ SNK ask（≤7d）。
- JP：SNK+Yahoo(+Mercari) sold 30d median → SNK market 線 → PC → SNK ask；sold vs ask 背離 >1.75x 退守 ask 並記 guard。
- sold／market／ask 永不溝埋；rejected/outlier rows 全部留底帶 reason（store-all + mark）。

## Bind → Freeze SOP（人手閘）

```powershell
# 同上 $PY（Windows）：PC discover／resolve／image 全部經 9333。
# Discovery：先對已有網站數據／手冊 listing，search 只補 leftover。scan 會攞 lease 行頭兩條；直接行就自己避開 V2 11:00–17:00
& $PY -X utf8 pipelines\sealed_snk_discover.py          # harvest jsonl first, HTML search leftovers
& $PY -X utf8 pipelines\sealed_pc_discover.py           # category → console table → bind; no search
& $PY -X utf8 pipelines\sealed_fullname_backfill.py
& $PY -X utf8 pipelines\sealed_image_harvest.py --refresh
& $PY -X utf8 pipelines\sealed_price_triage.py

& $PY -X utf8 pipelines\sealed_bind_resolve.py --source snkrdunk
& $PY -X utf8 pipelines\sealed_bind_resolve.py --source pricecharting
# 睇 data/runtime/operator/sealed/bind-resolve-receipt.json 嘅 note（名／overlap／langOk）
& $PY -X utf8 pipelines\sealed_daily.py accept-binding --sku optcg-en-op-09-booster-box-std --kind source --source-code pricecharting
# 核清一批舊貨先可以 bulk（新貨、ptcg-jp 一律逐隻）。件 source 貨已經綁住另一隻 SKU（任何 SNK 寫法、未 reject）就拒收：
# 其餘照 commit，但 exit≠0 並列出 heldBy；先 reject 錯嗰條 bind 再 accept。
& $PY -X utf8 pipelines\sealed_daily.py accept-binding --all-resolved --kind source --source-code snkrdunk --group optcg-en
& $PY -X utf8 pipelines\sealed_daily.py accept-binding --all-resolved --kind image
# accept 完第一次一定要全量 stock，唔准靠 incr 頂（incr 只揀已經有數嘅 SKU）：
& $PY -X utf8 -u pipelines\sealed_daily.py stock
# stock 唔會拉同另一隻 SKU 共用緊同一件 source 貨嘅 SKU（SNK 剷走 trading-cards:/apparels: 前綴，
# 767625 兩個寫法係同一件貨），收據 blocked 轉紅；人手裁決邊隻 SKU 擁有件貨、reject 錯嗰條 bind 之後先 stock。
# incr 照刷已經有數嘅 SKU，收據 shared 列出共用。

# Live-bar qualify（獨立線，唔掂 PSA10 pass／promote／GitHub live；自己唔攞 lease，唔好喺 V2 11:00–17:00 跑）
# --apply 只 reject 垃圾 candidate，永不 accept／freeze；收據 acceptSample 只係建議，逐隻睇完先 accept-binding --sku。
& $PY -X utf8 pipelines\sealed_live_qualify.py
& $PY -X utf8 pipelines\sealed_live_qualify.py --apply
```

教訓實例（2026-08-14）：Kimi 條 `apparel-groups:450` link 其實係 FEAR OF GOD Polo，resolve 靠 master 名 auto-reject 咗；錯 bind 拉咗嘅 typed rows 要 purge。**呢個就係 human accept 閘存在嘅原因。**

教訓實例（2026-09-23）：DB 全部 451 條 sealed source freeze 都係 2026-08-14 `sealed_live_qualify.py --apply` 自動 accept，冇一條經人手；`/box` 出咗 Game Boy jukebox 喺 BW2、Volt Tackle 盒喺 S1W、JP 盒頂 EN SKU，六件 SNK 貨用 `trading-cards:`／`apparels:` 兩個寫法各自綁咗兩隻 SKU。live-qualify 而家唔再 accept；discover 同 accept 都認同一件貨嘅三個寫法。同日 `scan` 嘅 PC inventory ingest（行晒全部 SKU）將 193 條 accepted PC bind 降返 candidate 兼覆寫 note；而家 discover 撞到 exact row 一律唔郁。

## Product vs operator surface

- `sealed_daily.py export --output <path>`（唔加 flag）＝ product 紀律：**只出有 accepted source freeze 嘅 SKU**。
- `--include-candidates` ＝ engineering／operator FE surface（dual-mode operator loader 用呢個）。
- 併入 product snapshot（bake 前）：

```bash
node scripts/merge_sealed_snapshot.mjs \
  --snapshot data/runtime/operator/product-subset-snapshot.json \
  --sealed   data/runtime/operator/sealed/sealed-subset-snapshot.json \
  --output   data/runtime/operator/product-subset-snapshot.sealed.json
```

merge 會校驗輸入 hash 再重算 `generation.contentSha256`（算法同 `packages/market-data/src/validate.ts` 一致）。Sealed 唔綁入現有 762/five-adapter pass gate；照舊 DADDY visual approve 先 commit/deploy。

## FE 預覽

```powershell
$env:MARKET_DATA_SNAPSHOT_PATH="<merged-or-fixture>.json"; $env:MARKET_DATA_ALLOW_DEMO="true"
$env:CARDZ_RUNTIME="node"; $env:MARKET_DATA_ASSETS_PATH="data\public\market-assets"
npm run dev -w @cardz/web    # /sealed + /sealed/[id]
```

## Artifacts

`data/runtime/operator/sealed/`：`status.json`、`gaps.json`、`attention.json`、`daily_summary.json`、`compose-receipt.json`、`ingest-receipt.json`、`bind-resolve-receipt.json`、`image-harvest-receipt.json`、`backfill-receipt.json`、`sealed-subset-snapshot.json`、`collect/last_{stock,incr}.json`、`refresh-receipt.json`、`stock-receipt.json`、`catalog-changes.jsonl`（`release`／`add-product`／`set-product` 每次改 catalog 嘅 actor／note／前後值，commit 之前寫）。

## 已知待調（tuning backlog）

- OP-01 W1/W2 嘅 Yahoo query 係英文（catalog 原樣），日拍搵唔到 → 換 JP query hint（ロマンスドーン＋初版/再販 heuristics）。
- 個別 JP SKU sold 混源後波動大（例：S4 −91%）→ 檢查 yahoo accepted rows、需要時收緊 QC pattern。
- SNK EN/JP 版本確認：bind note 有 `snkName`，accept 前人眼核（Kimi 已標「可能日版」嗰批）。
- `sealed_mercari` 未有 transport；`sealed_ebay` 直連只食 export file（PC 頁已供 eBay sold rows）。
