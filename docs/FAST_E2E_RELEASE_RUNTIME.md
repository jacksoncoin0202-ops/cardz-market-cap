# CARDZ Market Cap 最短 E2E Release Runtime

## 唯一正解

由 WSL 單一 owner 執行：

```bash
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap
PY=/home/jackson0202/cardz-market-cap/.venv-backend/bin/python
RELEASE=/mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap-release-20260804
```

- Source authority：目前較新、包含最新修改意圖嘅 `cardz-market-cap` source tree。
- 修改日期只用嚟辨認邊份 source 較新；一進 pass 就以 `frontendBundle.sha256` 鎖死，之後唔再靠日期猜版本。
- Product frontend policy：`product-top100-no-graders-v1`。`Graders` route/nav、`Grading Pulse`、grader share、card-detail multi-grader panel 全部禁止進 release。
- 圖片 policy：`displayed-top100-snk-public-exact-first-v1`。**以 pass 當刻重新計算嘅實際 market-cap Top 100 為準**；有 exact、public-approved、raw-front SNK 圖就必須用 SNK，冇合資格 SNK 先保留原 accepted freeze 圖。101+ 不受今次 override 影響。
- 只可有一個 collector／Chrome／DB writer。監視器只讀 receipt/checkpoint，唔開第二個 runner。

## 一次成功流程

### 1. SNK 圖盤點一次

```bash
"$PY" -X utf8 pipelines/operator_control.py snk-image-priority-status
```

呢一步只報告實際顯示 Top 100 嘅 SNK eligible／selected／switch 數，唔寫 DB、唔改 snapshot。

### 2. Refresh + pass 一次

正常每日正式路徑：

```bash
"$PY" -X utf8 pipelines/operator_control.py daily --refresh --pass
```

只重生 presentation contract、而已完成五個 adapter refresh 時：

```bash
"$PY" -X utf8 pipelines/operator_control.py daily --refresh-report data/runtime/operator/pass_receipt.json --pass
```

`--refresh-report` 只接受五個 required adapters 全部成功且 receipt freshness 全部 `slaOk=true`；同一個命令會先將舊 receipt 以 SHA 命名保存，再寫新 pass。新 receipt 必須同時綁定：

- 762-card snapshot generation/hash；
- Top-100 每卡 SNK eligibility、selected source、selected SHA；
- 最新 frontend bundle policy/hash/file count；
- 762 active、776 backlog、`gapCards=0`；
- 五個 adapter refresh evidence。

Pass 一入場先做 frontend bundle preflight；未符合 production-source boundary 就即刻非零停止，唔會先跑 collector 或 762-card DB export。

### 3. Promotion 一次

```bash
"$PY" -X utf8 pipelines/operator_control.py promote-product-subset \
  --snapshot data/runtime/operator/product-subset-snapshot.json \
  --receipt data/runtime/operator/pass_receipt.json \
  --output data/runtime/operator/promoted-product-snapshot.json \
  --expected-cards 762 \
  --expected-backlog 776
```

Promotion 會重新比對 SNK decisions、snapshot image SHA、當前 frontend bundle hash；只產生 production candidate，唔 deploy。

### 4. Materialize 一次

```bash
"$PY" -X utf8 scripts/materialize_snapshot_assets.py \
  --snapshot data/runtime/operator/promoted-product-snapshot.json \
  --assets data/public/market-assets \
  --output-snapshot data/runtime/operator/materialized-product-snapshot.json \
  --receipt data/runtime/operator/asset-materialization-receipt.json
```

每個 snapshot image 必須有 hash-matched base、`_200`、`_600`。

### 5. Bake 一次

```bash
"$PY" -X utf8 scripts/bake_tonight_release.py \
  --snapshot data/runtime/operator/materialized-product-snapshot.json \
  --source-assets data/public/market-assets \
  --release-root "$RELEASE" \
  --frontend-receipt data/runtime/operator/frontend-bundle-receipt.json \
  --expected-cards 762
```

Bake 先將 pass-bound 完整 frontend bundle 同必要 runtime 同步到 clean worktree，移除受管範圍內舊檔，再只帶 snapshot 引用嘅 image triplets。

### 6. Validator 一次

```bash
"$PY" -X utf8 scripts/validate_tonight_release.py \
  --snapshot data/runtime/operator/materialized-product-snapshot.json \
  --receipt data/runtime/operator/pass_receipt.json \
  --assets-root "$RELEASE/data/public/market-assets" \
  --release-root "$RELEASE" \
  --frontend-receipt data/runtime/operator/frontend-bundle-receipt.json \
  --asset-materialization-receipt data/runtime/operator/asset-materialization-receipt.json \
  --expected-cards 762 \
  --expected-backlog 776
```

Validator 同一命令檢查 DB、五 adapter freshness、762/776、SNK Top-100 DB decision、snapshot/receipt/hash、clean frontend bundle、全部圖片 triplets。

### 7. Production build 一次

Windows PowerShell，clean release root：

```powershell
npm run build
```

### 8. 3800 preview + health 一次

```powershell
npm --workspace @cardz/web run start -- --hostname 127.0.0.1 --port 3800
Invoke-RestMethod http://127.0.0.1:3800/api/health
```

Health generation/cards/hash 必須等於 pass-approved generation。DADDY 畫面批准前禁止 commit、push、webhook、live deploy。

### 9. DADDY 批准後

只 commit clean worktree 已預覽 bytes；commit message 包含 `[deploy]`，只 push `main`。Webhook 200 後讀 live health 一次；generation/cards/hash 完全一致先叫已上線。

## 唔再犯

- 唔用 universe 舊 `market_rank` 決定 SNK Top 100；一定用 pass 當刻 `price × PSA10 POP` 排序。
- 唔逐份揀 frontend 檔搬去 release；pass 綁完整受管 bundle，bake 做 deterministic mirror。
- Frontend bundle 只收 production source；`.next`／`.open-next`／Wrangler canary／preview／runtime／temp／market-assets generated copies、`.env*`、`*.tsbuildinfo`、實驗 HTML 全部排除，clean release 同步時清走。
- 唔用 `value or -1` 驗合法 0；先 parse，再比較。
- 唔直接 bake promoted snapshot；一定先 materialize。
- 唔開第二個 Chrome／collector 睇進度。
- 已消耗嘅 inventory、pass、promotion、materialize、bake、validator、build、health 唔自行再跑。
