# SNK 圖審批／Promotion — Hard Gates

> **2026-07-29：** 停 bulk 加圖當完成。檔落地 ≠ 可公開。  
> Strict gate：`public_allowed=1` **且** `semantic_match_status=human_or_vision_confirmed`。  
> **Approve 前必須有 canonical printing**；printing 變動 → fingerprint stale → 舊審批失效。

---

## 狀態機

```text
snk_image_ingest
  → pending_review / public_allowed=0  (landing only)

immutable canonical-db-qc report + receipt
  → enroll-from-qc  (marketRank 排序；quick-win blockers)
  → queue.jsonl pending  (fingerprint 綁 exact SNK + printing + qcRunId + evidence)

approve (human|vision)
  → 必須 in pending queue（禁 DB-only bypass）
  → live re-verify: exact-1 SNK · asset/raw/path · fingerprint · printing
  → receipts/{sha}.json
  → QC confirmed + 只 promote 該 asset/sourcePath
  → manifest 由 DB rebuild upsert

full QC / strict scan / local canary
```

---

## 命令

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
$env:CARDZ_DB_HOST = "127.0.0.1"
# load data\runtime\config\backend.env

python -X utf8 pipelines\snk_image_promotion.py multi-snk-audit
python -X utf8 pipelines\snk_image_promotion.py identity-quarantine-receipts --write
python -X utf8 pipelines\snk_image_promotion.py demote-false-public --write

# 由 immutable QC 生成 ranked queue（唔係 variant_id 盲排）
python -X utf8 pipelines\snk_image_promotion.py enroll-from-qc `
  --qc-run qc_20260729_sale_contract_01 --tcg one-piece --limit 34 --write

python -X utf8 pipelines\snk_image_promotion.py list-queue --status pending

# 僅 printing canonical 後先 approve
python -X utf8 pipelines\snk_image_promotion.py approve `
  --variant-id N --operator human --note "..." --write
```

`enroll`（舊）已 **deprecated** → 必須 `enroll-from-qc`。

---

## Fingerprint / Receipt 綁定欄

| 欄 | 用途 |
|---|---|
| variantId, snkId, snkIdentitySha256, assetId | exact source 身份／資產 |
| contentSha256, rawSha256, sourcePath | 圖內容 |
| **canonicalPrintingSha256** | printing 漂移失效 |
| **qcRunId**, qcReportSha256, qcReceiptSha256, cardEvidenceSha256 | QC 溯源 |
| queueFingerprint | approve recompute 必等 |

---

## Hard gates（approve）

| Gate | 失敗 reason |
|---|---|
| 不在 pending queue | `not_in_pending_queue` |
| exact SNK ≠ 恰好 1 | `multi_or_zero_snk` |
| snk / asset / hash / path 漂移 | `*_drift` |
| fingerprint 唔等 | `queue_fingerprint_stale` |
| 無 canonical printing | `canonical_printing_missing` |
| printing 變咗 | `printing_hash_drift` |
| 磁碟 hash 唔等 | `disk_content_hash_mismatch` |

`--allow-missing-printing` 已移除；production/dev 都冇 bypass。

---

## Identity quarantine（唔造數）

`identity-quarantine-receipts --write` 寫 append-only evidence JSON：

- multi-SNK variants（14 / 203 / 256 / 343 …）
- Newgate 1213 疑錯綁  

**唔** DELETE/UPDATE identity；只標記禁止 promote。清理完成亦唔刪舊
receipt，而係新增 self-hashed `identity_quarantine_resolution`，綁：

- 原 quarantine receipt SHA
- 唯一揀定嘅 exact SNK ID
- 清理前／後 source identity fingerprint
- actor、時間及 policy version

現況：14 / 203 / 256 / 343 已有有效 resolution；Newgate 1213 仍 active。

Failed pilot queue 存：

`data/runtime/private-source-map/snk-image-review/failed-pilot-evidence-*/`

---

## 正確大次序

1. ✅ Promotion hard gates + regression tests  
2. Identity quarantine evidence + append-only resolution  
3. **逐卡 printing approval receipt → materialize canonical printing**（否則 approve 全擋）  
4. 新 immutable full QC  
5. `enroll-from-qc` 真正 ranked quick-win  
6. 逐張 approve → strict scan → local canary  

Production pointer / WSL timer：**唔郁**。

---

## 測試

```powershell
python -m pytest tests\test_snk_image_promotion.py -q
```

---

## 變更

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 首版 pending/promotion |
| 2026-07-29 | Hard gates：QC-ranked enroll、禁 bypass、printing 綁 fingerprint、quarantine、tests |
| 2026-07-29 | Exact-only SNK fingerprint；4 個 multi-SNK 以 append-only resolution 解鎖，Newgate 保持 quarantine |
