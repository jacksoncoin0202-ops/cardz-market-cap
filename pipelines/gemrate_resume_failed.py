#!/usr/bin/env python3
"""Re-harvest failed GemRate sets (curl_cffi path), then rebuild combined files.

Reads data/private/gemrate_brute/failed_sets.jsonl written by
gemrate_brute_harvest.py, retries each set via gh.extract_row_data(set_link),
merges recovered rows back into all_cards.jsonl / psa10_1000_plus.jsonl the
same way the harvester does, and rewrites failed_sets.jsonl with only the
still-failing sets. All output files are written atomically (temp + os.replace).
"""
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location(
    "gh", str(Path(__file__).parent / "gemrate_brute_harvest.py"))
gh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gh)


def save_jsonl_atomic(data: list, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main() -> None:
    failed_path = gh.DATA_DIR / "failed_sets.jsonl"
    if not failed_path.exists():
        print(f"no {failed_path} — nothing to resume")
        return
    failed = [json.loads(l) for l in open(failed_path, encoding="utf-8") if l.strip()]
    if not failed:
        print("failed_sets.jsonl is empty — nothing to resume")
        return
    print(f"retrying {len(failed)} failed sets")

    sets_data = gh.extract_sets_data()
    by_id = {s["set_id"]: s for s in sets_data}

    still_failed = []
    recovered = 0
    for i, fs in enumerate(failed, 1):
        sid = fs["set_id"]
        s = by_id.get(sid)
        if not s:
            print(f"[{i}/{len(failed)}] {str(sid)[:12]} not in setsData")
            still_failed.append(fs)
            continue
        try:
            cards = gh.extract_row_data(s["set_link"])
            for c in cards:
                c["_set_id"] = sid
                c["_set_name"] = s.get("set_name")
                c["_set_link"] = s.get("set_link")
            safe = re.sub(r"[^\w\-]+", "_", s.get("set_name", "unknown"))[:60]
            save_jsonl_atomic(cards, gh.DATA_DIR / f"set_{sid}_{safe}.jsonl")
            recovered += 1
            print(f"[{i}/{len(failed)}] OK {s.get('set_name')} -> {len(cards)}")
            time.sleep(gh.SET_DELAY)
        except Exception as e:
            print(f"[{i}/{len(failed)}] STILL FAIL {s.get('set_name')}: {e}")
            still_failed.append(fs)
            time.sleep(gh.SET_DELAY * 2)

    # rebuild combined from all set_*.jsonl (same shape as harvest_all_sets)
    all_cards = []
    for f in sorted(gh.DATA_DIR.glob("set_*.jsonl")):
        for line in open(f, encoding="utf-8"):
            if line.strip():
                all_cards.append(json.loads(line))
    save_jsonl_atomic(all_cards, gh.DATA_DIR / "all_cards.jsonl")
    psa = [c for c in all_cards if int(c.get("psa_10") or 0) >= 1000]
    save_jsonl_atomic(psa, gh.DATA_DIR / "psa10_1000_plus.jsonl")
    save_jsonl_atomic(still_failed, failed_path)
    print(f"SUMMARY recovered={recovered} still_failed={len(still_failed)} "
          f"total_cards={len(all_cards)} psa10_1000={len(psa)}")


if __name__ == "__main__":
    main()
