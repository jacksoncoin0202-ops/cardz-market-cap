# CARDZ Image Picker（人類揀唯一卡圖）

## 政策（SNK / G10 預設 OK）

| 情況 | 處理 |
|------|------|
| 單圖（任何來源） | **auto** — 冇 Double |
| 多圖但全部 SNK / G10 / market-assets（SNK 血統） | **auto** — 物認 100% OK |
| 多圖（≥2 distinct SHA）且含 **非 SNK·G10**（如 Limitless） | **人類佇列** |

G10 圖/價多來自 SNK + eBay 工具鏈，與 SNK 一齊視為 trusted identity。  
Auto 結果寫入 `public/data/auto_selections.json`；人類只處理爭議 Double。

## 本機

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
# 1) 建 worklist + 複製 candidate 圖
python -X utf8 tools\image-picker\build_worklist.py

# 2) 靜態預覽（無多人同步）
cd tools\image-picker\public
# 任意 static server，例：
npx --yes serve -l 4173 .
Start-Process http://127.0.0.1:4173
```

快捷鍵：**A–E** 揀圖 · **N/P** 下/上一張 · **S** skip · **R** 重拉遠端狀態。

## Cloudflare 部署（多人 KV 狀態）

```powershell
cd tools\image-picker
npx wrangler kv namespace create SELECTIONS_KV
# 把回傳 id 填入 wrangler.toml 的 id / preview_id

npx wrangler pages deploy public --project-name cardz-image-picker
# 或者 Worker+assets：
npx wrangler deploy
```

部署後：
1. 開網站，填「操作者」名  
2. 揀圖會 `PUT /api/selections` 合併去 KV  
3. 其他人開同一站會自動 merge 較新嘅選擇  

## 輸出

- 瀏覽器 **Export JSON** → `cardz-image-selections-YYYY-MM-DD.json`  
- 結構：`selections[variantId] = { contentSha256, assetId, provenance, actor, at }`  
- 之後 pipeline 讀呢份表 → 寫 `market_image_qc` 唯一 canonical / 重建 `image-qc.json`

## 梵高比卡超 rank 說明

- DB index 最新：**#3**（市值 ~$145M），**冇被 QC 剔除**  
- **#1 Venusaur / #2 Blastoise** 用咗 **ebay identity = derived** 嘅離譜 G10 價（$42k 等）— 已喺 `market_alerts` 加 **exact-only identity 閘**  
- 前端 preview 之前排到 Umbreon #1 係因為 **錯誤圖包 / 不完整 export**，唔係梵高被 ban  

## 圖包注意

- `plan_a_qc3` bake 圖 **錯**（名/卡面對唔上）— 唔好再當正確源  
- 本 picker 用 **DB asset + 本機 market-assets** 多候選，人類揀完先係唯一真理表  
