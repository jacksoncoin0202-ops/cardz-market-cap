# TAG Grading Population 數據管道

> 來源：`api.taggrading.com`（TAG Portal 公開 pop report API，已逆向）
> 逆向報告 + 完整端點表：`reverse-skill\work\tag-grading\TAG_GRADING_REVERSE.md`
> 對照文件：`SNK_MARKET_DATA.md`（SNKRDUNK 價格管道）

## 數據性質

| | SNKRDUNK（現有） | TAG Grading（本管道） |
|---|---|---|
| 維度 | 日本二級市場**價格** | 第三方評級**供應量（pop）+ 品相** |
| 粒度 | 每卡每日 | 每卡每 grade 存量快照 + 每 cert 1000 分制評分 |
| 更新 | 每日 | 建議每週（pop 變化慢） |

## 認證機制（逆向結果）

兩條 passphrase 硬編碼喺公開 JS bundle（`main.3f8d7f36.chunk.bundle.js` module 155），屬混淆非秘密：

```
x-tag-key = sha256_hex( SALT + ":" + sorted(非空query值).join(",") )
SALT      = "TZY0j76MKF1AA0QK0ppAGySAaCNgKG"
回應 body = "<ivHex>:<cipherHex>"，AES-256-CBC
key       = sha256("K5ucGQIf7vigW9ITOXLak5MjSIxxsgixqj")
必要 header：Accept: application/json, text/plain, */* + Origin: https://my.taggrading.com
```

Rate limit：實測 6.6 req/s 無 429；管道預設 0.15s delay 保守行走。
若 TAG 換 key（要重新 deploy bundle），重開逆向報告第 2 節照做一次即可。

## 用法

```powershell
# 全量回填（約 10 分鐘，26,145 卡行 / 784k 張 graded，可斷點續跑）
python pipelines\tag_pop_data.py --dump --out data\tag\pops_pokemon.jsonl
```

## SNK ↔ TAG join（已驗證）

join key = `cardNumber`（SNK name 入面嘅 `[SV1a 080/073]` → TAG `080/073`）+ set 英文名
（SNK name `(...Enhanced Expansion Pack "Triplet Beat")` → TAG `setName="Triplet Beat"`）。
實測 Magikarp AR[SV1a 080/073] 命中 TAG 日文版 pop：`{"9":56,"10":428,"10P":16,...}`。
注意 TAG 同一 cardNumber 會出多個語言版（Japanese / Traditional Chinese / Simplified Chinese），
join 時要加 `brandName` 過濾（SNK 主要係日文版 → `brandName` 尾隨 `Japanese`）。

## 數據 schema（JSONL 每行一卡）

```json
{
  "category": "Pokémon",
  "year": "2021",
  "brandName": "Pokémon Sword & Shield",
  "setName": "Battle Styles",
  "cardName": "Kricketune V",
  "cardNumber": "006/163",
  "variation": "",
  "grades": {"8": 1, "9": 3, "10": 12, "10P": 1}
}
```

grade key：`"1"`–`"10"`、半分 `"8.5"`、`"10P"`（Pristine）、`"VA"`（Authentic）。
600 卡對照 join key：`(setName, cardNumber)`；TAG 日文 brand 命名（`P.M. Pokémon Japanese` 等）同 SNK 嘅日文 set 名要靠 `source_crosswalk.py` 擴充 mapping。

## 更深一層（可選，未自動化）

- `/pops/card/rank` → 每張卡全部 graded copy 嘅 cert 編號 + 1000 分制 `tagGrade` + `dateGraded`（leaderboard）
- `/graded-cards/public/detail/{cert}` / `score/{cert}` → 逐 cert 完整鑑定（centering DTE、defects 座標、hi-res slab 圖 CloudFront URL）
- 用處：600 卡可以建「TAG 評級 census」，計每週新增評級速度（grade velocity）做 scarcity 動能指標
