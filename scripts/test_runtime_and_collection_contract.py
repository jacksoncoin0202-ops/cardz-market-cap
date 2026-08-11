#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime ownership and adapter coverage have one fail-closed authority."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from collection_contract import ADAPTER_LANE, CHECKPOINT_ADAPTERS  # noqa: E402
from runtime_paths import _assert_expected_target  # noqa: E402


runtime = Path("C:/probe/fe/data/runtime")
expected = Path("C:/probe/cardz-market-cap/data/runtime")
try:
    _assert_expected_target(
        runtime_root=runtime,
        expected_target=expected,
        resolved_target=runtime,
    )
except RuntimeError:
    print("NEGATIVE_OK redirected runtime target was rejected")
else:
    raise AssertionError("negative runtime-target fixture did not fire")

_assert_expected_target(
    runtime_root=runtime,
    expected_target=expected,
    resolved_target=expected,
)
assert CHECKPOINT_ADAPTERS == tuple(ADAPTER_LANE)
assert ADAPTER_LANE["snk_en_image"] == "http"
assert set(ADAPTER_LANE.values()) <= {"http", "browser", "manual"}

operator_source = (ROOT / "pipelines" / "operator_control.py").read_text(encoding="utf-8")
collector_source = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
assert operator_source.count("assert_runtime_root(ROOT)") == 1
assert collector_source.count("assert_runtime_root(ROOT)") == 1
assert "CHECKPOINT_ADAPTERS = (" not in operator_source
assert "CHECKPOINT_ADAPTERS = tuple(" not in collector_source
print("POSITIVE_OK runtime startup checks and one six-adapter authority hold")

