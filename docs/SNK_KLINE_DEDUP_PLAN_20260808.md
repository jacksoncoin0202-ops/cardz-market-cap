# SNK kline 重複 observation 清理計劃（2026-08-08）

狀態：**報告 only — 未執行任何 DELETE**。Writer freeze 生效中；本報告全程用 read-only `cardz@%` SELECT 取數。

背景：commit `69a3d18f`（2026-08-08 22:05 +0900）之前，`pipelines/snk_market_data.py` 每次 incremental poll 都將每張卡全段 ~3 年 kline 當新行插入 `market_source_observation`。因為 `uq_market_source_observation` 包含 `payload_sha256`，而 payload 內嵌 `fetchedAt`，每次 poll 嘅 sha 都唔同，unique key 完全冇擋到（`pipelines/migrations/002_market_observations.mysql.sql:52`）。

## 1. 膨脹實測（全部 [KNOWN]，2026-08-08 由 live DB SELECT 取得）

| 指標 | 數值 |
|---|---|
| `market_source_observation` 總行數 | 1,548,401 |
| 表大小（data + index） | 1,048.6 MB（651.0 + 397.6） |
| SNK kline 行（`source_code='snkrdunk'`, `observation_kind='psa10_reference_price'`） | **952,181**（全表 61%） |
| 唯一 (external_entity_id, observed_date) 組 | 162,148 |
| 唯一卡（external_entity_id） | 715 |
| 冗餘行（行數 − 組數） | **790,033**（SNK kline 83%） |
| 每組 copies：平均 / 最多 | 5.87 / 21 |

分佈（copies per group → 組數）：1×42,766、4×14,007、6×8,327、**7×61,411（大宗）**、8×16,232、14–21 之間仲有 ~13,300 組。

插入時間線：08-04 895 行 → 08-05 7,983 → **08-06 815,029（爆炸日）** → 08-07 8,010 → 08-08 120,264。最後一次 SNK 插入 2026-08-08 10:45（DB 時間，[INFERRED] UTC），早過 fix commit 13:05 UTC — 出血應已止，但**未有 post-fix run 實證**。

抽樣實證（entity 同一張卡、observed_date=2025-01-08）：21 行 `effective_at` 完全相同，21 個唔同 `payload_sha256`，`created_at` 橫跨 08-05 至 08-07 嘅多次 poll — 機制同背景描述完全吻合。

## 2. FK 依賴（live DB information_schema，[KNOWN]）

live DB 有 **3 條 FK** 指住 `market_source_observation(id)`，全部 `ON DELETE NO ACTION`（= RESTRICT，誤刪會被 DB 擋）：

| 引用表 | 欄 | 落喺 SNK kline 嘅 refs | 指 keeper（組內 max id） | 指舊重複行 |
|---|---|---|---|---|
| `market_price_observation` | `source_observation_id` | 161,878 | **161,878（100%）** | 0 |
| `market_source_effective_observation` | `observation_id` | 8,784 | 319 | **8,465** |
| `market_source_observation_payload_pointer` | `observation_id` | 8,795 | 319 | **8,476** |

要點：
- `market_price_observation` 完全冇阻力 — 036 backfill 已將全部 ref 指向每組最新行。
- effective/payload_pointer 有 ~8.5k refs 指住舊重複行（archive 時 pin 死當時嗰行）。呢啲行**唔刪、唔重指** — `payload_pointer.content_sha256` 對應嗰行嘅實際 payload，重指會斷 archive provenance。
- 004 migration 檔冇呢啲 FK — 係後期 migration 加嘅。**任何 delete 計劃唔可以齋信 migration 檔，要以 live information_schema 為準。**

## 3. Keep / Delete 定義

**Keep-set**（union）：
1. 每組 keeper = `MAX(id)` per (external_entity_id, observed_date) — 即最新 `created_at`／最新 fetchedAt payload：162,148 行
2. 任何被三張 FK 表引用嘅行：額外 +8,476 行（8,465 同 effective 重疊）

**Delete-set**：SNK kline 行、非 keeper、且三張表零引用 = **781,557 行**（[KNOWN] 已用完整 NOT EXISTS 謂詞實數）。id 範圍 2,572,962 – 3,974,921。

對數：790,033 冗餘 − 8,476 被引用保留 = 781,557 ✓

空間估算 [COMPUTED]：781,557 / 1,548,401 × 1,048.6 MB ≈ **530 MB** 邏輯空間；實際還碟要 `OPTIMIZE TABLE`（見 step 6）。

## 4. 執行步驟（批准後先做；用 `data/runtime/config/rebuild.env` 嘅 `cardz_rebuild` 寫入帳號）

```sql
-- Step 1：物化 delete id 清單（可重跑、可審計）
CREATE TABLE _dedup_snk_delete_ids (id BIGINT UNSIGNED NOT NULL PRIMARY KEY);
INSERT INTO _dedup_snk_delete_ids (id)
SELECT mso.id
FROM market_source_observation mso
JOIN (SELECT external_entity_id, observed_date, MAX(id) AS keep_id
      FROM market_source_observation
      WHERE source_code='snkrdunk' AND observation_kind='psa10_reference_price'
      GROUP BY external_entity_id, observed_date) g
  ON mso.external_entity_id=g.external_entity_id AND mso.observed_date=g.observed_date
WHERE mso.source_code='snkrdunk' AND mso.observation_kind='psa10_reference_price'
  AND mso.id <> g.keep_id
  AND NOT EXISTS (SELECT 1 FROM market_price_observation mpo WHERE mpo.source_observation_id=mso.id)
  AND NOT EXISTS (SELECT 1 FROM market_source_effective_observation e WHERE e.observation_id=mso.id)
  AND NOT EXISTS (SELECT 1 FROM market_source_observation_payload_pointer pp WHERE pp.observation_id=mso.id);
```

```sql
-- Step 2：三項 gate，全部要過先落 Step 3
SELECT COUNT(*) FROM _dedup_snk_delete_ids;             -- 必須 = 781,557（±post-fix 新增日造成嘅細差；大偏差即停）
SELECT COUNT(*) FROM _dedup_snk_delete_ids d
  JOIN market_price_observation mpo ON mpo.source_observation_id=d.id;      -- 必須 = 0
SELECT COUNT(*) FROM _dedup_snk_delete_ids d
  JOIN market_source_effective_observation e ON e.observation_id=d.id;       -- 必須 = 0
SELECT COUNT(*) FROM _dedup_snk_delete_ids d
  JOIN market_source_observation_payload_pointer pp ON pp.observation_id=d.id; -- 必須 = 0
```

```sql
-- Step 3：分批刪（10k/批 ≈ 79 批；批間 sleep 1-2s 減 binlog/replica 壓力）
-- 由 min_id 2,572,962 開始，按 id 段推進；FK RESTRICT 係最後保險 — 任何一批炸 FK 即全停覆盤
DELETE mso FROM market_source_observation mso
JOIN _dedup_snk_delete_ids d ON d.id = mso.id
WHERE d.id BETWEEN @lo AND @hi;   -- 每批一段，執行層記錄每批行數
```

```sql
-- Step 4：收貨驗證
SELECT COUNT(*) FROM market_source_observation
 WHERE source_code='snkrdunk' AND observation_kind='psa10_reference_price';  -- ≈ 170,624（162,148 keeper + 8,476 保留被引用行）
SELECT COUNT(*) FROM (SELECT external_entity_id, observed_date
  FROM market_source_observation
  WHERE source_code='snkrdunk' AND observation_kind='psa10_reference_price'
  GROUP BY external_entity_id, observed_date) t;                             -- 必須仍 = 162,148（一組都冇消失）
SELECT COUNT(*) FROM market_price_observation mpo
  LEFT JOIN market_source_observation mso ON mso.id=mpo.source_observation_id
 WHERE mpo.source_observation_id IS NOT NULL AND mso.id IS NULL;             -- 必須 = 0
```

Step 5：`DROP TABLE _dedup_snk_delete_ids;`（保留 Step 2/4 輸出做 receipt）

Step 6（可選、另排時段）：`OPTIMIZE TABLE market_source_observation;` 攞返 ~530 MB 實碟。InnoDB online rebuild，需要 ~1× 表大小嘅臨時碟位，尾段有短暫鎖。

## 5. Go / No-Go

**技術上 GO — 但唔係而家**。方案 FK-safe（delete-set 已證零引用 + RESTRICT 兜底）、可審計（物化 id 表 + 前後 gate）、可分批回退（隨時停，數據唔會爛，最多刪剩啲重複）。

執行前置條件（全部滿足先開波）：
1. **Writer freeze 解除／036 rebuild 到達 checkpoint** — owner 拍板。而家 rebuild 可能 in flight，期間唔准郁。
2. **一次 post-fix incremental run 實證**只插新日行（~715 行/日，一卡一行）— fix 係今日 13:05 UTC 先 commit，未有 run 行過。冇呢步，清完會再髒。
3. 刪前 backup（mysqldump 該表或 Docker volume snapshot）。
4. 對照 out-of-band writer baseline（docs 有 identity-forensics 記錄；掂 DB 前照舊程序過一次）。

後續 hygiene（唔擋本次清理）：SNK kline 162,148 組入面只有 8,784 組有 `market_source_effective_observation` 行，當中 8,465 指住舊重複行 — effective selection 對 SNK kline 嚟講似乎未跑全／未重選，值得另開單跟。

—
取數方法：全部經 `docker exec cardz-market-cap-db-1 mysql -u cardz`（SELECT-only）於 2026-08-08 執行；表大小來自 `information_schema.tables`。
