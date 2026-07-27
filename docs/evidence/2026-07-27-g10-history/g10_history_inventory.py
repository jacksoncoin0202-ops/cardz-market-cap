"""G10 歷史 POP 存量盤點：磁碟 history_full.json vs DB 兩張 population 表。

唯讀。零外部 API 調用。

跑法（要先 source DB env）：
    set -a && . data/runtime/config/backend.env && set +a
    python -X utf8 docs/evidence/2026-07-27-g10-history/g10_history_inventory.py

roster identity map 唔會寫入呢個目錄（gemrate id 屬 gitignored 嘅 data/runtime/），
每次經 scripts/ro_sql.py 即場由 DB 攞，落 temp/。

輸出三段：
  [A] 磁碟存量        —— data/private/gemrate/cards/*/history_full.json
  [B] roster × 磁碟   —— 邊啲 population variant 有歷史檔
  [C] 序列質量        —— 可用點數、同 DB 日期重疊、負 delta
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[3]
CARDS = ROOT / "data" / "private" / "gemrate" / "cards"
IDENTITY_CACHE = ROOT / "temp" / "g10_roster_gemrate_ids.json"

IDENTITY_SQL = (
    "SELECT DISTINCT p.variant_id, csi.external_entity_id "
    "FROM market_grader_population_observation p "
    "JOIN catalog_source_identity csi "
    "ON csi.variant_id=p.variant_id AND csi.source_code='gemrate'"
)

# pipelines/canonical_public_snapshot.py 個 POPULATION_HISTORY_KEYS 嘅同一份對照
KEYS = {
    "PSA": ("psa", "psa_10"),
    "BGS": ("beckett", "beckett_10_pristine"),
    "CGC": ("cgc", "cgc_10_perfect"),
    "SGC": ("sgc", "sgc_10_pristine"),
}

# DB market_grader_population_observation 現有嘅 observed_date（2026-07-27 量）
DB_DATES = {
    "2026-07-21", "2026-07-22", "2026-07-23",
    "2026-07-24", "2026-07-25", "2026-07-26", "2026-07-27",
}


def load_identity_map() -> dict[int, set[str]]:
    if not IDENTITY_CACHE.is_file():
        IDENTITY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "ro_sql.py"),
             "--json", "--limit", "5000", IDENTITY_SQL],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        )
        if proc.returncode != 0:
            sys.exit(f"ro_sql.py failed ({proc.returncode}): {proc.stderr[:400]}")
        IDENTITY_CACHE.write_text(proc.stdout, encoding="utf-8")
    payload = json.loads(IDENTITY_CACHE.read_text(encoding="utf-8"))
    rows = payload[0]["rows"] if isinstance(payload, list) else payload["rows"]
    out: dict[int, set[str]] = {}
    for r in rows:
        out.setdefault(int(r["variant_id"]), set()).add(str(r["external_entity_id"]))
    print(f"identity_rows={len(rows)}")
    return out


def scan_disk() -> tuple[set[str], set[str]]:
    total_dirs = 0
    parse_fail = 0
    grader_counter: Counter[str] = Counter()
    point_counter: Counter[int] = Counter()
    window_intervals: Counter[str] = Counter()
    all_dates: set[str] = set()
    total_points = 0
    on_disk_dir: set[str] = set()
    on_disk_history: set[str] = set()

    for d in sorted(CARDS.iterdir()):
        if not d.is_dir():
            continue
        total_dirs += 1
        on_disk_dir.add(d.name)
        hf = d / "history_full.json"
        if not hf.is_file():
            continue
        on_disk_history.add(d.name)
        try:
            doc = json.loads(hf.read_text(encoding="utf-8-sig"))
        except Exception:
            parse_fail += 1
            continue
        pop = (doc.get("data") or {}).get("population") or {}
        win = pop.get("window") or {}
        if win.get("interval"):
            window_intervals[str(win["interval"])] += 1
        by_grader = pop.get("by_grader") or (pop.get("population_data") or {}).get("by_grader") or {}
        card_dates: set[str] = set()
        for grader, block in by_grader.items():
            hist = (block or {}).get("history") or []
            if hist:
                grader_counter[grader] += 1
            for pt in hist:
                dt = pt.get("date")
                if dt:
                    card_dates.add(str(dt))
                    total_points += 1
        if card_dates:
            point_counter[len(card_dates)] += 1
            all_dates |= card_dates

    print("=== [A] 磁碟存量 data/private/gemrate/cards/")
    print(f"card_dirs_total={total_dirs}")
    print(f"dirs_with_history_full={len(on_disk_history)}")
    print(f"parse_fail={parse_fail}")
    print(f"grader_history_present={dict(grader_counter)}")
    print(f"window_intervals={dict(window_intervals)}")
    print(f"total_grader_date_points={total_points}")
    print(f"distinct_dates={len(all_dates)}")
    sd = sorted(all_dates)
    print(f"date_min={sd[0]}  date_max={sd[-1]}")
    print(f"per_card_datepoint_hist={dict(sorted(point_counter.items()))}")
    return on_disk_dir, on_disk_history


def join_roster(by_variant, on_disk_dir, on_disk_history) -> list[int]:
    covered, dir_no_history, no_dir = [], [], []
    for vid, gids in by_variant.items():
        if gids & on_disk_history:
            covered.append(vid)
        elif gids & on_disk_dir:
            dir_no_history.append(vid)
        else:
            no_dir.append(vid)
    roster_gids = {g for gids in by_variant.values() for g in gids}
    print()
    print("=== [B] roster(population variants) × 磁碟")
    print(f"roster_variants_with_gemrate_identity={len(by_variant)}")
    print(f"roster_covered_by_history={len(covered)}")
    print(f"roster_dir_exists_but_no_history={len(dir_no_history)}")
    print(f"roster_no_disk_dir_at_all={len(no_dir)}")
    print(f"history_files_not_in_roster={len(on_disk_history - roster_gids)}")
    return covered


def quality(by_variant, on_disk_history, covered: list[int]) -> None:
    series_total = 0
    series_with_neg = 0
    neg_steps = 0
    neg_magnitudes: Counter[str] = Counter()
    worst = None
    usable_points = 0
    dropped: Counter[str] = Counter()
    overlap_points = 0
    new_points = 0
    dates_seen: set[str] = set()
    per_grader_series: Counter[str] = Counter()

    for vid in sorted(covered):
        gid = next(iter(by_variant[vid] & on_disk_history))
        doc = json.loads((CARDS / gid / "history_full.json").read_text(encoding="utf-8-sig"))
        by_grader = ((doc.get("data") or {}).get("population") or {}).get("population_data", {}).get("by_grader") or {}
        for grader, (src_key, grade_key) in KEYS.items():
            block = by_grader.get(src_key)
            pts = (block or {}).get("history") if isinstance(block, dict) else None
            if not isinstance(pts, list) or not pts:
                continue
            seq: list[tuple[str, int]] = []
            for p in pts:
                if not isinstance(p, dict):
                    continue
                v = (p.get("grades") or {}).get(grade_key)
                d = p.get("date")
                if not isinstance(v, int) or isinstance(v, bool) or v < 0 or not d:
                    if d:
                        dropped[grade_key] += 1
                    continue
                seq.append((str(d), v))
            if not seq:
                continue
            series_total += 1
            per_grader_series[grader] += 1
            seq.sort()
            bad = 0
            for a, b in zip(seq, seq[1:]):
                if b[1] < a[1]:
                    bad += 1
                    delta = b[1] - a[1]
                    bucket = "-1" if delta == -1 else ("-2..-9" if delta >= -9 else ("-10..-99" if delta >= -99 else "<=-100"))
                    neg_magnitudes[bucket] += 1
                    if worst is None or delta < worst[0]:
                        worst = (delta, vid, grader, a, b)
            if bad:
                series_with_neg += 1
                neg_steps += bad
            for d, _ in seq:
                usable_points += 1
                dates_seen.add(d)
                if d in DB_DATES:
                    overlap_points += 1
                else:
                    new_points += 1

    sd = sorted(dates_seen)
    print()
    print("=== [C] 覆蓋卡嘅序列質量")
    print(f"series_(variant,grader)={series_total}")
    print(f"series_per_grader={dict(per_grader_series)}")
    print(f"usable_grade_points={usable_points}")
    print(f"points_overlapping_db_dates={overlap_points}")
    print(f"points_on_new_dates={new_points}")
    print(f"distinct_dates={len(sd)}  range={sd[0]} .. {sd[-1]}")
    print(f"points_dropped_missing_gradekey={dict(dropped)}")
    print(f"series_with_negative_delta={series_with_neg}")
    print(f"negative_delta_steps={neg_steps}")
    print(f"negative_delta_magnitude_buckets={dict(neg_magnitudes)}")
    print(f"worst_negative_step={worst}")


def main() -> None:
    by_variant = load_identity_map()
    on_disk_dir, on_disk_history = scan_disk()
    covered = join_roster(by_variant, on_disk_dir, on_disk_history)
    quality(by_variant, on_disk_history, covered)


if __name__ == "__main__":
    main()
