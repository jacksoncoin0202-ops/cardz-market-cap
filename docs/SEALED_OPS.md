# Sealed（原盒）Ops Runbook

> **⚠ 部分過時（2026-08-24 標記）：** 下面啲命令仲指住 read-only 舊樹 `cardz-market-cap`（AGENTS 規矩 5 唔准寫）；BOX 產出已入 V2 鏈（`sealed_daily.py` in-chain 產 `/box` sidecar）。人手 Bind→Freeze SOP 嗰段仍然有效。現狀睇 [../PROJECT_STATE.md](../PROJECT_STATE.md) §2。
> 2026-08-14 起。Sealed 係 PSA10 單卡以外嘅平行線：自己嘅 identity／freeze／observations／composer／snapshot block，唔掂 GemRate gate、universe lock、`latest_prices()`、Top100 pass contract。
> **2026-08-15：** 公開路徑 `/box`。Live BOX overlay 喺 fe-db／037 sidecar。呢棵樹只養 sidecar，**唔准** merge 入 PSA10 seed／pass／`[deploy]`。

## 日常命令（WSL）

```bash
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap
PY=/home/jackson0202/cardz-market-cap/.venv-backend/bin/python

$PY -X utf8 pipelines/operator_control.py sealed-status
$PY -X utf8 pipelines/operator_control.py sealed-gaps
$PY -X utf8 pipelines/operator_control.py sealed-daily --refresh   # 每日：incr 3 adapters（含 candidate）+ compose + status + gaps + export
$PY -X utf8 pipelines/operator_control.py sealed-scan              # 每週：release 到期 / upcoming / 未 bind
```

前置：PriceCharting 需要 CDP Chrome —— `powershell -NoProfile -File scripts/ensure_chrome_cdp.ps1 -Port 9333 -UserDataDir $env:LOCALAPPDATA\cardz-chrome-cdp-9333`（Windows 側）。`sealed-daily --refresh` 會 `--allow-candidates` 刷新未 freeze 嘅 bind；product export 仍然只出 accepted freeze。PC incr 失敗唔擋 SNK／Yahoo／compose。唔好塞入 PSA10 `daily --refresh --pass`。

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

```bash
# Discovery：先對已有網站數據／手冊 listing，search 只補 leftover
$PY -X utf8 pipelines/sealed_snk_discover.py          # harvest jsonl first, HTML search leftovers
$PY -X utf8 pipelines/sealed_pc_discover.py           # category → console table → bind; no search
$PY -X utf8 pipelines/sealed_fullname_backfill.py
$PY -X utf8 pipelines/sealed_image_harvest.py --refresh
$PY -X utf8 pipelines/sealed_price_triage.py

$PY -X utf8 pipelines/sealed_bind_resolve.py --source snkrdunk
$PY -X utf8 pipelines/sealed_bind_resolve.py --source pricecharting
# 睇 data/runtime/operator/sealed/bind-resolve-receipt.json 嘅 note（名／overlap／langOk）
$PY -X utf8 pipelines/operator_control.py sealed-accept-binding --sku optcg-en-op-09-booster-box-std --kind source --source-code pricecharting
# 核清一批之後可以 bulk：
$PY -X utf8 pipelines/operator_control.py sealed-accept-binding --all-resolved --kind source --source-code snkrdunk --group optcg-en
$PY -X utf8 pipelines/operator_control.py sealed-accept-binding --all-resolved --kind image

# Live-bar qualify（獨立線，唔掂 PSA10 pass／promote／GitHub live）
$PY -X utf8 pipelines/operator_control.py sealed-live-qualify
$PY -X utf8 pipelines/operator_control.py sealed-live-qualify --apply
```

教訓實例（2026-08-14）：Kimi 條 `apparel-groups:450` link 其實係 FEAR OF GOD Polo，resolve 靠 master 名 auto-reject 咗；錯 bind 拉咗嘅 typed rows 要 purge。**呢個就係 human accept 閘存在嘅原因。**

## Product vs operator surface

- `export-sealed-subset`（唔加 flag）＝ product 紀律：**只出有 accepted source freeze 嘅 SKU**。
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

`data/runtime/operator/sealed/`：`status.json`、`gaps.json`、`attention.json`、`daily_summary.json`、`compose-receipt.json`、`ingest-receipt.json`、`bind-resolve-receipt.json`、`image-harvest-receipt.json`、`backfill-receipt.json`、`sealed-subset-snapshot.json`、`collect/last_{stock,incr}.json`。

## 已知待調（tuning backlog）

- OP-01 W1/W2 嘅 Yahoo query 係英文（catalog 原樣），日拍搵唔到 → 換 JP query hint（ロマンスドーン＋初版/再販 heuristics）。
- 個別 JP SKU sold 混源後波動大（例：S4 −91%）→ 檢查 yahoo accepted rows、需要時收緊 QC pattern。
- SNK EN/JP 版本確認：bind note 有 `snkName`，accept 前人眼核（Kimi 已標「可能日版」嗰批）。
- `sealed_mercari` 未有 transport；`sealed_ebay` 直連只食 export file（PC 頁已供 eBay sold rows）。
