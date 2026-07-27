# 三榜 derivation（OP100 / PTCG100 / TCG300）

- **量度日期**: 2026-07-26（snk loader 465-variant 重寫落地之後、gap33 POP 落地之後）
- **點量**:
  ```bash
  set -a && . data/runtime/config/backend.env && set +a
  python -X utf8 docs/evidence/2026-07-26-three-boards/produce_three_boards.py
  ```
- **前提**: evaluation 8 係現行 eval；FRESH_FLOOR=2026-07-24；PSA10 價源只認
  snk_psa10 / ebay（snkrdunk raw 上架價唔准做 PSA10 市值分子）；POP 只認
  gemrate PSA 最新觀測。catalog_variant 已知有 24 組重複實體（例：OP05-119
  Luffy 有多個 variant_id），榜面照出唔合併 —— 收斂係
  `catalog_printing_identity` 嘅工作，唔係榜嘅工作。

## 結論一句

**誠實深度：OP 40 / PTCG 100（滿）/ TCG 265。OP100 填唔滿嘅樽頸唔係價格，
係榜外 OP 卡冇 gemrate PSA POP —— 71/79 張榜外 priced OP 卡缺 POP，
而 POP 採集範圍係 roster 決定（scope 鐵律 1590，用戶話事）。**

## 數字（produce_three_boards.py 實測輸出）

| 池 | 行數 |
|---|---|
| eval8 ready | 257（OP 33 / PTCG 224） |
| 榜外 PSA10-priced candidates | 221 |
| 榜外 derived ready（過齊三閘） | 8（OP 7 / PTCG 1） |
| **pool 總深** | **265**（OP 40 / PTCG 225） |

| 榜 | 交付 | 目標 | 欠 |
|---|---|---|---|
| [op100.csv](op100.csv) | 40 | 100 | 60 |
| [ptcg100.csv](ptcg100.csv) | 100 | 100 | 0 |
| [tcg300.csv](tcg300.csv) | 265 | 300 | 35 |

## Blockers（[blockers.csv](blockers.csv)，213 行 —— 冇靜刪，全部記低）

| tcg | reason | 數 |
|---|---|---|
| pokemon | stale_price（<2026-07-24） | 114 |
| one-piece | stale_price | 59 |
| pokemon | no_psa_pop | 27 |
| one-piece | no_psa_pop | 13 |

**OP 樽頸拆解**（榜外 79 張 PSA10-priced OP 卡）：
- 7 ready 已入榜
- **1 張**淨係差新鮮價（有 POP）—— 補一次價即入榜
- 13 張價新鮮但冇 POP
- 58 張又舊價又冇 POP
- → **71/79 缺 POP**。stale 59 張入面得 1 張有 POP，所以就算幫佢哋全部
  刷新價格，OP 榜最多去到 41。**解鎖 OP100 嘅唯一路徑係將榜外 OP 卡納入
  POP 採集範圍 —— roster/scope 決定，等用戶。**

stale 日期分佈集中喺 07-19～07-23（129/173），即係呢批卡歷史上有過 PSA10
成交價，只係唔喺每日 collector 名單（入圍先爬價 —— 政策使然，唔係故障）。

## 榜面 sanity

op100 榜首 Monkey.D.Luffy L [EB02-010] cap $45.06M（px $3,991.65 × POP 11,288），
derived 行（origin=derived_out_of_roster，price_source=ebay）插喺 rank 6/9/10/11，
cap $10.5M–$15.9M，provenance 欄齊。每行帶 price_as_of / pop_as_of /
origin / price_source 四欄，出街可稽。

## 事後翻返（唔好停，過咗佢先 —— 已記低）

1. 71 張榜外 OP 卡缺 POP → 需要用戶 scope 決定先可以採集
2. 1 張 OP 卡（blockers.csv 入面 stale_price 而 pop_as_of 非空嗰行）補一次
   價即入榜
3. catalog_variant 重複組（OP05-119 Luffy ×4 榜面共存）→
   `catalog_printing_identity` 收斂工作，另一件事
