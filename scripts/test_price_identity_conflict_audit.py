#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""price_identity_conflict_audit 嘅裁決器契約。

1. 矛盾偵測器對種落去嘅「兩家族差 3 倍」真係會紅（規矩 9）；
2. 家族一致 / 舊過 180 日嘅行唔會誤中；
3. SQL 只可以隔離 ready 行、只寫 'quarantined'（身份隔離字，release lane 識放）、
   永遠唔 DELETE。
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import price_identity_conflict_audit as A  # noqa: E402

FAILED: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got == want:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}: got {got!r}, want {want!r}")


TODAY = date(2026, 8, 12)


def row(vid, src, day, usd):
    return {"variant_id": vid, "source_code": src, "observed_date": day, "price_usd": usd}


# 1. 種毒：同一 variant，snk 中位 $5,000 vs PC 中位 $90 → 必須 fire。
poison = [
    row(111, "snkrdunk", date(2026, 7, 1), 5000.0),
    row(111, "snk_psa10", date(2026, 7, 10), 5200.0),
    row(111, "pricecharting", date(2026, 7, 1), 90.0),
    row(111, "pricecharting", date(2026, 8, 1), 95.0),
]
conflicts = A.find_conflicts(poison, TODAY)
check("cross-family 55x conflict fires", 111 in conflicts, True)
check("conflict ratio computed", conflicts[111]["ratio"] > 50, True)

# 2. 家族一致（JP/EN 市場正常價差 <3x）→ 唔准誤中。
honest = [
    row(222, "snkrdunk", date(2026, 7, 1), 700.0),
    row(222, "pricecharting", date(2026, 7, 2), 650.0),
]
check("consistent families pass", A.find_conflicts(honest, TODAY), {})

# 3. 舊過 180 日嘅行唔入中位數（歷史價唔可以撞今日身份裁決）。
stale = [
    row(333, "snkrdunk", date(2024, 1, 1), 5000.0),
    row(333, "pricecharting", date(2026, 7, 1), 90.0),
]
check("stale rows outside window ignored", A.find_conflicts(stale, TODAY), {})

# 4. 單一家族永遠唔 fire（冇對照冇矛盾）。
solo = [row(444, "pricecharting", date(2026, 7, 1), 90.0), row(444, "pricecharting", date(2026, 8, 1), 9000.0)]
check("single family never fires", A.find_conflicts(solo, TODAY), {})

# 5. SQL 契約。
check("scope query only reads ready rows", "p.metric_status = 'ready'" in A.READY_ROWS_SQL, True)
check("apply stamps releasable identity status", f"SET metric_status = '{A.QUARANTINE_STATUS}'" in A.APPLY_QUARANTINE_SQL, True)
check("apply only touches ready rows", "AND metric_status = 'ready'" in A.APPLY_QUARANTINE_SQL, True)
check("identity status is the releasable one", A.QUARANTINE_STATUS, "quarantined")
source = Path(A.__file__).read_text(encoding="utf-8")
check("audit never deletes", "DELETE" not in source.upper().replace("DELETES", ""), True)

# 6. snk 家族身份掛喺 snkrdunk 名下（source_code 家族形狀，形狀 22 教訓）。
check("snk_psa10 identity maps to snkrdunk", A.IDENTITY_SOURCE_OF_FAMILY[A.FAMILY_OF_SOURCE["snk_psa10"]], "snkrdunk")

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    raise SystemExit(1)
print("all price_identity_conflict_audit contracts hold")
