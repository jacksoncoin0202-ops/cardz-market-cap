#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次過跑晒 repo 入面所有 test 入口。

點解要有呢個檔：呢個 repo 有 11 個 `scripts/test_*.py` 同 12 個 `pipelines/*.py`
嘅 self-test，總共 23 個入口，而**冇任何嘢跑佢哋** —— 冇 CI、冇 Makefile、冇
pytest 設定、git hook 全部係 git-lfs。即係話每一個「防再犯」嘅 test 寫完之後
由第一日起就係死代碼：邏輯被人刪走，冇人會知。

兩個入口形狀唔同，所以淨係 glob `scripts/test_*.py` 係唔夠嘅 —— 咁樣會靜靜漏
咗全部 12 個 self-test。下面 SELF_TEST_ENTRIES 逐個寫明；同時 _discover 會自己
掃返 pipelines/ 睇有冇未登記嘅 self-test，有就即刻紅。加咗新 self-test 而唔登記
= 呢個 runner 唔過，唔會出現「有 test 但零 call site」呢個形狀。

Run: python -X utf8 scripts/run_all_tests.py
     python -X utf8 scripts/run_all_tests.py --no-db   # 跳過要連 DB 嗰啲
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

# module -> 跑法。`--self-test` 係 flag 形，db_runtime 係 subcommand 形。
SELF_TEST_ENTRIES: dict[str, list[str]] = {
    "active_universe.py": ["--self-test"],
    "daily_prices.py": ["--self-test"],
    "db_runtime.py": ["self-test"],
    "fx_rates.py": ["--self-test"],
    "g10_ingest.py": ["--self-test"],
    "g10_public_snapshot.py": ["--self-test"],
    "gemrate_client.py": ["--self-test"],
    "market_alerts.py": ["--self-test"],
    "market_discovery.py": ["--self-test"],
    "market_source_sync.py": ["--self-test"],
    "pc_psa10_price_materialize.py": ["--self-test"],
    "source_crosswalk.py": ["--self-test"],
    "tag_daily_capture.py": ["--self-test"],
    "bootstrap_quote_revisions.py": ["--self-test"],
}

# Script-side self-tests do not match either test_*.py or pipeline discovery.
# Keep them explicit for the same reason as SELF_TEST_ENTRIES: an uncalled
# negative self-test is not a guard.
SCRIPT_SELF_TEST_ENTRIES: dict[str, list[str]] = {
    "validate_daily_release.py": ["--self-test"],
    "proof_historical_quote_resolver.py": [],
    "promo_chain.py": ["self-test"],
}

# 要連 3308 先跑得嘅入口。--no-db 淨係跳過呢啲，其餘照跑。
# test_price_lane_contracts 係 scripts/test_*.py glob 嗰邊；glob loop 都會查呢個 set。
NEEDS_DB = {"db_runtime.py", "proof_historical_quote_resolver.py", "test_price_lane_contracts.py", "test_collect_shares_e2e_lease.py"}

# g10_public_snapshot 個 `--self-test` 唔係 unit test，係「照砌 snapshot 但唔寫
# asset、容許舊價」，所以要成棵 G10 source tree。呢棵 tree 唔喺呢個 repo 入面。
# 唔存在就報住個路徑 SKIP，唔准當 PASS；tree 一擺返落嚟就會照跑照紅。
REQUIRES_PATH: dict[str, Path] = {
    "g10_public_snapshot.py": ROOT / "integrations" / "grade10" / "data",
}

# These are valid local evidence tests, but their fixtures intentionally live
# under ignored private/runtime roots and are absent from the clean WSL release
# checkout. A public bake must report that absence as SKIP, not reinterpret it
# as a code failure. Portable guards remain mandatory.
SCRIPT_REQUIRES_PATH: dict[str, Path] = {
    "test_identity_name.py": (
        ROOT / "data" / "private" / "gemrate" / "cards"
        / "80a8b349acb400cc08fe55617c3ff152256d371d"
        / "card_details.raw.receipt.json"
    ),
    "test_pc_identity_discover_rules.py": (
        ROOT / "data" / "runtime" / "operator" / "psa-identity-repair-034"
        / "audit.json"
    ),
    "test_pc_sale_identity.py": (
        ROOT / "data" / "private" / "pricecharting_session" / "html" / "full900"
        / "1_pikachu-with-grey-felt-hat-85_r.html"
    ),
}

SELF_TEST_PATTERN = re.compile(
    r'add_argument\(\s*"--self-test"|add_parser\(\s*"self-test"'
)


def _discover_self_tests() -> set[str]:
    found = set()
    for path in sorted((ROOT / "pipelines").glob("*.py")):
        if SELF_TEST_PATTERN.search(path.read_text(encoding="utf-8", errors="replace")):
            found.add(path.name)
    return found


def _run(label: str, argv: list[str], timeout: int) -> tuple[str, float, str]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", time.monotonic() - started, f"超過 {timeout}s"
    took = time.monotonic() - started
    if proc.returncode == 0:
        return "PASS", took, ""
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    tail += (proc.stderr or "").strip().splitlines()[-3:]
    return "FAIL", took, " / ".join(line.strip() for line in tail if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="跑晒 23 個 test 入口")
    parser.add_argument("--no-db", action="store_true", help="跳過要連 3308 嗰啲")
    parser.add_argument("--skip-fe", action="store_true", help="跳過 scripts/test-*.mjs")
    parser.add_argument("--skip-pipelines", action="store_true", help="跳過 pipelines/* --self-test")
    parser.add_argument("--skip-script-tests", action="store_true", help="跳過 scripts/test_*.py")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    results: list[tuple[str, str, float, str]] = []

    # 未登記嘅 self-test 直接當一條紅，唔係 warning。
    discovered = _discover_self_tests()
    unregistered = sorted(discovered - set(SELF_TEST_ENTRIES))
    stale = sorted(set(SELF_TEST_ENTRIES) - discovered)
    if unregistered:
        results.append(
            (
                "runner:self-test-registry",
                "FAIL",
                0.0,
                "pipelines/ 有未登記 self-test：" + ", ".join(unregistered),
            )
        )
    elif stale:
        results.append(
            (
                "runner:self-test-registry",
                "FAIL",
                0.0,
                "登記咗但已經冇 self-test：" + ", ".join(stale),
            )
        )
    else:
        results.append(("runner:self-test-registry", "PASS", 0.0, ""))

    for module in sorted(SELF_TEST_ENTRIES):
        label = f"pipelines/{module}"
        if args.skip_pipelines:
            results.append((label, "SKIP", 0.0, "--skip-pipelines"))
            continue
        if args.no_db and module in NEEDS_DB:
            results.append((label, "SKIP", 0.0, "--no-db"))
            continue
        required = REQUIRES_PATH.get(module)
        if required is not None and not required.exists():
            results.append((label, "SKIP", 0.0, f"欠 {required.relative_to(ROOT)}"))
            continue
        argv = [PY, "-X", "utf8", str(ROOT / "pipelines" / module)]
        argv += SELF_TEST_ENTRIES[module]
        results.append((label, *_run(label, argv, args.timeout)))

    for script in sorted(SCRIPT_SELF_TEST_ENTRIES):
        label = f"scripts/{script}:self-test"
        if args.no_db and script in NEEDS_DB:
            results.append((label, "SKIP", 0.0, "--no-db"))
            continue
        argv = [PY, "-X", "utf8", str(ROOT / "scripts" / script)]
        argv += SCRIPT_SELF_TEST_ENTRIES[script]
        results.append((label, *_run(label, argv, args.timeout)))

    for path in sorted((ROOT / "scripts").glob("test_*.py")):
        label = f"scripts/{path.name}"
        if args.skip_script_tests:
            results.append((label, "SKIP", 0.0, "--skip-script-tests"))
            continue
        if args.no_db and path.name in NEEDS_DB:
            results.append((label, "SKIP", 0.0, "--no-db"))
            continue
        required = SCRIPT_REQUIRES_PATH.get(path.name)
        if required is not None and not required.exists():
            results.append((label, "SKIP", 0.0, f"欠 machine-private fixture: {required}"))
            continue
        argv = [PY, "-X", "utf8", str(path)]
        results.append((label, *_run(label, argv, args.timeout)))

    # Node 側嘅 test（`test-*.mjs`，連字號，同上面下劃線嗰批分得開）。唔可以淨係
    # 掃 Python：出街閘（public-surface-gate）、robots 政策、舊 URL 308 三樣都係
    # .mjs，漏咗佢哋等於呢個 runner 話「全綠」但最貼近出街嗰三件事根本冇跑過。
    node = "node.exe" if sys.platform == "win32" else "node"
    for path in sorted((ROOT / "scripts").glob("test-*.mjs")):
        label = f"scripts/{path.name}"
        if args.skip_fe:
            results.append((label, "SKIP", 0.0, "--skip-fe"))
            continue
        results.append((label, *_run(label, [node, str(path)], args.timeout)))

    width = max(len(row[0]) for row in results)
    for label, status, took, detail in results:
        line = f"{status:<8}{label:<{width}}  {took:6.1f}s"
        if detail:
            line += f"  {detail[:160]}"
        print(line)

    failed = [row for row in results if row[1] in ("FAIL", "TIMEOUT")]
    skipped = [row for row in results if row[1] == "SKIP"]
    print(
        f"\n{len(results) - len(failed) - len(skipped)}/{len(results) - len(skipped)}"
        f" passed, {len(failed)} failed, {len(skipped)} skipped"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
