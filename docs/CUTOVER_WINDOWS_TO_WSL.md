# Cutover runbook:Windows → WSL(2026-07-31 完成)

**狀態**:完成。WSL 係唯一 runtime;Windows 環境已退役（read-only legacy)。
**規矩**:由 2026-07-31 起，所有 build/test/daily/publish 只准喺 WSL(Linux）行。見 `AGENTS.md` 第一句。

## 點解有今次 cutover

過往唔同 session 分別喺 Windows 同 WSL 起嘢，搞到兩個 checkout、兩個 Docker daemon、兩個 MySQL container、仲有唔同代碼世代（WSL branch schema 010 vs production schema 020)。今次統一做晒。

## 事前狀態（2026-07-31 朝）

- Windows Docker Desktop container（`cardz-market-cap-db-1`, `127.0.0.1:3308`)：事實上嘅 live DB — WSL process 經 localhost forwarding 一直透明寫入。
- WSL-native docker container：係舊殘影（400 variants、停喺 07-22)，由更早 session 留低。
- Windows Task Scheduler `CARDZ-Market-Cap-Daily`:Ready，每日 09:30 JST。
- 每日鏈今朝兩度 blocked:`universe hash mismatch`(56 張 lock 成員嘅 printing identity 未 promote)+ `discovery radar 235 unresolved candidates`。

## Cutover 步驟（已執行，可以照住重做一次）

1. **停 Windows writer**:`schtasks.exe /Change /TN "CARDZ-Market-Cap-Daily" /Disable`。
2. **Commit 修復**(worktree `~/cardz-aws`, commit `870dbc3`):eBay daily 預設開、PC export 鮮度閘、跨 feed 成交 dedupe、`promote_printing_candidates.py`。
3. **身份修復**:`promote_printing_candidates.py --write`(56 張 candidate → canonical，用 GemRate receipt parallel 證據）,`universe_authority.py build-candidate` → `materialize` → `promote`(lock 31 → 37,integrity valid)。
4. **拆 WSL 舊殘影**:`docker rm ebac6799be99`。
5. **出新 seed**:`scripts/canonical_seed.py build`（由 live DB 出，27 個 canonical 表；raw payload 表按設計排除）。
6. **乾淨 WSL-native MySQL**:`docker compose -f compose.backend.yaml down -v && up -d`(port 3310;3308 有遺留 bind 問題，見「已知事項」),`scripts/seed_restore.py restore --allow-empty-db`。
7. **停 Windows Docker container**:`docker.exe stop 960e51dfb4c8`（數據保留喺佢個 volume，做冷備份）。
8. **backend.env 改去 3310**。

## 已知事項 / 伏

- **Port 3308 phantom bind**:WSL docker 對 3308 嘅 port publish 無聲失效（NetworkSettings.Ports 空），改 3310 即刻好。EC2 唔受影響（冇 host port mapping)。
- **docker-proxy RTT ~60ms(bulk load 伏,2026-08-01 發現）**:WSL host 經 `127.0.0.1:3310`(docker-proxy userland）連 MySQL,每個 round-trip ~62ms,seed restore 得 ~17 INSERT/s(716k 行要 ~12 個鐘）。直連 container IP(`docker inspect` 攞，port 3306）係 ~0.3ms,~1150 INSERT/s,12 分鐘搞掂。**大批量操作(restore/backfill/migration）一定要用直連**:`CARDZ_DB_HOST=<container-ip> CARDZ_DB_PORT=3306`。日常 app query 量少唔覺，但 daily pipeline 如果慢，第一樣要查呢樣。EC2/RDS 唔受影響（冇 docker-proxy)。
- **兩個 docker daemon**:WSL-native(`/usr/bin/docker`, unix socket)vs Docker Desktop(`docker.exe`, npipe)。localhost forwarding 會將 WSL `127.0.0.1:3308` 指去 Windows container — 所以淨係停自己個 container，forward 先現形。
- **GemRate direct API 已停(403)**:所有 population 收集而家係 keyless 逆向（public card page / G10 mirror)。`gemrate_candidate_backfill.py` 要加 `--collect-public`(Playwright)先行得。
- **q940 名單文件會過期**:`qualified-940-identity.jsonl` 嘅 variantId 同 opaqueId 都會喺 identity convergence 後失效。真維護名單永遠以 `market_universe_lock`(is_current=1）為準。
- **Cloudflare auth 會過期(blocker,2026-08-01 發現)**:staging publish 靠 wrangler OAuth(`~/.config/.wrangler/config/default.toml`),token 2026-07-29 過期後 non-interactive refresh 失敗,`wrangler whoami` 報 not authenticated。WSL 同 Windows 兩邊嘅 OAuth store 都已過期;repo 同 `~/.hermes/.env` 都冇 `CLOUDFLARE_API_TOKEN`。**修復方法(要用戶做一次)**:揀一個 — (a) 喺 WSL 行 `cd ~/cardz-aws && node node_modules/wrangler/bin/wrangler.js login`(要 browser 完成 OAuth);或 (b) 去 Cloudflare dashboard 出 API token(Edit R2 + Workers 權限),放入 `~/.hermes/.env` 做 `CLOUDFLARE_API_TOKEN=...`,publish 前 `source` 佢。publish 鏈本身(`pipelines/publish-snapshot.mjs` + canary + pointer)唔使改。

## 完成後嘅驗收

- [ ] `universe_authority.py status` → `matchesCandidate: true, status: valid`
- [ ] 42 表 + variant 數同 live DB 一致 + lock 37 在
- [ ] daily backend-only 行通冇 blocked
- [ ] staging publish canary 全綠

## EC2 嘅時候

同一本 runbook 直接用，淨係第 6 步變成 RDS(`--external-db` + `CARDZ_DB_SSL_CA`)，同埋唔使理 port bind 嗰啲嘢。
