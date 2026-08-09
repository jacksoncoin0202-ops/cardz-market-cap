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
- **`stage_identity_resolve` 仲有一個 `match_status != 'rejected'` 分支**。行為係啱嘅
  （incident 講嘅係 live binding 漂移，rejected 行唔 live，而且 S5 冇 closure path），
  註釋已經改返講真原因，但呢個位提我哋：`rejected` 呢個字散落幾多處要定期查。
