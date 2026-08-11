# 036 收官交接計劃（給 Codex 執行）

> 作者：Claude Opus 5 ｜ 撰於 2026-08-11 08:30 UTC（本機 17:30 +0900）
> 狀態：**計劃書，未執行任何改動。** 本檔只係文檔，唔屬於 release commit。
> 依據：036 e2e 全鏈跑完之後嘅實測 + 四路並行盤點（docs 欠單 / task 對數 / worktree / repeat-offender）。
> 所有數字都標明來源。凡標「未驗證」嘅，**唔准當已驗證嚟做決定**。

---

## §0 硬規矩（違反即停）

1. **`git add -A` / `git add .` 絕對禁止**（`AGENTS.md:7`）。逐檔 add。唯一例外係 path-scoped 嘅 `git add -u -- data/public/market-assets`（因為要收 8,806 個 deletion），加完必須 `git diff --cached --numstat` 核對範圍。
2. **密碼永遠唔可以出現喺 log／commit／聊天**。查 DB 一律：
   ```
   docker exec cardz-market-cap-db-1 sh -c 'mysql -u root -p"$MYSQL_ROOT_PASSWORD" -D cardz_market_cap -e "<SQL>"' 2>&1 | grep -v "Using a password"
   ```
3. **`backend.env` 一個字都唔准改**（讀得）。**唔准 probe / trigger webhook endpoint**，webhook secret 唔准入 repo／log。
4. **唔准開 generation 037。** 呢次仍然係 `036_20260808T084217Z`。
5. **唔准放鬆任何 acceptance gate 嚟造靚數。** 有卡出唔到，就寫低點解出唔到，唔好改個閘。
6. **唔准加 QC 功能、唔准加防禦性代碼。**（owner 明令）
7. **舊 folder `cardz-market-cap/` 唯讀** —— 佢揸住 MySQL 3308 嘅 compose + 14GB volume。
8. **Grep/Glob 一定要收窄 path + glob。** repo 有 524MB 圖樹 + `data/runtime/`，裸跑會爆。
9. **唔准同時起兩個 rebuild-036 orchestrator。**
10. **`data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl` 唔准手改**（`COLLECTION_RUNBOOK.md:960-969`）—— 佢係 gate 唯一嘅第二意見，改咗個 gate 就自己同自己比。
11. Commit trailer：`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
12. **唔准 `--no-verify`／唔准繞過 signing。**

---

## §1 現況（2026-08-11 08:2x UTC 實測）

### 1.1 036 e2e 全鏈已經跑完，全部 stage 綠

```
steps: scheduler-disable → freeze → invalidate → backup → linear
     → activate → post-activation ×2 → unfreeze → scheduler-restore → bake
```

| 項目 | 值 | 來源 |
|---|---|---|
| generation | `036_20260808T084217Z` | e2e log |
| activatedAt | 2026-08-11 08:12:07 | activate receipt |
| members / accepted | **1,322** | activate receipt |
| ranking sha | `8c191762b625b8c0…` | activate receipt |
| canary | `checksPassed: true` | canary artifact |
| DB freeze | **已解**（`cardz` = ALL PRIVILEGES，`cardz_rebuild` 已 drop） | `SHOW GRANTS` |
| snapshot | `db3308_8c191762b625b8c0`，68.81 MB，1,322 張 | `data/public/seed-snapshot.json` |
| 圖樹 | **3,966 檔 / 524 MB**（原 1.82 GB） | prune manifest |

`stage_validate` 11 條 check 全 PASS，包括 **`literalPsaDescriptionByteExact: true`**（Gate 2 由紅轉綠）。

### 1.2 兩件即刻要處理

**(a) Scheduled task 全部仍然 Disabled —— 夜鏈唔會跑。**

e2e log 寫住 `{"step": "scheduler-restore", "tasks": []}` —— **restore 咗零條**。原因：我喺開 e2e 之前已經手動 disable 咗兩條，e2e 嘅 scheduler-disable 見到佢哋已經 disabled 就冇記帳，restore 自然冇嘢還原。實測 11 條 `CARDZ*` task **全部 Disabled**。

> 呢個唔係新病：task #29「收尾：unfreeze 後重新 Enable 兩條 CARDZ Task Scheduler」曾經 completed 過一次 —— 即係**同一個坑踩第二次**。根治寫喺 §4.3-D。

即時恢復（兩條 036 主鏈）：
```bash
powershell -NoProfile -Command "Enable-ScheduledTask -TaskName 'CARDZ-036-Morning-Browser-Lanes'; Enable-ScheduledTask -TaskName 'CARDZ-036-Nightly-Collect-Accept'; Get-ScheduledTask -TaskName 'CARDZ-036-*' | Select-Object TaskName,State"
```
> 其餘 9 條（`cardz-beta-*`、`CARDZ-Market-Cap-Daily`、`CARDZ-Nightly-Bake`…）喺我開工之前已經 Disabled，**我冇改過佢哋，唔知係咪應該開** —— 要 owner 講。

**(b) 今次 release 完全未出街。**

live `https://cardzmarketcap.com/` 仍然出 `db3308_cdaebc6778559f70`（＝ `git show HEAD:data/public/seed-snapshot.json`）。branch `rebuild/036-foundation` 對 `origin/main` 係 `0 0`。**e2e 冇 publish step**（`rebuild_036.py:8802-8811` bake 完就 print e2e-complete，冇 git、冇 `[deploy]`）。詳見 §3。

---

## §2 已完成 —— **唔准重做**

> 呢節嘅存在意義：owner 明講「完成嗰啲唔使再做」。以下每條都有實測證據。
> **Codex 見到呢啲就跳過，唔好「順手再修一次」—— 重做曾經整爛過嘢。**

### 2.1 今次 session 做完嘅

| # | 事項 | 證據 |
|---|---|---|
| #58 | **卡名收成一個寫入點** `pipelines/identity_name.py` | 5 個 importer：`new_era_db_tidy.py:17`、`psa_identity_repair.py:28`、`rebuild_036.py:32`、`resolve_active_psa_identity.py:24`、`validate_psa_identity_repair.py:15`。⚠️ 仲有第 5 個 writer 未收（見 §4.1-A） |
| #59 | **編號補分母**（`_capture_fingerprint` 讀其他 grader 行） | 新 snapshot 1,289 complete / 33 false（舊 snapshot 係 1,286/1,286 全 true 嘅假值） |
| #50 | **rank 16 出 `170` → `170/181`** | 新 bake：**1,322 張入面 0 張 officialName 甩咗完整編號**。opaque id 冇郁：舊 1,286 個 id **全部生還**（0 lost / 36 new），冇 `/card/{id}` 死 link |
| #27 / #56 | **假 reject 連坐修復** | SNK **48 條升 exact** + 48 個 `snk_reverify` receipt。棒球路飛 v8 已出街：**marketRank 20，pop 11,488，price 3,405.42，cap 39,121,451** |
| #61 | **圖 prune 落地並實跑** | quarantine manifest：`count 8,847 / bytes 1,284,544,033`，`data/public/market-assets` 由 1.82 GB → **524 MB / 3,966 檔**。**係搬唔係刪**，manifest 逐檔記低 |
| — | **migration 039 / 040 已 apply** | `cardz_schema_version` 有 040；`information_schema.CHECK_CONSTRAINTS` 有 `ck_source_identity_rejection_labelled` |
| — | **Gate 2 由紅轉綠** | `validate-036` receipt：`validator034.pass true`、`literalPsaDescriptionByteExact true` |

### 2.2 文檔仲寫住「未修」但**其實 code 已經修好**（只需改文檔）

> 呢批係最浪費時間嗰種 —— 文檔滯後令下一個 agent 重做一次。

| 事項 | 文檔仲話未修 | 實際 |
|---|---|---|
| `stage_validate` 相關子查詢慢 | `COLLECTION_RUNBOOK.md:658-676` 話「未做，要 maintenance window」 | **已做**：`migrations/038_canonical_metric_rank_covering_index.mysql.sql`，實測 20,852ms → 8,793ms（2.37×） |
| poll list vs strict-identity 唔夾 | `COLLECTION_RUNBOOK.md:860-877` | **已做**：`collect_control.py:1137` `snkPriceIdentityNotStrict` + `test_snk_identity_discover_rules.py:383-406` 守住 |
| `collectorNumber.display/.complete` | `POSTMORTEM_PSA_AUTHORITY_20260811.md:335-341` | **已做**，但仲未 commit（`live-db-snapshot.ts:230,487-492`） |
| `data/private/**` 冇 gitignore | `PLAN_036_FE02.md:521` | **已 ignore**（`.gitignore:11-12`） |

**動作：§5-P3 有一條「文檔對數」，一次過改晒呢四處，唔好逐條開工單。**

### 2.3 Repeat-offender 已經真正閂咗嘅（唔好翻叮）

| id | 事項 | 點解算閂咗 |
|---|---|---|
| RO-2 | `set_name_by_code`（用 catalog 多數決推 set name） | `6efaa955` 已刪走整個參數 —— 實測 24 個 one-piece/en code 入面 9 個「全部答咗第二個 set 嘅名」 |
| RO-3 | 「印刷編號當 set code」 | `fbc30bae` → `06353b58` → `66be6252` 三刀斬清，家陣 `rebuild_036.py:920` 單參數 |
| RO-9 | S0 backup gate 自己擋自己 | 4 次修完，`scripts/test_s0_gate_reaches_every_stage.py` 守住 |
| RO-12 | PC map 兩個 writer | `967dd321`。**呢個係唯一一個三個特徵齊嘅修法（單一 writer + proposal 走另一條 path + test 釘住兩條 path 唔同），§4 所有 S1 修法照抄佢。** |

---

## §3 P0：今次 release 出街

> **呢步做完，1,322 張卡（比 live 多 36 張）、正確卡名、正確編號、棒球路飛先至見得到人。**

### 3.1 之前必須確認

```bash
cd C:/Users/jackson0202/Documents/Playground/cardz-market-cap-fe-db-20260805 && git branch --show-current && git log --oneline -1 && git diff --cached --stat
```
要求：branch = `rebuild/036-foundation`、HEAD = `15c36c77`、**index 空**。

### 3.2 重新量檔案清單（唔准照抄本文檔）

bake 喺 17:18:55 +0900 改過個 tree，本文檔嘅數字係 17:23 嗰刻嘅快照，**唔係合約**。

```bash
cd C:/Users/jackson0202/Documents/Playground/cardz-market-cap-fe-db-20260805 && git status --porcelain=v1 > /tmp/relstatus.txt && wc -l /tmp/relstatus.txt && grep -c '^ D data/public/market-assets/' /tmp/relstatus.txt && grep -c '^?? data/public/market-assets/' /tmp/relstatus.txt
```

參考值（17:23 +0900）：10 M、8,806 D、84 ??（＝ 76 圖 + 8 個檔）。

### 3.3 要 stage 嘅（A 組：源碼）

**9 個 modified**（327+/52−）——
`apps/web/src/lib/live-db-snapshot.ts`、`pipelines/g10_public_snapshot.py`、`pipelines/new_era_db_tidy.py`、`pipelines/operator_control.py`、`pipelines/psa_identity_repair.py`、`pipelines/rebuild_036.py`、`pipelines/resolve_active_psa_identity.py`、`scripts/bake-public-snapshot.mjs`、`scripts/validate_psa_identity_repair.py`

**5 個新檔**（改咗嘅 code 已經 import 緊佢哋，漏咗即 ImportError）——
`pipelines/identity_name.py`、`pipelines/migrations/039_psa_projection_display_name_split.mysql.sql`、`pipelines/migrations/040_source_identity_rejection_must_be_labelled.mysql.sql`、`scripts/prune_public_assets.py`、`scripts/test_identity_name.py`

### 3.4 要 stage 嘅（C 組：release output）

- `data/public/seed-snapshot.json`（68.81 MB）
- **76 個新圖**（31 個 sha × base/\_200/\_600；6.96 MB；全部行 Git LFS）—— 由 fresh `git status` 嘅 `?? data/public/market-assets/` 行嚟 derive，**唔准 glob 成個 directory**
- **8,806 個 deletion**：`git add -u -- data/public/market-assets`（path-scoped，唔違反 §0.1）

### 3.5 **絕對唔准 stage**（B 組，而且**冇被 .gitignore 擋住**）

| path | 點解危險 |
|---|---|
| `.claude/`（`launch.json`） | 只有 `settings.local.json` 被 ignore，而且係靠 **user 全域 ignore**（`~/.config/git/ignore:1`），**唔會跟住 repo 走** |
| `fe03-server.ps1` | repo root，任何 root-level bulk add 即中 |
| `scripts/fe03-verify.cjs` | **同 `prune_public_assets.py`、`test_identity_name.py` 同一個 folder** → `git add scripts/` 同 `git add .` 一樣邪 |

已被 `.gitignore` 正確擋住（安全）：`data/runtime/`、`data/private/`、`.env*`、`node_modules`、`apps/web/.next`。

### 3.6 Commit + push

```bash
git commit -m "release 036: PSA 全名收單一寫入點、編號補分母、假 reject 修復、圖樹 prune 1.82GB→524MB [deploy]"
```
commit message **必須含字面 `[deploy]`**，**必須推 `main`**（`AWS_GITHUB_PULL_DEPLOY.md:9-11, 242-258`）。

推完先驗 LFS：`git lfs status` —— **76 個新 object 要真係上到**，只上 pointer 就會出 404 圖。

### 3.7 出街驗收（缺一不可）

```bash
curl -sI https://app.cardzmarketcap.com/ | grep -i "x-cardz-generation\|x-cardz-build"
```
1. `X-CARDZ-Generation` 要等於 `db3308_8c191762b625b8c0`
2. 隨機抽 5 張卡：卡名有完整編號、圖唔係 placeholder
3. 棒球路飛（v8）喺榜上，rank ≈ 20
4. ⚠️ 順手睇 `X-CARDZ-Build` —— 而家係 `local`，即係 build 嗰陣 `CARDZ_PUBLIC_BUILD_ID` 冇設（`apps/web/next.config.ts:6-9` fallback）。**唔阻 release，但要記帳。**

### 3.8 根治（唔好下次又靠人手）

`rebuild_036.py` `cmd_e2e`（:8802-8811）bake 完就完。**加一個 publish step**：按 §3.2-§3.6 stage、commit、push。冇呢步，每次 036 都停喺出街前一步 —— 今次就係咁，1,322 張卡冇人見到。（＝ task #45）

---

## §4 根治：五個形狀

> owner 嘅原話：「唔好淨係針對其中一個問題去解決，你一睇就要揾返個根本原因。」
> 以下唔係按病徵排，係按**形狀**排。同一個形狀嘅所有現存個案一次過修。

### §4.1 形狀 S1 —— 同一個 fact 幾個 writer / 幾份實作

> 修一份，其餘幾份繼續行舊邏輯。**呢個係 repeat-offender 排名第一嘅成因**（RO-1/3/5/11）。

**A. `catalog_variant.canonical_name` 仲有第 5 個 writer 未收（P1）**
- 六個 SQL 寫入點：`db_runtime.py:767`、`new_era_db_tidy.py:2099`、`:2314`、`psa_identity_repair.py:561`、`rebuild_036.py:2590`、`resolve_active_psa_identity.py:433`
- `identity_name` 覆蓋咗 5 個 importer，**`db_runtime.py` 冇 import**（imports 完於 `:24`），佢喺 `upsert_variant` 直接寫 `str(card['name'])` 原字
- **動作**：先確認 `db_runtime` 嘅 `import` lane 仲有冇用（`db_runtime.py:1531 → :1351 import_lock → :849`）。有用就 route 過 `identity_name.complete_collector_tail`；冇用就刪走個寫入點。**然後加一個 static test：每個 `UPDATE catalog_variant SET canonical_name` 站點都必須 import `identity_name`，最少命中數 ≥ 5**（照抄 `test_pop_upsert_restates_label.py` 嘅最低命中數寫法，唔好變綠色牆紙）
- **唔修嘅後果**：呢個 writer 一跑，成個卡名修復打返轉頭 —— 就係「修完又返嚟」本身

**B. `_pc_rarity_only_parallel` 三個 call site，只收緊咗一個（P1）**
- `rebuild_036.py:7761`（`_pc_print_signature_ok`）收咗 `_PC_BASE_PRINTINGS` 閘；`:3451`（stage_snk_refresh）同 `:8328`（cmd_snk_identity_reverify）冇
- ⚠️ **`POSTMORTEM_OP_GAP_20260809.md:397-400` 明令唔准盲抄 PC 個修法** —— SNK 個 bracket 靠字面 `パラレル`/`parallel` 推，而幾乎全部 `snk_parallel` 都係空，照抄會**一次過打殘成個 SNK cohort**
- **動作**：**先量爆炸半徑**（收緊之後幾多條現有 exact SNK binding 會反轉，按 `snk_parallel` 空／唔空拆開），再決定收緊定係明文記低「呢兩個位故意鬆」+ 實測 cohort 大細

**C. `'rejected'` 字面散落 9 個分支（P2）**
- `rebuild_036.py` 入面：`:1151`、`:1877`、`:1942`、`:1957`、`:2033`、`:2095`、`:2241`、`:3282` + SQL predicate `:2432`
- **動作**：收成一個 predicate function，9 個站點全部 call 佢（`AGENTS.md:29-31` rule 13）。**做喺任何 identity rule 改動之前**，否則又係「lane A 過、lane B 唔過」

**D. 「呢張卡屬邊個 set」四份獨立實作（P2）**
- S7 / snk-identity-reverify / snk-identity-discover / `_fingerprint_variant_conflicts`，48 小時內 8 個 commit（`COLLECTION_RUNBOOK.md:908-919` shape 22）
- **動作**：加一個 test（形狀照 `test_pc_identity_discover_rules.py:288`「兩條 lane 讀同一個 derivation」）釘住四個讀者一致

**E. red list 係 opt-in import（P1）**
- `rebuild_036.py:1430 red_listed_variants()` 只有兩個消費者（`pc_identity_discover.py:460`、`snk_identity_discover.py:170`）。**冇任何嘢逼第三條 lane call 佢**
- 歷史：`POSTMORTEM_OP_GAP_20260809.md:283-301` —— 「呢個月已經第二次有 lane 由一道冇蓋過印嘅門行入去」，寫咗 39 條 ready price + 60 條 sales + 2 個圖 pointer 先被最後一道閘捉到
- **動作**：**唔好再靠 helper**。照 migration 040 個做法，將個裁決搬落佢管住嗰啲 row 上面（或者喺共用 binding-write path 落閘），令「為紅卡寫 proposal」喺**寫入嗰刻**就唔成立

### §4.2 形狀 S2 —— 一個欄位做兩份工

**已根治**：`canonical_name` 唔再同時做「PSA 逐字憑證」同「出街顯示名」（migration 039 拆咗 projection display name）。

**仲未清嘅同形（P2）**：`stage_bind` 而家會 restate `catalog_variant.set_name` 同 `collector_number`（`rebuild_036.py:2583`，counter `psaCollectorNumbersRestated`），但 `db_runtime.py:746-754` 嘅 `opaque_id` identity assert 就係釘住 `(tcg_code, card_language, set_name, collector_number)`，唔夾就 `ValueError("opaque_id canonical identity changed")`。
- **動作**：落一次 universe-lock import **之前**，決定邊邊擁有 `set_name`/`collector_number` —— 要麼將 PSA-restated 欄位豁免出 assert，要麼唔好再喺 lock document 帶住 restate 之前嘅值
- ⚠️ **未驗證**：`db_runtime` 個 `import` lane 而家仲有冇被排程用。呢條係 code-shape 風險，**唔係觀察到嘅故障**，係全部發現入面佐證最少嗰條

### §4.3 形狀 S3 —— 有檢查但零 call site

**A. 10 個 test 全部冇 runner（P0，本節最重要）**

`scripts/` 有 10 個 `test_*.py`，**冇一個有自動 call site**：冇 `.github/workflows`（`.github` 係空）、冇 husky、git hooks 全部係 git-lfs 嘅（`post-checkout`/`post-commit`/`post-merge`/`pre-push`）。

包含 runbook 自己叫做「守門人」嗰幾個：`test_op_printed_codes.py`（`RUNBOOK:601`）、`test_pop_upsert_restates_label.py`（`:778`）、`test_pc_identity_discover_rules.py`（`:841`）、`test_snk_identity_discover_rules.py`（`:876`）。

> 按 `AGENTS.md` rule 9「有檢查但零 call site 當冇檢查」嘅標準，**呢個 repo 而家有十個假閘**。
> 而其中 `test_identity_name.py` 本身就係專登用嚟擋「命名邏輯再被人整段刪走」（`f24b2447` 真係咁刪過一次）。**佢而家擋唔到。**

**⚠️ 唔止 10 個。並行審計（adversarial 驗證過）再揾到 13 個入口**：`pipelines/` 有 **11 個 module 帶 `--self-test`**（`active_universe.py:670`、`daily_prices.py:442`、`fx_rates.py:262`、`g10_ingest.py:1146`、`g10_public_snapshot.py:1537`、`gemrate_client.py:127`、`market_alerts.py:723`、`market_discovery.py:153`、`market_source_sync.py:968`、`source_crosswalk.py:703`、`tag_daily_capture.py`）加 `db_runtime.py:1543` 個 `self-test` subcommand。全部零 call site。審計員逐個跑過，**十個全綠**，所以今日唔痛 —— 但佢哋唔叫 `test_*`，**一個 glob `scripts/test_*.py` 嘅 runner 會全部漏晒**。

> 呢啲唔係空殼：`active_universe.py:561` 砌 331 張卡嘅 fixture universe 驗 lock/validation；`fx_rates.py:217` 驗過期 FX 會唔會擋。

**動作（四步，缺一唔可）：**
1. 寫 `scripts/run_all_tests.py`，**兩邊都收**：
   - **glob** `scripts/test_*.py`（唔准寫死清單 —— 寫死即係新 test 又入唔到網）
   - **具名清單** 13 個 `--self-test` 入口
   - **而且**：掃 `pipelines/*.py`，發現有 `--self-test`／`self_test` 入口但唔喺具名清單，**runner 自己要紅** —— 否則下次加新 suite 又會冇 call site
2. 俾佢**兩個真 call site**：
   - `cmd_e2e` 最頭（`rebuild_036.py:8744`，喺 scheduler-disable 之前）—— test 紅就唔准開鏈
   - `scripts/bake-public-snapshot.mjs`（照 prune 嗰個寫法，非零就 throw）—— test 紅就出唔到 snapshot，即係推唔到 live
3. **即場證明佢會紅**：將 `079` bug 塞返落 `identity_name`（前導零唔折），睇住 runner 變紅，還原。**冇呢步唔算做完**（`AGENTS.md` rule 9）
4. 順手：`g10_public_snapshot.py:1568` 個 `allow_stale_price` 而家搭住 `args.self_test` —— 拆做獨立 flag

**A′. `_active_checkpoint_gate()` —— 36 小時採集新鮮度閘，零 call site（P0，本次審計最大發現）**

`pipelines/operator_control.py:1646` 定義咗個閘：讀 `market_ingest_checkpoint`，六個 `CHECKPOINT_ADAPTERS`（`:33-40`）任何一個缺失或者舊過 `CHECKPOINT_SLA_HOURS=36`（`:41`）就 `RuntimeError`（`:1694-1698`）。

**全 repo grep 佢個名（`*.py/*.ps1/*.json/*.md/*.mjs/*.js/*.ts/*.yml/*.sh`）—— 得一行命中：個 `def` 自己。**

而兩條註冊咗嘅 Task Scheduler 鏈（`scripts/nightly_collect_accept.ps1:19-25`、`scripts/morning_browser_lanes.ps1:27-33`）行嘅係 `collect_control.py incr` → `operator_control.py daily-accept` → `rebuild_036.cmd_daily_accept`（`:7524-7652`），而 `cmd_daily_accept` **由頭到尾冇掂過 `market_ingest_checkpoint`**。佢只有三個 abort：lock 數唔係 1（`:7549`）、零 member（`:7561`）、冇 activated generation（`:7568`）。**冇任何 recency filter。**

實測（2026-08-11 08:38 UTC，只算 current `market_universe_lock` 入面嘅 variant）：

| adapter | streams | 最舊 | 最新 | 超 36h |
|---|---|---|---|---|
| `snk_price` | 785 | **164.3h** | 14.1h | 157 |
| `snk_trades` | 785 | **164.3h** | 14.1h | 246 |
| `en_price_ref` | 945 | **163.4h** | 7.8h | 31 |
| `pc_ebay_sales` | 945 | **163.4h** | 7.8h | 31 |
| `snk_en_image` | 787 | **145.2h** | 0.5h | 159 |
| `gemrate_pop` | 1,320 | 98.1h | 14.1h | 408 |
| **合計超 SLA** | | | | **1,032** |

**六個 adapter 全部都會 raise，而 daily-accept 照樣 accept。**

後果：榜每晚重新 accept 一次舊證據、蓋上新嘅 `accepted_at`、generation 一 flip，**塊板睇落就好似更新咗**。`nightly_collect_accept.ps1:21-23` 個 comment 明講「collector lane 死咗都照收落到嘅證據」—— 呢句只有喺下游有嘢拒收過期證據先至安全。**而家冇。**

**動作**：喺 `rebuild_036.cmd_daily_accept` 現有 ratchet 隔籬（`:7576`）加 `_active_checkpoint_gate(cur, active_ids=set(ready_ids))`，過期就同「discovery gap 變闊」一樣 abort。**如果 36h 硬停喺運作上唔想要，就連個 function 同 `CHECKPOINT_SLA_HOURS` 一齊刪** —— 唔好留一個寫低咗但冇人執行嘅 SLA。

**額外缺口**：`snk_en_image` 喺 `CHECKPOINT_ADAPTERS` 名單入面，但**兩條 ps1 鏈都冇跑佢** —— 即係冇任何排程 job 更新緊佢。

**B. `--no-prune` 開得甩而且唔留痕（P2）**
`bake-public-snapshot.mjs:138`。用咗要寫低（receipt / stderr 大聲講），或者索性 release path 唔准用。

**C. `PHASE_WAIVED_036` 冇到期日（P3，**低危，唔好誇大**）**
`validate_psa_identity_repair.py:86-92` 明文豁免 5 條 762 年代 invariant，而且喺 report 入面 `phaseWaived` 逐條攤開 —— **呢個係誠實嘅做法，唔係靜靜地放水**。今日 receipt 有 4 條 false 全部喺豁免名單。唯一可以執：`activeGemrateExactBindingUnique` 而家係 **True**，唔使再豁免，可以除名。另外每條豁免加一句理由。

**D. scheduler-disable/restore 唔對稱（P1）**
`{"step":"scheduler-restore","tasks":[]}` —— restore 只還原「自己 disable 過」嗰啲。人手預先 disable 咗就永遠冇人開返。task #29 完成過一次，今日再發生。
- **動作**：freeze 前**記低每條 task 嘅原始 State**（唔止記自己改咗邊啲）入 receipt，restore 對住 receipt 還原並**assert 還原後 State 等於原始值**，唔等就大聲報。

**E. `red13` 之後先捉到（見 §4.1-E）** —— 同一形狀，唔重覆。

### §4.4 形狀 S4 —— 大範圍改狀態、唔寫理由

**已根治**：migration 040 落咗 `ck_source_identity_rejection_labelled` CHECK constraint，DB 層擋住，35 個 SQL 寫入點（包括 repo 外嗰個 writer）全覆蓋。

**同形未查（P2）**：其餘 bulk state mutation（`published`/`quarantine`/`visibility`/`accepted` 一類）有冇同樣問題。**呢一格由並行審計補**（見 §9 —— 審計未埋數）。

### §4.5 形狀 S5 —— 修正根本冇跑過（最陰險）

> `fbc30bae` commit message 原話：兩個修正「下一次 run 會 print stage-skip，然後出返舊答案；佢哋只係因為有人手動 force 先至落到地」。
> **一個冇跑過嘅正確修正，同一個唔work嘅修正，睇落一模一樣。** 呢個就係「我哋明明修過」嘅來源。

現況：`rebuild_036.py:147 _CODE_SHA_CACHE` / `:166 _code_sha` 已經 hash code；`:150 _data_file_sha` 已經 hash policy JSON 內容（之前 hash 咗個 Path，即係記住個檔名唔係個內容）。

**仲欠（P1）：**
1. **逐個 stage 列晒佢讀嘅非 row input**（code / policy JSON / env / editorial 檔），逐個 assert 有落 checkpoint sha。而家係逐次出事逐次補
2. **`--invalidate-from` 要按「邊個 stage 寫嗰個 field」解析，唔好淨係收 operator 打嘅 stage 名**。`RUNBOOK:920-930` shape 23：`--invalidate-from bind` 乜都冇郁，因為 `binding.conflicts` 係 `stage_identity_resolve`（`:771`）寫嘅，而 `stage_bind` 只係 reload + 重寫個 JSON，但 `computed_at` 會更新 → **睇落好似重算咗**

---

## §5 逐條欠單

> 標 P0/P1/P2/P3。**每條都有 file:line。** 標「數字未驗證」嘅唔准當現況。

### P0 —— 阻住出街 / 阻住每日運作

| id | 事項 | 位置 / 動作 |
|---|---|---|
| §3 | **release 未出街** | 見 §3 全節 |
| §1.2a | **11 條 scheduled task Disabled** | 見 §1.2a 指令 |
| §4.3-A′ | **36h 採集新鮮度閘零 call site，1,032 條 stream 超 SLA** | `operator_control.py:1646` → 落 call site 喺 `rebuild_036.py:7576` |
| §4.3-A | **23 個 test / self-test 入口零 runner** | 見 §4.3-A 四步 |

### P1 —— 會令錯數出街，或者令修正無效

| id | 事項 | 證據 / 動作 |
|---|---|---|
| #46 | **published 價冇日期下限，最舊 433 日** | `price-route-036…json`：17 張出街卡個「現價」大過 30 日，最舊 v1248（2025-06-04，SNK fallback，3 個 kline 點）；v111 / v1209 出緊 2026-06-16 / 06-19 嘅價。動作：`rebuild_036.py:4209-4238` 加最大年齡上限，過期就 route `none` 或者標 stale |
| #46 | **價格路由淨係語言 primacy，冇比較** | 同上 `:4209-4238`：`en → PC`、否則 SNK，攞到就用；`:4225` 更加禁止非 en 卡 fallback 落 PC。代價：**v235 / v989（ja）各自有 21 / 37 個 PC history point + 30 / 29 條 PC sale，但出唔到價** |
| §4.1-A | canonical_name 第 5 個 writer | `db_runtime.py:767` |
| §4.1-B | `_pc_rarity_only_parallel` 兩個 SNK call site | `rebuild_036.py:3451`、`:8328`（**先量爆炸半徑**） |
| §4.1-E | red list opt-in | `rebuild_036.py:1430` |
| §4.5 | checkpoint 非 row input / `--invalidate-from` | `rebuild_036.py:147,150,166` |
| §4.3-D | scheduler restore 唔對稱 | e2e wrapper |
| DEBT | **freshness gate 一個 MAX 冚兩個源** | `rebuild_036.py:5505-5508`。實測（`RUNBOOK:690-695`）：combined MAX 完全由 snkrdunk 個未來時間戳決定，PriceCharting 半點貢獻都冇；`snk_psa10`（138,037 行、+136.12h 舊）餵緊價格路由但**根本唔喺 gate 個 source 名單**。動作：改成逐源計、全部要過。**預期第一次收緊會 fail 喺 `snk_psa10` —— 要另約時間做，唔好喺 release 中間開火** |
| DEBT | **freshness gate 冇下限，未來時間戳當新鮮** | `rebuild_036.py:5527-5530` 冇 `0 <=`。實測負值：−9.42h（08-09）、−19.36h（08-10）。**次序好緊要**：`RUNBOOK:697-710` 揀咗 option D（改讀 `market_source_observation.observed_at`）**要先做**，再收緊不等式；掉轉做會由「靜靜地假過」變成「日日假 fail」。注意 `priceAgeHours` 喺 `_RECEIPT_VOLATILE_FIELDS`（`:7226-7227`） |
| DEBT | **冇閘比對 universe lock 同新鮮算出嚟嘅 product_ready cohort** | `POSTMORTEM_PINNED_CAPTURE_20260809.md:83` 自認「真欠單，未做」；grep `lockCohortDrift|cohortDrift` 零命中。**呢個正正就係當日冚住 129 張 OP 卡而所有閘都報健康嗰個故障** |
| #42 | **freeze 唔會殺舊 connection** | `rebuild_036.py:8471-8488` 只有 CREATE/ALTER/GRANT/REVOKE/FLUSH，全 repo 冇 `KILL`、冇讀 PROCESSLIST。`prove_writer_freeze.py:41-65` 開**新**連線證 1142，對已連線 session 一個字都冇講。動作：`cmd_freeze`（`:8490`）FLUSH 之後 enumerate `PROCESSLIST WHERE USER='cardz'` 逐個 KILL，再 assert 清空、寫入 freeze receipt；**用一條 hold 住嘅 cardz session 跨 freeze 證佢真係會死** |
| #44 | **restore proof 從來冇 restore 過** | `rebuild_036.py:8673-8729` 只寫 dumpFile/sha/marker/freezeOpenedAt；全 repo 冇任何 code load 過個 dump。今次 dump 1,372,615,952 bytes。動作：喺同一個 container 開 throwaway schema 灌落去、對幾個 row count、DROP、寫入 receipt，令 S0 可以拒絕一個從未還原過嘅 proof |

### P2 —— 用戶睇得到 / 覆蓋率

| id | 事項 | 實測 |
|---|---|---|
| #55 | **內頁小故事出緊 raw markdown** | `card-detail.tsx:86-91` `<p>{story}</p>`。696 個出街 en story 入面 **269 個含 raw markdown**（101 個 `### `、269 個 bullet、214 個 `**bold**`）。`plain-text.ts` 只用喺 metadata（`:47`），冇用喺可見面板。⚠️ **唔准掂 underscore** —— 有一張卡真名係 `______'s Pikachu` |
| #25 | **626 / 1,322 張卡一種語言故事都冇（47%）** | 新 bake：en 696、zhTW 673、zhCN 673、ja 673、ko 0。舊 snapshot 624 —— **個窿基本冇縮，係 cohort 大咗**。`stage_validate` **冇任何 story 覆蓋率閘**（唯一 story check 係 `:5549-5559` koTemplateStoriesZero）。任務原文寫 329，**實測 626** |
| #26 | **108 張 one-piece 仍然 qualified 但唔 product_ready** | `catalog_rebuild_member`：one-piece product_ready 189 / pending 108。起點係 99 → 而家 189。任務原文寫 129，**實測 108** |
| #47 | **8 張 en 卡冇 strict PC 身份**（任務原文寫 4） | v8/42/111/1209/1248/1814 走 snkrdunk（`primary_zero_data`）、v1458/1723 route none。**其中 6 張英文卡而家出緊日本市場 SNK 價**。另有 9 張有 PC id 但 0 history（v1427/1876/1915/1962/1974/2034/2146/2153/2172）—— 呢批可能係 replay capture 缺口，唔係身份缺口 |
| #56 殘留 | 13 條 strictly-bound variant `no_current_price_evidence` | v235/989/1427/1458/1723/1876/1915/1962/1974/2034/2146/2153/2172（同 #46/#47 重疊）。**原文個「66」我用任何現有 artifact 都重現唔到** |
| §4.1-C/D | `'rejected'` 9 份 / set identity 4 份 | 見 §4.1 |
| §4.3-B | `--no-prune` 冇 receipt | `bake-public-snapshot.mjs:138` |
| DEBT | **morning lane 冇 PC 重新 capture，page_missing 永遠唔會自愈** | `scripts/morning_browser_lanes.ps1:27` 只跑 collector，`:33` 直接 daily-accept。11 張機械可修嘅 EN 卡（`POSTMORTEM_OP_GAP_20260809.md:378-392`）「永遠唔會自己好返」。CDP 9333 喺 `:20` 已經 ensure 好 |
| DEBT | **`op-printed-codes.json` 冇 ST 系列** | 125 個 codes / 22 advisory、22 個 product slug，**零個 `st` 開頭**。⚠️ `RUNBOOK:953` 明令：**唔准將 PriceCharting 自己個 console name 餵返做候選** —— 咁樣就變成 PC 同 PC 比，乜都收 |
| DEBT | **冇日文 promo event-name 表** | ~14 張 ja 卡淨係輸喺英文 event 字（v1878 `['center','cracked','ice','skytree','town']`、v1938 `['pokeca']`），另 9 張同形。`data/editorial/set-names.json` 只有 en→zhTW/zhCN/ja 翻譯，冇 SNK 英文別名。⚠️ 兩份文檔都明講：**修 input，唔准放鬆 `product_mismatch`** |
| DEBT | **8,477 條冗餘 SNK kline row 被 effective_observation 引住** | `SNK_KLINE_DEDUP_RECEIPT_20260809.md` deviations #3。要重新指向 canonical (item, day) 先刪得，要 maintenance window + 同一套三個 FK zero-join 閘 |
| DEBT | **print_signature_mismatch 要 PC 產品重新發現，唔係改閘** | v1582 影咗 base Sylveon 而唔係 Master Ball Reverse；v849（pop 21,380）影咗 base Umbreon VMAX。`POSTMORTEM_OP_GAP_20260809.md:355` 明令「唔准為咗過數放鬆 print signature」。動作：`pc_identity_discover.py` 搵返每個 parallel 自己嘅 PC product id，再 `pc-identity-reverify` 升格。**唔准手改 map 檔**（§0.10） |

### P3 —— 衛生 / 文檔

| id | 事項 |
|---|---|
| §2.2 | **文檔對數**：`RUNBOOK:658-676`（038 已做）、`:860-877`（已做）、`POSTMORTEM_PSA_AUTHORITY:335-341`（已做未 commit）、`PLAN_036_FE02.md:521`（已 ignore） |
| DEBT | **`PROJECT_STATE.md` 落後 4 個 migration + 1 個 generation**：`:70-72` 仲寫住「materialized through 033」「034/035 未發佈」「FE02」「762 張卡」。實際 migration 有 034–040、現行係 036/FE03、1,322 張卡。**下一個接手嘅 agent 會照住一個 2026-08-09 已經唔存在嘅世界做計劃** |
| DEBT | `AWS_GITHUB_PULL_DEPLOY.md` §1–§3 係未實作嘅提案，但同 §0 混住 → 2026-08-10 有人當咗提案入面 `cards == 762` 係真閘，**擋停咗一次正確嘅 release**。動作：實作佢，或者刪走佢，唔好兩個權威並存 |
| #52 | **CSP 擋住 Cloudflare Analytics beacon**：live HTML 有 `cloudflareinsights.com/beacon.min.js`，但同一個 response 個 CSP `script-src 'self' 'unsafe-inline'` / `connect-src 'self'` 兩邊都冇佢。`apps/web/next.config.ts:17,22`。**而家零 analytics** |
| #57 | 提煉 `~/.agents/skills/cardz-036/SKILL.md`（而家唔存在）。順手：`~/.agents/skills/cardz-marketcap-deploy/` 係**空 folder** —— 填佢或者刪佢 |
| — | `X-CARDZ-Build: local`（`next.config.ts:6-9` fallback，build 時 `CARDZ_PUBLIC_BUILD_ID` 冇設） |
| — | `/watchlist` 仲喺 `(market)` route group 外面（`POSTMORTEM_PSA_AUTHORITY_20260811.md:314-316` 話搬咗 5 版，實際得 4 版）。**只係冇 loading skeleton**；佢個 `notFound()` 行為正確就係因為喺 Suspense boundary 外面。改咗要用 **HTTP status 驗，唔准 grep**（同一份 postmortem `:276-278` 證過 grep 睇唔到 SSR serialization） |
| — | `rebuild_036.py` 8,813 行、最近 80 個 commit 入面掂咗 36 次（第二名 `snk_identity_discover.py` 得 8 次）。**唔准喺 release chain 期間 refactor。** 交接時只需記低邊段揸邊條規則：set signals `:920`、red list `:1430`、print signature `:7741`、bind restatement `:2551-2712`、PC replay `:2848` |

---

## §6 要 owner 拍板（Codex 唔准自己決定）

| # | 事項 | 要決定咩 |
|---|---|---|
| #54 | **市值 delta 同價格 delta 永遠一樣** | `live-db-snapshot.ts:131-134`：`anchorCap = anchor.priceUsd × currentPopulation`，而 `currentCap`（`:124`）= `currentPrice × currentPopulation` —— 兩邊乘同一個 `currentPopulation`，所以 `capChange` 代數上恆等於 `priceChange`，**市值 delta 完全唔帶 population 資訊**，但當兩個獨立數字出街（`:147` / `:140`，`card-detail.tsx:94,97`）。選項：(a) 用 anchor 當日嘅歷史 population，(b) 唔好再出一個獨立市值 delta |
| #51 | **榜頁全部 dynamic，Cloudflare 冇 cache** | 每條 market route 都 `export const revalidate = 300`，**但唔生效**：`layout.tsx:24` 個 `await headers()` 令成棵樹 dynamic（`:15-20` 個 comment 自己講咗）。live 實測 `cf-cache-status: DYNAMIC`、`Cache-Control: no-store`。`PLAN_036_FE02.md:456` 量過每 request 5.151s。選項：headers() 搬去 middleware / client component / per-route segment |
| #48 | **SNK 兩個實體版本（膠套 / 去套）** | ⚠️ **呢個概念喺成個 repo 搵唔到**：`膠套`/`去套`/`sleeve`/`スリーブ`/`シュリンク` 全部零命中；而家 binder 結構上係一 variant 一 item（`snk_identity_discover.py:539-572` 只喺剩返一個候選先寫），今次 1,335 個 variant 每個最多 1 個 snkItemId。**唔准喺 owner 講出一張具體卡 + 兩個 SNKRDUNK item id 之前開始寫 code** |
| §1.2a | 另外 9 條 scheduled task 要唔要開返 | 我冇改過佢哋，唔知原意 |
| DEBT | ST 系列 5 張要人手裁決嘅 printed code | v2040、v2135、v2027、v1445、v2235（`RUNBOOK:603-605`）。要人揀，唔係 code |
| DEBT | published 價年齡政策 | 硬排除 vs 出街但標 stale（**閘要落喺 acceptance projection，唔係 FE component** —— `POSTMORTEM_PSA_AUTHORITY_20260811.md:280`） |
| 037 | 兩條明文推遲到 037 嘅 schema | `market_ingest_run.status` `complete` vs `completed`（16 個 writer 出 `completed`，4 個 reader 只收 `complete`，而且**呢個分裂係故意嘅** —— `g10_research_ingest.py:30-36` 有文檔，`PLAN:380` 禁止用統一常數去改嗰 4 個讀點）；`uq_market_price_daily` 加闊（會解 26 條 `collapsed_binding_price_overwrite`，但改動 293,893 行嘅 cardinality） |

---

## §7 執行次序

```
0. 恢復 scheduled task（§1.2a）              ← 即刻，唔使等
1. 出街（§3）                                 ← 1,322 張卡見人；唔好夾雜任何新改動
2. §4.3-A test runner + 即場證明會紅          ← 之後每一步都靠佢守
2b. §4.3-A′ 36h 新鮮度閘落 call site          ← 落之前預期會即刻紅（1,032 條超 SLA）；
                                                先開返 scheduler 補數，或者同 owner 講定
3. §4.1-A canonical_name 第 5 個 writer       ← 唔做呢步，卡名修復隨時打返轉頭
4. §4.5 checkpoint / --invalidate-from        ← 唔做呢步，之後所有修正都可能「冇跑過」
5. §4.1-E red list 落 row / 落 write path
6. §4.3-D scheduler restore 對稱
7. #42 freeze KILL + #44 restore probe        ← 下次開鏈之前
8. §4.1-B 量 SNK 爆炸半徑 → 再決定
9. freshness gate（先 option D，後收不等式）  ← 另約時間，預期會 fail
10. #46 價格日期下限 + 路由比較                ← 要 owner 揀政策
11. P2 覆蓋率（#25 #26 #47 #55）
12. P3 文檔對數 + §3.8 publish step 入 code + #57 skill
```

**2 同 4 唔可以掉轉。** 冇 runner，第 4 步嘅修正一樣係「睇落做咗其實冇跑」。

---

## §8 驗收（逐項要證據）

| 項 | 命令 / 準則 |
|---|---|
| 出街 | `X-CARDZ-Generation` == `db3308_8c191762b625b8c0`；抽 5 張卡對名同編號；棒球路飛喺榜 |
| Scheduler | `Get-ScheduledTask -TaskName 'CARDZ-036-*'` 兩條都 `Ready` |
| Test runner | `python -X utf8 scripts/run_all_tests.py` 綠；**然後塞返 `079` bug，睇住佢紅，還原** |
| canonical_name | `grep -rn "SET canonical_name" pipelines/ --include=*.py` 每個站點都 import `identity_name`，static test 最低命中 ≥5 |
| checkpoint | 改一個 policy JSON 嘅內容（唔改檔名），下次 run **唔准** print stage-skip |
| freeze | 開住一條 cardz session 跨 freeze，睇住佢被 KILL，freeze receipt 有 killed id |
| restore | restore-proof.json 有 probe 結果同 row count 比對 |
| 圖 | `data/public/market-assets` 檔數 == snapshot `referencedAssets`；`du -sh` ≈ 524 MB |
| LFS | `git lfs status` 76 個新 object 上到 |

---

## §9 老實講：未驗證 / 有誤差嘅嘢

> **呢節唔准刪。** Codex 唔好將下面任何一句當已證。

1. **所有卡數／行數都係「量嗰日」嘅數，唔係實時**：122 OP gap、44 SNK ja target、1,213 條 restated set_name、8,477 條 FK держ住嘅 kline row、106 條 hard_conflict、61 張冇分母。動手之前重量。
2. **任務標題嘅數字全部同實測對唔上**：#25 寫 329 → 實測 **626**；#26 寫 129 → **108**；#47 寫 4 → **8**；#56 寫 66 → **重現唔到**（可量到嘅係 13 條 strictly-bound 冇現價證據 + 283 條 qualified_market_pending）。**唔好用舊數字驗收。**
3. **283 條 `qualified_market_pending` 拆唔開「差價」定「差圖」** —— 要 eligibility view，實測 ~67s，當時鏈跑緊冇做。
4. **四道閘只證到「有 call site 而且今日綠」**，我證唔到有人真係睇住佢哋紅過一次（Gate 1 更加根本冇 runner）。§8 補返呢一步。
5. **`db_runtime` 個 `import` lane 而家仲有冇被排程用 —— 未查。** §4.2 個 opaque_id 風險靠呢個。
6. **FE 側嘅「已閂」判斷全部只係 source-level**，冇拉過真 HTTP response。同一份 postmortem（`:276-278`）證過 grep 睇唔到 SSR serialization —— 要驗就要真 HTTP。
7. **#52 只證到 beacon tag 有出、CSP 冇放行**；Cloudflare dashboard 嗰邊有冇開 analytics，喺 repo 外面，未查。
8. **`_pc_rarity_only_parallel` 收緊之後嘅 SNK 爆炸半徑 —— 未量。** 未量之前唔准改。
9. **並行審計已埋數，但只驗到頭 8 條。** 五個方向（零 call site／預設關嘅安全掣／一個 fact 幾個 writer／大範圍改狀態／無人清嘅累積物）交返 **20 條原始發現**，逐條派人**反駁**（唔係確認）之後：
   - **3 條成立** —— 全部係 S3 形狀，已經寫入 §4.3-A / §4.3-A′
   - **5 條被推翻** —— 例：「040 個 CHECK 擋唔到佢寫嚟擋嗰個 shape」（兩個 writer 其實都係 dead code）、「`materialize_snapshot_assets` 計完 byte-integrity 掉咗佢」（其實有 call site）、「`--require-ranking-ready` 冇人傳」（個 script 而家根本行唔到，`manifests/` 唔存在）、「e2e 硬寫 `force_stage=True`」（4 個數字有 3 個歸錯位）、「`--allow-unledgered-migrations` 冇痕跡」（形狀分類啱啱相反）
   - **12 條未驗證就被截走**（只驗頭 8 條）。**呢 12 條唔存在於本計劃書入面** —— 想要就要再跑一次審計，唔好當「掃過就冇嘢」。

   > 5/8 被自己人推翻，代表未經反駁嘅發現大概有一半靠唔住。**任何未標「已驗證」嘅嘢，動手之前自己再量一次。**
