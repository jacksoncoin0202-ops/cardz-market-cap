# CARDZ 資料清洗與對照規則

這份文件定義「保留哪些原始資料、何時正規化、何時拒絕」，避免每次新增 source 或欄位都重新猜規則。路由、script、DB column 與 consumer 的機器可讀對照由 `config/data-routing.json` 提供；本文件只定義 ingestion rule。

## Data layers

| Layer | 保存內容 | 可否修改 | 可否進 MySQL／public |
|---|---|---|---|
| immutable private landing | 原始 payload、content hash、request context 的安全 pointer | 不可 | MySQL 只存 pointer/hash；public 不可 |
| normalized observation | parsed identity、metric、value、native currency、effective/fetched time、transport | 可由同一 raw deterministic replay | 可進 MySQL；public 不可直接讀 |
| canonical fact | resolved printing、dated GemRate POP、exact SNK reference price、cleaned sales aggregate | 只可由 validated normalized input 寫入 | MySQL 可以；public 經 exporter sanitize |
| derived snapshot | USD market cap、rank、1d/7d/30d、membership、alert | 隨 daily generation 重算 | 可公開，但無 provider ID／raw data |

## Normalization and rejection table

| Field / metric | Accept | Normalize | Reject / quarantine |
|---|---|---|---|
| Printing identity | exact TCG, set, complete collector number, language, edition, parallel, finish | canonical opaque CARDZ ID | missing/partial number, language conflict, ambiguous multiple match |
| PSA 10 POP | GemRate exact PSA grade 10 with source date, live observation time and transport | integer `populationPsa10`; direct API preferred; page-initiated current JSON records `sourceDate`/`lastPopulationChange` separately from live `fetchedAt`; Grade10 mirror labelled transport | estimated / generic total / search result / grade not PSA 10 |
| POP history | GemRate direct history point with effective date | one observation per printing + grader + grade + effective date + source scope + payload hash | mirror/public current copied as history, synthetic date, duplicate conflicting point |
| PSA 10 price | exact SNK printing + PSA 10 daily reference point | decimal native currency + FX observation to USD; retain effective date | `used_min_price`, non-PSA10, fuzzy name match, stale beyond policy |
| Tracked sale | exact PSA 10 non-bundle transaction | preserve sale date, fetched time, unit price, quantity, transaction value separately | bundle total as unit price, relative date promoted to sale date, grade mismatch |
| Currency | source native value + dated FX rate | store native amount/currency and derived USD separately | recalculate old history with today FX, unknown currency |
| Missing data | explicit `unavailable` / `accumulating` / `stale` | nullable metric + status/reason | numeric zero placeholder |

## De-duplication keys

- population: `canonical_variant + grader + grade + effective_date + source_scope + payload_sha256`.
- reference price: `canonical_variant + grade_scope + effective_date + source_scope + payload_sha256`.
- sales aggregate: `canonical_variant + window + as_of + source_scope + payload_sha256`.
- immutable raw: content SHA-256 only; successful GemRate page-initiated JSON is content-addressed beneath the private landing path, while DOM fallback records `dom_evidence_only`; large raw response is never copied repeatedly into MySQL.

## Eligibility and monitoring

- Formal ranking: confirmed canonical identity, complete collector number, GemRate PSA 10 POP `>=1000`, valid exact PSA 10 price within freshness policy.
- Pre-entry monitoring: GemRate PSA 10 POP `971–999`; collect current POP and exact price to measure possible entry.
- Discovery only: POP `<=970`, identity unresolved, or price unavailable; retain current evidence/worklist only, not daily target history.
- A source failure cannot lower the gate. It leaves last-good fact in place only while freshness permits; otherwise the card becomes unavailable and exits the certified ranking.

## Evidence required for every new route

1. Registry entry: authority, transport, script, output, canonical DB metric, consumer and test.
2. Fixture: success, partial failure, identity conflict and duplicate replay.
3. Immutable manifest with hash/counts and safe failure receipts.
4. Fail-closed test: partial transport cannot advance checkpoint, ranking generation or public pointer.
