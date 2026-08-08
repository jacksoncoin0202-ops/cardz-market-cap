# SNK kline dedup — 執行收據（2026-08-09）

執行藍本：[SNK_KLINE_DEDUP_PLAN_20260808.md](SNK_KLINE_DEDUP_PLAN_20260808.md)。
本收據記錄實際執行數字；同 plan 有偏差之處喺尾段明列。

## 結果一覽

| 項 | 數 |
|---|---|
| 刪前 kline scope 總行數 | 955,031 |
| 唯一 (item, day) 組數 | 162,374 |
| 冗餘行 | 792,657 |
| FK 保留行（effective_observation 引用舊行） | 8,477 |
| 實際刪除 | **784,180**（= 792,657 − 8,477，一粒不差） |
| 刪後 kline scope 行數 | **170,851**（= 162,374 keepers + 8,477 FK-kept ✓ 精確重驗） |
| 組數保全 | 162,374 → 162,374（不失） |
| Orphan 檢查（3 FK zero-join） | 0 / 0 / 0（刪前 gate + 刪後收貨均通過） |
| 全表行數（刪後精確 COUNT） | 767,531 |

## 對數鏈（同 plan 數字對返）

- Plan 時點（2026-08-08）：952,181 行 / 162,148 組 / 790,033 冗餘 / 781,557 可刪。
- 執行時點多咗 +2,850 行：rebuild S8 backfill 重跑對 7 個重新 fetch items 全歷史重插（run_id **7869**；8127/8128/8129 無辜，0 kline 行）。
- 955,031 = 952,181 + 2,850；162,374 組 = 162,148 + 226 新日；792,657 冗餘 = 790,033 + 2,624；delete-set 784,180 = 781,557 + 2,623。

## 執行程序（實際）

1. **備份**：`data/private/backups/snk_kline_pre_dedup_20260809.sql.gz`（56.7MB；`gzip -t` OK；dump footer `Dump completed on 2026-08-08 18:21:51`）。
2. **物化 delete-set**：`_dedup_snk_delete_ids` 表，784,180 ids。
3. **三重 FK zero-join gates**：effective_observation / price acceptance / sale acceptance 對 delete-set join 全部 0 行先開刪。
4. **Cursor-driven 批刪**：10k/批 × 79 批（尾批 4,180），批間 1s，錯即停；總刪 784,180。
5. **收貨**：組數 162,374 不失；kline scope 剩 170,851；orphan 0/0/0。
6. **清場**：DROP `_dedup_snk_delete_ids`。
7. **OPTIMIZE TABLE**：InnoDB 行 recreate + analyze，status OK。

## 空間回收

| 量度 | 刪前 | OPTIMIZE 後 |
|---|---|---|
| information_schema data_length | 651.0 MB | **308.8 MB**（`information_schema_stats_expiry=0` fresh 讀） |
| information_schema index_length | 397.6 MB | **170.2 MB** |
| `.ibd` 檔案（磁碟真身） | —（未記錄） | **515,899,392 bytes ≈ 492 MB** |

估算取回 ~570 MB（estimate-vs-estimate）。注意：OPTIMIZE 後即刻查 information_schema 會見到舊數（MySQL 8 stats cache 預設 24h），要 `SET SESSION information_schema_stats_expiry=0` 先見真身。

## 根因修（已落 code）

- Commit **6f90a8a7**（已 push）：`pipelines/snk_market_data.py` evidence-level dedup — 任何 mode 下 (卡, 日) 價值同 persisted latest 相同就 reuse persisted row，唔開新 source row；同 run 內同 (entity, day) 後者勝。
- 根因：uq 鍵含 `payload_sha256`，而 payload 嵌 `fetchedAt` → 每次 fetch sha 必不同 → uq 攔唔住重插；69a3d18f 只修咗 incremental path，rebuild S8 backfill path 照插全歷史。
- 實證：`scratchpad/test_kline_dedup_proof.py`（91118 真數據 744/744 dedup、0 insert、rollback 淨）。
- 刪除 driver：`scratchpad/dedup_delete_driver.sh`。

## 同 plan 嘅偏差

1. **執行帳號**：plan 寫 `cardz_rebuild`；實際用 root（`cardz_rebuild` 已喺 unfreeze 時 drop）。
2. **刪除量**：plan 781,557 → 實刪 784,180（S8 重跑 +2,850 行帶入 2,623 額外冗餘；對數鏈見上）。
3. **豁免行**：8,477 行雖冗餘但被 effective_observation 引用，保留（#19 audit 收尾時再處理指向）。
