# 940 池 × 前端分層（市值優先）
> 量度：2026-07-28T16:50:14Z · marketCap = PSA10價 × GemRate POP
## 總覽
| 層 | 張數 | 前端？ | 要靚仔？ |
|---|---:|---|---|| **FE_FULL_TARGET**（上板目標） | **357** | ✅ 要上 | ✅ 要齊：價+POP+市值+圖+史（故事 production 再嚴） || FE_RESERVE（301–350） | 42 | 預設唔上 | 可半殘 || POOL_RANKABLE（有市值但榜外） | 517 | ❌ | 半殘 OK · **唔使圖** || POOL_NO_MCAP（**冇價→冇市值**） | 24 | ❌ | 先補價先有機會 |
- 940 有市值可排：**916** · 無市值：**24**
- 上板目標入面已夠基本：**44** · 仲要 polish：**313**
- 上板缺口計數：圖 103 · 歷史 31 · 故事i18n 309 · 成交(可選) 264
## 前端要咩（對上板卡）
| 欄位 | 必要？ | 計市值？ |
|---|---|---|| PSA10 價 | ✅ | ✅ 分子 || GemRate PSA10 POP | ✅ | ✅ 分子 || **marketCap** | ✅ | = 價×POP || rank | ✅ | 由市值排序 cut || 圖 raw_front | ✅ 上板 | 否 || 1d/7d/30d Δ | 榜要 | 要有 ≥ 窗長日史 || historyDaily / K 線 | 內頁要 | ≥2 點先畫線 || tracked sales | 榜有欄，可 partial | 否 || 四語故事 | production top100 嚴 | 否 || grader POP 其他 | grader 頁 | 否 |
## 結論
1. **市值係入圍前提**——冇 PSA10 價就冇 market cap，唔上前端榜。
2. **唔使 940 張張靚**——只有 FE_FULL_TARGET 要完全版；池內其餘半殘 OK。
3. **圖只打上板目標缺圖**，唔好對 940 全量硬補。

JSON 詳表：`temp/frontend_tier_940_report.json`
