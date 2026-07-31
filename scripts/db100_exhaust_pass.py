#!/usr/bin/env python3
"""One exhaustive pass toward DB 100% (local factory).

Runs the proven growth methods in order without inventing data:
  1) merge any new harvest jsonl into snkrdunk_all
  2) PTCG + OP exact matchers if present under temp/agent-swarm
  3) match-snk + clean
  4) trades for bound snk (via local_factory_chain)
  5) status dump

Does NOT push git. Does NOT weak-bind.  G10-derived K-lines/indexes are
explicitly banned from canonical DB and are not an executable step here.
"""

from __future__ import annotations

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
SWARM = ROOT / "temp" / "agent-swarm-20260729"
BANNED_DB_WRITE_STEPS = {
    "g10_kline_price_bridge": "G10-derived K-line data is forbidden from canonical DB",
}


def env_load() -> None:
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


def run(cmd: list[str], check: bool = True) -> int:
    print("[db100]", " ".join(str(c) for c in cmd[:10]))
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    if check and rc != 0:
        print(f"[db100] warn rc={rc} (continuing)" if not check else f"[db100] fail rc={rc}")
        if check:
            return rc
    return rc


def merge_all_new_jsonl() -> int:
    patterns = [
        "snk-psa10-SCALE-S*.jsonl",
        "snk-psa10-c01*.jsonl",
        "snk-psa10-c04*.jsonl",
        "snk-psa10-c05*.jsonl",
        "snk-psa10-ceiling*.jsonl",
        "snk-psa10-h3*.jsonl",
        "snk-psa10-SCALE-H3*.jsonl",
        "snk-psa10-factory*.jsonl",
    ]
    have: set[str] = set()
    if ALL.is_file():
        for line in ALL.open(encoding="utf-8"):
            if not line.strip():
                continue
            try:
                o = json.loads(line)
                if o.get("item_id") is not None:
                    have.add(str(o["item_id"]))
            except Exception:
                pass
    added = 0
    ALL.parent.mkdir(parents=True, exist_ok=True)
    with ALL.open("a", encoding="utf-8") as out:
        for pat in patterns:
            for path in sorted(MAP.glob(pat)):
                if path.name.endswith(".partial"):
                    continue
                for line in path.open(encoding="utf-8"):
                    if not line.strip():
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    iid = o.get("item_id")
                    if iid is None or str(iid) in have:
                        continue
                    out.write(json.dumps(o, ensure_ascii=False) + "\n")
                    have.add(str(iid))
                    added += 1
    print(f"[db100] merged +{added} into snkrdunk_all (now ~{len(have)})")
    return added


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
        return {}


def main() -> int:
    env_load()
    before = status()
    merge_all_new_jsonl()

    for name in ("scale_s11_ptcg_exact.py", "scale_s8_op_exact.py", "c09_write.py"):
        script = SWARM / name
        if script.is_file():
            run([PY, "-X", "utf8", str(script)], check=False)

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
        ],
        check=False,
    )
    run(
        [PY, "-X", "utf8", str(ROOT / "pipelines" / "semi_auto_identity.py"), "clean", "--write"],
        check=False,
    )
    run([PY, "-X", "utf8", str(ROOT / "pipelines" / "build_identity_registry.py")], check=False)

    run(
        [PY, "-X", "utf8", str(ROOT / "scripts" / "local_factory_chain.py"), "--trades"],
        check=False,
    )

    for step, reason in BANNED_DB_WRITE_STEPS.items():
        print(f"[db100] banned step {step}: {reason}")

    after = status()
    report = {
        "at": datetime.now(timezone.utc).isoformat(),
        "before": before,
        "after": after,
        "goal": "DB_100_EXHAUST",
        "bannedSteps": BANNED_DB_WRITE_STEPS,
    }
    out = ROOT / "temp" / "db100_exhaust_pass_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[db100] wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
