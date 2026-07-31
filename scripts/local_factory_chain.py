#!/usr/bin/env python3
"""本機全自動工廠 chain（方案 A 背景填庫 · 可選 bake）。

步驟（自動化 → 存量 → 增量 精神）：
  1) status
  2) optional: harvest ids file (增量)
  3) merge harvest jsonl → snkrdunk_all
  4) match-snk + clean（存量 exact 靠 semi_auto）
  5) trades for bound snk ids
  6) optional: bake_publish_pack

唔 push git。唔弱 bind。

例：
  python -X utf8 scripts/local_factory_chain.py --status-only
  python -X utf8 scripts/local_factory_chain.py --harvest-ids data/runtime/private-source-map/SCALE_HARVEST_S1.txt
  python -X utf8 scripts/local_factory_chain.py --match --trades --bake
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
MAP = ROOT / "data" / "runtime" / "private-source-map"
ALL = ROOT / "data" / "private" / "snkrdunk_brute" / "snkrdunk_all.jsonl"


def _load_env() -> None:
    p = ROOT / "data" / "runtime" / "config" / "backend.env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip("\r").strip('"').strip("'")
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    print("[factory]", " ".join(cmd[:8]), "…" if len(cmd) > 8 else "")
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, encoding="utf-8")
    if check and proc.returncode != 0:
        raise SystemExit(f"command failed rc={proc.returncode}: {cmd[:6]}")
    return proc


def status() -> dict:
    proc = subprocess.run(
        [PY, "-X", "utf8", str(ROOT / "pipelines" / "qualified_pool_operator.py"), "status"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    print(proc.stdout)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {"raw": proc.stdout[-500:]}


def harvest_ids(ids_file: Path, out: Path, delay: float) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            PY,
            "-X",
            "utf8",
            str(ROOT / "pipelines" / "snk_market_data.py"),
            "--ids-file",
            str(ids_file),
            "--condition",
            "trading_card_single_psa10",
            "--out",
            str(out),
            "--delay",
            str(delay),
        ]
    )


def merge_jsonl(src: Path) -> int:
    """Append src rows into snkrdunk_all by item_id."""
    import json as _json

    if not src.is_file():
        print("[factory] skip merge missing", src)
        return 0
    have: set[str] = set()
    ALL.parent.mkdir(parents=True, exist_ok=True)
    if ALL.is_file():
        for line in ALL.open(encoding="utf-8"):
            if not line.strip():
                continue
            try:
                o = _json.loads(line)
                if o.get("item_id") is not None:
                    have.add(str(o["item_id"]))
            except Exception:
                pass
    added = 0
    with ALL.open("a", encoding="utf-8") as out, src.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                o = _json.loads(line)
            except Exception:
                continue
            iid = o.get("item_id")
            if iid is None or str(iid) in have:
                continue
            out.write(_json.dumps(o, ensure_ascii=False) + "\n")
            have.add(str(iid))
            added += 1
    print(f"[factory] merge +{added} → {ALL}")
    return added


def match_snk() -> None:
    run(
        [
            PY,
            "-X",
            "utf8",
            str(ROOT / "pipelines" / "semi_auto_identity.py"),
            "match-snk",
            "--write",
            "--recall-min",
            "20",
        ]
    )
    run(
        [
            PY,
            "-X",
            "utf8",
            str(ROOT / "pipelines" / "semi_auto_identity.py"),
            "clean",
            "--write",
        ],
        check=False,
    )
    run([PY, "-X", "utf8", str(ROOT / "pipelines" / "build_identity_registry.py")], check=False)


def trades() -> None:
    # reuse post-c09 style: harvest all bound then ingest if files exist
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ids_out = MAP / f"factory_bound_snk_ids_{ts}.txt"
    # ids from registry if present
    reg = MAP / "qualified-940-identity.jsonl"
    ids: list[str] = []
    if reg.is_file():
        import json as _json

        for line in reg.open(encoding="utf-8"):
            if not line.strip():
                continue
            o = _json.loads(line)
            sid = o.get("snkItemId") or o.get("snk_id") or o.get("snkrdunkId")
            if sid:
                ids.append(str(sid))
    if not ids:
        print("[factory] no bound snk ids in registry; skip trades")
        return
    ids_out.write_text("\n".join(sorted(set(ids))) + "\n", encoding="utf-8")
    harvest = MAP / f"snk-psa10-factory-trades-{ts}.jsonl"
    harvest_ids(ids_out, harvest, delay=0.35)
    run(
        [
            PY,
            "-X",
            "utf8",
            str(ROOT / "pipelines" / "ingest_snk_trades_sales.py"),
            "--harvest",
            str(harvest),
        ]
    )


def bake() -> None:
    run(
        [
            PY,
            "-X",
            "utf8",
            str(ROOT / "scripts" / "bake_publish_pack.py"),
            "--sync-local-serve",
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status-only", action="store_true")
    ap.add_argument("--harvest-ids", type=Path, help="ids file to harvest (increment)")
    ap.add_argument("--harvest-out", type=Path, default=None)
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--merge", type=Path, help="jsonl to merge into snkrdunk_all")
    ap.add_argument("--match", action="store_true")
    ap.add_argument("--trades", action="store_true")
    ap.add_argument("--bake", action="store_true")
    args = ap.parse_args()
    _load_env()
    MAP.mkdir(parents=True, exist_ok=True)

    before = status()
    if args.status_only:
        return 0

    if args.harvest_ids:
        out = args.harvest_out or MAP / f"snk-psa10-factory-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.jsonl"
        harvest_ids(args.harvest_ids, out, args.delay)
        merge_jsonl(out)

    if args.merge:
        merge_jsonl(args.merge)

    if args.match:
        match_snk()

    if args.trades:
        trades()

    if args.bake:
        bake()

    after = status()
    report = {
        "before": before,
        "after": after,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    rp = ROOT / "temp" / "local_factory_chain_report.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[factory] report", rp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
