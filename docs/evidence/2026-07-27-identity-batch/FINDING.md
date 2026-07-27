# catalog_variant 重複組收斂：29 組／68 行入 catalog_printing_identity，其中 10 組根本唔係重複

**Measured** 2026-07-27 00:30–01:05 local。寫入 commit 之後即刻用獨立唯讀路徑
（[scripts/ro_sql.py](../../../scripts/ro_sql.py)）重量過一次，再跑第二次 `--write`
驗 idempotency。
**Branch** `master`，working tree 未 commit。
**Access** 寫，但只寫一張表：`catalog_printing_identity`。冇 UPDATE／DELETE 任何
`catalog_variant` 或 `market_*` fact 表。

## Conclusion

`catalog_printing_identity` 由 0 行變 68 行，覆蓋 `catalog_variant` 全部 29 個重複組——
但當中 **只有 19 組（39 行）係真重複，另外 10 組（29 行）根本唔係同一張卡**，係
`collector_number` 缺失或者版本後綴造成嘅假重複，所以標 `review` 而**冇**選 canonical。

## 三個要記住嘅發現

1. **舊報告嘅 24 組／52 行係 1,590 行時嘅快照，今日實測 29 組／68 行。**
   口徑冇變（照用 `docs/DB_INVENTORY_20260726.md` §4.2 嘅主口徑），變嘅係 `catalog_variant`
   由 1,590 行長到 1,705 行，多入嘅卡帶埋新重複組。所以「24 組」唔係錯，係過期——
   呢個正好係要即場重量、唔准齋信舊文嘅理由。

2. **10 組係假重複，merge 落去會寫錯數據。** 用 `(tcg_code, card_language, set_name,
   collector_number)` 分組，`collector_number` 一缺就會將完全唔同嘅卡撞埋一組：
   `pokemon/ja Expansion Pack #Unknown` 一組四個成員係 Pikachu／Charizard／Mewtwo／
   Blastoise；`Rocket Gang #Unknown` 係四張 Dark 系；`Celebrations Classic Collection #15`
   係 Here Comes Team Rocket!／Venusaur／Rocket's Zapdos 三張唔同卡撞同一個 collector
   number。另一類係同號唔同版：`OP13-119` 有 Portgas D. Ace／(Error)／(Super)，
   `OP01-016` 有 Nami／Nami (Errata)，`OP09-004 ja` 有 Shanks Gold Background 同 Silver
   Background。呢兩類都唔應該 merge，所以全部成員標 `review`，一個 canonical 都唔選——
   選 canonical 等於喺 canonical 層宣告 Charizard 係 Pikachu 嘅副本。

3. **張表結構上表達唔到「N 個 variant 係同一個 printing」。**
   `catalog_printing_identity` 同時有 `PRIMARY KEY (variant_id)` 同
   `UNIQUE KEY (canonical_printing_sha256)`，兩個約束一齊擺就係「一 variant 一 printing
   key、而且 key 唔准撞」。所以 UNIQUE 唔係阻礙，佢係**探測器**。收斂做法：真重複組
   選一個 canonical 成員拿純 7-part hash，其餘成員拿 `<純key>|variant:<id>` salted hash
   並標 `duplicate`／`review`。組員身份靠七條 identity 欄位 join 返（同組成員值一樣），
   唔靠 hash；`WHERE identity_status='canonical'` 就係每個真重複組一行。

## 數字

**寫前／寫後 exact `COUNT(*)`**（`information_schema.TABLE_ROWS` 全程冇用，佢喺呢個 DB
報 1,590，係錯嘅）：

| | 寫前 | 寫後 | 紅線 |
|---|---:|---:|---|
| `catalog_printing_identity` | **0** | **68** | 唯一准寫嘅表 |
| `catalog_variant` | **1705** | **1705** | 一行都唔准變 |
| `catalog_variant` distinct `opaque_id` | 1705 | 1705 | 一個 opaque_id 都唔准變 |

<!--@verified 2026-07-27 id=identity-batch.printing_identity_rows expect>=68
    sql=SELECT COUNT(*) FROM catalog_printing_identity-->
<!--@verified 2026-07-27 id=identity-batch.catalog_variant_rows expect>=1705
    sql=SELECT COUNT(*) FROM catalog_variant-->

**`opaque_id` 一個都冇變** —— 唔用 `GROUP_CONCAT`（1024-byte 截斷），用對次序免疫嘅
aggregate 指紋，寫前寫後四個值逐個字一樣。下表係 **2026-07-27 01:05 嘅點量**，唔戳
`@verified`：新入一張卡呢四個值就會變，戳落去只會日日報假 DRIFT。要重驗嘅係「寫前寫後
一致」，唔係「等於某個數」：

| 指標 | 寫前 | 寫後 |
|---|---|---|
| `COUNT(*)` | 1705 | 1705 |
| `BIT_XOR(CRC32(CONCAT(id,':',opaque_id)))` | 1575974989 | 1575974989 |
| `SUM(CRC32(opaque_id))` | 3667728756811 | 3667728756811 |
| `SUM(CRC32(CONCAT_WS('\|',tcg_code,card_language,canonical_name,set_name,collector_number)))` | 3680829030494 | 3680829030494 |

戳得住嘅係另一句：`catalog_variant` 冇任何重複 `opaque_id`——呢句唔會因為長行而變。
<!--@verified 2026-07-27 id=identity-batch.no_duplicate_opaque_id expect=0 ttl=14
    sql=SELECT COUNT(*) - COUNT(DISTINCT opaque_id) FROM catalog_variant-->

呢個指紋唔止係事後對比：[pipelines/converge_printing_identity.py](../../../pipelines/converge_printing_identity.py)
喺同一個交易入面 commit 前會重量一次，唔一致就 rollback 兼非零退出。即係話紅線係
fail-closed 守嘅，唔係靠人望。

**68 行嘅組成**：

| `identity_status` | 行數 | 覆蓋幾多組 | 意思 |
|---|---:|---:|---|
| `canonical` | **19** | 19 | 真重複組嘅代表，拿純 printing hash |
| `duplicate` | **20** | 19 | 真重複組嘅非代表成員 |
| `review` | **29** | 10 | 名唔一致，唔係同一張卡，**唔准 merge** |
| 合計 | **68** | 29 | |

三個 status 嘅行數都係只升唔跌（`catalog_variant` 長大就會有新組），所以用 `expect>=`：
<!--@verified 2026-07-27 id=identity-batch.canonical_rows expect>=19
    sql=SELECT COUNT(*) FROM catalog_printing_identity WHERE identity_status='canonical'-->
<!--@verified 2026-07-27 id=identity-batch.duplicate_rows expect>=20
    sql=SELECT COUNT(*) FROM catalog_printing_identity WHERE identity_status='duplicate'-->
<!--@verified 2026-07-27 id=identity-batch.review_rows expect>=29
    sql=SELECT COUNT(*) FROM catalog_printing_identity WHERE identity_status='review'-->

**完整性驗證**（全部一次過過）：

| 檢查 | 結果 |
|---|---|
| 寫入行全部係真重複組成員 | 68／68，`not_in_a_dup_group` = 0 |
| FK 打得返 `catalog_variant` | orphan = 0 |
| `canonical` 每組剛好一行 | 19 行 ／ 19 個 distinct group key |
| `canonical_printing_sha256` 全部 64-hex 且互不相撞 | 68 distinct ／ 68 |
| `evidence_sha256` 全部 64-hex | 68／68 |
| 第二次 `--write` 之後行數 | 仍然 68（idempotent） |

## 重複判定口徑

照 `docs/DB_INVENTORY_20260726.md` §4.2 記錄嘅主口徑，一個字都冇改：

```sql
SELECT tcg_code, card_language, set_name, collector_number, COUNT(*) AS members
FROM catalog_variant
GROUP BY tcg_code, card_language, set_name, collector_number
HAVING COUNT(*) > 1;
```

同一份舊報告記錄嘅副口徑 `(canonical_name, set_name, collector_number, card_language)`
今日實測係 20 組／41 行（舊報告 20 組／41 行——數字一樣純屬巧合，總行數已經唔同）。
主副兩個口徑嘅差額就係上面第 2 點嗰批假重複。

`canonical` 成員嘅選法（決定性，唔靠隨機、唔靠 `updated_at`）：
`market_price_observation` 行數最多 → `market_grader_population_observation` 行數最多
→ `catalog_source_identity` 行數最多 → `variant_id` 最細做最後 tiebreak。
即係「證據最厚嗰個做代表」。例：`OP13-118 en` 三個成員選 1466，因為佢係唯一有價格歷史
（13 行）同兩個 source identity 嘅成員。

`edition_code` / `parallel_code` / `finish_code` 一律留空（schema default）。**冇**由
`canonical_name` 尾嘅 `(Error)` / `(Super)` / `Gold Background` 反推 parallel code——
咁做係猜，猜錯就係寫假數據落 canonical 層。要填就要有真正嘅版本證據來源，唔係字串後綴。

## How to reproduce

由 repo root 跑。dry-run 唔會寫任何嘢（行完 rollback），可以安全重跑：

```bash
cd "C:/Users/jackson0202/Documents/Playground/cardz-market-cap"
set -a && . data/runtime/config/backend.env && set +a

# 睇逐組明細 + 打算寫嘅 status 分佈，唔寫 DB
python -X utf8 pipelines/converge_printing_identity.py

# 真寫（idempotent，重跑唔會加行）
python -X utf8 pipelines/converge_printing_identity.py --write \
  --json-out docs/evidence/2026-07-27-identity-batch/converge_printing_identity.json

# 獨立唯讀複核
python -X utf8 scripts/ro_sql.py "SELECT identity_status, COUNT(*) FROM catalog_printing_identity GROUP BY identity_status"
python -X utf8 scripts/ro_sql.py "SELECT COUNT(*) FROM catalog_variant"
```

* Producer：[pipelines/converge_printing_identity.py](../../../pipelines/converge_printing_identity.py)
  ——放 `pipelines/` 唔放 `temp/`，因為佢係要重跑嘅（每次 `catalog_variant` 長大就有新組）。
* 凍結輸出：[converge_printing_identity.json](converge_printing_identity.json)
  ——29 組全部組成員、每人嘅 opaque_id／canonical_name／證據權重、68 行嘅 hash 同 status。

## Premises

呢啲量嗰陣係真，會腐爛，所以逐條寫明：

* `catalog_variant` = 1,705 行（1,705 個 distinct `opaque_id`，冇重複 opaque_id）。
  **舊文 `docs/DB_INVENTORY_20260726.md` 寫 1,590 係 `information_schema.TABLE_ROWS`
  嘅估算陷阱**，唔好照抄。
* `catalog_printing_identity` 寫之前係 0 行——即係本次 68 行係第一批數據，冇同任何
  既有 convention 撞。
* Schema 來源：[pipelines/migrations/007_canonical_provenance.mysql.sql](../../../pipelines/migrations/007_canonical_provenance.mysql.sql)。
  `PRIMARY KEY (variant_id)` + `UNIQUE KEY (canonical_printing_sha256)` 兩個約束係上面
  第 3 點成個設計嘅前提，schema 一改結論即失效。
* Hash 配方跟 [pipelines/db_runtime.py](../../../pipelines/db_runtime.py) 嘅
  `identity_candidate` 分支：7 part、`|` join、tcg／set／collector casefold，語言只 strip
  唔 casefold。兩邊配方一定要繼續一致，唔然同一張卡兩條 pipeline 會出兩個 hash。
* `identity_status` 用嘅字（`canonical` / `duplicate` / `review`）：`review` 係 repo 既有
  詞（`g10_ingest.py`、`source_crosswalk.py` 都用），`canonical` / `duplicate` 係本次
  新增，只出現喺 `catalog_printing_identity`，冇覆蓋 `catalog_variant.identity_status`
  （全部 68 個成員喺 `catalog_variant` 度仍然係 `confirmed`，本次冇改）。
* Fact 表：本 producer 由頭到尾只有一條 DML，係 `catalog_printing_identity` 嘅
  `INSERT ... ON DUPLICATE KEY UPDATE`，冇任何 `market_*` 或 `catalog_variant` 嘅寫語句。
  量度期間 `market_price_observation` = 120,918 行、`market_grader_population_observation`
  = 9,944 行，但**呢兩個數唔可以當「本次冇動過」嘅證據**——同期有平行 collector agent
  喺 append，數字本身會升。可以當證據嘅係「producer 冇對佢哋發過任何寫語句」同埋
  `catalog_variant` 指紋一模一樣。

## 未做、下一步要人拍板

* **10 組 `review` 冇處理。** 佢哋要嘅唔係 merge，係補資料：4 組要真 `collector_number`
  （`Unknown` 係源頭缺失），1 組（Celebrations #15）要查係邊個 scrape 派錯 collector
  number，5 組要真正嘅 `edition_code`／`parallel_code`／`finish_code` 版本證據。呢啲全部
  要外部資料源，唔係 SQL 搞得到。
* **19 組真重複冇 merge，只係標記。** 標記之後價格／POP 歷史仍然分散喺 39 個
  `variant_id` 上面。要真正合併就要動 `catalog_variant` 同 fact 表嘅 `variant_id`，
  本次嘅紅線明確唔准，亦應該係另一個有明確 rollback 計劃嘅任務。
* **1,637 個非重複 variant 冇入 convergence 表。** 本次範圍係「已記錄嘅重複組」，
  淨係寫 68 行。將來要全庫 backfill 係做得到嘅（singleton 嘅純 hash 天然唔會撞），
  但係另一個決定，唔應該喺一個「收斂重複組」嘅任務裡面偷偷擴大。
