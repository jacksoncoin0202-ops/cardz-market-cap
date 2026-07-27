#!/usr/bin/env python3
"""量度 One Piece 卡嘅 gemrate PSA POP 缺口，並推算補 POP 之後嘅 OP 榜深度變化。

READ-ONLY。零 GemRate API 調用。只讀 MySQL + repo 檔案，唔改任何數據。

輸出（同目錄）：
  op_no_pop_cards.csv        - 冇 gemrate PSA POP 嘅 OP 卡完整名單（含價、假設市值）
  op_roster_blockers.csv     - roster 內 227 張 OP 卡入唔到榜嘅真實原因分佈
  measurements.json          - 所有 headline 數字，逐個註明 SQL 出處
  board_depth_projection.csv - top-N scope 選項對 OP 榜深度嘅推算

跑法：
    cd C:\\Users\\jackson0202\\Documents\\Playground\\cardz-market-cap
    set -a && . data/runtime/config/backend.env && set +a
    python -X utf8 docs/evidence/2026-07-27-op-pop-decision/produce_op_pop_gap.py
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_claims import db_config  # noqa: E402

# ---- 生產線常數（照抄，唔係我估） -------------------------------------------
# pipelines/market_alerts.py:25-29
PRICE_SOURCE_PRIORITY = {"snk_psa10": 10, "snk": 10, "g10": 20}
POPULATION_SOURCE_PRIORITY = {"gemrate": 10, "g10": 20}
CURRENT_POPULATION_MAX_GAP_DAYS = 2
PRICE_MAX_GAP_DAYS = 2  # market_alerts.py:250 pick_observation(..., max_gap_days=2)
BOARD_POP_MINIMUM = 1000  # market_alerts.py:335 tracked_indexes()

EVALUATION_ID = 9  # market_alert_evaluation 最新一行（effective_date 2026-07-26）
EFFECTIVE_DATE = date(2026, 7, 26)


def fetch(cursor, sql, params=()):
    cursor.execute(sql, params)
    return list(cursor.fetchall())


def scalar(cursor, sql, params=()):
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return list(row.values())[0] if row else None


# ---- 「冇 gemrate PSA POP」嘅準確定義 ----------------------------------------
# 表：market_grader_population_observation
# 條件：source_code='gemrate' AND grader_code='PSA'，取該 variant 最大 observed_date
#       嗰行（同日多行取最大 id），要求 top_grade_population 非 NULL 且 > 0。
LATEST_GEMRATE_POP = """
SELECT o.variant_id, o.observed_date, o.top_grade_population, o.estimated
FROM market_grader_population_observation o
JOIN (SELECT variant_id, MAX(observed_date) AS md
      FROM market_grader_population_observation
      WHERE grader_code='PSA' AND source_code='gemrate'
      GROUP BY variant_id) m
  ON m.variant_id = o.variant_id AND m.md = o.observed_date
WHERE o.grader_code='PSA' AND o.source_code='gemrate'
"""

# 任何來源嘅 PSA POP（gemrate 之外仲有 g10 mirror），用嚟分開「完全冇 POP」同
# 「冇 gemrate POP 但有 g10 POP」兩種缺口 —— 後者唔使花 quota。
LATEST_ANY_PSA_POP = """
SELECT o.variant_id, o.source_code, o.observed_date, o.top_grade_population
FROM market_grader_population_observation o
JOIN (SELECT variant_id, MAX(observed_date) AS md
      FROM market_grader_population_observation
      WHERE grader_code='PSA'
      GROUP BY variant_id) m
  ON m.variant_id = o.variant_id AND m.md = o.observed_date
WHERE o.grader_code='PSA'
"""

LATEST_PRICE = """
SELECT p.variant_id, p.source_code, p.observed_date, p.price_usd
FROM market_price_observation p
JOIN (SELECT variant_id, MAX(observed_date) AS md
      FROM market_price_observation
      WHERE price_usd IS NOT NULL AND price_usd > 0
      GROUP BY variant_id) m
  ON m.variant_id = p.variant_id AND m.md = p.observed_date
WHERE p.price_usd IS NOT NULL AND p.price_usd > 0
"""


def main() -> int:
    import pymysql

    cfg = db_config()
    conn = pymysql.connect(**cfg, cursorclass=pymysql.cursors.DictCursor)
    M: dict[str, object] = {}
    try:
        with conn.cursor() as cur:
            # ---- 母體 -------------------------------------------------------
            op_variants = fetch(cur, """
                SELECT id, opaque_id, canonical_name, set_name, collector_number,
                       identity_status, card_language
                FROM catalog_variant WHERE tcg_code='one-piece' ORDER BY id
            """)
            M["op_catalog_total"] = len(op_variants)

            roster_ids = {int(r["variant_id"]) for r in fetch(cur, """
                SELECT m.variant_id FROM market_universe_member m
                JOIN market_universe_lock l ON l.id=m.universe_lock_id
                WHERE l.is_current=1
            """)}
            M["roster_total"] = len(roster_ids)
            op_roster_ids = {int(v["id"]) for v in op_variants if int(v["id"]) in roster_ids}
            M["op_in_roster"] = len(op_roster_ids)
            M["op_out_of_roster"] = len(op_variants) - len(op_roster_ids)

            # ---- POP / 價 / identity 索引 -----------------------------------
            gem_pop = {int(r["variant_id"]): r for r in fetch(cur, LATEST_GEMRATE_POP)}
            any_pop = {int(r["variant_id"]): r for r in fetch(cur, LATEST_ANY_PSA_POP)}
            price = {int(r["variant_id"]): r for r in fetch(cur, LATEST_PRICE)}
            gem_id = {int(r["variant_id"]): str(r["external_entity_id"]) for r in fetch(cur, """
                SELECT variant_id, external_entity_id FROM catalog_source_identity
                WHERE source_code='gemrate'
            """)}

            def has_gem_pop(vid: int) -> bool:
                row = gem_pop.get(vid)
                return bool(row and row["top_grade_population"])

            no_pop = [v for v in op_variants if not has_gem_pop(int(v["id"]))]
            M["op_without_gemrate_pop"] = len(no_pop)
            M["op_without_gemrate_pop_in_roster"] = sum(
                1 for v in no_pop if int(v["id"]) in op_roster_ids)
            M["op_without_gemrate_id"] = sum(
                1 for v in op_variants if int(v["id"]) not in gem_id)
            M["op_without_gemrate_pop_and_without_id"] = sum(
                1 for v in no_pop if int(v["id"]) not in gem_id)
            M["op_without_gemrate_pop_but_has_g10_pop"] = sum(
                1 for v in no_pop
                if any_pop.get(int(v["id"])) and any_pop[int(v["id"])]["top_grade_population"])
            M["op_without_any_psa_pop"] = sum(
                1 for v in op_variants
                if not (any_pop.get(int(v["id"])) and any_pop[int(v["id"])]["top_grade_population"]))

            # ---- POP 假設基準 ------------------------------------------------
            # 參考類別選擇（呢個係全份報告最容易搞錯嘅一步）：
            #   roster 227 張 OP 卡嘅 gemrate POP 中位數 = 1392、74% >= 1000。
            #   但缺 POP 嗰 76 張全部係 promo / serial / championship 高價稀有卡，
            #   同 roster 主流卡唔同類 —— 攞 roster 中位數會系統性高估。
            #   正確參考類別 = 呢 76 張自己入面「有他源（ebay/snkrdunk）PSA POP」
            #   嗰 47 張嘅實測分佈。下面兩組數都出，等讀者自己睇偏差有幾大。
            known = [int(gem_pop[int(v["id"])]["top_grade_population"])
                     for v in op_variants if has_gem_pop(int(v["id"]))]
            known.sort()
            M["op_with_gemrate_pop"] = len(known)
            M["roster_ref_pop_median"] = statistics.median(known) if known else None
            M["roster_ref_pop_share_ge_1000"] = (
                round(sum(1 for p in known if p >= BOARD_POP_MINIMUM) / len(known), 4)
                if known else None)

            observed = sorted(
                int(any_pop[int(v["id"])]["top_grade_population"])
                for v in no_pop
                if any_pop.get(int(v["id"])) and any_pop[int(v["id"])]["top_grade_population"])
            M["comparable_ref_n"] = len(observed)
            M["comparable_ref_pop_min"] = observed[0] if observed else None
            M["comparable_ref_pop_median"] = statistics.median(observed) if observed else None
            M["comparable_ref_pop_max"] = observed[-1] if observed else None
            M["comparable_ref_pop_p25"] = observed[len(observed) // 4] if observed else None
            M["comparable_ref_pop_p75"] = observed[(len(observed) * 3) // 4] if observed else None
            M["comparable_ref_share_ge_1000"] = (
                round(sum(1 for p in observed if p >= BOARD_POP_MINIMUM) / len(observed), 4)
                if observed else None)
            comparable_median = int(M["comparable_ref_pop_median"] or 0)

            # ---- 名單 -------------------------------------------------------
            rows = []
            for v in no_pop:
                vid = int(v["id"])
                px = price.get(vid)
                px_usd = float(px["price_usd"]) if px else None
                other = any_pop.get(vid)
                # POP 假設優先序：
                #   1. 該卡自己有他源（ebay/snkrdunk）PSA POP → 直接用（實測，非估算）
                #   2. 冇 → 用可比類別（上面 47 張）嘅中位數
                if other and other["top_grade_population"]:
                    assumed = int(other["top_grade_population"])
                    assumed_basis = f"observed_{other['source_code']}"
                else:
                    assumed = comparable_median
                    assumed_basis = "comparable_median_assumption"
                rows.append({
                    "variant_id": vid,
                    "opaque_id": v["opaque_id"],
                    "canonical_name": v["canonical_name"],
                    "set_name": v["set_name"],
                    "collector_number": v["collector_number"],
                    "identity_status": v["identity_status"],
                    "in_roster": int(vid in op_roster_ids),
                    "has_gemrate_id": int(vid in gem_id),
                    "other_psa_pop_source": (str(other["source_code"])
                                             if other and other["top_grade_population"] else ""),
                    "other_psa_pop": (int(other["top_grade_population"])
                                      if other and other["top_grade_population"] else ""),
                    "latest_price_usd": round(px_usd, 2) if px_usd else "",
                    "price_source": str(px["source_code"]) if px else "",
                    "price_as_of": str(px["observed_date"]) if px else "",
                    # g10_kline = PSA10 日 K 線收盤（pipelines/g10_kline_price_bridge.py:4），
                    # snk_psa10/ebay 亦係 PSA10 口徑；snkrdunk 生貨掛牌價唔算。
                    "price_is_psa10_source": int(bool(px) and str(px["source_code"]) in
                                                 ("snk_psa10", "ebay", "g10_kline")),
                    "assumed_pop": int(assumed),
                    "assumed_pop_basis": assumed_basis,
                    "would_pass_pop1000_gate": int(assumed >= BOARD_POP_MINIMUM),
                    "potential_market_cap_usd_assumed": (
                        round(px_usd * assumed, 2) if px_usd else ""),
                    "potential_market_cap_usd_at_pop1000": (
                        round(px_usd * BOARD_POP_MINIMUM, 2) if px_usd else ""),
                })
            rows.sort(key=lambda r: -(r["potential_market_cap_usd_assumed"] or 0))
            M["op_no_pop_with_price"] = sum(1 for r in rows if r["latest_price_usd"] != "")
            M["op_no_pop_without_price"] = sum(1 for r in rows if r["latest_price_usd"] == "")
            M["op_no_pop_with_psa10_price"] = sum(1 for r in rows if r["price_is_psa10_source"])

            with (HERE / "op_no_pop_cards.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)

            # ---- roster 227 張 OP 卡真實 blocker 分佈 -------------------------
            # 生產線閘（market_alerts.py load_candidates + tracked_indexes）：
            #   價：observed_date 喺 [eff-2d, eff] 內；POP：同窗口 + estimated 假
            #   ready = 兩者齊；入榜 = ready 且 POP >= 1000
            floor = EFFECTIVE_DATE - timedelta(days=PRICE_MAX_GAP_DAYS)
            board_ids = {int(r["variant_id"]) for r in fetch(cur, """
                SELECT variant_id FROM market_index_constituent
                WHERE index_snapshot_id=(SELECT MAX(id) FROM market_index_snapshot
                                         WHERE index_code='one-piece')
            """)}
            M["op_board_current_depth"] = len(board_ids)
            M["op_board_snapshot_effective"] = str(scalar(cur, """
                SELECT effective_date FROM market_index_snapshot
                WHERE index_code='one-piece' ORDER BY id DESC LIMIT 1
            """))

            snap = {int(r["variant_id"]): r for r in fetch(cur, """
                SELECT variant_id, metric_status, psa10_population, reference_price_usd
                FROM market_candidate_daily_snapshot WHERE evaluation_id=%s
            """, (EVALUATION_ID,))}

            blockers = []
            counts: dict[str, int] = {}
            for vid in sorted(op_roster_ids):
                s = snap.get(vid)
                pop = int(s["psa10_population"]) if s and s["psa10_population"] else None
                px_ok = bool(s and s["reference_price_usd"])
                if vid in board_ids:
                    reason = "on_board"
                elif pop is None and not px_ok:
                    reason = "no_current_price_and_no_current_pop"
                elif pop is None:
                    reason = "no_current_pop_in_2d_window"
                elif not px_ok:
                    reason = "no_current_price_in_2d_window"
                elif pop < BOARD_POP_MINIMUM:
                    reason = f"pop_below_{BOARD_POP_MINIMUM}"
                else:
                    reason = "other"
                counts[reason] = counts.get(reason, 0) + 1
                v = next(x for x in op_variants if int(x["id"]) == vid)
                blockers.append({
                    "variant_id": vid,
                    "canonical_name": v["canonical_name"],
                    "set_name": v["set_name"],
                    "collector_number": v["collector_number"],
                    "blocker": reason,
                    "psa10_population": pop if pop is not None else "",
                    "reference_price_usd": (round(float(s["reference_price_usd"]), 2)
                                            if px_ok else ""),
                    "has_gemrate_pop_ever": int(has_gem_pop(vid)),
                    "latest_gemrate_pop_date": (str(gem_pop[vid]["observed_date"])
                                                if vid in gem_pop else ""),
                    "latest_gemrate_pop": (int(gem_pop[vid]["top_grade_population"])
                                           if has_gem_pop(vid) else ""),
                })
            M["op_roster_blockers"] = counts
            M["price_window_floor"] = str(floor)
            # 對照組：roster 內 OP 卡「POP 已達標、淨係差價」有幾多張。
            # 呢個數決定咗補 POP 值唔值 —— 如果佢遠大過補 POP 嘅預期收益，
            # 樽頸就唔喺 POP 度。
            M["op_roster_never_priced"] = scalar(cur, """
                SELECT COUNT(*) FROM market_universe_member m
                JOIN market_universe_lock l ON l.id=m.universe_lock_id
                JOIN catalog_variant v ON v.id=m.variant_id
                WHERE l.is_current=1 AND v.tcg_code='one-piece'
                  AND NOT EXISTS (SELECT 1 FROM market_price_observation p
                                  WHERE p.variant_id=m.variant_id AND p.price_usd>0)
            """)
            M["op_roster_pop_ge_1000_but_no_price"] = scalar(cur, """
                SELECT COUNT(DISTINCT s.variant_id)
                FROM market_candidate_daily_snapshot s
                JOIN catalog_variant v ON v.id=s.variant_id
                WHERE s.evaluation_id=%s AND v.tcg_code='one-piece'
                  AND s.psa10_population>=%s AND s.reference_price_usd IS NULL
            """, (EVALUATION_ID, BOARD_POP_MINIMUM))
            with (HERE / "op_roster_blockers.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(blockers[0].keys()))
                w.writeheader()
                w.writerows(blockers)

            # ---- top-N scope 推算 -------------------------------------------
            # 一張榜外卡要入 OP 榜，要順序過晒四閘：
            #   1. 有 gemrate_id（冇就要先做免費 keyless search 解 identity）
            #   2. 抓到 POP 且 >= 1000
            #   3. 入 roster（要開新 universe lock，唔係抓 POP 就得）
            #   4. 有 2 日內新鮮價
            share = M["comparable_ref_share_ge_1000"] or 0
            proj = []
            for label, n in (("top10", 10), ("top30", 30), ("all_76", len(rows))):
                subset = rows[:n]
                with_id = sum(1 for r in subset if r["has_gemrate_id"])
                need_search = len(subset) - with_id
                fresh_price = sum(
                    1 for r in subset
                    if r["price_as_of"] and str(r["price_as_of"]) >= str(floor))
                # 入榜期望值分兩截，唔混做一個數：
                #   已實測 POP 嗰批 → 直接數 POP>=1000 有幾多張（唔係估）
                #   完全冇 POP 嗰批 → 用可比類別 P(POP>=1000) 加權
                obs_pass = sum(1 for r in subset
                               if r["assumed_pop_basis"].startswith("observed_")
                               and r["would_pass_pop1000_gate"]
                               and r["price_as_of"] >= str(floor))
                unk = sum(1 for r in subset
                          if r["assumed_pop_basis"] == "comparable_median_assumption"
                          and r["price_as_of"] >= str(floor))
                expected = round(obs_pass + unk * share, 1)
                proj.append({
                    "option": label,
                    "cards_targeted": len(subset),
                    "already_have_gemrate_id": with_id,
                    "need_keyless_identity_search_first": need_search,
                    "api_calls_current_pop_only": with_id * 1,
                    "api_calls_freeze_with_history": with_id * 2,
                    "cards_with_price_in_2d_window": fresh_price,
                    "observed_pop_ge_1000": obs_pass,
                    "pop_unknown_weighted": round(unk * share, 1),
                    "expected_new_board_entries_if_roster_expanded": expected,
                    "board_depth_after": round(len(board_ids) + expected, 1),
                })
            with (HERE / "board_depth_projection.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(proj[0].keys()))
                w.writeheader()
                w.writerows(proj)
            M["board_depth_projection"] = proj

            # ---- 併榜模擬：22 張實測 POP>=1000 嘅卡會插到邊 -------------------
            live = [(float(r["market_cap_usd"]), str(r["canonical_name"]), "現榜")
                    for r in fetch(cur, """
                        SELECT c.market_cap_usd, v.canonical_name
                        FROM market_index_constituent c JOIN catalog_variant v ON v.id=c.variant_id
                        WHERE c.index_snapshot_id=(SELECT MAX(id) FROM market_index_snapshot
                                                   WHERE index_code='one-piece')
                    """)]
            newcomers = [(float(r["potential_market_cap_usd_assumed"]),
                          str(r["canonical_name"]), "新入")
                         for r in rows
                         if r["would_pass_pop1000_gate"]
                         and r["assumed_pop_basis"].startswith("observed_")
                         and r["potential_market_cap_usd_assumed"] != ""]
            merged = sorted(live + newcomers, key=lambda t: -t[0])
            M["merged_board_total"] = len(merged)
            M["merged_new_in_top10"] = sum(1 for t in merged[:10] if t[2] == "新入")
            M["merged_new_in_top30"] = sum(1 for t in merged[:30] if t[2] == "新入")
            M["merged_newcomer_best_rank"] = next(
                (i for i, t in enumerate(merged, 1) if t[2] == "新入"), None)
            with (HERE / "merged_board_simulation.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["rank", "origin", "canonical_name", "market_cap_usd"])
                for i, (cap, name, origin) in enumerate(merged, 1):
                    w.writerow([i, origin, name, round(cap, 2)])

            # ---- 觀測窗口佐證 -----------------------------------------------
            M["gemrate_pop_observation_window"] = fetch(cur, """
                SELECT source_code, grader_code, COUNT(*) rows_,
                       COUNT(DISTINCT variant_id) cards_,
                       MIN(observed_date) mn, MAX(observed_date) mx
                FROM market_grader_population_observation
                GROUP BY source_code, grader_code ORDER BY rows_ DESC
            """)
            for r in M["gemrate_pop_observation_window"]:
                r["mn"], r["mx"] = str(r["mn"]), str(r["mx"])
    finally:
        conn.close()

    M["_measured_at"] = "2026-07-27"
    M["_evaluation_id"] = EVALUATION_ID
    M["_effective_date"] = str(EFFECTIVE_DATE)
    (HERE / "measurements.json").write_text(
        json.dumps(M, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(M, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
