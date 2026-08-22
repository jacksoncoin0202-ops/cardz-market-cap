# 加一個新資料源（Daily Chain V2）

> 目標：加源＝**填聲明**，唔係改 orchestrator。以下每步都寫明改邊個檔、邊個 function／table。
> 漏咗邊步，第 7 節嗰啲 test 會即刻紅，唔會靜靜地跌一半卡。

以下用 `<src>` = canonical source code（例：`snkrdunk`、`pricecharting`、`gemrate`），
`<adapter>` = collect adapter key（例：`snk_price`、`pc_ebay_sales`）。

---

## 1. Migration（如果要新表／新欄）

檔名 **一定** 要 `pipelines/migrations/05N_daily_chain_v2_<name>.mysql.sql`（下一個係 `055_`）。

- glob `0[5-9][0-9]_daily_chain_v2_*.mysql.sql` 由 `daily_chain_v2_contract.list_v2_migrations()` 掃，
  `daily_chain_v2_stage.migrate_command()` 自動加 `--only`，plan 亦自動多一個 blocking stage `schema-<nnn>`。
- **零 code 要改。** 但反過來：未寫完嘅檔擺落去會即刻擋住成條 daily chain，未 ready 就改名做 `.sql.wip`。
- 要 additive + idempotent（information_schema guard + PREPARE/EXECUTE），唔准 DROP。

## 2. Registry row

`pipelines/daily_chain_v2_adapters.py` → `build_default_registry()` 加一個 `SourceSpec`：

```python
SourceSpec(
    source_code="<src>",
    capabilities=("quote", "price", "identity"),  # pop / rates / quote / identity / …
    transport="http",              # 或 "cdp:9333"
    concurrency_group="host:<src>",
    max_concurrency=1,
    cadence="daily",
    freshness_sla_minutes=405,
    required_class="quote",        # core = 唔齊就唔准出街；quote / extra
    identity_lane="http",          # None = 呢個源唔擁有 discovery lane
    route_priority=30,             # None = registry stage 派預設 30
)
```

`sync_source_registry()` 會把佢寫落 `market_source_registry`，
並且**只有喺佢仲未有 route policy 時**先開一行 `market_quote_route_policy`（唔會改舊源價位）。

⚠️ `route_priority` **唔係求其填**：佢會原封不動寫落 `market_quote_route_policy.priority`，
而 `operator_control._is_canonical_price_route` 要求呢個數 **等於** 該源喺
`current_quote_revision.language_quote_route()` 入面嘅位置 ×10（第 1 位 = 10、第 2 位 = 20…）。
兩邊唔夾 = 該源每一行 ranked row 都會被 gate reject。見第 7 節。

## 3. Collect adapter

- `pipelines/collection_contract.py` → `SOURCE_ADAPTERS` 加一行：
  `"<adapter>": {"lane": "http", "runner": "run_<adapter>", "kind": "price"}`
- `pipelines/collect_control.py` 定義 `run_<adapter>(...)`。
- 喺第 2 步嗰個 adapter 嘅 `worker_payload={"adapters": ["<adapter>", ...]}` 入面 claim 佢。
- **唔准**再喺 collect_control 寫 `if "<src>" in requested:`；未註冊嘅源會 fail closed 做 `source_not_registered`。

## 4. Guardrail（有價先要）

`data/policy/daily-release-guardrails.json` → `priceMaxAgeDaysBySource` 加 `"<src>": <日數>`。

`rebuild_036._load_daily_guardrails()` 會對住 registry 嘅 quote 源逐個查，
冇 entry 就 raise 兼講返邊個源、邊個檔。以前冇 entry 會靜靜地跌落 `default`。

## 5. 出街面

- `scripts/public-surface-gate.mjs` → `FORBIDDEN_TOKENS` 加 `"<src>"`（品牌名唔准出現喺公開 payload）。
- Release checkout `apps/web/src/lib/live-db-snapshot.ts`：如果新源要出喺前台（價、圖、charts），
  要喺嗰邊接。**呢個 repo 改唔到出街 gate**，兩個檔都住喺 release checkout，記得同步。

## 6. 手動試（唔會寫嘢）

```bash
python3 -X utf8 pipelines/collect_control.py incr --adapter <adapter> --dry-run --limit 5
python3 -X utf8 pipelines/daily_chain_v2.py status --brief
```

`--dry-run` / `--limit` 只係手動 override；排程路徑兩樣都唔傳（預設 `limit=None`、`dry_run=False`）。
`daily_chain_v2_worker.py --kind` 嘅選項由 adapter 聲明推導，唔使改 list。

## 7. 漏咗會邊度紅（實名）

| 漏咗 | 邊度即刻紅 |
| --- | --- |
| Migration 檔名唔啱 | `scripts/test_daily_chain_v2.py` — `a new V2 migration file joins migrate --only and the infra plan with no code edit` |
| Registry row（core 源冇 pop／rates capability） | 同上 — `current_run_contract measures one section per registry source and blocks an unmeasurable core source` |
| Route policy 被覆寫／新 quote 源冇 policy row | 同上 — `the registry stage grants a new quote source its route policy row and never re-prices an existing one` |
| Identity lane 冇聲明 | 同上 — `identity lanes and their concurrency groups are registry declarations, not a literal pair` |
| Worker kind 唔存在 | 同上 — `worker --kind choices are derived from the adapters; an unknown kind is refused` |
| 冇 repair adapter | 同上 — `a source without a repair adapter journals REPAIR_SKIPPED_NO_ADAPTER and the stage keeps going` |
| `SOURCE_ADAPTERS` 冇加／runner 唔存在 | `scripts/test_pc_lane_durability.py` — `an unregistered source is named source_not_registered`；`scripts/test_runtime_and_collection_contract.py` — `one six-adapter authority hold` |
| Guardrail 冇 price age | `scripts/test_daily_accept_guardrails.py` — `a registry quote source with no price-age entry fails closed` |
| Adapter 冇 registry 認領 / `FORBIDDEN_TOKENS` 冇加 | `scripts/test_third_source_generic.py` — `an unclaimed collection adapter and a provider missing from FORBIDDEN_TOKENS are both detected` |
| Quote 源冇註冊就想寫 revision | `scripts/test_third_source_generic.py` — `minting a revision for an unregistered source fails closed` |
| `route_priority` ≠ 該源喺 `language_quote_route()` 嘅位置 ×10 | **冇 test 會自動紅**（test 用嘅係 fixture 源）：真路徑上 `_is_canonical_price_route` reject 晒該源每一行 ranked row，一路靜到 `rankedDropMaxRatio`。規則本身由 `scripts/test_third_source_generic.py` — `a route_priority that disagrees with the route position is refused, and the lead language never reaches route position 3` 釘死 |
| 新源服務嘅 `card_language` 未加入 `NON_LEAD_ROUTE_LANGUAGES` | **同樣冇 test 會自動紅**：`language_quote_route()` 對未列出嘅語言回 `()`，`_is_canonical_price_route` 全部 False，嗰啲語言成批跌。`scripts/test_third_source_generic.py` — `the registry-derived route answers all 3696 pre-change inputs exactly as the literal route did` 已經證實 `fr`／`zh`／`zh-TW` 呢類未列出語言一律 False |

新 quote 源仲有兩個硬限制，**唔係 registry 填得掂**：
1. `market_quote_route_policy` 對新源只會開一行 `'*'`（priority = 聲明嘅 `route_priority`）。
   要 per-language 價位就要自己寫 migration 加行，數值一樣要等於該語言 route 嘅位置 ×10。
2. Lead 語言（`LEAD_LANE_ROUTE_LANGUAGES`，今日 = `en`）嘅 gate 只會睇 route 第 1、2 位
   （`eligible_pricecharting_exists` 二選一），所以第三個 quote 源喺 EN **任何 priority 都入唔到**；
   要佢出 EN 就要改 `_is_canonical_price_route` 嘅 lead 分支，唔係加 registry row。

另外執行期會 fail closed（唔係 test，係真路徑）：
`current_quote_revision.insert_quote_revision()` 遇到未註冊 source code 直接 `ValueError`。

## 8. 唔使再改嘅嘢

- `daily_chain_v2_stage.py` 嘅 `migrate --only` list、`discover --lane` choices
- `daily_chain_v2.py` 嘅 infra stage list、publication barrier、repair planner
- `daily_chain_v2_worker.py` 嘅 `--kind` choices
- `collect_control.py` 嘅 dispatch 鏈（http / browser 兩條 loop）
- `collection_contract.ADAPTER_LANE`、`CHECKPOINT_ADAPTERS`（由 `SOURCE_ADAPTERS` 推導）
- `current_quote_revision.py` 嘅 `source IN (...)`、legacy alias CASE
- `operator_control._is_canonical_price_route` 入面嘅 provider 名（route 由 registry 推導，
  但 **priority 同語言集仍然要夾**，見第 2、7 節）

**有三樣仲係寫死**，因為佢哋本身係「呢個源／呢個 catalog 嘅事實」，唔係 orchestrator：
`current_quote_revision.LEGACY_QUOTE_STORAGE_ALIASES`（舊 row 用過嘅 source code）、
`SPECIAL_QUOTE_OBSERVATION_SOURCES` / `LANGUAGE_GATED_QUOTE_SOURCES`（payload 契約特殊、或者淨係算某幾種語言）、
`LEAD_LANE_ROUTE_LANGUAGES` / `NON_LEAD_ROUTE_LANGUAGES`（邊啲 `card_language` 行 lead route、邊啲行 non-lead；
未列出嘅語言 `language_quote_route()` 回 `()`，成批 fail closed）。
新源三樣都唔關事就一行都唔使加。
