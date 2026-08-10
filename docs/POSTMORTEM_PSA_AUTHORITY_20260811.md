# Postmortem — PSA 權威只落一半，令 set_name 反過來咬死 provider 綁定（2026-08-11）

**症狀**：梵高比卡超（v1，`085/SVP`）市值排 #9，publish 咗 SNKRDUNK ¥ 折算 US$969.43，
而佢喺 PriceCharting 嘅 PSA-10 價係 US$2,900。pop 49,767 × $2,900 = **$144,324,300**，
應該係全站 #1。

**唔係**價格路由揀錯，**唔係** eBay flag，**唔係** SNK 抓漏。
係 PriceCharting product 5834844 由頭到尾**綁唔上** v1 —— 被 `hard_conflict` 扣起。

---

## 根因

`_fingerprint_variant_conflicts(fp, variant)` 判 provider 頁面同 catalog 係咪同一張卡，
其中一條係 set 名比對：

```python
# provider 講嘅 set          vs   catalog 講嘅 set
_set_signals(fp["setName"])       _set_signals(variant["set_name"])
```

右邊嗰個 `catalog_variant.set_name`，**從來冇由 PSA 權威重述過**。

它嘅來源係 seed / rollup / 人手，載住三類垃圾：

| 類 | 例 |
|---|---|
| 人手打字 | `SVP EN “Van gogh exhibition”` |
| rollup 用字 | `Pokemon Sword and Shield Astral Radiance`（PSA 印 `Sword & Shield`）|
| 我哋自己 vocabulary 飄咗 | `M3: Nihil Zero`（PSA 印 `Pokemon Japanese M3-Nullifying Zero`）|

於是 v1 嘅比對變成：

```
PC 頁面 console : 'Pokemon Promo'          -> {'promo'}
catalog set_name: 'SVP EN “Van gogh …”'    -> {'svp','van','gogh','exhibition'}
交集 = 空 -> set conflict -> 綁定被拒 -> 冇 PC 價 -> 跌返去 SNK 價 -> rank 9
```

**閘冇壞。閘嘅輸入係垃圾。**

### 點解會出現「只落一半」

2026-08-10 修 canonical_name 嗰陣（`psaNamesRestated`），已經確認 PSA 權威喺
`population_data[grader=psa][0]`，並且喺 `stage_bind` 同一個 transaction 尾段
用佢重述 `canonical_name`。但同一行 PSA 資料入面嘅 `set_name` 冇一齊攞。
名（display）修好咗，**set（judgment input）冇修**。

呢個就係「同類問題一定會再出現」嘅形狀：
**一個權威攤開幾個欄位，只搬其中一個落去，剩返嗰啲會靜靜地繼續做壞判斷。**

---

## Blast radius（實測，非推論）

量度日期 2026-08-11，全量 replay，唔取樣。

| 量度 | 數 |
|---|---|
| 該 generation 有 exact gemrate 綁定嘅 variant | 1,605 |
| `catalog_variant.set_name` 同 PSA 行**唔一致** | **1,213**（75.6%）|
| 一致 | 392 |
| 冇 PSA 行 | 0 |

即係四份三張卡,個 set 判斷一直係攞錯嘅字串去比。

---

## 修正

`pipelines/rebuild_036.py`，三處，全部喺已有機制入面加，**冇加新閘、冇加防禦分支**：

1. `_capture_fingerprint()` 多攞一個欄：`psaSetName = population_data[grader=psa][0].set_name`
2. `_PSA_SET_NAME_SQL` — 對應 `_PSA_FULL_NAME_SQL` 嘅 set 版本
3. `stage_bind()` 尾段，緊接 `psaNamesRestated` 之後，同一個 transaction：
   `UPDATE catalog_variant v … SET v.set_name = <PSA set name>`
   只打 exact gemrate 綁定，只打真係唔同嗰啲（`BINARY <>`）。

**次序係設計，唔係巧合**：重述行喺裁決之後。倒轉行嘅話，會將一個同一個
transaction 即將否決嘅綁定嘅 set 名寫死落 catalog。

---

## Regression gate（改之前跑，兩邊都要綠）

呢個改動係「攞走一個 conflict 訊號嘅噪音」，所以唯一要證明嘅係：
**冇任何本來啱嘅綁定，因為換咗 set 名而變成衝突。**

| 側 | replay 數 | 新增衝突 | 清返舊衝突 |
|---|---|---|---|
| GemRate exact 綁定 | 1,605 | **0** | 7（v1, v2, v70, v381, v383, v390, v395）|
| PriceCharting exact 綁定 | 997 / 1,004 | **0** | 1（v1）|

同時 replay 107 個 PC `hard_conflict` 扣起：**清 1，剩 106 繼續扣**
（54 set / 47 language / 10 collector_number）。剩返嗰 106 係真衝突 ——
其中 45 個係 031 將日文 SV2a-151 綁去**英文** PC console，閘扣得啱。

**呢個唔係放鬆 gate。** Gate 條件一個字冇改，改嘅係餵入去嘅係咪權威資料。

---

## 落地證據

```
bind counts: {"psaSetNamesRestated": 1213, "bindingsRejected": 0, …}
v1 set_name : 'SVP EN “Van gogh exhibition”' -> 'Pokemon Svp EN-SV Black Star Promo'
v1 identities: gemrate exact / pricecharting 5834844 **exact** / snkrdunk 146897 exact
pricecharting exact 綁定總數: 1004 -> 1005（淨加 v1）
```

第二次跑 `bind`：`psaSetNamesRestated: 0` —— idempotent，冇 double-apply。

---

## 點防再犯

1. **`psaSetNamesRestated` / `psaNamesRestated` / `psaLanguagesRestated` 係
   `stage_bind` 嘅常設 counts。** 一個新 generation 第一次 bind 見到大數字 = 正常；
   同一個 generation 重跑仲見到非零 = 有 out-of-band writer 改緊 `catalog_variant`。
2. **新增任何「provider 值 vs catalog 值」比對之前，先問嗰個 catalog 欄位由邊個權威重述。**
   冇人重述 = 嗰條比對係喺度比垃圾。
3. **`catalog_printing_identity.set_name` 永遠唔准跟呢個重述。** 見下面。

---

## 顯示載體：邊個要跟，邊個唔准跟

同一個 set 名喺三張表出現。搵權威嗰陣要分清邊個係**判斷／指紋輸入**、邊個係**顯示**：

| 表.欄 | 角色 | 跟 PSA？ |
|---|---|---|
| `catalog_variant.set_name` | 判斷輸入（`_fingerprint_variant_conflicts`）| ✅ 1213 行 |
| `catalog_variant_locale.localized_set_name` | 網站 `sets` map | ✅ en 812 行 + 未譯 copy 549 行 |
| `catalog_printing_identity.set_name` | **指紋 hash 輸入** | ❌ **禁止** |

### 點解 printing_identity 唔准跟（實試過，紅咗）

2026-08-11 我一度將 PSA 名鏡入 `catalog_printing_identity.set_name`（1241 行）。
`validate` 即刻紅 `completePrintingHashExact`：

[scripts/validate_psa_identity_repair.py:224](../scripts/validate_psa_identity_repair.py)
會由 **live 行**重算 `printing_sha()`（十個 casefold 欄，包括 `set_name`）再對
`canonical_printing_sha256`。即係嗰欄根本唔係顯示文字，係 hash 前像。

反方向「連 sha 一齊重算」都唔通：
- `canonical_printing_sha256` 有 UNIQUE KEY
- 佢係 `stage_bind` 用嚟偵測撞印嘅 owner key（`printing_sha_owner`）
- validator034 嘅 `red13` invariant pin 死咗一批歷史 hash

而且**根本唔需要** —— `printingIdentity.setName` 冇任何地方 render
（detail page 只出 `editionCode` / `setCode` / `finishCode`，見
[print-badge.tsx](../apps/web/src/components/print-badge.tsx)），網站個 `sets`
只讀 locale 表。指紋保留自己嗰套用字，顯示行讀 locale。

### 點解 locale 表唔可以一刀抹

1286 張出街卡入面 **568 張有真 zhTW/ja 譯名**（來自
[data/editorial/set-names.json](../data/editorial/set-names.json)，keyed 喺舊
vocabulary）。全部覆蓋成英文 PSA 名 = 用「修正」包裝嘅倒退。

落地規則：
- `en` 唔係譯名，係正式英文標籤 → 一律跟權威
- 非英文 locale：**只有**逐字等於 en 行（即係從來冇譯過嘅 copy）先跟；有譯名嘅原封不動
- 次序硬性：非英文行要喺 `en` 重述**之前**做，否則認唔出邊啲係 copy

---

## 還原嗰陣踩到嘅坑（記低）

由備份抽單表還原，`mysql` client 冇 `SET NAMES utf8mb4` 就會當 latin1，
`SVP EN “Van gogh exhibition”` 啲彎引號變咗 mojibake。1241 行入面淨係 v1 一行
有非 ASCII，所以只有佢一個 hash 仲錯 —— **charset 錯法就係咁：純 ASCII 全部靜靜地
過，得帶重音／彎引號嗰幾行爆**。抽 dump 段落一定要自己補 `SET NAMES utf8mb4`
（原 dump 個 header 唔喺你抽出嗰段入面）。

---

## 相關

- 上一手：`canonical_name` 由 Universal rollup 改讀 PSA 行（同一個權威、同一個 transaction）
- 同形未修：published 價冇日期下限，6 張卡 34–137 日舊仍然出街
