# CARDZ Market Cap Agent Entry

The only handwritten backend architecture source is
`config/data-routing.json`.

Before changing backend behavior:

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
