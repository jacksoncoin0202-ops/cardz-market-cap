# Canonical Printing Materialization

> Canonical business DB：只可用 MySQL `cardz_market_cap`。  
> Exact GemRate / SNK binding 只係必要條件，唔等於完整 printing。  
> 本流程唔修改 observation、universe、image approval、pointer、timer 或 publisher。

## 正式流程

```text
latest immutable canonical DB QC
  → content-addressed per-card approval receipt
  → printing-plan (DB read-only + immutable private candidate)
  → printing-materialize dry-run (transaction rollback)
  → printing-materialize --apply (explicit only)
  → commit-time exact read-back
  → rerun same candidate = changedRows 0 / replayedRows N
```

## Approval receipt contract

檔名必須係實際 bytes 嘅 SHA-256：

`data/runtime/private-source-map/printing-decisions/<sha256>.json`

每張 receipt 必須包括：

- `type=canonical_printing_approval`
- `schemaVersion=1`、`decision=approve`
- `variantId`、CARDZ `opaqueId`
- `actor`、`approvedAt`、`policyVersion=canonical-printing-v1` 或
  `canonical-printing-v2-pc`
- 最新 `qcRunId` 及該卡 `cardEvidenceSha256`
- 七欄完整 identity：
  `tcgCode / cardLanguage / setName / collectorNumber / editionCode / parallelCode / finishCode`
- v1：恰好一個 exact GemRate 及一個 exact SNK binding
- v2-pc：恰好一個 exact GemRate 及一個 exact、numeric PriceCharting product ID
  binding；PC 只作 printing identity corroboration。只要同一 variant 有任何 SNK
  identity，v2-pc 一律 quarantine，必須走 v1 或先解決 SNK identity。兩個版本都
  不會從 binding 推斷七欄任何值。
- 每個欄位各自嘅 `rawValue / normalizedValue / evidenceType /
  extractorVersion / sourceCode / externalEntityId /
  sourceReceiptPath / sourceReceiptSha256`
- `evidenceType` 只接受 `human_verified_source_field` 或
  `vision_verified_source_field`；未有 deterministic extractor receipt 前，
  普通 `source_field` 一律 fail-closed

空值、JSON null、`unknown`、推斷 default、alias、multi-source ambiguity、
mutable filename、非 private evidence path、hash drift 或 existing canonical conflict
一律 quarantine，禁止自動補 `base / standard / regular`。

`cardLanguage` 係 physical-print identity，會入 canonical printing hash；唔係 UI locale，亦唔拆榜。新 worksheet 只會由 `catalog_variant.card_language` 帶入 current authoritative draft，仍然必須有每欄 human/vision source-field receipt 先可以 seal；agent 只可使用 exact source evidence，唔可以由名稱、set 或其他 hint 推斷語言。

## Operator commands

先產生 immutable candidate：

```powershell
python -X utf8 scripts\backend.py printing-plan `
  --qc-run <qc_run_id>
```

用輸出嘅 exact candidate path 同 plan SHA 做 rollback-only validation：

```powershell
python -X utf8 scripts\backend.py printing-materialize `
  --printing-candidate data\runtime\private-source-map\printing-candidates\printing_<sha>.json `
  --printing-plan-sha256 <sha>
```

只有 non-empty candidate、review 完成及 DADDY 明確批准 DB write 後，先加：

```text
--apply
```

`--apply` 遇到 0 approved rows 會以 `no_approved_printing_rows` 失敗，
唔會報假綠燈。

## Transaction gates

- 只接受 database name `cardz_market_cap`
- QC receipt/report `asOf` 必須有效且不得晚於 current UTC
- 固定取得 import → alert → universe → printing advisory locks
- lock 內重新驗 QC、decision manifest 及 active quarantine manifest
- `FOR UPDATE` 重驗 variant、alias、source ownership、pre/post DB fingerprint
- exact plan hash、receipt bytes、field evidence bytes全部要一致
- write 後逐欄 read-back tuple、status、printing hash、evidence hash
- 任何一項 drift：全 transaction rollback，release 所有 locks
- 同一 candidate 第二次 apply 必須係 exact no-op

## Ranked field review procedure（唔改 DB）

```powershell
# 按批准批量開 worksheet（marketRank；跳過 identity quarantine）
python -X utf8 pipelines\printing_review_batch.py open `
  --qc-run <qc_run_id> --limit <batch_size> --write

# 人手／vision 填 worksheets 內 cardLanguage/edition/parallel/finish + evidence receipts
# 封印成 content-addressed approval（仍唔 materialize）
python -X utf8 pipelines\printing_review_batch.py seal-worksheet `
  --worksheet data\runtime\private-source-map\printing-review-batches\batch_...\worksheets\variant_N.json `
  --actor human --write

# approved > 0 後先 rebuild plan + dry-run
python -X utf8 scripts\backend.py printing-plan --qc-run <qc_run_id>
python -X utf8 scripts\backend.py printing-materialize `
  --printing-candidate data\runtime\private-source-map\printing-candidates\printing_<sha>.json `
  --printing-plan-sha256 <sha>
# --apply 要 DADDY 明確批准
```
