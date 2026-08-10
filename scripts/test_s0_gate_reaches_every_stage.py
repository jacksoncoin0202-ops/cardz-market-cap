#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S0 is the gate; prove it stands in front of the stage that deletes rows.

Two defects, both found by re-reading the orchestrator on 2026-08-10.

1. `--stage prune-apply` never ran `stage_preflight`. S0 holds the backup
   check, the prune allowlist review, the scheduled-task scan and the drained
   ingest-run check; only the linear walk passed through it, because preflight
   is simply LINEAR_STAGES[0]. The single-stage path checked the writer freeze
   and the activation and nothing else -- and prune-apply is the command that
   DELETES rows.

2. The backup check re-hashed a dump named in a hand-written proof and called
   that `backup_sha_verified`. A matching sha proves the file has not been
   modified since somebody wrote the proof; it says nothing about whether the
   file can restore the database it is guarding. Measured 2026-08-10: the dump
   completed 2026-08-08 07:58 UTC while the newest row in the checkpoint ledger
   was 2026-08-10 15:26 UTC, so restoring it would have thrown away every
   binding, every prune and two days of collection -- and the gate passed.

   The first fix compared the dump against the ledger, and that comparison is
   unsatisfiable: a run's own stages write, so at 17:34Z it stopped prune-apply
   and offered the activation prune-apply exists to follow as the reason. What
   a dump is, is a restore point for the freeze window it was taken in -- after
   the window closes the collectors run again and those bytes stop being a
   place it is safe to go back to. So the freeze stamps the window it opened
   and the dump names the window it was taken in.

Run: python -X utf8 scripts/test_s0_gate_reaches_every_stage.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as R  # noqa: E402

failures: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got != want:
        failures.append(label)
        print(f"FAIL {label}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {label}")


class _Cursor:
    """S0's only query is the drained-ingest-run count; answer it with zero."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return {"n": 0}


class _Conn:
    def cursor(self):
        return _Cursor()


# --- 1. every single-stage run pays S0 -------------------------------------
# Recorded rather than mocked away: the question is whether the call happens at
# all, and for which stages, so the stand-ins only note that they ran.
def _drive(stage: str) -> list[str]:
    called: list[str] = []
    saved = {name: getattr(R, name) for name in
             ("stage_preflight", "_assert_activated", "_checkpoints", "_run_stage")}
    R.stage_preflight = lambda ctx: called.append("preflight") or {}
    R._assert_activated = lambda ctx: called.append("activated")
    R._checkpoints = lambda conn, generation: {}
    R._run_stage = lambda ctx, name, fn, forced: called.append(f"stage:{name}")
    try:
        R._run_single_stage(SimpleNamespace(
            args=SimpleNamespace(stage=stage, force_stage=False),
            conn=None, generation="036_x",
        ))
    finally:
        for name, value in saved.items():
            setattr(R, name, value)
    return called


check("the stage that deletes rows runs S0 first",
      _drive("prune-apply")[:2], ["preflight", "activated"])
check("so does a linear stage asked for on its own",
      _drive("bind"), ["preflight", "stage:bind"])
# S0 asking for S0 would recurse; it is already the thing being run.
check("and S0 itself is not asked to precede itself",
      _drive("preflight"), ["stage:preflight"])


# --- 2. the backup has to be newer than the database it protects -----------
DUMP = ROOT / "data" / "runtime" / "rebuild-036" / "_s0_gate_probe.sql"
DUMP.parent.mkdir(parents=True, exist_ok=True)
MARKER = "-- Dump completed on 2026-08-09 12:00:00"
DUMP.write_text(f"-- fake dump\n{MARKER}\n", encoding="utf-8")
proof = {"dumpFile": str(DUMP), "dumpSha256": R.sha256_file(DUMP),
         "dumpCompletedMarker": MARKER}

check("the completion time is read off the dump, not the proof",
      R._dump_taken_at(proof, DUMP), datetime(2026, 8, 9, 12, 0, 0))
# The proof is hand-written and its claim decides whether a destructive stage
# may run, so a claim the sha-verified bytes do not carry is refused.
lying = dict(proof, dumpCompletedMarker="-- Dump completed on 2026-08-11 12:00:00")
try:
    R._dump_taken_at(lying, DUMP)
    check("a completion time the dump does not carry is refused", "accepted", "abort")
except SystemExit as error:
    check("a completion time the dump does not carry is refused",
          "does not end with" in str(error), True)

OPENED = "2026-08-09 11:30:00.000000"
proof["freezeOpenedAt"] = OPENED
saved_proof, saved_window = R.RESTORE_PROOF, R.FREEZE_WINDOW
R.RESTORE_PROOF = DUMP.with_suffix(".json")
R.RESTORE_PROOF.write_text(json.dumps(proof), encoding="utf-8")
R.FREEZE_WINDOW = DUMP.with_name("_s0_gate_probe_window.json")
R.FREEZE_WINDOW.write_text(json.dumps({"openedAt": OPENED}), encoding="utf-8")
saved_freeze, saved_sub = R._run_freeze_proof, R.subprocess
R._run_freeze_proof = lambda: None
tasks = json.dumps([{"name": f"cardz-t{i}", "path": "\\", "state": "Disabled",
                     "actions": "cardz"} for i in range(14)])
R.subprocess = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(
    returncode=0, stdout=tasks.encode("utf-8"), stderr=b""))
try:
    # A dump is a restore point for the window it was taken in and no other:
    # once the window closes the collectors run again, and going back to those
    # bytes means throwing that away. Nothing here compares the dump against
    # the ledger -- the run's own stages write, so that comparison stopped
    # prune-apply on 2026-08-10T17:34Z citing the activation it was following.
    result = R.stage_preflight(SimpleNamespace(conn=_Conn()))
    check("a dump taken inside the open window is a restore point",
          result["counts"]["freeze_opened_at"], f"{OPENED}Z")
    check("and the run records when that backup was taken",
          result["counts"]["backup_taken_at"], "2026-08-09T12:00:00Z")

    R.FREEZE_WINDOW.write_text(
        json.dumps({"openedAt": "2026-08-10 09:00:00.000000"}), encoding="utf-8")
    try:
        R.stage_preflight(SimpleNamespace(conn=_Conn()))
        check("a dump from an earlier freeze window is refused", "accepted", "abort")
    except SystemExit as error:
        check("a dump from an earlier freeze window is refused",
              "taken in the freeze window opened" in str(error), True)
    R.FREEZE_WINDOW.unlink()
    try:
        R.stage_preflight(SimpleNamespace(conn=_Conn()))
        check("and with no freeze open there is no restore point at all",
              "accepted", "abort")
    except SystemExit as error:
        check("and with no freeze open there is no restore point at all",
              "no freeze window is open" in str(error), True)
finally:
    R._run_freeze_proof, R.subprocess = saved_freeze, saved_sub
    R.RESTORE_PROOF.unlink(missing_ok=True)
    R.FREEZE_WINDOW.unlink(missing_ok=True)
    R.RESTORE_PROOF, R.FREEZE_WINDOW = saved_proof, saved_window
    DUMP.unlink(missing_ok=True)

# --- 3. yesterday's refusal must not refuse today's run --------------------
# S0 is re-judged on every pass, so its recorded failure describes the pass that
# wrote it. Blocking on it demanded --force-stage, which is one flag for the
# whole walk: the only way past a harmless preflight row also cleared the way
# for a stage that failed half-written.
def _walk(checkpoints: dict, force: bool = False) -> object:
    ran: list[str] = []
    saved = {name: getattr(R, name) for name in ("_checkpoints", "_run_stage")}
    R._checkpoints = lambda conn, generation: checkpoints
    R._run_stage = lambda ctx, name, fn, forced: ran.append(name)
    try:
        R._run_linear(SimpleNamespace(
            args=SimpleNamespace(force_stage=force), conn=None, generation="036_x",
        ))
        return ran
    except SystemExit as error:
        return str(error)
    finally:
        for name, value in saved.items():
            setattr(R, name, value)


_done = {name: {"status": R.REBUILD_STAGE_COMPLETE, "input_sha256": None}
         for name, _, _, _ in R.LINEAR_STAGES}
_s0_failed = dict(_done, preflight={
    "status": "failed", "error_code": "S0 ABORT: writer freeze is not in force.",
    "input_sha256": None,
})
check("a run is not stopped by the gate that stopped the last run",
      _walk(_s0_failed), ["preflight", "validate"])
# The same walk, one stage over: bind writes, so its failure is a half-written
# table and a human still has to look before it runs again.
_bind_failed = dict(_done, bind={"status": "failed", "error_code": "boom",
                                 "input_sha256": None})
check("but a stage that failed part-way through writing still stops it",
      "previously failed" in str(_walk(_bind_failed)), True)
check("and --force-stage is still the way past that one",
      _walk(_bind_failed, force=True), ["preflight", "bind", "validate"])


print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    raise SystemExit(1)
print("S0 stands in front of every stage, and the backup has to be current")
