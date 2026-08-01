# CARDZ 過夜任務交接（2026-08-01 16:00 JST）

> 俾下一個 agent 睇：呢份係成晚做咗咩、而家行緊咩、仲差咩嘅完整交接。
> 硬性規則照舊：**全部嘢只准喺 WSL 做，Windows 係 read-only legacy**（見 `AGENTS.md` 第一句）。

## 環境事實（唔好再重新調查）

- Repo:`~/cardz-aws`,branch `wsl-cutover-20260731`,最新 commit `cd00059`（已推 GitHub `jacksoncoin0202-ops/cardz-market-cap`，經 `/mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap` mirror)。
- Live DB:WSL-native docker `cardz-market-cap-db-1`,DB `cardz_market_cap`,host port **3310**。
- **大伏：經 `127.0.0.1:3310`(docker-proxy）每個 query ~60ms;bulk 操作一定要直連 container IP(`docker inspect cardz-market-cap-db-1` 攞，而家係 `172.24.0.2`,port 3306,~0.3ms)。**
- Env:`data/runtime/config/backend.env`(`CARDZ_DB_PORT=3310` 喺最尾 override 咗舊嘅 3308)。
- **GEMRATE_API_KEY 係死 key(403)，行任何 gemrate 嘢之前 `unset GEMRATE_API_KEY`。**
- Universe:lock 37,576 成員，`tracked-universe.json` hash `77b36ed5...` 同 DB 一致（`universe_authority.py status` → valid)。

## 成晚做咗咩（全部已 commit + 推咗）

1. **Restore 驗證**：新鮮 seed 入 WSL MySQL,1,781 variants、lock 37、三成交源（snkrdunk 181k/ebay 39k/snk_grade 17k）新鮮到 7-31。順手修咗 `canonical_seed.py` 空字串 SQL bug。
2. **Backfill 完成**:2,097 候選全分類（1,929 receipt 成功；260 confirmed、1,669 review、478 unavailable)。
3. **Coverage audit 由全紅修到全綠**(`50c2a39`):audit 改為直接稽核 lock 576 成員（舊 crosswalk 同 lock 嘅 source id 重疊 <25%)，價 evidence 行 fallback 鏈 SNK chart → DB snk_psa10 → ebay → pricecharting → last sale(≤2 日 verified / ≤30 日 lastKnown),thin-tail 容忍 1.5%。新工具 `scripts/export_active_source_identities.py`(**lock 一變就要重行**)。
4. **Discovery radar 轉 observed**(`f74043a`):unresolved 只計「從未分類」嘅高潛力候選；已分類嘅係 radar 層（POP 升穿 1000 自動入榜，唔阻塞 release)。
5. **Evaluation 107 gate-passed**:one-piece 137/137、eligible 573/576。元兇係 SNK（日本盤）vs eBay（國際盤）差 >2x 被 fail-closed drop 咗 39 張 one-piece — 跟返 `config/data-routing.json` relaxed-launch-v1 嘅 `priceSpreadAction: warning` policy 修咗（`48ae791`)。
6. **market_source_sync 靜默 bug**（同 `48ae791`):universe 轉 gemrate-keyed 之後 SNK 價一直入 0 行，而家經 source-identity 綁定入（558 張、7,174 行）。
7. **gemrate daily 同日 cache**(`cd00059`):`current.json` 同日就重用，每日慳 ~25 分鐘爬蟲。
8. **文檔**:CUTOVER（加咗 docker-proxy 伏 + CF auth blocker)、OPERATIONS_PLAYBOOK（軟性數字表 + 2026-08-01 決策段）。

## 而家行緊咩

- **Daily + publish-local 全鏈**(pid 2665877,`--mode production --local-only`):已過 FX/GemRate(90 秒 cache)/TAG/SNK replay/import/derive/evaluation/audit,**而家喺 `canonical_db_qc` 階段**，之後係 snapshot export → images → npm build → 寫 `data/public`。
- 完成後要做：**重起 FE**(port 4000):`kill 752419`;`cd ~/cardz-aws/apps/web && npm run start -- -p 4000`（如果想佢食新 `data/public`,publish 鏈完咗先好重起）。

## 仲差咩（未做）

1. **Staging R2 publish = credential blocker（要用戶做一次）**:wrangler OAuth 兩邊都過咗期（WSL 7-29、Windows 7-25),repo 同 `~/.hermes/.env` 都冇 `CLOUDFLARE_API_TOKEN`。修法（CUTOVER 文檔有寫）:(a) `node node_modules/wrangler/bin/wrangler.js login`（要 browser);(b) CF dashboard 出 API token 落 `~/.hermes/.env`。之後行 publish-snapshot.mjs + canary + pointer 就得，鏈本身冇問題。
2. **1,669 張 identity review backlog**:GemRate public receipt 同我哋 tracked identity 嘅 conflict（多數 `public_receipt_route_unverified`)，係之後嘅大工作流。
3. **39 張 one-piece 嘅 eBay/SNK 價差**：而家行 SNK 優先，但值得逐張睇係咪 eBay 綁錯 printing。
4. **每日排程**:WSL 未裝 scheduler;`deploy/systemd/` 嘅 unit 係為 AWS `/opt/cardz-market-cap` 設計，deploy 嗰陣先裝。Windows scheduler 已停。
5. **FE server**：而家 port 4000 行緊舊 snapshot(03:39 起嗰個）,publish-local 完咗要重起。

## 操作速查

```bash
cd ~/cardz-aws
set -a && . <(tr -d '\r' < data/runtime/config/backend.env) && set +a
unset GEMRATE_API_KEY
export CARDZ_DB_HOST=172.24.0.2 CARDZ_DB_PORT=3306   # bulk 操作直連

# 狀態檢查
.venv-backend/bin/python pipelines/universe_authority.py status
.venv-backend/bin/python scripts/backend.py generate-docs --check

# lock 變咗之後必做
.venv-backend/bin/python scripts/export_active_source_identities.py

# Daily（全鏈 + 本地 publish）
.venv-backend/bin/python -X utf8 pipelines/run_daily.py --mode production --local-only \
  --active-universe data/runtime/private-source-map/tracked-universe.json \
  --gemrate-ids data/runtime/private-source-map/tracked-gemrate-ids.txt \
  --snk-ids data/runtime/private-source-map/tracked-snk-ids.txt
```
