# Agent 交接：由 FE live → 全量池 → 增量研究

> 給下一位 agent／同事。**先讀** [PROJECT_STATE.md](../PROJECT_STATE.md) · [FILL_LOOP_LESSONS.md](FILL_LOOP_LESSONS.md) · [RECALL_VERIFY_OPS.md](RECALL_VERIFY_OPS.md) · [SESSION_RETRO_20260729.md](SESSION_RETRO_20260729.md)

> **2026-07-31 active policy:** 唔保留舊 runtime／G10 writer 指示。唯一
> code authority 係
> `/mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap`，WSL runtime
> 係 `/home/jackson0202/cardz-market-cap/.venv-backend`。`g10_kline`／G10
> analytics 明文禁止寫 canonical DB 或作 market-cap/FE 價；只可用 exact
> PriceCharting、SNK、eBay market facts。每次 canonical QC 後用
> `qc_failure_sync.py --write` 投影到原有 ledger，再按 lane
> `failure_ledger.py export-retry`；opaque ID 因語言 rekey 時以穩定
> `variantId + lane` 關閉舊 item，唔可以重撈 ghost work。完整現行契約見
> [DATA_CONTRACT.md](DATA_CONTRACT.md) 同
> [CARD_LANGUAGE.md](CARD_LANGUAGE.md)。

---

## 0. 而家狀態（交接點 · 2026-07-31）

| 項 | 狀態 |
|---|---|
| Canonical cohort | **937** 張（以最新 canonical DB QC receipt 為準） |
| 主資料庫 | MySQL `cardz_market_cap`，唯一 business DB |
| 價格來源 | exact PriceCharting PSA 10、SNK PSA 10、exact-bound eBay PSA 10 |
| 成交來源 | SNK、exact-bound eBay／PC 成交；來源內指紋冪等 |
| 圖片 | 獨立 lane；價＋成交 gate 先行 |
| 主入口 | **WSL Ubuntu** + `.venv-backend` |

```bash
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap
/home/jackson0202/cardz-market-cap/.venv-backend/bin/python \
  pipelines/canonical_db_qc.py --run-id handoff-check-YYYYMMDDTHHMMSSZ
```

---

## 1. 工作流（所有 agent 共用）

### 硬流程

```text
1. 讀 PROJECT_STATE + FILL_LOOP_LESSONS + RECALL_VERIFY_OPS
2. status 量度現況（唔好靠記憶）
3. 每個來源一次拉齊可得欄位，再按 exact printing 重組
4. exact identity gate 後寫 PriceCharting／SNK／eBay 價與成交
5. canonical DB QC（價、成交、POP、printing、圖）
6. `qc_failure_sync.py --write`，按 lane 輸出 retry worklist
7. Agent 只重跑 worklist 失敗項；成功綁定永久保留
8. 需要上 FE → immutable candidate → preview → 人手批准 → pointer
```

### 禁止

- 低門檻直接 INSERT  
- AI 感覺 OK 就 commit identity  
- Windows Python／第二個 DB 跑 production
- `g10`／`g10_kline`／G10 analytics 寫 canonical DB 或作 FE 價
- commit secrets / `backend.env` / 大体积 private harvest  
- first-hit 名搜 bind  

---

## 2. 增量研究路線（全量齊晒之前）

目標：**每卡有永久 ID + preferred 腳本**；未齊嘅繼續擴。

### 每日／每 session 建議次序

| 優先 | 工作 | 命令入口 |
|---:|---|---|
| 1 | status + FE_SET 抽樣 100% 仍綠 | `qualified_pool_operator status` + 讀 snapshot |
| 2 | SNK harvest 新 id（OP／新 set keyword） | `snkrdunk_discover` → `snkrdunk_bulk.pull_all` append |
| 3 | recall→verify bind | `semi_auto_identity.py run --write --recall-min 20` |
| 4 | 已 bind 拉成交 | `snk_market_data` + `ingest_snk_trades_sales` |
| 5 | PC exact-bound 成交／現價 | `c11_pc_sold_ingest` → `pc_psa10_price_derivation` → reviewed materializer |
| 6 | 全量 QC + failure ledger | `canonical_db_qc` → `qc_failure_sync --write` |
| 7 | Agent 重試價／成交缺口 | `failure_ledger export-retry --stage psa10_price|sales` |
| 8 | 圖缺口（只處理 `price-sales-gate.json` 批次） | image lane |
| 9 | 上板 | immutable candidate + preview approval，先至更新 pointer |

### 增量定義

- **有 registry 嘅卡**：只跑 `preferredLiquiditySource` 對應腳本  
- **無 registry**：recall→verify 發現 → mark → 下次變增量  
- **PC 精準修正後**：只跑 `pc_full_serial_driver.py --consolidate-only` 重建 current-exact map，禁止為重建 map 重跑全量 shard
- **成交**：永遠 full history upsert（指紋去重），唔截 30d  

### 全量完成標準（池）

| 指標 | 目標（建議） |
|---|---|
| any_price | ≥ 99% watchlist 或 rational no-price 清單 |
| snk_id 或 ebay_id | 盡量；無源卡記 reason |
| sale_any | 越高越好；無成交要 verify 真乾 |
| 圖 A/B/C | 上板 100%；池內可半殘 |
| FE_SET | **永遠維持 100%**（重建 snapshot 後驗） |

---

## 3. QC 責任

**搵料免 QC；入庫前必 QC**（2026-07-29 用戶更正）。見 repo 根 [`AGENTS.md`](../AGENTS.md)「QC 閘口」。

| 階段 | 層 | 誰 |
|---|---|---|
| Harvest／research | 免 QC | Agent 全速撈 |
| 寫 DB identity／成交／價 | Verify 硬閘 | **腳本** `semi_auto_identity` 等 |
| 寫 public 圖 | SAMPLE 硬閘 | `sample_image_qc` + store_* |
| 編排／擴 alias | 編排 | **Agent** |
| 殘渣 needsReview | 半自動 | Agent 批次 → 仍寫同一 DB |
| 品味／法律 | 人 | 人 |

詳：[RECALL_VERIFY_OPS.md](RECALL_VERIFY_OPS.md)

---

## 4. 驗 FE 100%（每次 snapshot 後）

```bash
/home/jackson0202/cardz-market-cap/.venv-backend/bin/python \
  pipelines/canonical_db_qc.py --run-id pre-public-YYYYMMDDTHHMMSSZ
```

只接受 immutable report + receipt；唔可以用舊 seed／舊 preview 代替。

---

## 5. 交接 checklist

- [ ] 讀 STATE + RECALL_VERIFY + 本檔  
- [ ] `status` 跑通  
- [ ] 知 canonical QC receipt 同 retry worklist 路徑
- [ ] 知 secrets 喺邊、唔 commit  
- [ ] 知 WSL venv 路徑
- [ ] 下一優先：全池成交／identity 增量（§2）  

---

## 6. 本 Repo 最高共同 Admin（用戶指定）

| 角色 | 聯絡 |
|---|---|
| 本 repo 最高共同 Admin（用戶指定） | **yoyyoy1924@gmail.com** |

用途：同 Owner（`jacksoncoin0202-ops`）一齊管理 **GitHub repo** [`jacksoncoin0202-ops/cardz-market-cap`](https://github.com/jacksoncoin0202-ops/cardz-market-cap)——改 code、settings、collaborators、deploy 相關。

**權限說明（GitHub 個人帳號 repo）：**
- Owner = 帳號 `jacksoncoin0202-ops`（唯一可 delete／transfer repo）
- Collaborator **Admin** = 最高可授角色（push、settings、manage access、merge）——即用戶要求嘅「最高級 admin 改嘢」
- 邀請入口：https://github.com/jacksoncoin0202-ops/cardz-market-cap/settings/access → **Add people** → email `yoyyoy1924@gmail.com` → 角色 **Admin**
- `gh` REST 邀請**必須**對方已有 GitHub username；純 email 未對應 username 時 API 會 404，要用網頁 UI 用 email 邀請

**狀態：** email 已寫入本檔；實際 collaborator invite 以 GitHub 上 pending／accepted 為準。
