# docs/evidence — measurements worth keeping

`temp/` is where a measurement is born and where most of them should die.
This directory is for the few that answered a question expensively enough that
the next agent should not have to ask it again.

The problem this solves is not disk tidiness. It is that on 2026-07-26 a single
afternoon produced fifteen diagnostic files in `temp/`, several of which cost a
full DB pass and a snapshot rebuild to produce, and none of which any future
agent would ever find. The next agent investigating card images would have
started from zero — and, judging by history, would have reached a *different*
answer, because the cheap version of the question gives a false one.

## What gets promoted

Promote when **all three** hold:

1. **It settled something.** There is a sentence you would now defend, not just
   a data dump. "12 cards are genuinely missing images, not 4" is a conclusion.
   "here is a 4 MB snapshot" is not.
2. **It is reusable.** Someone will plausibly ask this again — coverage,
   drift, gap, inventory, root-cause questions all recur.
3. **It was expensive.** Full-table scans, snapshot rebuilds, cross-source
   joins, anything that took real time or real API budget.

Everything else stays in `temp/` and is disposable: build logs, HTML dumps,
screenshots, one-off probes, intermediate batches, `*_probe*.py` shrapnel.

**Promote sparingly.** A directory that accepts everything is the graveyard
`temp/` already is, with a better name.

## Layout

One directory per investigation, named `YYYY-MM-DD-<slug>`:

```
docs/evidence/2026-07-26-image-coverage/
  FINDING.md          <- the card; read this first, it is the only required file
  img_skip_report.json    <- frozen output
  img_skip_diagnose.py    <- the producer that made it
```

Keep the **producer script next to its output**. An output nobody can
regenerate is an assertion, not evidence. If promoting a producer out of
`temp/` changes its depth, fix its `ROOT = Path(__file__).resolve().parents[N]`
and say so in `FINDING.md` — that is the one edit allowed to a promoted script.

## FINDING.md must carry four things

| Field | Why it is mandatory |
|---|---|
| **Measured** | date + time. Without it nobody can tell whether the number rotted. |
| **How** | the exact command or SQL, runnable from repo root. Not a description of it. |
| **Conclusion** | one sentence. If it takes a paragraph, the investigation is not finished. |
| **Premises** | what was true when it was measured — row counts, branch, which snapshot, which gate version. This is what makes a stale finding *interpretable* instead of merely wrong. |

Numbers inside a `FINDING.md` carry `@verified` stamps like any other document,
so `scripts/verify_claims.py` re-checks them. See CLAUDE.md for the grammar.

## Reading an old finding

A finding is a **photograph, not a live feed.** Check `Measured` first. Then run
`python -X utf8 scripts/verify_claims.py docs/evidence/<dir>/FINDING.md` — if
the stamps still hold, the conclusion probably survives; if they DRIFT, the
finding tells you what changed and the producer script is right there to re-run.

Do not edit an old finding's numbers in place. Re-run the producer, write a new
dated directory, and add a line to the old `FINDING.md` pointing at the new one.
Evidence is append-only; overwriting it destroys the ability to say *when* a
thing became true.
