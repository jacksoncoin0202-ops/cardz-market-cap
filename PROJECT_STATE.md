# PROJECT_STATE — CARDZ Market Cap

> MACHINE-GENERATED；唔好手改數字。唯一來源係 `python -X utf8 scripts/backend.py status --json`。
> asOf: **2026-07-29T11:37:11Z**

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
python -X utf8 scripts\backend.py status --json
python -X utf8 scripts\render_project_state.py
```

## Release gate

- Status: **BLOCKED**
- Eligible: **NO**
- Read-only status latency: **172 ms** (target < 5000 ms)

## Release blockers

- `universe_candidate_no_qualified_printings`
- `canonical_db_qc_failed`
- `generation_id_mismatch`
- `generation_hash_mismatch`
- `generation_not_production`
- `generation_not_eligible`
- `generation_declares_blockers`
- `generation_pointer_generated_at_mismatch`
- `generation_qc_receipt_key_invalid`
- `generation_pointer_qc_receipt_hash_invalid`
- `generation_media_hashes_invalid`
- `generation_media_assets_invalid`
- `generation_media_remote_unverified`

## Canonical database

| Field | Value |
| --- | --- |
| `authority` | canonical_mysql |
| `name` | cardz_market_cap |
| `connected` | yes |

## Universe integrity

| Field | Value |
| --- | --- |
| `activeFileError` | — |
| `activeFileHash` | a422e940db08877ef889f37530cac77da79c8af0ea06e05624d9e8f48312ceee |
| `candidateDiscoveryEvidence` | 932 |
| `candidateHash` | — |
| `candidateMembers` | 0 |
| `candidateMonitoring` | 0 |
| `candidateQualified` | 0 |
| `currentDeclaredMembers` | 997 |
| `currentDistinctVariants` | 997 |
| `currentHash` | a422e940db08877ef889f37530cac77da79c8af0ea06e05624d9e8f48312ceee |
| `currentLockId` | 23 |
| `currentStoredMembers` | 997 |
| `database` | cardz_market_cap |
| `matchesCandidate` | no |
| `status` | blocked |

## QC coverage

| Field | Value |
| --- | --- |
| `failedIngestRuns` | 0 |
| `fullDbQcAgeHours` | 2.57 |
| `fullDbQcAsOf` | 2026-07-29T09:02:59Z |
| `fullDbQcQualified` | 932 |
| `fullDbQcReleaseBlocked` | 932 |
| `fullDbQcReleaseReady` | 0 |
| `fullDbQcReportSha256` | 29eac6ddd9b44e7324f9ab66c05a786c4396655a08d12435c5190c1b866b0e26 |
| `fullDbQcRunId` | qc_20260729_sale_contract_01 |
| `fullDbQcStatus` | blocked |
| `imageChecksPassed` | 1076 |
| `imageChecksRejected` | 1358 |
| `status` | available |

## Pending reviews

| Field | Value |
| --- | --- |
| `identityReviews` | 89 |
| `ingestRuns` | 0 |
| `openAlerts` | 50 |
| `status` | available |

## Generation

| Field | Value |
| --- | --- |
| `ageHours` | 4.911887 |
| `computedContentSha256` | bb3e531ee2f311ede59188e80c817cebb22ba6e7f62da3d62dce21874c8a00dd |
| `contentSha256` | bb3e531ee2f311ede59188e80c817cebb22ba6e7f62da3d62dce21874c8a00dd |
| `effectiveAt` | 2026-07-29T00:00:00Z |
| `generatedAt` | 2026-07-29T06:42:29.149133Z |
| `generationId` | canonical_20260729_e19_5dcedcbb9c48_20260729T064229149133Z |
| `mode` | demo |
| `pointerGenerationId` | plan_a_qc3_20260729_154227 |
| `pointerPresent` | yes |
| `pointerQcReceiptSha256` | — |
| `pointerSha256` | e15ffd0cb7926fc014f4a75e7ad052cab3ff1434005a179f812d4888d894c5da |
| `productionEligible` | no |
| `qcReceiptFileSha256` | — |
| `qcReceiptKey` | — |
| `qcReceiptSha256` | — |
| `status` | blocked |

## Control plane

- Ownership、SLA、tool owner 同 work-item evidence：[generated tool registry](docs/generated/TOOL_REGISTRY.md)
- Data flow：[generated lineage](docs/generated/DATA_LINEAGE.html)
- 修復前手寫狀態已原樣封存：[PROJECT_STATE_PRE_QC_20260729.md](docs/archive/PROJECT_STATE_PRE_QC_20260729.md)
- Production promotion、writer/timer enablement 仍要 DADDY 明確批准。
- **Price full review（2026-07-29）：** [docs/PRICE_FULL_REVIEW_20260729.md](docs/PRICE_FULL_REVIEW_20260729.md) · 證據 [docs/evidence/2026-07-29-price-review/](docs/evidence/2026-07-29-price-review/) · 重跑 `python -X utf8 scripts/price_full_review.py`
