# Agent 交接：由 FE live → 全量池 → 增量研究

> 給下一位 agent／同事。**先讀** [PROJECT_STATE.md](../PROJECT_STATE.md) · [RECALL_VERIFY_OPS.md](RECALL_VERIFY_OPS.md) · [SESSION_RETRO_20260729.md](SESSION_RETRO_20260729.md)

---

## 0. 而家狀態（交接點）

| 項 | 狀態 |
|---|---|
| FE_SET（top100∪watchlist）素材 | **100%**（價/POP/市值/圖/史/成交/故事） |
| Snapshot | `data/public/publish-staging/generations/canonical_live_fe/` |
| Pointer | `data/public/publish-staging/latest.json` |
| 940 全池 | 未齊（正常）；增量繼續 |
| 主入口 | Windows · Docker MySQL `127.0.0.1:3308` · `CARDZ_DB_HOST=127.0.0.1` |

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
$env:CARDZ_DB_HOST = "127.0.0.1"
# load backend.env into env (never commit this file)
python -X utf8 pipelines\qualified_pool_operator.py status
```

---

## 1. 工作流（所有 agent 共用）

### 硬流程

```text
1. 讀 PROJECT_STATE + RECALL_VERIFY_OPS
2. status 量度現況（唔好靠記憶）
3. 低門檻 RECALL 多源撈
4. 腳本 VERIFY／QC（必過先寫 DB）
5. mark identity + registry + ledger
6. 需要上 FE → rebuild snapshot + pointer
7. 更新 PROJECT_STATE 數字
```

### 禁止

- 低門檻直接 INSERT  
- AI 感覺 OK 就 commit identity  
- WSL Python 跑 production（路徑 `\` 會爛）  
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
| 5 | G10 本地長史 | `g10_sales_cache_ingest --write --platforms snkrdunk` |
| 6 | 圖缺口（只 FE cut 或上板） | `fill-images` / `op_limitless_images` / Drive |
| 7 | 價缺口 | operator `map-tpl` / `harvest-tpl` / `ingest-prices` |
| 8 | 全源一條龍 | `full_volume_recall_verify.py --write --recall-min 20` |
| 9 | 上板 | `canonical_public_snapshot.py --view top300_boards` + 更新 pointer |

### 增量定義

- **有 registry 嘅卡**：只跑 `preferredLiquiditySource` 對應腳本  
- **無 registry**：recall→verify 發現 → mark → 下次變增量  
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

| 層 | 誰 |
|---|---|
| Verify 硬閘 | **腳本** `semi_auto_identity` |
| 編排／擴 alias | **Agent** |
| 殘渣 needsReview | Agent 半自動批次 → 仍寫同一 DB |
| 品味／法律 | 人 |

詳：[RECALL_VERIFY_OPS.md](RECALL_VERIFY_OPS.md)

---

## 4. 驗 FE 100%（每次 snapshot 後）

```powershell
python -X utf8 -c @"
import json
from pathlib import Path
d=json.loads(Path('data/public/publish-staging/generations/canonical_live_fe/snapshot.json').read_text(encoding='utf-8'))
cards=d['top100']+d['watchlist']
# expect all 7 fields ready for every card
print('n', len(cards))
"@
```

或重用 `temp/fe_live_readiness.py` 精神：top100+watchlist 七欄全綠。

---

## 5. 交接 checklist

- [ ] 讀 STATE + RECALL_VERIFY + 本檔  
- [ ] `status` 跑通  
- [ ] 知 FE_SET 100% 路徑同 pointer  
- [ ] 知 secrets 喺邊、唔 commit  
- [ ] 知 Windows Python 路徑  
- [ ] 下一優先：全池成交／identity 增量（§2）  
