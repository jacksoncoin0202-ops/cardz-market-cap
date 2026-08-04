# CARDZ Data Model — New Era (2026-08-03)

## 1. Universe gate

- Authority: **GemRate PSA10 population**.
- PSA10 pop counts graded cards and **only increases**.
- Qualified line: **PSA10 POP >= 1000**.
- When a card crosses the line: enter **candidate queue** automatically, start max harvest, then notify human/agent to finish binds/freezes.
- No “fell below 1000 so remove” policy.

## 1b. Product surface (2026-08-03)

Daily product work:

1. **Top 100 Market Cap** (price × current PSA10 POP)
2. **Universe gate / refresh:** PSA10 POP **>= 1000**
3. **Daily POP chase:** write/refresh **current POP level** for tracked cards

Single-card POP **growth** is **derived** from daily POP levels (today vs prior day/window). No deep multi-year multi-grader history harvest is required just to show growth. One POP point only => growth unknown until the next daily chase.

Not product daily work:

- Grading Pulse / multi-grader company growth series
- Bulk historical rebinding only to fill Accumulating bars

## 2. Scope

- Maintain only qualified / candidate pool (~1k cards), not all TCG cards.
- Split cleanly by TCG: Pokémon / One Piece / future games never mixed.
- Identity includes language, set/box, collector number, edition/parallel/finish.
- Same collector number can be many printings (One Piece especially). Box/set evidence matters.

## 3. Per-card once (full stock)

For each qualified identity, pull **all available** exact-bound data:

| Layer | Sources | Rule |
|---|---|---|
| Gate / POP | GemRate | PSA10 pop + history available |
| Identity links | GemRate, SNK, PriceCharting, eBay (incl G10-derived exact ids) | one exact link per source family when available |
| Price / sales | SNK deep history API, PC, eBay, G10 market facts | store all available; unitize lots; drop extremes |
| Images | Accepted freeze discovery: G10 > SNK > TCGplayer(language-aware) | RAW only; human accept once; freeze. Product-pass Top 100 uses the explicit SNK-first override below. |

Pull-first doctrine:

- If source has it, store it.
- Do not wait for a future column decision.
- “All” means all available from approved collectors, not infinite web fantasy.

## 4. G10 policy

Allowed:

- identity clues
- SNK / eBay bindings from G10 material
- images
- price / sales / market-cap usable market facts

Banned:

- **`g10_kline` only**

## 5. Links

State machine:

```text
missing → candidate → accepted(frozen) → (rare) unbind → rebind
```

- Accepted links freeze.
- Wrong link: unbind with reason, then bind correct one.
- A finished identity should have complete available links + complete available observations.

### Source disambiguation authority (locked 2026-08-03)

- **GemRate/PSA identity is printing truth** for POP-gated cards.
- Use `publicCardPage.identity` fields: `set_name` + `card_number` + **`parallel`**, plus PSA/history `description` when present.
- PriceCharting / SNK search results are candidates only. They must match that parallel.
- Auto-bind only after parallel filter leaves one free winner (or clear margin). Never steal another active exact id.
- Durable operator note: `data/runtime/operator/PC_PARALLEL_REBIND_RULE.md`

## 6. Price composition (Polaris, 2026-08-03)

Store-all still stands: pull every exact-bound observation into MySQL first.
Display authority is **language-routed** (not one global chain):

### EN cards
1. **Primary:** eBay completed sales units, last **30d**, median after extreme trim (`ebay_sales`, min 3).
2. **Fallback:** eBay listing/reference, then PriceCharting.
3. SNK does **not** hard-top EN boards when eBay/PC exist (JP market ≠ EN truth).
   Residual only: if EN has no eBay/PC sales or price, SNK may fill so the board is not blank.

### JA / other cards
1. **Primary:** SNK completed sales units, last **30d**, median after extreme trim (`snk_sales`, min 3; qty unitized).
2. **Fallback:** SNK reference only (`snk_psa10` / `snkrdunk` / `snk`).
3. eBay/PC do **not** hard-top JA boards.
4. Guard: if `snk_sales` diverges **>1.75x** from SNK reference, prefer SNK reference (mixed-grade / bad unit protection).

### Shared
1. If quantity/lot is known, unitize first.
2. Drop extremes roughly **>2x** / **>2.5x** peer cluster.
3. `g10_kline` banned for authority.
4. Missing price stays missing (gap), never fake 0.

## 7. Sales history

- SNK: reverse/use deep internal history endpoints; full available history upsert.
- PC / eBay: full available history upsert with fingerprint dedupe.
- “Full” = all obtainable history from that source, not a claim of omniscience.

## 8. Images

Accepted freeze discovery priority: **G10 > SNK > TCGplayer (language-aware)**.

Product-pass display override（DADDY locked 2026-08-04）：

- Pass 用當刻 `price × current PSA10 POP` 重排實際顯示 Top 100，唔用 universe 舊 rank。
- Top 100 有 exact pointer、`public_allowed=1`、`raw_front_confirmed=1`、`human_or_vision_confirmed` 嘅 SNK asset，就必須揀 SNK。
- 冇合資格 SNK 先保留原 accepted freeze image。
- Rank 101+ 不套用呢個 override。
- Authoritative decision 係 `daily --... --pass` 寫入 receipt 嘅 per-card policy/hash；inventory command 只讀。

DADDY rule (2026-08-03 live probe):

- **Pokemon EN:** TCGplayer product line `Pokemon`
- **Pokemon JA:** TCGplayer product line **`Pokemon Japan`** (never English `Pokemon`)
- **One Piece EN:** TCGplayer product line `One Piece Card Game`
- **One Piece JA:** TCGplayer has **no reliable separate Japan product line**; auto-search is refused. Use **SNKRDUNK** (`snkrdunk.com` / `cdn.snkrdunk.com`) as JP domestic primary. Explicit product_id only if human-confirmed.
- Limitless One Piece family remains blacklisted (SAMPLE).

Simple path:

1. harvest candidates with language-correct product line
2. OP JA -> SNK path; Pokemon JA -> Pokemon Japan line
3. auto-drop obvious non-usable
4. human site: RAW OK / no / skip
5. OK => freeze one `raw_front` forever

## 9. Cadence

- First time per card: full stock.
- After freeze-complete: **daily incremental** for price/sales/pop only.
- Identity / links / image do not re-open in batch.

## 10. Engineering vs product

- Engineering: live MySQL (`CARDZ_DATA_MODE=operator`).
- Product: freeze-qualified snapshot only.
- Daily incremental + DADDY pass => export product subset => promote/deploy frontend snapshot.

## 11. Operator commands

```bash
python -X utf8 pipelines/operator_control.py status
python -X utf8 pipelines/operator_control.py export-gaps
python -X utf8 pipelines/operator_control.py accept-binding --variant-id ID --kind identity|source|image
python -X utf8 pipelines/operator_control.py snk-image-priority-status
python -X utf8 pipelines/operator_control.py daily --refresh --pass
python -X utf8 pipelines/operator_control.py promote-product-subset
```

## 12. Success definition per card

Ready for product subset when:

- identity frozen
- at least one source link frozen
- image frozen
- has usable price after unitize/outlier rules
- pop/sales stored to the extent source provides

## Practical automation (2026-08-03)

1. `scan-candidates`
   - reads latest PSA10 pop from DB
   - qualified = pop >= 1000
   - new candidates = qualified - active universe
   - writes attention list for human/agent finish bind + full stock

2. `daily`
   - status + scan-candidates + export-gaps
   - `--refresh` runs `pipelines/collect_control.py status` + `incr` (real exact-id harvest; not `--help`)
   - `--pass` exports freeze-qualified product subset and writes image/frontend-bound promote receipt
   - `--refresh-report PATH` reuses one completed five-adapter refresh receipt without network; only use when DADDY explicitly approves a presentation-only pass regeneration
   - live deploy remains explicit after receipt

2b. Stock vs incremental collect (2026-08-03)

```bash
python -X utf8 pipelines/collect_control.py status
python -X utf8 pipelines/collect_control.py stock --adapter snk_trades --limit 20
python -X utf8 pipelines/collect_control.py incr --adapter all --limit 40
python -X utf8 pipelines/collect_control.py incr --adapter pc_ebay_sales --ensure-browser
```

- Design: [docs/STOCK_INCREMENTAL_OPS.md](STOCK_INCREMENTAL_OPS.md)
- Reports: `data/runtime/operator/collect/last_{status,stock,incr}.json`

## DB new-era tidy

- Reuse old observation tables.
- Wide pull-first store: market_source_warehouse.
- Ban g10_kline for price composition (db-tidy quarantines existing rows).
- Product still projects few fields from freeze-qualified cards.
- See docs/DB_NEW_ERA.md.
