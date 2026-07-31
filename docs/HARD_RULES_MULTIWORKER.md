# 多工 HARD RULES（寫入系統 · 唔靠人「留意」）

**Authority:** 任何 PC / multi-worker 任務必須跟呢份。Agent 唔准寫「請你留意 Chrome」。

---

## R1 — CDP 先於一切 PC 動作

- 任何 PriceCharting fetch **之前** 必須 `http://127.0.0.1:9222/json/version` = 200。
- 唔得 → 跑 `scripts/ensure_chrome_cdp.ps1` 自救重開。
- 再唔得 → **raise / exit ≠ 0**，supervisor 下一輪再救。**禁止** 空轉等鎖。

**Code:** `pipelines/pc_full_shard_runner.py` → `ensure_cdp_or_raise()`  
**Script:** `scripts/ensure_chrome_cdp.ps1`

---

## R2 — PC-CDP 最優並行度 = 1

- 同一 Chrome debug port **只准 1 個** PC worker（serial 跑晒 shards）。
- 6 個 worker 搶一把 `cdp.lock` = 假並行 + 死鎖放大器。
- Supervisor 見到 >1 runner → **kill 晒再開 1 條 serial**。

**Script:** `scripts/pc_full900_supervisor.ps1`

---

## R3 — 鎖死自動清

- `cdp.lock` 內 PID 唔存在 → 即清。
- lock 年齡 > 90s 且 holder 無進展 → 清。
- **禁止** 人工「記得去刪 lock」。

---

## R4 — Resume 必須 idempotent

- 只限已驗證 `attached` / `attached_no_sold` / `skip_have_pc` 寫入 `results_shard_*.jsonl` → 重跑 skip。
- `resolve_mismatch`、`search_unresolved`、fetch/CDP/parse/exception 失敗會寫入 `data/runtime/failures/events/`，而且重跑時唔准當完成。
- Worker / 機重啟 = 繼續，唔從頭。

---

## R5 — Supervisor 長駐

```powershell
# 開餐 / 長任務：只開呢條，唔開 6 隻裸 runner
powershell -NoProfile -File scripts\pc_full900_supervisor.ps1
```

循環：CDP 健康 → worker 健康 → 過多就收斂 → 全 summary 先 exit 0。

---

## R6 — CF 人手

- CF challenge **預期會出現**；唔當「環境壞咗」。
- 等長 timeout；Chrome 窗開住等 click。
- **唔** 另開 5 個 worker 幫手「衝」CF。

---

## 反例（禁止）

| 禁止 | 原因 |
|------|------|
| 「你留意下 Chrome」當交付 | 人唔係 watchdog |
| 同時 6 shard 搶 CDP | 鎖死、假快 |
| CDP 死仲 hold lock | 全場停 |
| 無 ensure 就 fetch | 必現空轉 |

---

## 驗收

1. 手動 kill Chrome debug → 45s 內 supervisor log 有 `CDP_DOWN` + `CDP_OK after revive` + worker 再開。  
2. 手動開 6 runner → supervisor 收到 `TOO_MANY_WORKERS` → 收斂到 1。  
