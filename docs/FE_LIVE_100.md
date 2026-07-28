# FE Live 100% 驗收

**日期**：2026-07-28/29（複核同日）  
**Snapshot**：`data/public/publish-staging/generations/canonical_live_fe/snapshot.json`  
**Pointer**：`data/public/publish-staging/latest.json` · `feSetComplete: true`

## 結果（複核 ALL=True）

| 集合 | n | 價 | POP | 市值 | 圖 | 史 | 成交 | 故事 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| top100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| watchlist | 129 | 129 | 129 | 129 | 129 | 129 | 129 | 129 |
| **FE_SET** | **229** | **100%** | **100%** | **100%** | **100%** | **100%** | **100%** | **100%** |

## DB 同前端係咪「同一件事」？

**係同一條組裝鏈，唔係兩個無關產品。**

```text
MySQL DB（原材料）
  → pipelines 採集／QC／identity
  → market_index_snapshot（排名）
  → canonical_public_snapshot.py（組裝）
  → publish-staging snapshot + pointer（出街包）
  → apps/web 只讀 snapshot（前端）
```

| 層 | 角色 |
|---|---|
| DB | 全池原料（可 >940；半殘 OK） |
| Snapshot FE_SET | 上板 229 張 · **要 100%** |
| 前端 | 展示 snapshot · 唔直連 DB |

所以：報告講「DB 夠／成交夠」= 原料；「FE 100%」= **已 bake 入 snapshot 嘅上板集合**。兩者同一組裝，層次不同。

## 重建

見 [DEPLOY_FOR_HANDOVER.md](DEPLOY_FOR_HANDOVER.md) §1.5 同 [AGENT_HANDOFF_INCREMENTAL.md](AGENT_HANDOFF_INCREMENTAL.md)。
