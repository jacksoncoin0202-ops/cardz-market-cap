# CARDZ Market Cap — Agent Entry

This is the stable entrypoint for every Agent. It contains no live counts,
dated handoff state, or copied script map.

## Progressive-disclosure funnel

Never preload the whole repository, docs tree, role catalogue, or skill
catalogue.

Dispatcher / MAIN:

1. Read [`docs/generated/DOCUMENT_AUTHORITY.md`](docs/generated/DOCUMENT_AUTHORITY.md).
2. Read [`docs/generated/AGENT_EXECUTION_FUNNEL.md`](docs/generated/AGENT_EXECUTION_FUNNEL.md).
3. Assign one `AGENT_ID`, one registered `WORK_ITEM_ID`, one immutable baseline,
   and one exact role-pack path.

Worker A01–A12:

1. Read this file and
   [`docs/generated/DOCUMENT_AUTHORITY.md`](docs/generated/DOCUMENT_AUTHORITY.md).
2. Read exactly `docs/generated/roles/<AGENT_ID>.md`; do not read the other
   eleven role packs.
3. Run `python -X utf8 scripts/backend.py explain <AGENT_ID>`.
4. Run `python -X utf8 scripts/backend.py explain <WORK_ITEM_ID>`.
5. Run `python -X utf8 scripts/backend.py explain document:<DOCUMENT_ID>`, then
   load only the role pack's one active manual needed for the current decision.
6. Load the named baseline, relevant [`PROJECT_STATE.md`](PROJECT_STATE.md)
   slice, current values, receipts, or skill only after the work item requires
   them.

If the generated files are missing or drifted, stop implementation and ask MAIN
to run:

```powershell
python -X utf8 scripts/backend.py generate-docs
python -X utf8 scripts/backend.py generate-docs --check
```

## Information classes and authority order

1. Timeless invariants define identity, authority, dependency order, write
   ownership, stop conditions, delegation, and feedback routing. They contain
   no live counts, prices, completion claims, or installed-plugin state.
2. `config/data-routing.json` is the only handwritten architecture, document
   authority, Agent assignment, and write-scope registry.
3. Versioned contracts—schema, migrations, formulae, CLI contracts, and QC
   predicates—remain exact for their declared version or hash.
4. Fresh read-only runtime evidence describes volatile current state and is raw
   material only; a changing number never becomes architecture.
5. Applied migration ledger, migration SQL, and `information_schema` define DB shape.
6. `packages/market-data/src/schema.ts` and `validate.ts` define public property shape.
7. Current source and tests define implementation behavior.
8. `docs/generated/*` are generated views; never hand-edit.
9. Every other Markdown file is reference-only unless the generated authority
   view explicitly marks it active.

No dated report, handoff, old map, chat summary, temp script, evidence script,
or archive file may override this order.

## Hard boundaries

- The sole CARDZ Market Cap business DB is MySQL schema `cardz_market_cap`.
- Exclude JLP, Kado legacy SQLite, CodeGraph/Miniflare caches, and unrelated
  project databases from merge, migration, and completeness claims.
- Deployment/runtime work targets WSL Ubuntu. Do not substitute a Windows
  production runtime.
- `temp/**`, `docs/evidence/**`, `docs/archive/**`, and `docs/mockups/**` are
  non-executable.
- A registered tool is necessary but not sufficient. It may execute only when
  it is in the assigned work-item nodes, matches the exact role stage and
  baseline, and its declared side-effect envelope fits the dispatch write set.
- A role may write only paths in its generated exclusive claim.
- A01–A12 do not edit `config/data-routing.json`, `docs/generated/**`, or
  `PROJECT_STATE.md`; MAIN integrates the registry and runs generators.
- DB apply, shared public assets, snapshot assembly, pointer promotion, timers,
  deploy, commit, push, and public send remain separate serialized approval gates.
- Never expose or commit secrets, tokens, cookies, identity/banking values, or
  sensitive document contents.

## Before editing

1. Confirm `WAVE_ID`, `ROLE_STAGE`, `AGENT_ID`, exact role mode, registered
   `WORK_ITEM_ID`, work-item status, write-claim ID/mode, allowed tool IDs,
   baseline manifest/hash, cohort hash, routing hash, DB fingerprint,
   dependencies, and acceptance.
2. Return exact `read_set`, `write_set`, and `generated_set`.
3. Stop on an overlapping write set or an unowned path; MAIN must repartition it.
4. For architecture work, use `backend.py explain` before scoped repository
   search.
5. Load a capability/skill only when the exact role pack `loadWhen` trigger is
   true. Installed or downloadable does not mean loaded, connected, or
   authorized.
6. For implementation impact, CodeGraph is read-only evidence, never authority.

## Delegation and feedback

- A child Agent inherits the same role, baseline, blocked actions, and no extra
  authority. Default child mode is read-only within the same role.
- A child may write only after MAIN assigns an explicit non-overlapping
  subclaim, output root, and acceptance command. The parent owns integration.
- Cross-role work returns a structured dependency request to MAIN; a worker
  does not silently spawn another department's writer.
- A soft fact such as a count, price, status, source response, or adapter
  availability may change only through its registered writer/read path with
  provenance, freshness, and read-back evidence.
- A contract gap pauses the affected decision and becomes a versioned proposal.
  An invariant conflict stops the affected work and requires DADDY + MAIN
  approval, registry versioning, regeneration, and reverse tests.
- Continue independent read-only work where possible; never bypass a blocked
  rule with a guessed fallback.

## Data/QC truth

- Report separately: harvest has data, DB written, QC green.
- Missing is not zero. Unknown is not complete. Asset present is not semantic image approval.
- Every DB/public write needs the registered ingestor, applicable QC gate,
  immutable evidence, explicit apply/write authority, and read-back validation.
- Full and incremental modes must share collectors, normalizers, ingestors, QC,
  and publisher; only selector, cursor, range, and freshness differ.
- Canonical writes are serialized even when harvest and read-only audit are parallel.

## Validation and handoff

Run the narrow tests for the owned change, plus:

```powershell
python -X utf8 scripts/backend.py generate-docs --check
python -X utf8 -m unittest tests.test_registry_lineage tests.test_data_routing -v
git diff --check
```

Never claim a test passed without its exact command output. Finish with the
handoff fields required by `docs/generated/AGENT_EXECUTION_FUNNEL.md`, including
risks, blockers, next owner, `intent_fidelity`, and `yagni`.
