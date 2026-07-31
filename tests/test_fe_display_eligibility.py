from __future__ import annotations

import unittest

from pipelines.fe_display_eligibility import launch_gate_from_qc


def qc_card(*, sales: int, source: str = "ebay", decision: str = "passed") -> dict:
    return {
        "decision": decision,
        "facts": {
            "sales30d": {"purePsa10Count": sales},
            "price": {
                "source": source,
                "valueUsd": "100.00",
                "asOf": "2026-07-31T00:00:00Z",
            },
        },
    }


class FrontendDisplayEligibilityTests(unittest.TestCase):
    def test_liquidity_boundary_is_ten_pure_psa10_sales(self) -> None:
        self.assertFalse(launch_gate_from_qc(qc_card(sales=9))["liquidityReady"])
        self.assertTrue(launch_gate_from_qc(qc_card(sales=10))["liquidityReady"])

    def test_non_authoritative_price_never_becomes_launch_ready(self) -> None:
        gate = launch_gate_from_qc(qc_card(sales=10, source="g10_kline"))
        self.assertFalse(gate["priceReady"])

    def test_unpassed_qc_card_fails_closed(self) -> None:
        gate = launch_gate_from_qc(qc_card(sales=10, decision="blocked"))
        self.assertFalse(gate["liquidityReady"])
        self.assertFalse(gate["priceReady"])


if __name__ == "__main__":
    unittest.main()
