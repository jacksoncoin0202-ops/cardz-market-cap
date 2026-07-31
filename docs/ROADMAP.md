# CARDZ Market Cap — 路線圖（短／中／長 + 距夢想差幾遠）

> **定位**：產品與架構階段規劃；唔取代 `PROJECT_STATE.md`（當日現況）。  
> **寫於**：2026-07-29 · 對齊用戶：短期上線 → 中期執靚內部 → 長期擴數據／API／整合 DB。  
> **前端 cut 固定**（top N / Grading≤100）；合資格池可 940→1000+。  
> **前端契約**：短期仍 **DB → snapshot → FE**；長期可加 **read API / read model**，唔係頁面直啃 raw 表。

---

## 0. 兩個「L」——唔好撈亂

本 repo 有兩套編號；**同時用、唔互相取代**。

### 0.1 Agent 任務級（`AGENTS.md`）— 開工先分級

| 級 | 名 | 做咩 | 動 MySQL / Docker？ |
|---|---|---|---|
| **L0** | Research / disposable | 一次性爬／分析／mock | ❌ |
| **L1** | Frontend-only | 改 UI，食現有 snapshot | 通常 ❌ |
| **L2** | Collector / pipeline | 單一源、單一腳本、針對測試 | 可 |
| **L3** | Production data / runtime | schema、日更、publish、契約 | ✅ 全套閘 |

### 0.2 運行時架構層（系統分層）— 數據點樣行

```text
┌─────────────────────────────────────────────────────────┐
│  Layer FE（展示）  apps/web · 固定 cut · 多語/多幣      │  ≈ 任務 L1 主戰場
├─────────────────────────────────────────────────────────┤
│  Layer Pub（出街包） snapshot generation · validate     │  handshake 邊界
│            pointer · 可加 BFF/read API（中後期）          │
├─────────────────────────────────────────────────────────┤
│  Layer Canon（權威庫） MySQL cardz_market_cap          │  ≈ 任務 L2/L3
│            identity · 價 · POP · 成交 · 圖 pointer       │
├─────────────────────────────────────────────────────────┤
│  Layer Ingest（採集） pipelines · GemRate/TPL/SNK…     │  ≈ 任務 L2
│            全量→增量 · fail-closed bind                  │
└─────────────────────────────────────────────────────────┘
```

| 架構層 | 而家狀態（2026-07-29） | 夢想態 |
|---|---|---|
| **Ingest** | TPL/SNK/GemRate 半全量；增量未開 | 日更穩、多源、可觀測 |
| **Canon DB** | 唯一業務庫已有；表多、身份/QC 仍亂 | 乾淨 read model + 治理 |
| **Pub** | snapshot 鏈存在；FE 卡未全綠 | versioned publish + 可選 live API |
| **FE** | 頁面齊（榜/分TCG/Grading/內頁）；數據靠 seed/LKG 參差 | 固定 cut 永遠靚；後期更多組合視圖 |

---

## 1. 夢想三階段（產品）

| 階段 | 名 | 目標一句 | 前端 | DB / 後端 |
|---|---|---|---|---|
| **S1 短期** | **可上線 Beta** | 訪客見到真市值榜、唔空、唔假 0 | 固定 cut 有價+圖+POP；Grading≤100 | FE 卡成套；snapshot 出街；日更可先手動/半自動 |
| **S2 中期** | **內部執靚** | 池可過 1000 都唔崩；自動化；債務清 | 同一 cut 更穩、故事/成交補 | identity/QC 治理、增量 daily、觀測/告警 |
| **S3 長期** | **擴展平台** | 更多數據組合 + 受控 API + 靚整合層 | 新視圖/組合（仍固定 viewport 哲學） | provider API 擴、read model、整合 DB 視圖 |

**池大小**：`POP≥1000` 合資格可以 **>1000 張**——正常。  
**前端唔跟住脹**：永遠係排序 + top N（總榜/分TCG/Grading 各≤100 等）。

---

## 2. 而家企喺邊（對夢想）

### 2.1 完成度粗估（主觀但可驗）

| 能力 | S1 需要 | 而家 | 距 S1 |
|---|---|---|---|
| 合資格池 + 身份 | 有 variant | ✅ ~940（會再增） | 細 |
| PSA10 價 → 市值 | FE 卡幾乎全有 | 全池 **916/940** 有價；FE 入圍靠市值 sort | 中：鎖 FE set + 出 snapshot |
| 固定 cut 前端 | 頁面 + 真數 | 頁面 ✅；生產 generation 仍常靠舊 LKG/~258 | **主缺口：新 generation 出街** |
| FE 圖 | 上板卡齊 | 全池 ~713；FE 缺圖 ~百級 | 中：只補 FE |
| Grading 頭 100 | 五家有 POP 可排 | PSA 厚；TAG 瘦但≤100 夠策略 | 中低 |
| 日史 / Δ | 榜+內頁 | TPL 短窗多；長 K 少 | 中：FE 夠 30d 即可 S1 |
| 成交 | partial OK | 全池 ~95 有 sale | 低優先 S1 |
| 故事四語 | production 嚴 | 很少 | S1 可隱藏空；S2 補 |
| 日更增量 | 上線後要活 | **未開**（全量優先） | S1 尾／S2 頭 |
| AWS/Node 部署 | Beta 可擺 | 文檔/Docker 有；cutover 未完 | 中 |
| 直連 raw DB | — | **唔做**（正確） | S3 先考慮 read API |

**一句：S1 進度大約 55–65%。**  
最大距離唔係「再爬全世界」，而係：**把市值可排嘅 FE 卡砌成可 publish 嘅 generation + 部署 pointer。**

### 2.2 距 S2 / S3

| 階段 | 粗估完成度 | 主要欠 |
|---|---|---|
| **S2** | ~25–35% | 增量 daily、identity queue、QC 真閘、池>1000 運維、告警 |
| **S3** | ~5–10% | 正式 public API、多視圖組合、整合 read DB、付費/更多源（若開） |

---

## 3. S1 短期 — 可上線 Beta（建議 1–3 週量級*)

\*曆日視人手；單線 agent 連做可更短，等人／部署可拉長。

### 3.1 目標

- 訪客打開：**真 PSA10 市值榜**（總 / 寶可夢 / 海賊王）、**Grading 各≤100**、內頁唔專業空洞。  
- 數據真、缺位隱藏、**唔假 0**。  
- 出街路徑：**DB → snapshot → FE**（同而家契約）。

### 3.2 工作包（按 checklist 填滿序）

| 序 | 工作 | 任務級 | 驗收 |
|---:|---|---|---|
| S1.1 | FE 卡集鎖定（市值 sort → top100+watchlist） | L2 | 名單可重跑；見 `FRONTEND_REQUIREMENT_CHECKLIST` |
| S1.2 | 價尾巴只打「可能入 FE」嘅無價卡 | L2 | FE 候選有價率目標 ≥95% |
| S1.3 | FE 缺圖 fill + raw_front QC 記錄 | L2 | FE 卡圖無大片 placeholder |
| S1.4 | FE 日史夠 1d/7d/30d（TPL 窗即可） | L2 | Δ 多數 ready 或明確 unavailable |
| S1.5 | Grading：FE 內五家 POP 盡量齊（TAG 有就有，唔硬 940） | L2 | 每頁≤100 有 rank；空 tab 可接受若 0 卡 |
| S1.6 | `canonical_public_snapshot` 出 production generation | L3 | validate 過；pointer 可切 |
| S1.7 | 部署 Node/AWS（或現有 runtime）掛 pointer | L3 | health + 榜頁真數；操作人拍板上線 |
| S1.8 | 增量 daily **最小集**（TPL incremental + POP scan） | L3 | 上線後 2 日有新數（可 S1 尾或 S2 頭） |

**S1 明確不做**：全池 1000+ 張張圖/故事；前端直連 MySQL；新大功能頁；付費 API。

### 3.3 S1 出口標準（Beta）

- [ ] FE_CARD_SET 每張有：身份、PSA10 價、POP、marketCap、rank  
- [ ] 上板圖可接受（raw_front；semantic 可仍 unreviewed，標明風險）  
- [ ] `/` `/pokemon` `/one-piece` `/graders/*` 有卡、cut 唔脹  
- [ ] generation 非 demo（或 demo 閘顯式 + 操作人同意）  
- [ ] 回滾：可切返上一 pointer  

---

## 4. S2 中期 — 內部執靚（建議 +1–2 個月量級）

| 序 | 工作 | 任務級 | 價值 |
|---:|---|---|---|
| S2.1 | 池自動增長（>1000）+ watchlist 衛生 | L3 | 宇宙長大前端仍穩 |
| S2.2 | 日更全自動 + 鎖 + 告警 | L3 | 唔靠人手全量 |
| S2.3 | Identity review 清積壓 / alias 治理 | L3 | 少錯卡 |
| S2.4 | 圖 QC：DB ↔ manifest 一致；strict semantic 路徑 | L2–L3 | 出街更硬 |
| S2.5 | 成交／故事：只補 FE | L2 | 榜同內頁更飽 |
| S2.6 | SNK exact 擴 + 長 K（有 bind 先） | L2 | 內頁專業感 |
| S2.7 | 觀測性：coverage dashboard、verify 常態 | L2 | 知漂 |
| S2.8 | （可選）**Read API v0**：HTTP 讀 **同一 generation snapshot** 或物化榜表 | L3 | 「似 live」但仍唔裸 DB |

**S2 不做**：頁面 `SELECT` raw observation；為 Grading 備第 101+ 張。

---

## 5. S3 長期 — 擴展（季度級）

| 序 | 工作 | 說明 |
|---:|---|---|
| S3.1 | 更多**前端數據組合** | 新 view（例：成交熱、POP 增速、分 set）——仍 **固定 N + 排序**，池再大 viewport 唔脹 |
| S3.2 | **Provider / 內部 API** 增多 | 有 key 或無 key 源擴；統一入 Canon |
| S3.3 | **Read model / 整合 DB** | 物化「卡寬表」、榜表；BFF 只讀呢層 |
| S3.4 | 準實時 | 短 TTL cache + read model 刷新；**仍唔推薦 FE 直連 raw** |
| S3.5 | 多租戶／多市場／權限 API | 真·平台化 |

呢層先係「靚整合 DB」夢想落地處。

---

## 6. 時程總覽（示意）

```text
而家 ──────────────────────────────────────────────────────────► 夢想
 |         S1 Beta 上線          |      S2 執靚 / 自動化      |    S3 平台
 |  FE填滿→snapshot→deploy     |  daily·治理·可選 read API |  組合視圖·整合層
 |  ████████░░░░  ~60%         |  ███░░░░░░░░  ~30%        |  █░░░░░░░░ ~10%
 |  目標：1–3 週*              |  目標：+1–2 月*           |  季度+*
```

\*人手、審批、部署環境會左右曆日；用 **出口標準** 唔用死日期當唯一真相。

### 「仲爭幾耐」直答

| 問 | 答 |
|---|---|
| 爭幾耐 **Beta 上線**？ | 以而家數據：**主要欠 FE 成套 + 新 snapshot publish + 部署切 pointer**。單線衝刺量級 **約 1–3 週**；若只算「可 demo 真數本機」可更短。 |
| 爭幾耐 **中期乾淨**？ | **約 1–2 個月** 日更+治理上軌道。 |
| 爭幾耐 **夢想平台**？ | **數季**；S1/S2 未穩唔好開 S3 大工程。 |

---

## 7. 每階段預設任務級用法

| 階段主戰場 | 常用任務級 |
|---|---|
| S1 補 FE 數、出 snapshot | **L2** 為主，publish/deploy **L3** |
| S1 純 UI 遮醜 | **L1** |
| S2 schema/日更/API | **L3** + 局部 **L2** |
| S3 探索新源 | 先 **L0** 驗證，再 L2/L3 接入 |

架構所有權仍只認：`config/data-routing.json`。  
現況數字只認：`PROJECT_STATE.md` + `qualified_pool_operator status`。  
FE 處方只認：`FRONTEND_REQUIREMENT_CHECKLIST.md`。

---

## 8. 風險（會拖長「幾耐」）

| 風險 | 拖邊段 | 緩解 |
|---|---|---|
| 無價／錯綁 | S1 | fail-closed；FE 先 |
| 圖 QC 太嚴上唔到 | S1 | Beta 允許 metadata unreviewed + 標風險 |
| GemRate / 源掛 | S1–S2 | 多源價；POP 權威保留 |
| 未開增量就上線 | S1 後腐爛 | S1.8 / S2.2 盡快 |
| 過早直連 raw DB | 全線 | 堅持 snapshot → 可選 read model |
| 池>1000 當前端要 1000 張圖 | 成本爆炸 | **cut 固定** |

---

## 9. 下一步（而家就做）

1. 執行 **S1.1–S1.5**（FE 填滿序）— 見 checklist。  
2. **S1.6** 出可 validate 嘅 generation。  
3. **S1.7** 部署 + 操作人拍板 Beta。  
4. 上線後立刻排 **S2.2 增量 daily**。  

收工更新 `PROJECT_STATE.md` §0；本路線圖只在階段目標變時改。

---

## 10. 文檔關係

| 檔 | 管 |
|---|---|
| **本檔 `ROADMAP.md`** | 短中長、距夢想、S1/S2/S3 |
| `PROJECT_STATE.md` | 今日數字與在做邊件 |
| `FRONTEND_REQUIREMENT_CHECKLIST.md` | FE 卡與欄位處方 |
| `BETA_PLAN_20260726.md` | 舊 Beta 計劃（歷史；以本檔 + STATE 為準若衝突） |
| `AGENTS.md` | L0–L3 任務級 |
| `FRONTEND_HANDSHAKE.md` | FE 唔直讀 MySQL |
