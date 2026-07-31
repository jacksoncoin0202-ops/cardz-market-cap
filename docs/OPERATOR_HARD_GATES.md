# CARDZ Market Cap — Operator Corrections / Hard Gates

呢份係 DADDY 已明確指正過、不可再次採用嘅決策清單。新 agent、retry
worker、importer、QC、snapshot 同 deploy 全部要 fail closed；歷史報告若有
衝突，以呢份 active contract、機器 policy 同最新用戶指令為準。

## 永久硬閘

| ID | 已否決錯誤 | 必須執行 |
|---|---|---|
| IMG-001 | 將 `limitless-one-piece-en` 當乾淨 One Piece 卡圖來源 | 呢個 source family 整批拒收；2026-07-31 current/latest 非 alias DB 實測為 **320 張 = release cohort 146 + outer/non-release 174**。改名、query string 或移除 `_EN.webp` suffix 都不可繞過。retry 只可換非 Limitless 來源。 |
| IMG-002 | expected JA 配 EN 圖，或 expected EN 配 JA 圖 | 語言必須 exact match；卡號相同唔代表同一 printing。missing／false language evidence 一律不可 public。 |
| IMG-003 | SAMPLE 圖因為只得一張或人工撳 OK 就出街 | 所有來源仍要 SAMPLE gate；已知污染 family 來源閘先行。已 human-reject 嘅 `(variant, content hash)` 不可翻生；只可換新 hash／新來源再審。 |
| IMG-004 | 只按 collector number 猜 set／edition／parallel／finish | 必須七欄 exact printing：game、language、set、collector、edition、parallel、finish。任何一欄缺證據就 quarantine/review。 |
| IMG-005 | 人工撳 OK 就由 writer 自己填 `language_match=1` 或沿用舊 identity | 公開 writer 必須讀 canonical printing identity、核對 evidence hash，並以 decision 提交嘅 expected canonical hash 做 CAS；identity 漂移即拒絕。 |
| IMG-006 | Master 圖存在就公開，缺 `_200`／`_600` derivatives 都照出街 | 公開前 master、`_200`、`_600` 必須存在、可解碼及符合各自尺寸 contract；不可等前端撞 404 先補。 |
| IMG-007 | 由 title、rarity token 或下載到嘅圖自動「推斷」edition／parallel／finish | 呢三欄只可由來源明示欄位或逐欄 human／vision receipt 封印；冇 explicit evidence 就保持 unresolved，唔准用似樣 default 補齊。 |
| IMG-008 | Receipt 有 path／file hash 就當已證明某個 printing 欄位 | 每個 field receipt 必須綁定 variant、field、raw value、normalized value、source、evidence hash；materialize 時逐值重驗，任何一項漂移即拒絕。 |
| IMG-009 | 舊 `language_match=1`／`human-review-v1` 可以繼續當 release evidence | Canonical QC、public DB image query 同 snapshot admission 只接受 `human-review-v2`；v2 decision 要綁 exact asset/content hash、七欄 printing hash及明示視覺語言。DB upsert 必須同步更新 `qc_version`，否則 fail closed。 |
| IMG-010 | 人工 OK 只 `UPDATE` 已存在 source pointer；冇 pointer 就靜靜漏綁 | OK writer 必須 upsert exact `variant + raw_front + sourceVersion` pointer，再由 DB distinct read-back 證明 QCv2 同 pointer 都存在。2026-07-31 實證 55 張曾只有 41 張 pointer；修復後係 55/55。 |
| IMG-011 | 將可變 `market_image_qc` flags 當永久人工判決 | Approval 必須寫 `market_image_review_approval`，綁死 asset、variant、content hash、source hash、七欄 printing hash同語言；reject 必須寫 `market_image_rejection_registry`。Canonical QC同 public snapshot 要同時重驗 binding，並排除 registry 任何 `(variant, content hash)`。 |
| IMG-012 | 將截斷卡號 `185`／`184` 當成完整 `185/159`／`184/159` | Collector 必須係來源明示嘅完整 printing number；bare/truncated、`185/A`、`185/0` 一律 unresolved/reject。不可由圖上見到完整號後反向合理化 DB 截斷 identity。 |
| IMG-013 | 卡名／主角／基本卡號似就忽略 parallel、finish 或特殊版 | 同角色同號嘅 standard、SP、Manga、Master Ball、Wanted 等仍係不同 printing；任何錯 parallel／finish 圖永久 reject，只可換 exact printing hash。 |
| IMG-014 | 同一 content hash 被多個非 alias variant 共用，因為每張都「有圖」就放行 | Canonical QC 保留 cross-variant duplicate blocker；2026-07-31 實證 20 組 One Piece 污染 hash 影響 49 variants。不可將呢個 blocker 誤解為同 variant 舊候選而放鬆。 |
| WRITE-001 | 舊 picker／binder 可以直接 copy public asset 或寫 `public_allowed=1` | 未接入完整 hard gate 嘅 legacy writer 必須永久停用 write mode；最多產生 private candidate／dry-run。 |
| LEDGER-001 | 每次重跑同一份 blocker 報告就新增同一 failure event | Open failure 以 stable item key + blocker set 去重；只有 evidence／blocker 真改變先新增 attempt，resolved 只 append resolution，唔刪歷史。 |
| SALE-001 | 近 30 日有 1 筆 PSA10 成交就當 live-ready | 正確門檻係近 30 日 **純 PSA10 合格成交 ≥10 筆**。完整歷史照入庫；30 日只係 release 窗。 |
| SALE-002 | 上游 candidate／terminal 寫住 `ready` 就當已過 live gate | 所有 `ready` 語意都要同公開門檻一致；少於 10 筆只可叫 accumulating／insufficient，最終仍由 canonical release QC 重驗。 |
| PRICE-001 | 將 generic raw／graded 價直接當 PSA10 現價 | 只接受 exact-bound 明確 PSA10 欄或 PSA10 成交衍生；PriceCharting、SNK、G10/eBay 可以用，但必須 exact identity、PSA10 grade 同 cross-source QC。raw 價只供卡內頁參考。 |
| PRICE-002 | 現價已重寫，但沿用舊 index evaluation／market-cap row 當一致 | 每次 current PSA10 price materialization 後必須重算同一 cohort evaluation；`market_cap_current_price_mismatch`／`market_cap_not_materialized` 未清零前不可當 live-ready。 |
| SCOPE-001 | 將 CardzOS、cardzpas10、JLP 或 Kado DB 混入 Market Cap | 本 repo 唯一 business DB 係 MySQL `cardz_market_cap`。其他系統係獨立產品／資料域，不可 discovery、merge、migration 或 completeness 計數。 |
| FLOW-001 | 未過價格／成交量 gate 就按全池做公開圖 promotion | 價格與成交量先決定上線 cohort；圖片只對該 cohort 做 final QC。全量缺口可進 private retry queue，但不可越過 public gate。 |

## 機器落點

- 污染來源：`config/image-source-policy.json`
- 已視覺確認污染嘅 immutable content hash：同一 policy 嘅 `contentRules`
- Canonical DB release QC：`pipelines/canonical_db_qc.py`
- Public snapshot QC：`pipelines/public_snapshot_qc.py`
- 公開成交門檻：`config/data-routing.json`
- 人工圖片決策：`pipelines/image_review_decisions.py`
- 不可變圖片判決 schema：`pipelines/migrations/020_image_review_bindings.mysql.sql`
- Agent retry queue：`pipelines/pm_retry_queue.py`
- 七欄 printing agent／review：`pipelines/printing_agent_fill_pass.py`、`pipelines/printing_review_batch.py`
- 七欄 printing receipt 驗證／materialize：`pipelines/db_runtime.py printing-materialize`
- Failure ledger／canonical QC sync：`pipelines/failure_ledger.py`、`pipelines/qc_failure_sync.py`

## Retry 規則

- 可 retry：換來源、換新 content hash、補 exact identity、補新成交證據。
- 不可 retry：重新提名已拒 hash、重新使用已拒 source family、降低門檻、
  以同卡號代替 exact printing。
- 每個失敗保留 stable item key、reason、evidence、attempt、next action；
  resolved 只新增 resolution，唔刪歷史。

## 2026-07-31 已實證錯誤基線

- `limitless-one-piece-en`：320 張整個 family 隔離，當中 release cohort 146。
- JA/EN 錯語言：至少 9 個 exact variant/content 綁定已永久 reject。
- 截斷 collector：`185` 對 `185/159`、`184` 對 `184/159` 已永久 reject。
- 視覺錯 printing：Black White Rare special parallel 冒充 standard；EN
  Umbreon VMAX TG23/TG30 冒充 JA Umbreon 092/187 Master Ball，均永久 reject。
- QC writer 舊漏洞：reject 曾只新增 v2 row而留下 v1 public；OK 曾只更新
  pointer，令 55 張中 14 張漏 pointer。兩者已有回歸測試及 DB read-back。
- 永久 reject ledger 共 43 個 `(variant, content hash)`，已全數同步到
  `market_image_rejection_registry`；差集為 0。
