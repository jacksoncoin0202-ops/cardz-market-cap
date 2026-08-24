#!/usr/bin/env python3
"""Refresh the broad private index and atomically rebuild its radar manifest."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "integrations" / "grade10" / "run_service.py"
RADAR = ROOT / "pipelines" / "market_discovery.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh CARDZ broad market discovery")
    parser.add_argument("--expected-cards", type=int, default=600)
    parser.add_argument("--timeout-seconds", type=int, default=1_800)
    args = parser.parse_args()
    subprocess.run(
        [sys.executable, "-X", "utf8", str(SERVICE), "collect", "--scope", "index",
         "--expected-cards", str(args.expected_cards), "--timeout-seconds", str(args.timeout_seconds)],
        check=True,
        cwd=ROOT,
        timeout=args.timeout_seconds,
    )
    subprocess.run([sys.executable, "-X", "utf8", str(RADAR)], check=True, cwd=ROOT, timeout=300)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
