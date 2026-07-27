# HANDOFF — 已批 7 卡 i18n 批案（discovery 完成，未落筆，交接俾下手）

- **量度日期**：2026-07-27 晚（~21:10 本機時區）
- **狀態一句**：**discovery 全部完成，一行 code 未改** —— 因為 quota 見底要轉 fallback model，
  喺乾淨切點停低。下手照住「§4 Edit 計劃」直行就得，唔使重新考古。
- **前提**：
  - snapshot generation `canonical_20260726_e88c81289ac3`（258 published），
    dev :3800 行緊（bare `npx next dev -p 3800` + `MARKET_DATA_SNAPSHOT_PATH` →
    `data/runtime/local-serve/snapshot.json`，log `temp/dev-server-3800.log`）
  - `apps/web/src/lib/card-names.ts` 嘅 FILE_CLAIMS claim 由 `pkg-release` 揸住
    （19:28 起，~23:28 到期）。**同一 session 換 model 接手 = 同一個 agent，claim 繼續有效**，
    過 23:28 仲做緊就照規矩 re-stamp 自己個 `Since`。
  - 今次改動**全部 display 層**，snapshot 檔唔郁 → 改完 TS hot-reload 即生效，**唔使重啟 dev server**。

---

## 1. 批案（用戶已批，範圍唔准擴）

| Rank | 卡 | 批准譯名 |
|---|---|---|
| 5 | ST21-014 Monkey.D.Luffy（Jump 雜誌 promo） | ja モンキー・D・ルフィ / 繁 蒙其・D・魯夫 / 簡 蒙奇・D・路飞 |
| 96 | ST10-006 「Monkey.D.Luff」（源頭斷字） | 同上（display 層補 y —— EN_DISPLAY 已存在） |
| 140 | P-110 Luffy（ONE PIECE DAY'25） | 同上 |
| 150 | ST21-014 Luffy（Flagship Battle） | 同上 |
| 175 | OP06-119 Sanji SEC-SP | ja サンジ / 繁 香吉士 / 簡 **山治** |
| 192 | P-043 Luffy（週刊少年 JUMP） | 同 Luffy |
| 218 | EB03-061 Uta SEC-SP | ja ウタ / 繁 烏塔 / 簡 乌塔 |

順手修（同批）：rank 47 譯名半截英文尾巴、「3th Anniversary」串錯字。
**只郁 display 層，唔掂 DB。批量完成後將結果清單俾用戶過目**（批案本身已批，過目係驗收唔係再審批）。

## 2. Discovery 實測（2026-07-27，對住 `data/runtime/local-serve/snapshot.json` 量）

1. **病灶 = producer pass-through**：snapshot `names` 入面 producer 將英文名照抄入晒
   ja/zhTW/zhCN。rank **5 / 140 / 150 / 192 / 175 / 218** 六張全中。
   而 [snapshot.ts](../../../apps/web/src/lib/snapshot.ts) `localised()`（translate 分支）
   見 locale 有值就照用（`localizedCardName()` 第一句 `if (current) return current`），
   所以 card-names.ts 明明有 Luffy/Sanji 條目都出唔到 —— 英文副本蓋死咗 lookup。
2. **全 snapshot pass-through 共 14 張**：上面 6 張 + rank 96，另外仲有
   100 / 118 / 132 / 160 / 207 / 208 / 236 七張 —— **嗰七張唔喺批案，唔准順手譯**
   （修完 pass-through 檢測後佢哋會行 token-walk / 英文 fallback，屬預期行為）。
3. **rank 96**（Monkey.D.Luff）：EN_DISPLAY 修復觸發 `stale=true` → 已經行緊 lookup 出日文。
   呢條路今日冇壞，改完之後淨係要驗證冇倒退。
4. **rank 47**（Monkey.D.Luffy SEC-SP Extra Booster Anime 25th Collection）：snapshot 譯名係
   producer token-walk 爛殘渣「モンキー・D・ルフィSEC-SPBooster Extra Anime 25th Collection」
   —— **唔等於英文原文，pass-through 檢測捉唔到**，要 exact override 贏佢（見 §4-A manual-first）。
5. **「3th」實況**：全 snapshot 得 **rank 36 同 rank 91** 兩張（同名
   「Monkey.D.Luffy SEC-SPC 3th Anniversary Special Card Booster Pack A Fist of Divine Speed」）。
   **批案講「rank 20/36」—— rank 20 個名係 "…Awakening Of The New Era"，冇 3th**。
   同用戶過目時要照直講明呢個出入。
6. **rank 20 / 36 locale 譯名係真翻譯**（唔係 pass-through，同 COMPOSED_EXACT 現值一致）——
   修 pass-through 時唔准誤殺。

## 3. 涉及檔案結構（已讀全檔，唔使再讀一次先落筆）

[card-names.ts](../../../apps/web/src/lib/card-names.ts)（display 層譯名真源；
`data/editorial/card-names.json` 只係佢嘅 dump）：
- `EN_DISPLAY`：英文全名修復表（rank 96 補 y 已存在）
- `LEXICON` / `KO_LEXICON`：token-walk 詞典（Luffy 已有；Sanji 簡體而家係「山智」要改「山治」；Uta 未有）
- `SKIP_TOKENS`：含 `"3th"`、`"3rd"`、`"sec-sp"` 等（唔使郁）
- `COMPOSED_EXACT`：SEC-SP 長名 exact override（4 條 Luffy 已有含 ko；「3th」key 喺度要跟 EN 改名；
  rank 47 同 Uta 嘅 key 未有）
- `normalizeKey()`：`trim → 除[.,] → 摺空格 → lowercase`（**數字唔郁**，所以 3th/3rd 係兩個唔同 key）
- `localizedCardName(englishName, current, locale)`：current 有值直接 return —— 呢句就係 pass-through 病灶

[snapshot.ts](../../../apps/web/src/lib/snapshot.ts) `localised()` translate 分支現行邏輯：
`stale = displayCardNameEn(raw) !== raw` 時先作廢 snapshot 副本；zh-CN 有獨立
`zhFallback = value.zhCN || value.zhTW` 借值行為（要保留，但都要過 pass-through 檢測）。

## 4. Edit 計劃（設計已定案，照做）

### A. snapshot.ts `localised()` translate 分支

1. **pass-through 檢測**：locale 值 `=== raw` 或 `=== en`（display 修復後）→ 當 null 處理，
   等 card-names.ts lookup 接手。zh-CN 嘅 zhFallback 借值路一樣要過呢個檢測。
2. **manual-first**：card-names.ts 新 export `manualCardName(englishName, locale)`
   （**只查 EXACT + COMPOSED_EXACT 兩層 + ko 對應表，唔行 token-walk**），
   有 hit 就贏過 snapshot 提供值。理由：snapshot names 本身就係 card-names.ts dump 嘅
   下游副本，.ts 改咗而 snapshot 未重出時 .ts 先係新鮮真源；亦係 rank 47 爛值嘅唯一解。
   rank 20/36 唔受影響（COMPOSED_EXACT 值同 snapshot 現值一致）。

### B. card-names.ts 逐條

1. `LEXICON` Sanji zh-CN：山智 → **山治**
2. `COMPOSED_EXACT` 「Sanji SEC-SP Premium Booster One Piece Card The Best vol.2」zh-CN：
   山智SEC-SP → **山治SEC-SP**
3. `LEXICON` 加 `["Uta", { "zh-TW": "烏塔", "zh-CN": "乌塔", ja: "ウタ" }]`（**ko 未批，唔加**）
4. `COMPOSED_EXACT` 加 `"Uta SEC-SP Extra Booster ONE PIECE Heroines edition"` →
   烏塔SEC-SP / 乌塔SEC-SP / ウタSEC-SP（**唔加 ko** —— ko 行 fallback 出英文，同今日行為一致，唔倒退）
5. `COMPOSED_EXACT` 加 `"Monkey.D.Luffy SEC-SP Extra Booster Anime 25th Collection"`（rank 47）→
   蒙其・D・魯夫SEC-SP / 蒙奇・D・路飞SEC-SP / モンキー・D・ルフィSEC-SP / ko 몽키・D・루피SEC-SP
   （照抄現有四條 Luffy SEC-SP pattern，同角色同款後綴唔算新譯名）
6. **3th → 3rd**：`EN_DISPLAY` 加全名 entry
   `"Monkey.D.Luffy SEC-SPC 3th Anniversary Special Card Booster Pack A Fist of Divine Speed"` →
   同名 `3rd` 版本；`COMPOSED_EXACT` 對應 key 同步 3th→3rd 改名（兩邊唔一致 lookup 會 miss）。
   生效機制：EN_DISPLAY 改名 → `stale=true` → snapshot 副本作廢 → 改名後 key hit。
   **rank 36 同 91 兩張一齊生效**。
7. export `manualCardName()`（§A-2 用）

### C. 驗證（缺一唔可）

1. `npx tsc --noEmit` + `npx vitest run`（card-names / snapshot 相關 suite）
2. `node temp/dump-card-names.mjs` 重出 `data/editorial/card-names.json`（dump 同 .ts 唔准分家）
3. :3800 實測 **rank 5 / 20 / 36 / 47 / 91 / 96 / 140 / 150 / 175 / 192 / 218** 全部四語顯示
   （ja/zh-TW/zh-CN 三 locale 頁面逐個睇，ko 抽 175/218 驗 fallback 冇倒退）

### D. 收尾

1. `FILE_CLAIMS.md` 刪 `card-names.ts` 自己嗰行
2. **結果清單俾用戶過目**（連「rank 20 冇 3th、實際係 rank 36+91」呢單出入）
3. PROJECT_STATE.md §3 嗰行剔走 i18n，之後先開 docker 打包隊列（GO 已俾）

## 5. 紅線（接手必讀）

- 只准 display 層，**唔准改 DB `canonical_name`**（會換 opaque_id 孤立價格/POP 史）
- **唔准機器直譯**：譯名以批案為限，ko 冇批就唔加；14 張 pass-through 得 7 張喺批案內，其餘唔准順手譯
- git 入面 `data/public/seed-snapshot.json` 保持 demo（另一個 claim 仲揸住，係打包用，唔關 i18n 事）
- 完成後將結果清單俾用戶過目先算收工
