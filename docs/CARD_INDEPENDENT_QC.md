# 卡獨立 QC（Collector 撞號 / 錯綁 / SAMPLE）

**日期：** 2026-07-29  
**觸發：** FE #3 Mega Charizard X 圖其實係 Rare Candy；+12576% 升跌；TPL 綁錯。  
**原則（用戶）：** 每張卡獨立 QC；關係綁錯就 **解綁**；用真卡圖／真源。

---

## 1. 問題模型

| 故障 | 典型症狀 | 根因 |
|---|---|---|
| **Collector 撞號綁錯** | 圖係 trainer、名係 chase；升跌萬級 % | TPL/eBay 只 match `125/132` 唔 match 物種 |
| **跨源價污染** | Venusaur $42k vs TPL $125 | eBay/G10 離群仍被當現價 |
| **SAMPLE 水印** | OP 卡面大字 SAMPLE | TCGplayer EN 商城圖出街 |
| **Universe 缺席** | 梵高有價有 POP 但唔上榜 | 冇入 watchlist/universe |

**鐵律：** `collector_number` 相等 **≠** 同一 printing。必須 **物種／卡名 semantic** 過閘。

---

## 2. 系統化流程（每次上板前）

```text
A. Identity QC（獨立於 collector）
   TPL slug / SNK title / eBay map
     → species|item token 對 card_name
     → 唔過：DELETE identity + 該源 poisoned prices
     → 過：保留

B. Price QC（排名用）
   pick_rank_price：可信源（SNK/TPL）優先
   eBay/G10 若 / 可信中位 > 5× → 拒
   （market_alerts.py）

C. Image QC（獨立於 identity）
   SAMPLE OCR / scan_sample_v2
     → 拒 publicAllowed
     → OP：Limitless _EN → G10 SNK CDN
     → PTCG：SNK upload_bg_removed / 原生 RGBA
   錯卡圖（號啱名唔啱）：撤銷 + 換真源圖 + 改 image-qc.json

D. 組裝
   market_alerts evaluate + mark-passed
   bake_publish_pack --sync-local-serve
   scan_sample_v2 → hits 必須 0
```

---

## 3. 現成工具

| 步驟 | 命令 / 檔 |
|---|---|
| TPL 錯綁掃描＋解綁 | `temp/agent-swarm-20260729/card_identity_image_qc_pass.py [--write]` |
| SAMPLE 掃描 | `temp/scan_sample_v2.py` |
| SAMPLE 換 Limitless | `temp/fix_sample_op_images.py` |
| 單卡錯 TPL+圖（Charizard 樣板） | `temp/agent-swarm-20260729/fix_mega_charizard_x_794.py` |
| 排名揀價 | `pipelines/market_alerts.py` → `pick_rank_price` |
| 手冊 | [CARD_SOURCING_HANDBOOK.md](CARD_SOURCING_HANDBOOK.md) · [SOURCE_QC_PATTERNS.md](SOURCE_QC_PATTERNS.md) · [FE_LIVE_100.md](FE_LIVE_100.md) |

---

## 4. 驗收清單（上板）

- [ ] TPL QC：`card_identity_image_qc_pass` bad=0 或已 write 解綁  
- [ ] SAMPLE：`scan_sample_v2` **hits=0**  
- [ ] Top1 唔應係 Celebrations Classic 普通再版帶萬級 %  
- [ ] 已知 chase（梵高比卡超等）在 universe + 有可信價  
- [ ] bake 後 hard-refresh FE 目視 3–5 張高風險卡  

---

## 5. 2026-07-29 已落地

| 修 | 結果 |
|---|---|
| Venusaur TPL/eBay 離群 | pick_rank_price；Classic 跌出 Top1 |
| 梵高入 universe | **#1** |
| Mega Charizard X TPL rare-candy + 圖 | 解綁 TPL；SNK 真圖；30d +12576%→**~-40%** |
| SAMPLE FE | scan → 2 hit → `fix_sample_op_images` Limitless |
| TPL 全池 scan | 解綁器 `card_identity_image_qc_pass.py`；species/item token 規則 |
| 離群價 purge | 刪 **781** 條 ebay/g10（>5× 可信中位） |
| Top100 \|30d%\| >200% | **0**（qc3 bake 後） |

### 常用一鍵（Windows）

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
python -X utf8 temp\agent-swarm-20260729\card_identity_image_qc_pass.py --write
python -X utf8 temp\agent-swarm-20260729\purge_price_outliers.py --write
python -X utf8 temp\scan_sample_v2.py
python -X utf8 temp\fix_sample_op_images.py
python -X utf8 pipelines\market_alerts.py --discovery-max-age-hours 99999 --coverage-status observed --unresolved-high-potential 0
# 再用 mark-passed + bake_publish_pack --sync-local-serve
```
