#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOX (sealed) daily entrypoint for the fe-db chain. No operator_control.py edits.
  python -X utf8 pipelines/sealed_daily.py collect --adapter sealed_pc
  python -X utf8 pipelines/sealed_daily.py compose
  python -X utf8 pipelines/sealed_daily.py export --output data/public/box-subset.json

Operator commands (docs/SEALED_OPS.md). The operator_control sealed-* commands that doc used to
list were never wired into this tree, and the V2 cutover archived the P6 collect lanes
(archive/scripts/morning_browser_lanes.ps1, nightly_collect_accept.ps1) with nothing in their
place, so BOX prices stood still from 2026-08-20 to 2026-09-23.
  python -X utf8 pipelines/sealed_daily.py status | gaps [--limit N]
  python -X utf8 pipelines/sealed_daily.py refresh         # daily: incr PC (CDP 9333) + SNK + Yahoo, then compose
  python -X utf8 pipelines/sealed_daily.py stock [--adapter sealed_snk]   # first full pull after a new accept
  python -X utf8 pipelines/sealed_daily.py accept-binding --sku <slug> --kind source --source-code snkrdunk
  python -X utf8 pipelines/sealed_daily.py scan            # release due / upcoming / unbound + SNK/PC discovery
  python -X utf8 pipelines/sealed_daily.py release --sku <slug> --note "..."   # unreleased -> active once its month has come
  python -X utf8 pipelines/sealed_daily.py add-product --game optcg --lang jp --set OP-18 --name-en "..." --name-jp "..." \
      --release 2026-11 --packs 24 --official-url https://... --note "official source"   # new box, goes in unreleased
  python -X utf8 pipelines/sealed_daily.py set-product --sku <slug> --release 2025-10 --note "official source"
refresh / stock / accept-binding / scan / release / add-product / set-product hold operator_e2e_lease, like collect_control. collect /
compose / export stay lease-free: the V2 box stage runs them as children while it holds the lease,
and a child's GET_LOCK on its own connection would be refused. scripts/test_sealed_daily_cli.py.
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

# PC first: the 9333 page capture runs before the HTTP adapters (P6 order). Accepted binds only, as P6 ran.
PULL_ADAPTERS = ("sealed_pc", "sealed_snk", "sealed_yahoo")
PULL_TIMEOUT_S = 7200


def compose(py: str) -> int:
    return subprocess.run([py, "-X", "utf8", "-u", str(ROOT / "pipelines" / "sealed_price_compose.py"), "--write"], cwd=str(ROOT)).returncode


def pull_verdict(adapter: str, mode: str, code: int, doc: dict | None, started: datetime) -> dict:
    """sealed_collect exits 0 even when every fetch failed (CDP down, SNK blocked), so a pull is
    judged by the report it wrote: exit 0, a report dated after this pull started, and ok > 0.
    A SKU that stock skipped because its source item is bound to another SKU is red until a human
    rules which SKU owns the item; `shared` rides along so incr receipts show it too."""
    step: dict = {"adapter": adapter, "mode": mode, "exit": code}
    try:
        fresh = datetime.fromisoformat(str((doc or {}).get("asOf")).replace("Z", "+00:00")) >= started
    except (TypeError, ValueError):
        fresh = False
    report = next((r for r in doc.get("reports") or [] if r.get("adapter") == adapter), None) if fresh else None
    if report is not None:
        step.update(attempted=int(report.get("attempted") or 0), ok=int(report.get("ok") or 0), note=report.get("note"))
        step.update({k: report[k] for k in ("blocked", "shared") if report.get(k)})
    if code != 0:
        step["red"] = f"exit {code}"
    elif report is None:
        step["red"] = "no report from this run"
    elif step["attempted"] and not step["ok"]:
        step["red"] = f"0/{step['attempted']} ok"
    elif step.get("blocked"):
        step["red"] = "blocked, source item bound to another SKU: " + ", ".join(b["sku"] for b in step["blocked"])
    return step


def pull(py: str, mode: str, adapter: str) -> dict:
    from sealed_runtime import OUT_DIR
    started = datetime.now(timezone.utc)
    try:
        code = subprocess.run([py, "-X", "utf8", "-u", str(ROOT / "pipelines" / "sealed_collect.py"), mode, "--adapter", adapter],
                              cwd=str(ROOT), timeout=PULL_TIMEOUT_S).returncode
    except subprocess.TimeoutExpired:
        return {"adapter": adapter, "mode": mode, "exit": None, "red": f"timeout {PULL_TIMEOUT_S}s"}
    report = OUT_DIR / "collect" / f"last_{mode}.json"
    doc = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    return pull_verdict(adapter, mode, code, doc, started)


def pull_all(py: str, name: str, mode: str, adapters: tuple[str, ...]) -> int:
    """A red adapter does not stop the next one or compose (P6: PC red still let SNK/Yahoo run); the run stays red."""
    from sealed_runtime import OUT_DIR
    steps = [pull(py, mode, adapter) for adapter in adapters]
    doc: dict = {"asOf": datetime.now(timezone.utc).isoformat(), "action": f"sealed-{name}", "steps": steps}
    if name == "refresh":
        doc["composeExit"] = compose(py)
    doc["red"] = [f"{s['adapter']}: {s['red']}" for s in steps if "red" in s] + (["compose: exit %s" % doc["composeExit"]] if doc.get("composeExit") else [])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{name}-receipt.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 2 if doc["red"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("collect"); p.add_argument("--adapter", required=True); p.add_argument("--allow-candidates", action="store_true")
    sub.add_parser("compose")
    e = sub.add_parser("export"); e.add_argument("--output", type=Path, required=True); e.add_argument("--include-candidates", action="store_true")
    sub.add_parser("status")
    g = sub.add_parser("gaps"); g.add_argument("--limit", type=int)
    sub.add_parser("refresh")
    s = sub.add_parser("stock"); s.add_argument("--adapter", choices=PULL_ADAPTERS, action="append", help="repeatable; default all three")
    b = sub.add_parser("accept-binding", help="human freeze for sealed identity/source/image")
    b.add_argument("--sku", default=None, help="sku_id or slug"); b.add_argument("--kind", choices=("identity", "source", "image"), required=True)
    b.add_argument("--source-code", default=""); b.add_argument("--actor", default="daddy"); b.add_argument("--note", default=None)
    b.add_argument("--all-resolved", action="store_true", help="bulk-accept every resolved candidate bind for --source-code")
    b.add_argument("--group", default=None, help="restrict bulk accept to one group_code")
    sub.add_parser("scan")
    r = sub.add_parser("release", help="catalog status unreleased -> active once the release month has come")
    r.add_argument("--sku", required=True, help="sku_id or slug"); r.add_argument("--actor", default="daddy"); r.add_argument("--note", default=None)
    n = sub.add_parser("add-product", help="a box the catalog does not know yet; goes in unreleased")
    n.add_argument("--game", required=True); n.add_argument("--lang", required=True); n.add_argument("--set", dest="set_code", required=True)
    n.add_argument("--kind", default="booster-box"); n.add_argument("--wave", default="std")
    n.add_argument("--name-en", required=True); n.add_argument("--name-jp", default=None)
    n.add_argument("--release", required=True, help="YYYY-MM"); n.add_argument("--packs", type=int, required=True)
    n.add_argument("--official-url", default=None)
    n.add_argument("--actor", default="daddy"); n.add_argument("--note", required=True, help="official source for these facts")
    c = sub.add_parser("set-product", help="correct catalog facts on one SKU")
    c.add_argument("--sku", required=True, help="sku_id or slug")
    c.add_argument("--name-en", default=None); c.add_argument("--name-jp", default=None); c.add_argument("--release", default=None)
    c.add_argument("--packs", type=int, default=None); c.add_argument("--official-url", default=None)
    c.add_argument("--actor", default="daddy"); c.add_argument("--note", required=True, help="official source for the correction")
    a = ap.parse_args(argv)
    py = sys.executable
    if a.cmd == "collect":
        cmd = [py, "-X", "utf8", "-u", str(ROOT / "pipelines" / "sealed_collect.py"), "incr", "--adapter", a.adapter]
        if a.allow_candidates: cmd.append("--allow-candidates")
        return subprocess.run(cmd, cwd=str(ROOT)).returncode
    if a.cmd == "compose":
        return compose(py)
    import sealed_operator
    if a.cmd == "export":
        sealed_operator.cmd_export_sealed_subset(output=a.output, include_candidates=a.include_candidates)
        return 0
    if a.cmd == "status":
        sealed_operator.cmd_sealed_status()
        return 0
    if a.cmd == "gaps":
        sealed_operator.cmd_sealed_gaps(limit=a.limit)
        return 0
    import operator_control
    with operator_control.operator_e2e_lease(f"sealed:{a.cmd}"):
        if a.cmd == "refresh":
            return pull_all(py, "refresh", "incr", PULL_ADAPTERS)
        if a.cmd == "stock":
            return pull_all(py, "stock", "stock", tuple(a.adapter or PULL_ADAPTERS))
        if a.cmd == "accept-binding":
            sealed_operator.cmd_sealed_accept_binding(sku=a.sku, kind=a.kind, source_code=a.source_code, actor=a.actor,
                                                      note=a.note, all_resolved=a.all_resolved, group=a.group)
            return 0
        if a.cmd == "release":
            sealed_operator.cmd_sealed_release(sku=a.sku, actor=a.actor, note=a.note)
            return 0
        if a.cmd == "add-product":
            sealed_operator.cmd_sealed_add_product(
                game=a.game, lang=a.lang, set_code=a.set_code, product_kind=a.kind, print_wave=a.wave, name_en=a.name_en,
                name_jp=a.name_jp, release_month=a.release, packs_per_box=a.packs, official_url=a.official_url,
                actor=a.actor, note=a.note)
            return 0
        if a.cmd == "set-product":
            sealed_operator.cmd_sealed_set_product(
                sku=a.sku, actor=a.actor, note=a.note,
                fields={"name_en": a.name_en, "name_jp": a.name_jp, "release_month": a.release, "packs_per_box": a.packs,
                        "official_url": a.official_url})
            return 0
        sealed_operator.cmd_sealed_scan()
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
