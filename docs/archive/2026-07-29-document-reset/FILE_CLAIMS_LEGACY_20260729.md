# FILE_CLAIMS — archived advisory leases (2026-07-29)

Seven agents share one repo, one Next.js app and one database. Nobody is
getting an isolated workspace: the merge cost would exceed the collision cost,
and that has already been decided. So collisions are avoided by *telling each
other*, not by separation.

**Before your first edit to any file, check this table. After your last edit,
release it.** That is the whole protocol.

This is advisory, not enforced. There is no lock daemon, nothing will stop you
writing to a claimed file. It works only if you read it — which costs you five
seconds and saves someone else an hour of untangling a clobbered edit.

---

## Live claims

Lease is **4 hours** from `Since`. After that the claim is dead and the file is
free — no permission needed, just take it (see *Dead agents* below).

| File / glob | Agent | Task | Since (local) |
|---|---|---|---|
| data/public/seed-snapshot.json | pkg-release | temp swap production snapshot into build context for docker build (waiting for user go), restore right after | 2026-07-27 18:57 |
| apps/web/src/lib/card-names.ts | pkg-release | approved 7-card i18n batch + cosmetic fixes | 2026-07-27 19:28 |
Release by **deleting your own row**. Do not tidy other people's rows.

### Claiming

Add one row. Keep it to one line — line-level edits to this table rarely
collide, block rewrites do. If your edit to this file conflicts, re-read and
re-apply your single row; never rewrite the whole table from memory.

```
| apps/web/src/components/rankings.tsx | delta-fix | wire trackedSalesChangePct | 2026-07-26 14:10 |
| apps/web/public/market-assets/**     | img-norm  | regenerate 429x600 canvases | 2026-07-26 15:02 |
```

`Agent` is whatever name the PM gave you in your brief. If you do not have one,
use something a human can page — `codex-sales-delta`, not `agent-3`.

Globs are fine and preferred for directories. Claim the narrowest thing that
actually covers your edits: claiming `apps/web/**` because you are touching two
components is how this register becomes useless.

### If the file you need is already claimed

In order of preference:

1. **Work elsewhere first.** Re-order your own task list; come back to it.
2. **Wait for the lease to expire**, if the claim is nearly dead.
3. **Tell the PM.** Two agents needing the same file at the same time is a task
   decomposition problem, and the PM is the one who can fix it.

Do **not** edit a live-claimed file "carefully". The other agent has the file
in context and will overwrite you without ever seeing your change.

### Dead agents

Agents crash, run out of context, and get cancelled. They do not release.

A claim whose `Since` is **more than 4 hours old is dead.** Take the file. You
may delete the dead row, and you should — leaving it means the next agent
repeats the same four-hour reasoning you just did.

If you are still working past four hours, **update your own `Since` to now.**
Re-stamping is how a long job stays alive; there is no separate renewal.

Why four hours: a lease that is too short causes two agents to believe they own
the same file, which is the actual harm. A lease that is too long only causes
an unnecessary wait, which the reader can override. So it errs long — but not
long enough that a claim abandoned before lunch is still blocking after dinner.

---

## Hot files — the ones that actually collide

Measured 2026-07-26 against the live working tree. These are not "important
files", they are files with a demonstrated history of two agents landing in
them at once.

| File | Why it collides |
|---|---|
| [apps/web/src/components/rankings.tsx](apps/web/src/components/rankings.tsx) | Every delta, sort, column and badge change lands here. The single busiest file in the repo. |
| [apps/web/src/lib/format.ts](apps/web/src/lib/format.ts) | Currency, FX and delta formatting. One non-finite FX rate blanks every money field on the page, so edits here have blast radius far beyond the diff. |
| [apps/web/public/market-assets/](apps/web/public/market-assets/) | Image pipeline writes it in bulk; UI work reads it. Bulk regeneration and a one-card fix will silently fight. |
| [apps/web/scripts/sync-snapshot.mjs](apps/web/scripts/sync-snapshot.mjs) | Deploy work and data work both edit it, from opposite ends. |
| [pipelines/canonical_public_snapshot.py](pipelines/canonical_public_snapshot.py) | The producer. Stories, deltas, POP series, images all reach the public snapshot through this one file. |
| [scripts/verify_handoff.py](scripts/verify_handoff.py) | Every agent that adds a check adds it here. |
| [CLAUDE.md](CLAUDE.md) | Rules and measured numbers, edited by whoever last learned something. |
| [PROJECT_STATE.md](PROJECT_STATE.md) | Single source of truth; every agent is told to update it on the way out, so the end of every task converges here. |

**`CLAUDE.md` and `PROJECT_STATE.md` are the two worst.** Every agent is
instructed to update them at the end of its task, which means collisions
cluster at exactly the moment everyone is finishing. Claim them, make the edit,
release immediately — do not hold either one across a long piece of work.

### Not on this list but worth a claim anyway

- Anything under `data/public/` — `seed-snapshot.json` in particular must stay
  demo data, and it is self-feeding, so a bad concurrent write propagates into
  the next generation.
- `manifests/image-qc.json` — rewritten wholesale by image runs.
- `data/editorial/*.json` — batch translation writes the whole file.

---

## What this does not do

It does not detect collisions, prevent them, or merge anything. It does not
know whether you actually edited what you claimed. An agent that does not read
this file is entirely unaffected by it.

It replaces one specific thing: the PM hand-writing "don't collide with
rankings.tsx" into seven separate prompts from memory. That does not scale and
it fails silently when the PM forgets a file. A register is at least
*consultable* by the agent who is about to cause the collision.
