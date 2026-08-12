#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""裁決撤綁：SNK item 413361（Event Organizer 版）唔係 v1874 普通 Battle Festa。

跨家族矛盾 gate（test_price_lane_contracts check 2）今晚捉到 v1874 兩家族
180d 中位數差 8.57x。查落唔係窗口/算式問題，係同號唔同 parallel 錯綁
（runbook 形狀 12 嘅變種：同一個 collector number 下面藏住兩個市場）：

  - 我哋張卡：2015 Battle Festa 175/XY-P 普通場販 kira —— PC 產品頁
    （binding exact，年份佐證過）current $4,714，最近 eBay 成交 $3-5k。
  - SNK item 413361：master name 明寫「バトルフェスタ2015 オリジナルキラカード
    **イベントオーガナイザー版**」（Event Organizer Version），
    usedMinPrice ¥6,300,000，兩單 PSA10 trades ¥3.58M／¥6.5M（$22k／$40k）。
    主辦方特別版，同號 175/XY-P，但係另一張卡另一個市場。

  binder 收咗佢係因為 SNK providerClaims.parallelCode 係空、collector number
  啱曬，而「battle festa」字面又出現喺名度。裁決：reject（人手核實 SNK 原文
  evidence JSON data/private/snk/.../items/413361.json，sha 3a8676…）。

  修 input 唔係修 gate（AGENTS 規矩 12）。reject + action verdict 落
  bind_evidence_json 之後：strict view 即刻剔走佢（match_status='exact' 先入），
  eligible quote / accepted sales 兩個 view 跟住 strict，¥6.5M 嗰兩行價、兩單
  trades 全部變 inert（append-only，唔刪）；snk_identity_discover 見到
  rejection_is_verdict=True 唔會 re-propose，第日佢搵到真・普通版 item 先會
  行 manual_review 入嚟。

用法：
  python -X utf8 pipelines/reject_snk_organizer_binding_20260813.py --dry-run
  python -X utf8 pipelines/reject_snk_organizer_binding_20260813.py --write
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import connect, DAILY_CREDENTIALS_ENV  # noqa: E402

EXT = "413361"
VARIANT = 1874
REASON = (
    "SNK master name reads 'Battle Festa 2015 Original Kira Card Event Organizer"
    " Version' (イベントオーガナイザー版): same collector number 175/XY-P but the"
    " organizer parallel, a different card in a different market (usedMinPrice"
    " JPY6,300,000; PSA10 trades JPY3.58M/6.5M vs our regular card's PC $4,714,"
    " recent eBay $3-5k). Cross-family conflict gate fired at 8.57x."
)
RECEIPT = ROOT / "data" / "runtime" / "operator" / "audit" / "snk_organizer_reject_20260813.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()

    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()

    cur.execute(
        """SELECT variant_id, match_status FROM catalog_source_identity
           WHERE source_code='snkrdunk' AND external_entity_id=%s""",
        (EXT,),
    )
    rows = cur.fetchall()
    if len(rows) != 1 or int(rows[0]["variant_id"]) != VARIANT or rows[0]["match_status"] != "exact":
        raise SystemExit(f"precondition failed, refusing: {rows}")

    cur.execute(
        "SELECT COUNT(*) n FROM operator_strict_source_identity"
        " WHERE source_code='snkrdunk' AND external_entity_id=%s AND variant_id=%s",
        (EXT, VARIANT),
    )
    strict_before = int(cur.fetchone()["n"])
    print(json.dumps({"strictBefore": strict_before, "plan": "reject"}, ensure_ascii=False))
    if args.dry_run:
        conn.close()
        return 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur.execute(
        """UPDATE catalog_source_identity SET match_status='rejected',
             bind_evidence_json=JSON_SET(
               COALESCE(bind_evidence_json, JSON_OBJECT()),
               '$.previousAction', JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json,'$.action')),
               '$.action', 'reject',
               '$.rejectedAt', %s,
               '$.rejectedBy', 'operator-adjudication-20260813',
               '$.reason', %s)
           WHERE source_code='snkrdunk' AND external_entity_id=%s
             AND variant_id=%s AND match_status='exact'""",
        (now, REASON, EXT, VARIANT),
    )
    if cur.rowcount != 1:
        conn.rollback()
        raise SystemExit(f"expected exactly 1 row updated, got {cur.rowcount}; rolled back")
    conn.commit()

    cur.execute(
        "SELECT COUNT(*) n FROM operator_strict_source_identity"
        " WHERE source_code='snkrdunk' AND external_entity_id=%s AND variant_id=%s",
        (EXT, VARIANT),
    )
    strict_after = int(cur.fetchone()["n"])

    receipt = {
        "completedAt": now,
        "action": "reject",
        "sourceCode": "snkrdunk",
        "externalEntityId": EXT,
        "variantId": VARIANT,
        "reason": REASON,
        "strictBefore": strict_before,
        "strictAfter": strict_after,
        "evidenceFile": "data/private/snk/rebuild-036_20260808T084217Z/items/413361.json",
        "evidenceSha256": "3a86765956511fb45c55abda3f4aae2ca993d68290d5e66348515e2272eabedf",
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    print(f"receipt: {RECEIPT}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
