# Postmortem — pop≥1000 嘅 One Piece 卡上唔到 FE（2026-08-09）

## 一句話

**冇一個 bug 係「數據唔夠」。** 七個獨立 defect，每個都令一個「窿」讀落似一堵「牆」——
腳本每次都畀出一個乾淨、自信、而且**錯**嘅答案：「呢張卡冇候選」。

## 影響

- pop≥1000 嘅 One Piece 卡，長期只得 99/約 300 張上到 FE。
- 一次 dry run 對住 54 張仲缺嘅卡，`accepted: 0`。零。修好之後同一批數據 `accepted: 9`。
- S12 activate 曾經因為 2 張卡（PSA10 pop 2,363 同 1,589）abort，而佢哋手上有 834 行完全正常嘅證據。

## 七個 defect

### A. 令「窿」睇落似「牆」（腳本自信咁講錯嘢）

| # | 症狀 | 根因 | 修法 | 防再犯 |
|---|---|---|---|---|
| 1 | 兩張卡有 834 行好價，S12 話「冇價」 | binding 修復會將價 quarantine，但**全 repo 冇任何 code path 寫得返 `ready`**。superseded 嘅一個猜測，永久封死證據 | `ingest_kline_jsonls` 加 release lane：只釋放「今 run 新抽到」**而且**「external id 等於已證 exact binding」嗰啲 | release 條件本身就係證明；動唔到嘅行保持 quarantined |
| 2 | 精確 query 抽到嘅候選被掉咗 | 兩條 query 結果**駁埋一齊再截 cap**。闊 query（「Nami 106」）填滿 20 格，精確 query（「OP09-106」）嗰 7 個——唯一可能係呢張卡嘅——抽咗返嚟即刻掉 | round-robin 合併，每條 query 有份額 | `test_snk_identity_discover_rules.py` |
| 3 | set code 一致嘅逃生閘，**由開服到今日一次都未 fire 過** | 佢用「掉最後一個 token」讀 claim 嘅 set code。Pokémon 係「S3a 056/076」啱；One Piece 係「OP09-106」黐埋，永遠讀出空字串 | 用同一支 `SET_CODE_RE` 抽 | 同上 |
| 4 | 54 張入面 50 張 `set_code` 係空 | code 其實寫喺 `set_name` 入面，而 `product_number()` **已經**喺嗰度讀佢去砌 search key。同一件事讀一邊唔讀另一邊 | rule 一齊讀 `set_name` | 同上 |

### B. 令你搵唔到原因

| # | 症狀 | 根因 | 修法 |
|---|---|---|---|
| 5 | S12 abort 只印 `missingPrice=[56, 306]` | 一串 id，答案喺四個 join 之外 | `_coverage_diagnosis` 逐條數 eligibility view 會 fail 嘅 clause，abort 直接讀成指示（`never_harvested` / `no_strict_identity` / `all_price_rows_quarantined` / `no_acceptance_row`） |
| 6 | discovery 話「已經有人綁咗」 | 見到 `catalog_source_identity` 有 row 就當已佔用。只有 `match_status='exact'` 先係證明 | 只認 exact；其餘可 repoint |

### C. 要人手執先過到

| # | 症狀 | 修法 |
|---|---|---|
| 7 | 加咗 binding → worklist 變 → `resumable SNK state belongs to another locked universe`，要人手搬三個檔。**同一原因咬過兩次** | request hash 變而 condition 冇變 = 舊 capture 被取代。自動搬入 `superseded-<runId>-<hash>/`（搬唔刪）再重新開始。condition 唔同仍然 raise |

### 8. 冇人問過「張卡上面係邊個」

OP03-112：set code、號碼、語言、treatment **全部一致**，而我哋張卡叫
「Charlotte **Pudding**」，listing 叫「Charlotte **Cracker**」——兩個 Charlotte，唔同人。
規則由頭到尾冇比較過角色名。

`character_agrees` 比**最後一個字**（given name），因為姓正正就係佢哋共有嗰part。
全 run 攔咗 32 個錯候選。

**佢只喺「有印出嚟而且唔同」先拒絕**：listing 讀唔到拉丁字就原樣放行，
所以呢個 check 淨係加拒絕，冇放鬆過任何閘。

## 跨越七個 bug 嘅同一個教訓

> **一個「乾淨嘅零」比一個 crash 危險得多。**

`accepted: 0`、`noSurvivor: 53`、`missingPrice=[56, 306]`——三個都係腳本喺
**運作正常**嘅情況下畀出嘅答案。冇 exception、冇 warning、冇紅色。
所以每次都要人（你）由外面睇一眼先發現不對路。

落地做法：

1. **零要有理由。** 任何「搵唔到」嘅輸出，必須連埋逐條 rejection 同原因（呢個 lane 而家做到）。
2. **只喺 code 度存在嘅 check 唔算數。** #3 嗰個逃生閘寫咗、有 test、有 comment，但對 One Piece 一次都未 fire 過。**加完 check 要即場證明佢真係會 fire**（今次：`112972:character_mismatch:ours=pudding`，32 次）。
3. **要人手執嘅 recovery 就係一個 bug。** #7 咬兩次先修，太遲。

## One Piece 同 Pokémon 嘅格式差異（點解 rule 唔可以照抄）

呢三個係實測出嚟嘅分別，**唔需要另開一支腳本**，但 rule 一定要識分：

| | Pokémon | One Piece |
|---|---|---|
| collector claim | 空格分開：`S3a 056/076` | 單 token 黐埋：`OP09-106` |
| set 身份 | set code 穩定 | **重印保留原本 set number**；GemRate 叫「賣佢嗰個產品」，SNK/PC 叫「印喺卡上嗰個號」 |
| set 名 | 兩邊講法一致 | 英文措辭唔同：OP04 = "Kingdoms of Intrigue"(GemRate) vs "The Kingdom Of Conspiracy"(SNK) |

因為第二、三行，**set 名對唔上唔可以當證據**；set code + 號碼 + 語言 + treatment +
**角色名** 先係 One Piece 嘅身份組合。共用 rule + 一個 `op_identity_rules.py`
詞彙表，好過兩支會慢慢分叉嘅腳本。

## 第八個缺陷：一個狀態撈埋兩個意思（2026-08-09 夜補）

`match_status='rejected'` 一直有兩個來源，而全部 lane 都當佢係強嗰個：

- **判決**——decision contract 睇過呢個 binding 之後否決，evidence 寫住
  `action='reject'` 或 `'reject-wrong-printing-source'`。
- **連坐**——`psa_identity_repair.py` 見到一張卡嘅 **GemRate** 身份解唔掂，就將
  嗰張卡**所有非 gemrate binding 一次過撳 rejected**，完全冇睇過嗰啲 binding，
  亦冇改 evidence，所以行入面照樣寫住 `"action": "confirm"`。

2026-08-07 14:08 一分鐘內連坐咗 226 個價源行。第二朝 GemRate 身份修好咗，
連坐嗰批冇人翻返轉頭。全 DB 379 個 rejected 入面，**2 個係判決，377 個係連坐**——
但 `pc-identity-reverify` 個 docstring 明寫「Never touches exact/rejected/conflict
rows」，所以嗰 121 張 pop≥1000 嘅卡，永遠冇機會再被審。

**呢個係「乾淨嘅零」嘅另一個樣。** 唔係腳本搵唔到，係腳本被叫咗唔准搵。

修法：`REJECTION_VERDICT_ACTIONS` 由 evidence 分辨兩者，SQL predicate 由同一個 set
生成（唔會分叉）。PC reverify 重審連坐嗰批——**用返一模一樣嗰條 fail-closed 契約，
零 gate 放鬆**；SNK discovery 拒絕覆寫真判決，記憶體檢查同 UPDATE 個 WHERE 各做一次。
`psa_identity_repair` 之後自己 stamp 連坐 evidence，原本嗰份 nest 住唔刪。

重審 101 行 → 49 行喺自己嗰版捕獲頁面上證到身份。one-piece 153→137、pokemon 447→413。

**教訓：一個狀態如果有兩個寫入者、兩個意思，佢就唔係狀態，係一個等緊爆嘅假設。**
狀態要自述——邊個寫、點解寫——唔係靠讀 code 嘅人記住。

## 第九個缺陷：解封條件寫死咗「今 run 抽到」（2026-08-09 夜補）

身份修好之後 S12 照樣 ABORT，29 張卡 `all_price_rows_quarantined`——
變體 60 有 **1,135 行價**，ready **0**。

`market_price_observation.metric_status` 同 `match_status` 一模一樣，有兩個寫入者：

- **判決**——`apply_verified_source_bindings` 否決一個 (variant, external id)，
  只 quarantine 嗰一對嘅行。
- **連坐**——`psa_identity_repair._quarantine_variant` 見 GemRate 身份解唔掂，
  將**成張卡所有價行**一次過 quarantine，唔理邊個源、邊個 item。

而全 repo 唯一嘅解封 lane（`snk_market_data.py:1445`，834 行證據嗰次加）寫住
`p.last_run_id = <今個 run>`：**一行只可以由捕獲佢嗰個 run 解封**。
但修復嘅本質就係「身份遲過捕獲先證到」——08-07 封、08-09 證，永遠對唔上。

修法：解封條件係身份，唔係新鮮度。行嘅 (variant, source, external id) 今日係
`catalog_source_identity` exact **而且** 喺 `operator_strict_source_identity`
入面，先解封。44,007 行入面解 **2,183 行 / 44 張卡**；其餘 91 張卡身份未證或者
被判決，一行都冇動，紅名單 13 張零命中。落喺 `stage_price_materialize` 同
`daily-accept` 兩處，共用同一條 predicate（count 同 update 由同一個 join 砌出嚟，
唔會分叉；兩邊數唔同即刻 abort）。

解封唔等於證明：FE eligibility view（migration 024）之後仍然自己再驗一次 exact
binding 同 payload sha。放鬆嘅係一個過期 flag，唔係一個閘。

**教訓同第八個一樣，換咗個欄位。** 一個 flag 由 A 設、只有 B 拆得到，而 B 嘅條件
包含「同一個 run」——即係只要真相遲過捕獲到達，就永遠冇人拆得返。

## 第十個缺陷：印刷處理當咗做卡名（2026-08-09 夜補）

睇住 pokemon gap sweep 跑，頭 26 個拒絕入面 **25 個嘅卡名都係 `Full Art/XXX`**。
GemRate 送過嚟嘅 fingerprint name 係「parallel + 名」焊埋一齊一個欄，
而下游全部人都當佢係一個名。

**兩邊都錯，唔止一邊：**

- **錯拒**：`Full Art/Charizard GX` 被佢自己嘅產品頁拒絕——個頁 title 寫
  「Charizard GX」，由頭到尾冇寫過「Full Art」。
- **錯收**：`Full Art/M Pidgeot EX` **收咗**一張叫「Double Full Heal」嘅 Trainer 卡，
  因為 `name_ok` 撞第一個字就短路，兩邊都有 "full"。
- **搜尋被污染**：`Full Art/` 照塞入 PriceCharting query，所以一條 pokemon query
  會回一個 One Piece 產品（Nico Olvia OP09-106）。

**淨係拆走個 treatment 會開窿。** `page_identity_ok` 個 docstring 寫住
「Fail closed when a PC page cannot prove the requested printing」，但
**號碼呢一項一直只對 one-piece 兌現**。所以英文 `Pikachu V` #001 綁咗
「Pikachu [Full Art] #708, Pokemon Chinese Gem Pack」，仲當咗成功收貨。

修法：兩條一齊落。名照拆（只拆認得嘅 print phrase，用返 `_PRINT_PHRASES`
同一份詞彙表，唔會分叉），pokemon 同時要證號碼。

**點解係兩條規則唔係一條？** 因為兩隻遊戲寫號碼嘅方式唔同——
One Piece 係單 token（`OP09-106`），pokemon 係裸號或者 set 前綴
（我哋 `050` 對頁面 `SWSH050`）。用 pokemon 嗰套拆 `OP09-106` 會讀出 **9106**。
呢個就係「格式唔同要唔要另開腳本」嘅答案：**唔使另開腳本，但規則要識分**。

實測（重播 sweep 已解析嘅 147 版頁）：**43 個拒絕變收貨、2 個收貨變拒絕
（兩個都證到係第二張卡）、95 個不變**，冇一版頁係印唔出號碼。
閘係**收緊咗**——pokemon 之前根本冇號碼證明。

名喺 shard 載入嗰一刻改一次（單一執行點），舊 shard 唔使重建都啱。
`scripts/test_pc_shard_identity_rules.py` 18 條，兩個方向都有 case。

**教訓：一個欄裝住兩樣嘢，同「一個狀態兩個意思」係同一個病。**
第八、九個缺陷係 `match_status` / `metric_status` 兩個寫入者；呢個係一個
字串欄兩個概念。分別只在於前者要讀 evidence 先分得開，後者一睇個 `/` 就知。

## 第十一個缺陷：查得啱，查錯範圍（2026-08-09 夜補）

`seed_pc_review_from_map.py` 每一行都問過 DB「呢個 PC product 係咪已經有人認咗」，
問題問得啱——但佢見唔到自己**手上嗰批**。兩張 pokemon 缺口卡（v1194、v2237）雙雙
resolve 到同一版 Jungle「Pikachu #60」（product 643327），兩行都過咗 DB 檢查，
去到 INSERT 先撞 primary key 1062。

**代價唔係嗰兩行。** seeding 行喺一個 transaction 入面，所以 rollback 掃埋成批
206 行——整批缺口卡一行都冇寫入。跑之前個 dry-run 亦都報「seeded 210」，因為
dry-run 同 write 行同一條有洞嘅檢查。

修法唔係「第一個佔咗就贏」：到達次序唔係證據。同一批入面兩張卡爭一版，
**兩張都唔 seed**，而且兩行都要出報告——resolver 將一版派俾兩張卡，呢件事本身
就係要睇嘅發現，唔係一行可以靜靜咁減半。規則抽咗做 `drop_contested()`，
`scripts/test_seed_pc_review_batch.py` 九個 case 釘死佢。

**教訓：一個檢查嘅範圍要覆蓋佢自己製造緊嘅嘢。** 呢個同前面十個唔同——前面係
「一個欄／一個狀態裝兩樣嘢」，呢個係「檢查嘅視野窄過寫入嘅視野」。同一形狀嘅位
仲有幾多：任何「查 DB 之後 batch insert」嘅腳本都要問返呢句。

## 第十二個缺陷：upsert 插入時講清楚，更新時唔講（2026-08-09 夜補）

`market_grader_population_observation` 嘅 unique key 係
`(variant_id, grader_code, source_code, observed_date)`——**`top_grade_label` 唔喺入面**。
但成條 population acceptance lane 就係睇 `top_grade_label`：`'10'` 先算 PSA 10 人口，
`'top'` 係未拆等級嘅「最高分持有量」，join 唔上。

兩條 lane 會落同一日嘅 GemRate 觀察：`db_runtime` 寫 `'top'`，
S12 個 bridge 同 nightly `collect_control` 寫 `'10'`。邊條先插就邊條擁有嗰行——
而 bridge 個 `ON DUPLICATE KEY UPDATE` 更新咗 run_id、人口、total、effective_at、
payload_sha，就係**冇更新 `top_grade_label`**。

結果：131 張 product_ready 卡嘅真實 PSA 10 人口寫咗入去，
但個 label 仍然係 `'top'`，acceptance lane 照 label 拒收，
S12 報 `missingPop`（`missingPrice` 係空嘅）成批 rollback。
數字一直喺張枱度——`v1116` 個 row 寫住 5282，同 v2 landing 一模一樣。
睇個報錯會以為「GemRate 冇俾人口」，去爬多一次都冇用。

修法係三條寫 `'10'` 嘅 lane 全部喺 update list restate
`top_grade_label` 同 `estimated`；寫 `'top'` 嗰條**唔准** restate——
佢係精度低嗰個形狀，唔可以反手蓋走 `'10'` 行嘅意思。
`scripts/test_pop_upsert_restates_label.py` 靜態掃三個檔釘死呢條規矩，
並且要求至少搵到 4 條 insert，唔係「搵唔到嘢查」都算過。

**教訓：唔喺 unique key 入面、但決定行意義嘅欄，upsert 一定要喺 update list restate。**
呢個又係另一個形狀：唔係欄裝兩樣嘢（第八個），唔係檢查視野太窄（第十一個），
而係**同一句 SQL 喺 INSERT 路徑同 UPDATE 路徑講唔同嘅嘢**。
同型位：任何 `ON DUPLICATE KEY UPDATE` 都要對住 unique key 逐欄問一次
「呢欄唔更新嘅話，行嘅意思會唔會變成第二樣」。

## 第十三個缺陷：為 One Piece 寫嘅字彙，靜靜咁攞去判 Pokémon（2026-08-09 夜補）

`pipelines/op_identity_rules.py` 個檔名已經講明係 One Piece 嘅字彙表，但兩條 discovery lane
（SNKRDUNK、PriceCharting）都直接 import 佢去判**所有**遊戲。三個具體傷害：

1. **遊戲自己個名當咗產品證據。** GemRate 每個 pokemon set name 開頭都係
   "2022 Pokemon Japanese …"，SNKRDUNK 嘅日文商品名唔會重複「Pokemon」呢個字 → `product_agrees`
   要求對方印一個唔指向任何產品嘅字。修法：`"pokemon"` 加入 `_PRODUCT_STOPWORDS`。
   遊戲身份唔會因此失守——`judge()` 喺睇 product 之前已經用 `tcg:<theirs>!=<ours>` 硬擋。
2. **稀有度當咗產品名。** 日文 SAR / AR / SR / UR / HR / IR 呢類卡有**自己嘅收藏編號**（號碼大過
   set size），號碼本身已經講晒係邊個印刷，冇 provider 需要再串一次
   "Special Art Rare"。修法：新 `names_a_treatment()` + `_NUMBERED_RARITY_WORDINGS`。
   **Finish 故意唔喺入面**：Master Ball Reverse Holo / Reverse Holo / Holo / 1st Edition 同
   base 印刷**共用同一個號碼**，號碼分唔到，啲字就係唯一證據 —— 佢哋照舊要求。
3. **攞住焊死咗 treatment 嘅卡名去搵嘢。** GemRate 寫 `Full Art/Pikachu Vmax`，
   `snk_identity_discover` 攞成串去 search → 冇人賣一張叫「Full Art/Pikachu Vmax」嘅卡。
   修法：load row 嗰陣行 `rebuild_036.card_name_without_treatment()` 剝走
   （PC lane 一早咁做，而家兩條 lane 喺同一個位做同一件事）。

**實測（同一批 25 張缺口卡）**：ACCEPT 4 → 13 → 15；`searchEmpty` 3 → 1；原本嗰 4 張冇一張
變返拒絕。全量 224 張：accepted 32、實綁 20（其餘 12 張已經指住第二個 master，`repointSkipped`）。

守門人：`scripts/test_snk_identity_discover_rules.py`（39 條 check），入面刻意有反證——
同一個 listing 換一個 set 仍然拒絕，證明豁免嘅係稀有度唔係「set 冇查」。

形狀：**一個模組嘅名已經講咗佢嘅適用範圍，但 import 佢嘅人冇讀。** 同型位：任何
`*_rules.py` / `*_vocab.py`，加第二個 tenant（遊戲 / 語言 / provider）之前先問「呢套字彙係邊個
寫嘅、為邊個寫」。

## 第十四個缺陷：新開一條 lane，四個 bug 都係「對方唔係照我哋嘅寫法寫」（2026-08-09 夜補）

背景：104 張英文 One Piece 卡冇價源，`pc-identity-reverify` 幫唔到手（佢只重審已有嘅
binding，而呢批 binding 本身指錯產品）。搜尋實測 35 張只解到 2 張。所以開咗
`pipelines/pc_identity_discover.py`：唔搜尋，**枚舉 set 版面**。

第一次跑出嚟四個 bug，四個都係同一個形狀 —— 我哋假設對方跟我哋嘅寫法：

1. **set 比對方向搞反。** 原本要求「我哋 set name 嘅字要出現喺對方版面」。但 GemRate 會將
   treatment 焊死入 set name（`2025 Carrying On His Will (Op13) - English Manga Alt. Art
   Parallel`），而 PriceCharting 個 set 版面冇理由講 "manga" / "parallel"。方向要反過嚟：
   **對方嘅字要出現喺我哋度**。改完 4 個測試由紅變綠。
2. **同一個號碼，兩種寫法。** GemRate 促銷卡掉咗前綴寫 `062`，PriceCharting 寫 `OP05-062`。
   實測：`062 → OP05-062 O-Nami`、`109 → OP07-109 Luffy`、`113 → OP07-113 Zoro`。
   `numbers_agree()` 因此容許「我哋 bare number = 佢個尾號」，**只限 One Piece**。
   同一版真係有一行 `P-062` 而佢係 Hody & Hyouzou —— 所以尾號從來唔單獨決定，角色／產品／
   印刷簽名三關照跑。
3. **HTML entity 當咗名嘅一部分。** `Hody &amp; Hyouzou` 唔 unescape，`amp` 就變成一個
   token，之後所有名字比對都同佢比。
4. **個 hold 講唔出自己點解 hold。** 為咗唔想 log 太長而靜靜 drop 咗號碼類 rejection，
   artifact 出 `"rejections": []` —— 分唔到「冇一行接近」同「個 filter 根本冇行過」。
   而家每個 hold 都記住：要嗰個號碼、嗰版實際載住咩前綴、幾多行過到號碼呢關。

守門人：`scripts/test_pc_identity_discover_rules.py`（53 checks，對住 PriceCharting 真係
serve 過嘅 HTML 跑，唔係手寫 fixture）。已即場證明會 fire：抽走尾號規則 → 4 條紅；
抽走 unescape → 1 條紅；還原 → 全綠。

**實測（One Piece en，pop≥1000，100 張）**：proposed 30、written 18（5 個 pid 屬於第二張卡、
7 個已經有 rejection verdict）、全部 18 張過到 reverify 未改動嘅閘。
hold：no_survivor 51、no_console 11、ambiguous 8。

## 第十五個缺陷：新 lane 唔識人手裁決，於是靜靜咁繞過咗佢（2026-08-09 夜補）

034 audit sheet 上面有 13 張卡係**人手讀過之後拒絕**嘅。呢個裁決本身寫得好清楚，但
discovery lane 由零寫起，佢**從來冇聽過呢個 list**：佢照樣為其中 3 張提案，reverify 用佢
自己一套（完全冇改過嘅）合約提升咗，跟住 price-materialize 為其中 2 張寫咗 39 條 ready
price + 60 條 sale + 2 個 public image pointer。

**攔截佢嘅係 validator034 嘅 `red13OldIdentityAndMarketQuarantined`** —— activation 前
最後一關。即係話：閘係有嘅，但響得太遲，而且呢個月已經係第二次有 lane 由一道冇蓋過印嘅門
行入去（上次係 reverify 學識「重新考慮連坐 quarantine」之後升咗 7 張）。

**根因唔係 reverify 太寬，係「人手裁決淨係存在於某啲 lane 嘅記憶入面」。**
修法：`select_targets` 用返 validator034 同 `stamp_red_sheet_quarantine` 同一個**推導**出嚟
嘅 id set（唔係抄一份），而且 fail-closed —— 睇唔到裁決嘅 lane 唔准提案。
復原：`python -X utf8 scripts/stamp_red_sheet_quarantine.py --write`（idempotent，
實測收返 2 個 binding、39 prices、60 sales、2 image pointers、2 freezes）。

**教訓（同「有檢查但零 call site」係同一族）**：一個裁決如果冇寫落佢管轄嗰行度，
下一條 lane 一定會繞過佢。要問嘅唔係「呢條 lane 記唔記得」，而係「呢個裁決寫咗落邊行」。

## 第十六個缺陷：一張卡上面有兩個都啱嘅 set（2026-08-09 夜補）

`OP08-106` 呢張 Nami，我哋 catalog 寫住 `set_name = "One Piece Emperors in the New World"`
（OP09），`collector_number = "OP08-106"`。第一個直覺係「set_code 錯咗」——**錯**。去睇
真嗰版：OP09-106 係 Nico Olvia，唔係我哋張 Nami。即係話卡面印住嘅號碼冇錯，`set_name` 講
嘅係**由邊個產品抽出嚟**。One Piece 會將一張卡再刷入後期產品而唔換號碼；GemRate 用「抽出
嚟嗰個」歸檔，PriceCharting 用「號碼嗰個」歸檔。**兩邊都冇錯，佢哋答緊兩條唔同嘅問題。**

實測（2026-08-09）：Emperors 個 console 版有 189 行，冇一行係 OP08-106；Two Legends 個版
有 `Nami [SP Foil] OP08-106`。淨係讀 catalog 嗰版，59 個英文 hold 入面有 **22 個**係搵錯地方。

修法：`rebuild_036.set_names_a_card_could_carry()` 一次過交出兩個讀法，
`pc_identity_discover.console_candidates()` 兩版都試，而且**每版用佢自己嗰個 set 名去判**
（唔係咁樣嘅話 `product_agrees` 會因為個版寫「Two Legends」而我哋寫「Emperors」拒絕晒成版）。
放寬嘅係「去邊度搵」，唔係「收咩」：號碼要全中（`OP08-106` 自己講咗佢邊個 set）、角色要中、
print signature 要中。守門人：`scripts/test_pc_identity_discover_rules.py` 3d/3f 段。

## 第十七個缺陷：搵到嘅 lane 學識咗，批准嘅 gate 唔知（2026-08-09 夜補）

上面條 rule 落咗 discovery lane 之後，pass 3 寫咗 11 個新提案。跟住行
`pc-identity-reverify`——**佢拒絕咗其中 3 張**，理由係
`product_mismatch:missing=['awakening','era']` / `['captain','wings']`：reverify 淨係識
catalog 嗰個 set 名，唔識號碼嗰個。即係「搵嘅人」同「批嘅人」對同一個字有兩種理解。

同一晚同一個形狀撞咗第二次：`stage_pc_replay` 自己抄咗一份 bracket 比對，所以收緊咗共用
嗰個 `_pc_print_signature_ok` 之後，**真正將 binding stamp 做 `exact` 嗰個 stage 仲用緊鬆
嗰個讀法**（修完實測一次過 downgrade 9 條）。

**根因係同一個：一條規則有第二個實現。** 修法唔係「記得兩邊一齊改」，係得一個定義處，
其他人 import。`set_name_by_code` / `set_names_a_card_could_carry` 而家住喺 `rebuild_036`，
`pc_identity_discover` 用 `R.` 前綴叫佢；`stage_pc_replay` 刪走自己嗰份，直接叫
`_pc_print_signature_ok`。

## 第十八個缺陷：只讀咗 catalog 證據嘅一半（2026-08-09 夜補）

`_pc_print_signature_ok` 由頭到尾淨係讀 `parallel_code`，冇讀 `printing_code`。後果：
v1199（OP05 Yamato **Special Alternate Art**，`printing_code='sp'`）因為 `parallel_code`
得個裸 rarity，就過咗一版**完全冇 bracket** 嘅 base print「Yamato OP01-121」，長期出緊平
嗰張卡嘅價；而佢霸住個 product 嘅期間，**真正嘅 base 卡 v1251 搵唔到自己嗰版**。

量度過先改（AGENTS.md rule 9 嘅前置）：928 條 live exact PC binding 入面 919 條唔受影響、
7 條被拒，7 條全部係 parallel 坐咗喺 base print 上面。修完之後 pass 3 分返正：v1251 攞返
base 版、v1716 攞「Yamato [Alternate Art] OP01-121」。

**教訓**：catalog 由頭到尾都講咗（`printing_code='sp'`），係我哋冇問佢。查「明明有證據但
判錯」嘅時候，先數吓手上有幾多個欄位，再數吓個判斷讀咗幾多個。

## 仲未修（欠單，唔係已修）

- **`print_signature_mismatch` 唔係規則問題，係 map 指錯頁。** 實測 v1582 捕獲到嘅
  係「Sylveon #68 Terastal Festival」基本卡，唔係 Master Ball Reverse Holo 平行卡；
  v849（pop 21,380）捕獲到嘅係基本 Umbreon VMAX。攔截係啱嘅，要修係重掃搵返平行卡
  自己嗰個 PC product id。**唔准為咗過數放鬆 print signature。**
- **剩返嘅缺口**（都要 headed Chrome CDP :9333 重掃）：one-piece en
  `product_mismatch` 21 張、`print_signature_mismatch` 19 張、`hard_conflict` 19 張；
  pokemon ja `hard_conflict` 37 張、`page_missing` 10 張。`hard_conflict` 大部分係
  正確攔截（綁緊嘅 PC 產品真係另一隻），佢哋要嘅係 **discovery**，唔係 reverify。
- **PC 搜尋結果頁做 discovery 唔掂**：35 張有 search capture，規則只能唯一解析 2 張。
  唔值得起呢條 lane。
- **catalog identity 缺陷**：54 張入面 50 張 `set_code` 空、`collector_number` 得個裸
  號碼。而家靠 rule 由 `set_name` 補讀——治標。
- **日文 promo 嘅「活動名」缺口（14 張，2026-08-09 實測）。** 全量 SNK discovery 之後，仲有
  14 張日文卡係**淨係**因為 `product_mismatch` 被拒，而缺嘅字全部係 GemRate 用英文寫嘅活動／
  通路名：v1878 `['center','cracked','ice','skytree','town']`、v1938 `['pokeca']`、
  v2073 `['campaign','dragon']`、v1892 `['archdjinni','giveaway','rings']` 等等。日文商品名
  唔會有呢啲英文字。**故意唔放寬**：日文 promo 嘅編號雖然多數係唯一，但一放寬就冇嘢分得到
  同號嘅 finish 變體，而錯綁係靜默錯誤。要修就要為 promo 起一套日文活動名對照，唔係拆閘。
  （另外 v109「1st Edition」同 v1582「Master Ball Reverse Holo」喺呢 14 張入面，佢哋
  **本來就應該**被拒——共用號碼，啲字係唯一證據。）
- **全量 224 張入面仲有 159 張 `no_survivor` + 29 張 `search_empty`。** 拒絕理由分佈：
  hard_conflict 816、product_mismatch 150、proven_binding_elsewhere 50、page_missing 37、
  character_mismatch 29。大部分係正確攔截（搵返嚟嘅根本係第二張卡）。
- **036 activate 之後嘅實數（2026-08-09 14:35 UTC，universe lock 58）**：product_ready
  1235（原本 851）、qualified_market_pending 370（原本 754）。One Piece en 102 ready /
  79 pending、ja 70 ready / 43 pending。即係 OP 仲欠 **122 張**。
- **SNK ja lane 撞緊同一個 cross-set reprint 形狀，但未修。** 全量 44 個目標，bound 0，
  40 個 `no_survivor`，286 個 `hard_conflict`。**唔准照抄 PC 嗰條 fix**：SNK 側嘅
  「bracket」係由字面「パラレル／parallel」推出嚟，幾乎每個 SNK binding 嘅
  `snk_parallel` 都係空字串，照套會一次過 demote 晒。要先量度，再決定。
- **`_pc_rarity_only_parallel` 仲有兩個 call site 冇收緊**：`rebuild_036.py` S7
  snk-refresh 同 snk reverify（原因同上——SNK 側）。已記帳，唔准盲改。
- **`stage_identity_resolve` 仲有一個 `match_status != 'rejected'` 分支**。行為係啱嘅
  （incident 講嘅係 live binding 漂移，rejected 行唔 live，而且 S5 冇 closure path），
  註釋已經改返講真原因，但呢個位提我哋：`rejected` 呢個字散落幾多處要定期查。
