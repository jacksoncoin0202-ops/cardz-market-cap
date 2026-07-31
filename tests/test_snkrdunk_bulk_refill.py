from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from snkrdunk_bulk import build_price_refill_worklist, run_price_refill  # noqa: E402


def candidate(external_id: str, *, pop: int = 1000, collector: str = "OP01-120") -> dict[str, object]:
    return {
        "canonicalSourceCode": "snkrdunk",
        "canonicalExternalId": external_id,
        "pokedexId": f"cmc_{external_id}",
        "market": "one-piece",
        "setName": "Romance Dawn",
        "collectorNumber": collector,
        "language": "ja",
        "edition": "Manga Alternate",
        "parallel": "Manga",
        "finish": "Foil",
        "identityStatus": "exact_confirmed",
        "populationPsa10": pop,
    }


def master(item_id: int, *, collector: str = "OP01-120") -> dict[str, object]:
    return {
        "id": item_id,
        "identity": {
            "tcg": "one-piece",
            "setName": "Romance Dawn",
            "collectorNumber": collector,
            "language": "ja",
            "edition": "Manga Alternate",
            "parallel": "Manga",
            "finish": "Foil",
        },
    }


class FakeSnk:
    def __init__(self, graph: dict[int, list[int]], masters: dict[int, dict[str, object]], *, fail_once: int | None = None) -> None:
        self.graph = graph
        self.masters = masters
        self.fail_once = fail_once
        self.master_calls: list[int] = []

    def get_same_category(self, item_id: int, page: int = 1, per_page: int = 13) -> list[int]:
        return self.graph.get(item_id, []) if page == 1 else []

    def get_master(self, item_id: int) -> dict[str, object]:
        self.master_calls.append(item_id)
        if self.fail_once == item_id:
            self.fail_once = None
            raise RuntimeError("upstream temporary failure")
        return self.masters[item_id]


class SnkrdunkBulkRefillTests(unittest.TestCase):
    def test_worklist_uses_formal_and_pre_entry_candidates_but_never_first_matches(self) -> None:
        high = candidate("101")
        pre_entry = candidate("102", pop=999, collector="OP01-122")
        low = candidate("104", pop=970)
        mismatched = {**candidate("103", collector="OP01-121"), "snkItemId": 2}
        worklist = build_price_refill_worklist(
            [high, pre_entry, low, mismatched],
            {1: master(1), 2: master(2, collector="OP01-999"), 3: master(3, collector="OP01-122")},
        )
        self.assertEqual(worklist["counts"]["eligible"], 3)
        self.assertEqual(worklist["counts"]["formal"], 2)
        self.assertEqual(worklist["counts"]["preEntryRadar"], 1)
        self.assertEqual(worklist["counts"]["resolved"], 2)
        self.assertEqual(worklist["counts"]["review"], 1)
        self.assertEqual(worklist["counts"]["belowPopulation"], 1)
        resolved = next(row for row in worklist["cards"] if row["canonicalExternalId"] == "101")
        self.assertEqual(resolved["snkItemId"], 1)
        review = next(row for row in worklist["cards"] if row["status"] == "review")
        self.assertEqual(review["reason"], "snk_identity_mismatch")
        self.assertIn("masterIdentity", review["identityEvidence"])

    def test_non_exact_identity_never_enters_snk_collection(self) -> None:
        unresolved = {**candidate("101"), "identityStatus": "confirmed"}
        worklist = build_price_refill_worklist([unresolved], {1: master(1)})
        self.assertEqual(worklist["counts"]["resolved"], 0)
        self.assertEqual(worklist["cards"][0]["reason"], "candidate_identity_not_confirmed")

    def test_language_mismatch_cannot_bind_an_exact_printing(self) -> None:
        row = candidate("101")
        row["language"] = "en"
        row["snkItemId"] = 1
        snk_master = master(1)
        snk_master["identity"]["language"] = "ja"
        worklist = build_price_refill_worklist([row], {1: snk_master})
        self.assertEqual(worklist["counts"]["resolved"], 0)
        self.assertEqual(worklist["counts"]["review"], 1)
        self.assertEqual(worklist["cards"][0]["reason"], "snk_identity_mismatch")

    def test_ambiguous_exact_match_goes_to_review(self) -> None:
        worklist = build_price_refill_worklist([candidate("101"), candidate("102")], {1: master(1)})
        self.assertEqual(worklist["counts"]["resolved"], 0)
        self.assertEqual(worklist["counts"]["review"], 2)
        self.assertTrue(all(row["reason"] == "multiple_exact_candidates" for row in worklist["cards"]))

    def test_partial_run_never_promotes_and_resume_is_idempotent(self) -> None:
        api = FakeSnk({10: [11]}, {10: master(10), 11: master(11)}, fail_once=11)
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "refill.json"
            with self.assertRaisesRegex(RuntimeError, "partial"):
                run_price_refill(api, [candidate("101")], [10], out, max_ids=10)
            self.assertFalse(out.exists())
            self.assertTrue(out.with_suffix(".json.partial").exists())

            completed = run_price_refill(api, [candidate("101")], [10], out, max_ids=10)
            self.assertFalse(completed["replayed"])
            self.assertTrue(out.is_file())
            calls_after_complete = list(api.master_calls)

            replay = run_price_refill(api, [candidate("101")], [10], out, max_ids=10)
            self.assertTrue(replay["replayed"])
            self.assertEqual(api.master_calls, calls_after_complete)


if __name__ == "__main__":
    unittest.main()
