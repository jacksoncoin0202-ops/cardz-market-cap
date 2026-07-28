# OP 卡圖 A/B/C — 點做

> 缺口名單：[`temp/op_image_gaps.json`](../temp/op_image_gaps.json) · 寶藏 link：STATE §F

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
$env:CARDZ_DB_HOST = "127.0.0.1"
```

---

## 1. 一次做足定義

| 層 | 要有 |
|---|---|
| **A** | `market_image_asset` raw_front（variant + sha） |
| **B** | `data/public/market-assets/{sha}.webp` **同** `apps/web/public/market-assets/` |
| **C** | `market_image_qc` public_allowed=1 · raw_front_confirmed=1 · 可接受 semantic |

---

## 2. 命令次序

```powershell
# 1) 全池 / OP 缺圖 — TCGplayer
python -X utf8 pipelines\qualified_pool_operator.py fill-images --write

# 2) Limitless CDN（OP）
python -X utf8 pipelines\op_limitless_images.py --write --only-missing
# URL: https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/one-piece/{SET}/{SET}-{NUM}_EN.webp

# 3) A/B/C 補齊 + 缺檔 refetch
python -X utf8 pipelines\ensure_image_abc.py --write --refetch-missing
```

**再唔中**：

1. 開 Drive ×3 / OP.gg（STATE §F）  
2. 按 collector 落檔 → `data/private/op-image-inbox/`  
3. `store_face_art_image` + QC  

---

## 3. 瀑布（嚴格）

```text
1) tcgplayerId → CDN
2) TPL imageUrl
3) TCGplayer search（collector+名 fail-closed）
4) Limitless OP CDN
5) OP.gg
6) Drive ×3
7) SNK product 圖
8) G10 / 本地 hash 回收
```

對唔上 collector／角色名 → 下源，**唔 first-hit**。

---

## 4. 寶藏 URL

| | |
|---|---|
| D1 | https://drive.google.com/drive/folders/12Y9o5_LzXtAry6tw042j7Fgk4eyyrmfj |
| D2 | https://drive.google.com/drive/u/0/folders/1w18aBFle3uMOSiD78B5O1sxTn6WI1hVq |
| D3 | https://drive.google.com/drive/folders/13HwWKRkiwZTarPbH4W0A2WhYqYkDWyxr |
| Limitless | https://onepiece.limitlesstcg.com/cards |
| OP.gg | https://onepiece.gg/cards/ |
