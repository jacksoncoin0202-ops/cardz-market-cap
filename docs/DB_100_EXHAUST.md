# DB 100% 全齊 — 窮盡方法論（2026-07-29 用戶硬目標）

## 目標

`market_gemrate_psa10_watchlist`（及對應 observation）**內容 100% 全齊**：

| 欄／能力 | 100% 定義（務實） |
|---|---|
| 價 PSA10 | 每卡 ≥1 可信價（已近滿） |
| 圖 asset+ptr | 每卡有（已近滿） |
| snk **或** ebay **或** documented no_source | 每卡有市場 id **或** 永久 reason |
| sale | 有 id 者盡量有成交；真乾記 reason |
| 30d 價可用 | 有 id 後 kline/harvest 補 |
| FE bake | 上板集合 100%（方案 A 推板） |

**原則：** 歷史講過嘅可行方法全部可試；**有增長加大劑量**；準>多；verify 先寫；本機工廠。

## 三層 + 方法目錄

```text
1 自動化  browser / CF / cookie /（指紋瀏覽器 CDP）
2 存量    exact 變種 · query ladder · rehome · registry
3 增量    discover → harvest → merge → match → trades → kline → bake
```

| ID | 方法 | 狀態 |
|---|---|---|
| M1 | OP/PTCG 變種 exact（comic/SAR/AA…） | ✅ 有增長 · 循環跑 |
| M2 | 人類 query discover（角色×號×set） | ✅ 有增長 · 循環跑 |
| M3 | 大批 keyword discover（C01/02/03） | ✅ 供給 · harvest 中 |
| M4 | harvest 並行 + merge all | 🟡 |
| M5 | match-snk verify-only | 🟡 跟 harvest |
| M6 | trades 全 bound | ✅ 邊際升 · 新 id 後重跑 |
| M7 | g10_kline_bridge | id 後 |
| M8 | TPL map/harvest/ingest | 價深；源可能乾 |
| M9 | PC browser map（C11）+ sold | ✅ map 100 + E5 sold +2877（identity 等 UUID） |
| M10 | G10 altxyz/sales_cache | 供給封則停 |
| M11 | set_hints / collector_forms | 基建已擴 |
| M12 | twin PK 策略（EN 另源） | 結構 |
| M13 | full_volume_recall_verify | 殘渣 |
| M14 | 指紋瀏覽器 + Playwright | 限速時 |
| M15 | bake_publish_pack（方案 A） | 推板另閘 |

## 本機 CLI（已寫）

```powershell
python -X utf8 scripts\local_factory_chain.py --status-only
python -X utf8 scripts\local_factory_chain.py --harvest-ids <ids.txt> --match --trades
python -X utf8 scripts\bake_publish_pack.py --sync-local-serve
# exact 腳本（temp 可收編）
python -X utf8 temp\agent-swarm-20260729\scale_s11_ptcg_exact.py
python -X utf8 temp\agent-swarm-20260729\scale_s8_op_exact.py
```

## 停止條件

- 連續 2 輪：snk+ebay+sale **0 真新** 且 no_source reason 覆蓋餘卡  
- 或用戶改閘  

## 唔做

- invent 價／UUID  
- 弱 bind 抬 KPI  
- 為 push 假 100%  
- AWS 重 harvest（方案 A：本機工廠）
