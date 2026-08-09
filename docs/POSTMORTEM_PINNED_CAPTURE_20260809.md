# Postmortem — 一個 reader bug 令 1,762 張卡被判「來源對唔上」（2026-08-09）

## 一句話

GemRate 嘅 `raw/` 係 **append-only content-addressed store**，增量採集加多一份新 payload 再改 receipt 指針；但 `load_psa_raw()` 淨係識跟最新指針，於是驗證器攞新 sha 去對舊 accepted sha，成 1,762 張卡當場「provenance mismatch」。**一 byte 數據都冇少，係讀錯位。**

## 影響

- `validator034` 由 pass 變 fail（`literalPsaDescriptionByteExact` 幾百條 `raw_literal_or_hash_mismatch`）。
- S11 receipt `passed=false` → **S12 activate 開唔到閘**。
- FE 一直服務緊舊 universe lock 43（2026-08-08 17:00Z 建），而 S8–S11 喺 17:42–17:56 已經重跑過、由頭到尾冇 activate。
- 用戶睇到嘅結果：**pop≥1000 嘅 One Piece 卡得 14/143 上到 FE**，其餘全部似「未採集」。

## 時間線（UTC）

| 時間 | 事件 |
|---|---|
| 08-08 17:00:52 | universe lock 43 建立 → FE 由呢刻起服務呢個 cohort |
| 08-08 17:42–17:56 | S8–S11 重跑，product_ready 由 854 升；**冇 activate** |
| 08-08 17:57 | validate 仲係 pass |
| 08-08 18:25 / 18:50 | 兩轉 gemrate incremental（run 8142 / 8145）**重寫 1,762 張卡嘅 capture 指針** |
| 08-09 01:47 | validate 首次 fail — 表面睇似「無端端壞咗」 |
| 08-09 02:01 | 修好 reader → validate pass → activate lock 45（904 members） |

## 根因

```
data/private/gemrate/cards/<gemrate_id>/
  raw/<sha-A>.json          ← acceptance 當日 pin 住嗰份（永遠唔刪）
  raw/<sha-B>.json          ← 增量採集加嘅新一份
  card_details.raw.receipt.json   ← 指針，只指住最新嗰份（B）
```

- `catalog_psa_identity_accepted.raw_payload_sha256` 存住 **A**（immutable pin）。
- `load_psa_raw()` 讀 receipt → 攞到 **B**。
- 驗證器 `A == B`？→ false → 判 mismatch。

**A 由頭到尾仲喺硬碟度。** 全庫實測：1,756 張卡有 2 份 payload、6 張有 3 份，冇一份被覆蓋或刪除。

### 點解冇即刻爆

原本有個 escape hatch：capture 被刷新之後，如果 successor payload 自己有 receipt（新 `psaRowSha256` 落咗 population observation，或者有 `market_ingest_run.payload_sha256` = 新 raw sha）就照過。但：

- `market_ingest_run.payload_sha256` 係**成個 run 嘅 batch sha**，唔係逐張卡嘅 raw sha — 永遠對唔上。
- population observation 只有喺 **pop 數值有變**先會插新行；pop 冇變嘅卡就冇 successor 行。

即係話 escape hatch 保護唔到「刷新咗但數值冇變」呢個最常見情況 —— 而嗰個情況正正係大多數。

## 修正

`pipelines/psa_identity_repair.py::load_psa_raw(gemrate_id, pinned_sha=None)`

- 有 `pinned_sha` → 直接開 `raw/<pinned_sha>.json`，**並要求 bytes hash 返嗰個 sha**。
- 冇 pin（proposal / acceptance 路徑）→ 維持跟最新指針，行為不變。
- pin 咗但檔案唔喺度 → **唔准靜靜跌返最新嗰份扮成功**，`resolvedBy` 保持 `receipt_pointer`，fail closed。

呢個係**收緊**唔係放鬆：以前係「同最新嗰份比」，而家係「當日 accept 嗰份 bytes 必須仲喺度、而且 hash 對得返」。驗收閘一條都冇動過。

`scripts/validate_psa_identity_repair.py` 改成傳 `pinned_sha=accepted["raw_payload_sha256"]`。

## 防再犯（會真係 fire）

`scripts/test_psa_raw_pinned_resolution.py` — 造一個假 card dir、模擬一次增量刷新，斷言三件事：

1. 冇 pin 讀到最新（proposal 路徑唔可以壞）
2. 有 pin 讀返 accepted 嗰份（`resolvedBy=pinned_sha`）
3. pin 咗但冇檔 → 唔准假裝 resolved

**實證過會 fire：** 暫時將 pinned 分支關掉再跑，即刻出 3 條 FAIL、exit 1，病徵同 production 一模一樣。

```bash
python -X utf8 scripts/test_psa_raw_pinned_resolution.py
```

## 兩條可以搬去其他地方嘅教訓

**1. 唔可以用「會郁嘅指針」去驗「唔會郁嘅 pin」。**
凡係 store 分「content-addressed 內容」同「指住最新嘅指針」兩層，任何 provenance 驗證都必須**用 sha 直接定位**，唔准經指針。同一形狀嘅位仲有邊度：任何讀 `current.json` / `latest` symlink / `*.receipt.json` 去比對歷史 sha 嘅 code。

**2. 衍生狀態會靜靜落後於佢嘅輸入，而且冇任何 run 會嗌。**
`operator_strict_source_identity` 係 **VIEW 唔係 table**（`pipelines/migrations/031_active_exact_identity_market_repair.mysql.sql:68`）。base table 一改，成員資格即刻變，唔使跑任何嘢。今次 50 張 OP 卡就係喺 S8/S9 artifact 寫完之後先入到 view（artifact 881 → view 931），所以 artifact 同現實脫節而冇人知。

**欠單（未修）：** 冇任何閘檢查「現役 universe lock 嘅成員」同「即刻重算一次嘅 product_ready cohort」係咪一致。應該加落 `daily-accept`：唔一致就嗌，唔好等人肉發現。呢條係真欠單，**未做**。

## 順帶記低

- 呢個 checkout 嘅 `data/` 係 junction 指去舊 folder `cardz-market-cap`；`Path.resolve()` 會著草出 ROOT 之外，寫測試 fixture 要避開。
