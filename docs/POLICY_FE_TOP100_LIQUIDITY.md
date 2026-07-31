# Policy: 公開前端流動性閘 — 30d 純 PSA10 成交少於 10 筆則剔除

**asOf:** 2026-07-31  
**Authority:** DADDY — 近 30 日純 PSA10 合格成交必須 **≥10 筆**  
**Status:** **ACTIVE**

> 2026-07-31 更正：舊版「有 1 筆／`>0` 就過」已作廢。任何 agent、
> script、snapshot 或 deploy gate 都不得再採用舊門檻。

---

## 1. 決策

| # | 規則 |
|---|------|
| 1 | **所有公開前端 cohort**（TCG 綜合 / Pokémon / One Piece 公開榜）**只展示**近 **30 日**有 **≥10 筆合格純 PSA10 成交** 嘅卡 |
| 2 | 合格成交 = QC 同口徑：`grader=psa` + grade 10 + `coverage_status` 可接受 + 有效 unit price + 可綁 identity（現有 QC sales 規則） |
| 3 | **0–9 筆 30d 合格成交** → **唔入公開前端座位** |
| 4 | 剔除後座位：**由下一名有 ≥10 筆且其他 gate 過嘅卡遞補**，或暫時少於目標張數——**唔 invent 成交** |
| 5 | Watchlist / 研究池可保留低成交卡；**公開 cohort 唔保留** |
| 6 | 採集仍保存完整成交歷史；30 日只係 release 衍生窗口，唔係採集截斷條件 |

## 2. 同 QC 關係

- QC blocker `psa10_sales_30d_missing` **已存在**  
- 本政策升格為：**前端 board 硬閘**，唔只係「未 ready」標籤  
- Exact 價（SNK / G10）同 30d 成交係**兩道閘**：兩邊都要過先入公開 Top100

## 3. 實作落點（依序）

1. **報告層（即刻）**：`FRONTEND_DISPLAY_ELIGIBILITY.json` — 每張 qualified 標 `has_sales_30d` / `fe_top100_eligible`  
2. **QC / board 衍生**：公開列表 filter `purePsa10Count_30d >= 10`  
3. **Public snapshot / ranking**：export 前套同一 filter  
4. **UI**：唔顯示「有 rank 但 0 成交」嘅 Top100 位

## 4. 反例（唔剔除）

- 有 30d 成交但 exact 價 stale → 仍可因價閘未 ready；**唔係本政策範圍**（價另閘）  
- OP/Poke 研究用內部榜 → 可用完整 universe

## 5. 驗證

- 9 筆 → release blocker `psa10_sales_30d_insufficient`
- 10 筆 → 成交量 gate 通過
- 抽公開任一卡 → DB 必有近 30 日 **≥10 筆**合格純 PSA10 sale
