# CARDZ Market Cap Agent Entry

## Read PROJECT_STATE.md first, update it last

`PROJECT_STATE.md` at the repo root is the single source of truth for current
state: what is in flight, what is already done, hard deadlines, standing
decisions, and known traps. Read it before doing anything else. Update it before
you finish. It is model-agnostic on purpose — a task list that lives inside one
agent's session is invisible to every other agent, which is how work gets
repeated.

Do not trust its prose alone. Section 0 lists three verification commands; run
them and let the real output override anything the document claims.

`PROJECT_STATE.md` records **operational state**. `config/data-routing.json`
remains the only handwritten source for **architecture ownership**. Never
restate node ownership, routing, or acceptance contracts in `PROJECT_STATE.md`.

## Task level comes before tooling

Classify the request before choosing infrastructure. Use the lightest existing
path that can produce the requested artifact. Do not promote a one-off research,
scraping, analysis, visualization, or mockup task into production
infrastructure unless the user explicitly asks for that outcome.

- **L0 — Research and disposable output:** one-off public-data scraping,
  analysis, charts, visualizations, and mockups. Use direct HTTP, an existing
  script, or the smallest disposable script that completes the request. Do not
  start Docker, MySQL, CodeGraph, or the full backend workflow. Do not modify
  canonical data.
- **L1 — Frontend-only change:** work from the existing public snapshot and
  run frontend-scoped validation. Do not start backend infrastructure unless
  the requested result cannot otherwise be verified.
- **L2 — Focused collector or pipeline change:** inspect only the relevant
  owner files and routing node, then run targeted tests. Use the full
  architecture workflow below only when the change alters architecture,
  ownership, dependencies, or the public data contract.
- **L3 — Production data architecture or runtime:** schema changes,
  migrations, canonical database work, daily runtime changes, and publication
  changes use the full backend workflow below, including the applicable
  database and release gates.

Docker and MySQL are opt-in tools. Start them only when the requested result
requires the canonical MySQL runtime; the presence of a Compose file is not a
reason to start them.

The only handwritten backend architecture source is
`config/data-routing.json`.

For L3 work, and for L2 work that changes architecture, ownership,
dependencies, or the public data contract:

1. Run `python scripts/backend.py explain <metric|field|node|task>`.
2. Run `python scripts/backend.py work-items --status in_progress`.
3. Run `npm run graph:sync`, then use `codegraph explore "<implementation>"` to
   inspect callers, imports, and impact.
4. Change only the owner files attached to the matching work item.
5. Update that node or work item in `config/data-routing.json` when the
   architecture, ownership, dependency, or acceptance contract changes.
6. Run `python scripts/backend.py generate-docs` and
   `python scripts/backend.py generate-docs --check`.

CodeGraph and its local SQLite database are read-only code evidence. They do
not define source authority, data transport, storage, ranking, presentation,
or task status.

Never hand-edit `docs/generated`, and never create a second routing or
architecture contract in a README, runbook, frontend, or database.
