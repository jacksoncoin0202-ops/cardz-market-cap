#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove sealed_daily's operator commands hold the e2e lease and grade each pull by its report.

2026-09-23: /box prices had stood still since 2026-08-20. The V2 cutover kept the box stage's
compose/export and archived the P6 collect lanes with nothing in their place; the sealed-*
operator commands docs/SEALED_OPS.md listed were never wired into this tree. sealed_daily.py
now carries refresh / stock / accept-binding / scan / release / add-product / set-product.
  - refresh / stock / accept-binding / scan / release / add-product / set-product run inside operator_e2e_lease;
    a refused lease runs nothing. add-product / set-product refuse to parse without --note (the source).
  - so do the corrections (quarantine / reject-binding / move-binding / add-binding / revoke-image / add-image),
    each with a required --note naming its evidence.
  - collect / compose / export / status / gaps take no lease: the V2 box stage holds it around
    compose/export, and a child's GET_LOCK would be refused.
  - sealed_collect exits 0 even when every fetch failed, so a pull is red on 0/N ok, on a report
    left over from an earlier run and on a non-zero exit -- and the other adapters and compose
    still run.
No MySQL, no network: operator_control, sealed_operator, sealed_runtime and subprocess.run are faked.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import types
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

EVENTS: list[tuple] = []
STATE = {"held": False, "refuse": False}
STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"  # sealed_runtime.utc_now()


@contextmanager
def fake_lease(owner):
    if STATE["refuse"]:
        raise RuntimeError(f"{owner} refused: another CARDZ 026 operator run owns the lease")
    STATE["held"] = True
    EVENTS.append(("lease", owner))
    try:
        yield
    finally:
        STATE["held"] = False


def recorder(name):
    def call(*args, **kwargs):
        EVENTS.append((name, STATE["held"], kwargs))
        return {}
    return call


TMP = Path(tempfile.mkdtemp(prefix="sealed-daily-cli-"))
operator_control = types.ModuleType("operator_control")
operator_control.operator_e2e_lease = fake_lease
sealed_operator = types.ModuleType("sealed_operator")
for name in ("cmd_sealed_status", "cmd_sealed_gaps", "cmd_sealed_accept_binding", "cmd_sealed_scan", "cmd_sealed_release",
             "cmd_sealed_add_product", "cmd_sealed_set_product", "cmd_export_sealed_subset", "cmd_sealed_quarantine",
             "cmd_sealed_reject_binding", "cmd_sealed_move_binding", "cmd_sealed_add_binding", "cmd_sealed_revoke_image",
             "cmd_sealed_add_image"):
    setattr(sealed_operator, name, recorder(name))
sealed_runtime = types.ModuleType("sealed_runtime")
sealed_runtime.OUT_DIR = TMP
sys.modules.update(operator_control=operator_control, sealed_operator=sealed_operator, sealed_runtime=sealed_runtime)

import sealed_daily  # noqa: E402

# adapter -> (exit code, attempted, ok[, extra report fields]); attempted None = the child writes no report this time
PULLS: dict[str, tuple] = {}


def fake_run(cmd, cwd=None, timeout=None):
    script = Path(cmd[4]).name
    if script == "sealed_collect.py":
        mode, adapter = cmd[5], cmd[7]
        code, attempted, ok, *extra = PULLS.get(adapter, (0, 3, 3))
        EVENTS.append(("pull", STATE["held"], mode, adapter))
        if attempted is not None:
            report = TMP / "collect" / f"last_{mode}.json"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({
                "asOf": datetime.now(timezone.utc).strftime(STAMP),
                "reports": [{"adapter": adapter, "mode": mode, "attempted": attempted, "ok": ok, **(extra[0] if extra else {})}],
            }), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, code)
    EVENTS.append((script, STATE["held"]))
    return subprocess.CompletedProcess(cmd, 0)


sealed_daily.subprocess.run = fake_run


def run(argv, pulls=None):
    EVENTS.clear()
    PULLS.clear()
    PULLS.update(pulls or {})
    return sealed_daily.main(argv)


def receipt(name):
    return json.loads((TMP / f"{name}-receipt.json").read_text(encoding="utf-8"))


def pulled():
    return [(e[2], e[3], e[1]) for e in EVENTS if e[0] == "pull"]


def main() -> int:
    adapters = list(sealed_daily.PULL_ADAPTERS)
    assert adapters == ["sealed_pc", "sealed_snk", "sealed_yahoo"], adapters

    code = run(["refresh"])
    assert code == 0, code
    assert EVENTS[0] == ("lease", "sealed:refresh"), EVENTS
    assert pulled() == [("incr", a, True) for a in adapters], "refresh must pull PC, SNK, Yahoo inside the lease: %r" % EVENTS
    assert EVENTS[-1] == ("sealed_price_compose.py", True), "compose runs last, inside the lease: %r" % EVENTS
    assert receipt("refresh")["red"] == [] and receipt("refresh")["composeExit"] == 0, receipt("refresh")
    print("POSITIVE_OK refresh pulls PC, SNK, Yahoo, then composes, all inside operator_e2e_lease")

    code = run(["refresh"], {"sealed_pc": (0, 5, 0)})
    assert code == 2, code
    assert receipt("refresh")["red"] == ["sealed_pc: 0/5 ok"], receipt("refresh")["red"]
    assert [p[1] for p in pulled()] == adapters and EVENTS[-1][0] == "sealed_price_compose.py", \
        "a red PC must not stop SNK, Yahoo or compose: %r" % EVENTS
    print("NEGATIVE_OK every PC fetch failing (sealed_collect still exits 0) turns refresh red; SNK/Yahoo/compose still run")

    code = run(["refresh"], {"sealed_yahoo": (1, 4, 4)})
    assert code == 2 and receipt("refresh")["red"] == ["sealed_yahoo: exit 1"], (code, receipt("refresh")["red"])
    print("NEGATIVE_OK a non-zero sealed_collect exit is red")

    stale = TMP / "collect" / "last_stock.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(json.dumps({
        "asOf": (datetime.now(timezone.utc) - timedelta(hours=30)).strftime(STAMP),
        "reports": [{"adapter": "sealed_snk", "mode": "stock", "attempted": 9, "ok": 9}],
    }), encoding="utf-8")
    code = run(["stock", "--adapter", "sealed_snk"], {"sealed_snk": (0, None, None)})
    assert code == 2 and receipt("stock")["red"] == ["sealed_snk: no report from this run"], (code, receipt("stock")["red"])
    print("NEGATIVE_OK a report left over from an earlier run does not count as this pull")

    code = run(["stock"])
    assert code == 0 and EVENTS[0] == ("lease", "sealed:stock"), (code, EVENTS)
    assert pulled() == [("stock", a, True) for a in adapters], "stock pulls all three by default, inside the lease: %r" % EVENTS
    print("POSITIVE_OK stock does the first full pull for every adapter inside the lease")

    eb05 = {"sku": "optcg:en:EB-05:booster-box:std", "key": "snkrdunk:767625", "sharedWith": ["optcg:en:EB-03:booster-box:std"]}
    code = run(["stock", "--adapter", "sealed_snk"], {"sealed_snk": (0, 0, 0, {"note": "nothing due", "blocked": [eb05]})})
    assert code == 2 and receipt("stock")["red"] == ["sealed_snk: blocked, source item bound to another SKU: optcg:en:EB-05:booster-box:std"], \
        "a SKU stock skipped for a shared source item must be red: %r" % ((code, receipt("stock")["red"]),)
    assert receipt("stock")["steps"][0]["blocked"] == [eb05], receipt("stock")["steps"]
    print("NEGATIVE_OK a SKU stock skipped for a shared source item is red, even when nothing else was due")

    shared = {"snkrdunk:145974": ["optcg:en:OP-06:booster-box:std", "optcg:jp:OP-06:booster-box:std"]}
    code = run(["refresh"], {"sealed_snk": (0, 3, 3, {"shared": shared})})
    snk = next(s for s in receipt("refresh")["steps"] if s["adapter"] == "sealed_snk")
    assert code == 0 and snk.get("shared") == shared and "red" not in snk, \
        "the refresh receipt must list the shared item without turning red: %r" % ((code, snk),)
    print("POSITIVE_OK incr keeps refreshing, and its receipt lists source items bound to 2+ SKUs")

    code = run(["accept-binding", "--sku", "ptcg-jp-m6a-booster-box-std", "--kind", "source", "--source-code", "snkrdunk", "--note", "n"])
    name, held, kwargs = EVENTS[-1]
    assert code == 0 and name == "cmd_sealed_accept_binding" and held, EVENTS
    assert kwargs == {"sku": "ptcg-jp-m6a-booster-box-std", "kind": "source", "source_code": "snkrdunk", "actor": "daddy",
                      "note": "n", "all_resolved": False, "group": None, "external_id": None}, kwargs
    code = run(["scan"])
    assert code == 0 and EVENTS[-1][:2] == ("cmd_sealed_scan", True), EVENTS
    code = run(["release", "--sku", "ptcg-jp-m6a-booster-box-std", "--note", "on sale 2026-09-16"])
    assert code == 0 and EVENTS[0] == ("lease", "sealed:release"), (code, EVENTS)
    assert EVENTS[-1] == ("cmd_sealed_release", True, {"sku": "ptcg-jp-m6a-booster-box-std", "actor": "daddy",
                                                       "note": "on sale 2026-09-16"}), EVENTS
    print("POSITIVE_OK accept-binding, scan and release write under the lease")

    add = ["add-product", "--game", "optcg", "--lang", "jp", "--set", "OP-18", "--name-en", "The Dominance of God",
           "--name-jp", "神の支配", "--release", "2026-11", "--packs", "24",
           "--official-url", "https://one-piece.com/news/81629/index.html", "--note", "one-piece.com news 81629"]
    code = run(add)
    assert code == 0 and EVENTS[0] == ("lease", "sealed:add-product"), (code, EVENTS)
    assert EVENTS[-1] == ("cmd_sealed_add_product", True, {
        "game": "optcg", "lang": "jp", "set_code": "OP-18", "product_kind": "booster-box", "print_wave": "std",
        "name_en": "The Dominance of God", "name_jp": "神の支配", "release_month": "2026-11", "packs_per_box": 24,
        "official_url": "https://one-piece.com/news/81629/index.html", "actor": "daddy", "note": "one-piece.com news 81629"}), EVENTS
    code = run(["set-product", "--sku", "optcg-jp-eb-03-booster-box-std", "--release", "2025-10", "--note", "news 75635"])
    assert code == 0 and EVENTS[0] == ("lease", "sealed:set-product"), (code, EVENTS)
    assert EVENTS[-1] == ("cmd_sealed_set_product", True, {
        "sku": "optcg-jp-eb-03-booster-box-std", "actor": "daddy", "note": "news 75635",
        "fields": {"name_en": None, "name_jp": None, "release_month": "2025-10", "packs_per_box": None, "official_url": None}}), EVENTS
    # every flag reaches its field: a dropped one would leave the wrong fact in place without a word
    code = run(["set-product", "--sku", "x", "--name-en", "E", "--name-jp", "J", "--release", "2026-01", "--packs", "10",
                "--official-url", "https://o.example/p", "--note", "n"])
    assert code == 0 and EVENTS[-1][2]["fields"] == {"name_en": "E", "name_jp": "J", "release_month": "2026-01", "packs_per_box": 10,
                                                     "official_url": "https://o.example/p"}, "set-product flag lost: %r" % EVENTS
    for argv in (add[:-2], ["set-product", "--sku", "x", "--release", "2025-10"]):
        try:
            run(argv)
        except SystemExit as exc:
            assert exc.code == 2, exc
        else:
            raise AssertionError(f"{argv[0]} without --note must not run: a catalog change needs its source")
        assert EVENTS == [], f"{argv[0]} without --note ran {EVENTS}"
    print("POSITIVE_OK add-product and set-product write under the lease; neither runs without --note naming the source")

    # the corrections: each flag reaches its field, each writes under the lease, none runs without --note (its evidence)
    snk = ["--source-code", "snkrdunk", "--ext", "apparels:1"]
    fixes = [
        (["quarantine", "--table", "sale", "--ids", "334667, 334755", "--note", "e"], "cmd_sealed_quarantine",
         {"table": "sale", "ids": [334667, 334755], "restore": False, "actor": "daddy", "note": "e"}),
        (["reject-binding", "--sku", "s10b", *snk, "--note", "e"], "cmd_sealed_reject_binding",
         {"sku": "s10b", "source_code": "snkrdunk", "external_id": "apparels:1", "actor": "daddy", "note": "e"}),
        (["move-binding", "--from-sku", "a", "--to-sku", "b", *snk, "--note", "e"], "cmd_sealed_move_binding",
         {"source_code": "snkrdunk", "external_id": "apparels:1", "from_sku": "a", "to_sku": "b", "actor": "daddy", "note": "e"}),
        (["add-binding", "--sku", "s10b", *snk, "--url", "https://snkrdunk.com/apparels/1", "--note", "e"], "cmd_sealed_add_binding",
         {"sku": "s10b", "source_code": "snkrdunk", "external_id": "apparels:1", "url": "https://snkrdunk.com/apparels/1",
          "actor": "daddy", "note": "e"}),
        (["revoke-image", "--sku", "s10b", "--note", "e"], "cmd_sealed_revoke_image", {"sku": "s10b", "actor": "daddy", "note": "e"}),
        (["add-image", "--sku", "s10b", "--url", "https://x.example/b.jpg", "--note", "e"], "cmd_sealed_add_image",
         {"sku": "s10b", "url": "https://x.example/b.jpg", "actor": "daddy", "note": "e"}),
    ]
    for argv, name, kwargs in fixes:
        code = run(argv)
        assert code == 0 and EVENTS[0] == ("lease", f"sealed:{argv[0]}") and EVENTS[-1] == (name, True, kwargs), (argv, EVENTS)
        try:
            run(argv[:-2])
        except SystemExit as exc:
            assert exc.code == 2, exc
        else:
            raise AssertionError(f"{argv[0]} without --note must not run: a correction needs its evidence")
        assert EVENTS == [], f"{argv[0]} without --note ran {EVENTS}"
    code = run(["accept-binding", "--sku", "s10b", "--kind", "image", "--ext", "abc", "--note", "n"])
    assert code == 0 and EVENTS[-1][2]["external_id"] == "abc", "accept-binding --ext lost: %r" % EVENTS
    print("POSITIVE_OK quarantine / reject / move / add-binding / revoke-image / add-image write under the lease; "
          "none runs without --note")

    STATE["refuse"] = True
    try:
        for argv in (["refresh"], ["stock"], ["accept-binding", "--sku", "x", "--kind", "image"], ["scan"], ["release", "--sku", "x"],
                     add, ["set-product", "--sku", "x", "--release", "2025-10", "--note", "n"], *[f[0] for f in fixes]):
            try:
                run(argv)
            except RuntimeError as exc:
                assert "refused" in str(exc), exc
            else:
                raise AssertionError(f"{argv}: a refused lease must stop the command")
            assert EVENTS == [], f"{argv} ran {EVENTS} without the lease"
    finally:
        STATE["refuse"] = False
    print("NEGATIVE_OK a refused lease runs nothing")

    for argv, expect in ((["compose"], "sealed_price_compose.py"),
                         (["export", "--output", str(TMP / "box-subset.json")], "cmd_export_sealed_subset"),
                         (["status"], "cmd_sealed_status"), (["gaps", "--limit", "5"], "cmd_sealed_gaps"),
                         (["collect", "--adapter", "sealed_pc"], "pull")):
        code = run(argv)
        assert code == 0 and any(e[0] == expect for e in EVENTS), (argv, code, EVENTS)
        assert not any(e[0] == "lease" for e in EVENTS), \
            f"{argv} must stay lease-free (the V2 box stage holds the lease around compose/export): {EVENTS}"
    print("POSITIVE_OK collect/compose/export/status/gaps stay lease-free for the V2 box stage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
