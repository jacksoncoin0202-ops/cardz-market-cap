#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""snk_price_lane_audit 嘅裁決器同讀者隔離契約。

呢個 audit 動 DB status，所以測試要守住三件事：
1. 裁決器對種落去嘅毒行真係會紅（規矩 9：檢查要證明識 fire）；
2. 佢淨係可以隔離 ready 行、淨係寫 'quarantined_lane'、永遠唔 DELETE；
3. 'quarantined_lane' 同身份隔離 lane（'quarantined'，release 會放行）唔可以共用一個字，
   而 FE / legacy composition 兩個讀者真係識避開佢。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import snk_price_lane_audit as A  # noqa: E402
import rebuild_036 as R  # noqa: E402

FAILED: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got == want:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}: got {got!r}, want {want!r}")


# 1. 裁決器：種毒 → 紅；真數 → 綠。
chart = {"2026-05-29": 800000.0, "2026-06-16": 950000.0}

# 毒 A：carry-forward 捏造日（chart 07-28 冇成交）
verdict, _ = A.adjudicate("2026-07-28", 1831.61, 300000.0, chart)
check("fabricated day fires", verdict, "fabricated_day")

# 毒 B：同日但掛價冒充成交（native 差 68%）
verdict, _ = A.adjudicate("2026-06-16", None, 300000.0, chart)
check("value mismatch (native) fires", verdict, "value_mismatch")

# 毒 C：USD 行匯率帶外（$104 vs ¥950,000 → ratio 9134）
verdict, _ = A.adjudicate("2026-06-16", 104.0, None, chart)
check("value mismatch (usd band) fires", verdict, "value_mismatch")

# 真數 D：同日 native 齊桁
verdict, _ = A.adjudicate("2026-06-16", None, 950000.0, chart)
check("honest native replay keeps", verdict, "keep")

# 真數 E：同日 USD 落喺合理帶（ratio 950000/5903 ≈ 161）
verdict, _ = A.adjudicate("2026-06-16", 5903.55, None, chart)
check("honest usd replay keeps", verdict, "keep")

# 位移 F：JST 錯日一天，值齊桁 → shifted，交俾 canonical 兄弟判斷
verdict, _ = A.adjudicate("2026-05-30", None, 800000.0, chart)
check("shifted day detected", verdict, "shifted:2026-05-29")

# 2. SQL 契約。
check("suspect query only reads ready rows", "p.metric_status = 'ready'" in A.SUSPECT_ROWS_SQL, True)
for prefix in A.CANONICAL_RUN_PREFIXES:
    check(f"suspect query excludes {prefix}", f"NOT LIKE '{prefix}%'" in A.SUSPECT_ROWS_SQL, True)
check("apply stamps quarantined_lane", f"SET metric_status = '{A.QUARANTINE_STATUS}'" in A.APPLY_QUARANTINE_SQL, True)
check("apply only touches ready rows", "AND metric_status = 'ready'" in A.APPLY_QUARANTINE_SQL, True)
# 身份未證實行行標準身份隔離字，先入得 release 生命週期（exact+strict 證實後放返）。
check("identity apply stamps releasable status",
      f"SET metric_status = '{A.IDENTITY_QUARANTINE_STATUS}'" in A.APPLY_IDENTITY_QUARANTINE_SQL, True)
check("identity apply only touches ready rows", "AND metric_status = 'ready'" in A.APPLY_IDENTITY_QUARANTINE_SQL, True)
check("identity status is the releasable one", A.IDENTITY_QUARANTINE_STATUS, "quarantined")
source = Path(A.__file__).read_text(encoding="utf-8")
check("audit never deletes", "DELETE" not in source.upper().replace("DELETES", ""), True)

# 3. 隔離字唔可以同身份隔離 lane 共用（release 會放行 'quarantined'）。
check("status is not identity-quarantine", A.QUARANTINE_STATUS != "quarantined", True)
check("release lane only frees identity-quarantine rows",
      "p.metric_status = 'quarantined'" in R._RELEASABLE_PRICE_ROWS_WHERE, True)
check("release lane cannot free lane-quarantine rows",
      A.QUARANTINE_STATUS in R._RELEASABLE_PRICE_ROWS_WHERE, False)

# 4. 讀者隔離：FE 歷史 query 同 legacy composition 都要避開非 ready 行。
# 2026-08-24（R6）：FE 日線唔再讀 market_price_observation —— K 線全線踢走，日線只准
# 由真成交嚟（scripts/test-fe-history-sale-only.mjs）。「冇讀路」比「有讀路 + ready
# filter」更嚴，但兩種形狀都要 fail-closed：讀路返嚟就一定要帶 ready filter；冇讀路
# 就要連個 JOIN 都真係一條都冇（唔准有人改個 alias 就靜靜溜返入嚟）。
fe = (ROOT / "apps" / "web" / "src" / "lib" / "live-db-snapshot.ts").read_text(encoding="utf-8")
needle_from = "FROM market_metric_history_acceptance history"
if needle_from in fe:
    block = fe[fe.index(needle_from):]
    block = block[:block.index("ORDER BY")]
    check("FE history query filters ready", "price.metric_status='ready'" in block, True)
else:
    fe_code = "\n".join(line for line in fe.splitlines() if not line.lstrip().startswith(("//", "*", "/*")))
    check("FE 冇 chart 觀測讀路（比 ready filter 更嚴）",
          "market_price_observation" in fe_code, False)

# 2026-08-23：legacy composition（operator_control 嗰個 chart 價 composer）成個
# 刪咗 —— 零 call site，而且佢砌價唔經 F-MINT。讀者唔存在 = 佢冇可能讀到非 ready
# 行，所以契約由「要有 ready-only filter」升做「唔准再有呢個讀者」。呢個係收緊
# 唔係放鬆：一旦有人加返 `FROM market_price_observation`，佢要重新自己證明
# ready-only，而呢行會即刻紅住等佢。
oc = (ROOT / "pipelines" / "operator_control.py").read_text(encoding="utf-8")
check("legacy composition reader is gone (no market_price_observation query)",
      "FROM market_price_observation" in oc, False)
check("legacy composition dropped the blocklist form",
      "NOT IN ('banned_g10_kline','quarantined_lane')" in oc, False)

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    raise SystemExit(1)
print("all snk_price_lane_audit contracts hold")
