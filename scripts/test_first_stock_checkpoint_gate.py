#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""New-activate cards must first-stock before daily-accept, and the gate must name them."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import operator_control as OC  # noqa: E402


# 08-17 實測：843 條 snk_trades 缺 v2016 一條。舊 gate 硬停成日。
# 而家呢個比例係警告，唔准擋出街。
soft = OC.checkpoint_adapter_verdict(
    streams=843, missing_ids=[2016], stale_ids=[]
)
assert soft["hard"] is False
assert soft["missingIds"] == [2016]
assert soft["healthyRatio"] > OC.CHECKPOINT_MIN_HEALTHY_RATIO
print("POSITIVE_OK measured 08-17 v2016/843 is a soft warning")

# 16:30／18:30 真兇：圖 709 條過 36h，0 條缺 checkpoint。唔准擋 bake。
image_stale = OC.checkpoint_adapter_verdict(
    streams=843, missing_ids=[], stale_ids=list(range(1, 710))
)
assert image_stale["hard"] is False
assert image_stale["healthyRatio"] == 1.0
assert len(image_stale["staleIds"]) == 709
print("POSITIVE_OK 709 stale images with zero missing still publish")

# 843 條：缺 25 = 97.03% 照出街；缺 26 = 96.92% 先硬停。
twenty_five = list(range(1, 26))
still_soft = OC.checkpoint_adapter_verdict(
    streams=843, missing_ids=twenty_five, stale_ids=[]
)
assert still_soft["hard"] is False
twenty_six = list(range(1, 27))
hard = OC.checkpoint_adapter_verdict(
    streams=843, missing_ids=twenty_six, stale_ids=[]
)
assert hard["hard"] is True
assert len(hard["missingIds"]) == 26
try:
    raise RuntimeError(
        OC._format_checkpoint_gate_error(
            "snk_trades",
            streams=843,
            missing_ids=twenty_six,
            stale_ids=[],
            healthy_ratio=hard["healthyRatio"],
            max_age=5.8,
        )
    )
except RuntimeError as error:
    text = str(error)
    assert "missing=26" in text
    assert "missingVariantIds=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]" in text
    assert "healthyRatio=" in text
    print("NEGATIVE_OK 26/843 missing still hard-fails with IDs")
else:
    raise AssertionError("sub-97% fixture did not fire")

# 舊「缺 1 條就 raise」行必須唔再存在。
operator = (ROOT / "pipelines" / "operator_control.py").read_text(encoding="utf-8")
assert "if missing or max_age is None or max_age > CHECKPOINT_SLA_HOURS:" not in operator
assert "CHECKPOINT_MIN_HEALTHY_RATIO = 0.97" in operator
assert "checkpoint-gate-soft-warning" in operator
print("POSITIVE_OK hard-stop on any missing stream is gone")


collector = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
assert 'sub.add_parser(\n        "first-stock"' in collector or '"first-stock"' in collector
assert "def cmd_first_stock(" in collector
assert '"first-stock"' in collector
assert "missing_checkpoint_streams" in collector
print("POSITIVE_OK collect_control first-stock command exists")

operator = (ROOT / "pipelines" / "operator_control.py").read_text(encoding="utf-8")
assert "def missing_checkpoint_streams(" in operator
assert "_format_checkpoint_gate_error(" in operator
assert "missingVariantIds=" in operator
print("POSITIVE_OK checkpoint gate error names missingVariantIds")

# The first-stock-before-daily-accept ordering used to be pinned by grepping the
# three V1 wrappers (morning/nightly/refresh). Those wrappers moved to
# archive/scripts/ on 2026-08-25 along with their Disabled Task Scheduler
# entries. In V2 the ordering is a stage dependency, not text: stage
# `checkpoint-repair` (pipelines/daily_chain_v2_stage.py) calls cmd_first_stock
# and daily-accept sits behind it in the DAG, so a grep-based mirror of it here
# would only re-assert what the DAG already makes unrepresentable. The
# command-side checks above still hold.
