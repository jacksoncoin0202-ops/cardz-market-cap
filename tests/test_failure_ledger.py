from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from pipelines.failure_ledger import (
    current_failures,
    export_retry_worklist,
    main,
    read_events,
    record_failure,
    record_resolution,
    stable_failure_id,
    summary,
)


class FailureLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def stamp(self, second: int) -> datetime:
        return datetime(2026, 7, 31, 1, 2, second, tzinfo=timezone.utc)

    def test_stable_identity_attempt_history_and_resolution(self) -> None:
        kwargs = {
            "source": "pricecharting",
            "stage": "collect",
            "script": "pipelines/pc_full_shard_runner.py",
            "item_key": 1445,
            "reason_code": "resolve_mismatch",
            "ledger_root": self.root,
        }
        record_failure(**kwargs, occurred_at=self.stamp(1))
        record_failure(**kwargs, occurred_at=self.stamp(2))

        states = current_failures(ledger_root=self.root)
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["attempts"], 2)
        self.assertEqual(states[0]["firstSeenAt"], "2026-07-31T01:02:01.000000Z")
        self.assertEqual(states[0]["lastSeenAt"], "2026-07-31T01:02:02.000000Z")

        record_resolution(
            source=kwargs["source"],
            stage=kwargs["stage"],
            script=kwargs["script"],
            item_key=kwargs["item_key"],
            ledger_root=self.root,
            occurred_at=self.stamp(3),
        )
        self.assertEqual(current_failures(ledger_root=self.root), [])
        self.assertEqual(len(read_events(ledger_root=self.root)), 3)

    def test_success_without_prior_failure_does_not_create_an_orphan_event(self) -> None:
        path = record_resolution(
            source="gemrate",
            stage="harvest_set",
            script="pipelines/gemrate_brute_harvest.py",
            item_key="set-ok",
            ledger_root=self.root,
            occurred_at=self.stamp(1),
        )
        self.assertIsNone(path)
        self.assertEqual(read_events(ledger_root=self.root), [])

    def test_reimport_of_same_failure_and_resolution_is_idempotent(self) -> None:
        identity = {
            "source": "pricecharting",
            "stage": "collect",
            "script": "pipelines/pc_full_shard_runner.py",
            "item_key": 1445,
            "ledger_root": self.root,
        }
        failure = {
            **identity,
            "reason_code": "resolve_mismatch",
            "run_id": "historical-import",
            "occurred_at": self.stamp(1),
        }
        self.assertIsNotNone(record_failure(**failure))
        self.assertIsNone(record_failure(**failure))
        self.assertIsNotNone(
            record_resolution(
                **identity,
                run_id="historical-import",
                occurred_at=self.stamp(2),
            )
        )
        self.assertIsNone(
            record_resolution(
                **identity,
                run_id="historical-import",
                occurred_at=self.stamp(2),
            )
        )
        self.assertEqual(len(read_events(ledger_root=self.root)), 2)

    def test_reopen_after_resolution_preserves_history(self) -> None:
        identity = {
            "source": "snkrdunk",
            "stage": "collect",
            "script": "pipelines/snk_market_data.py",
            "item_key": "349473",
        }
        record_failure(
            **identity,
            reason_code="http_error",
            ledger_root=self.root,
            occurred_at=self.stamp(1),
        )
        record_resolution(
            **identity,
            ledger_root=self.root,
            occurred_at=self.stamp(2),
        )
        record_failure(
            **identity,
            reason_code="timeout",
            ledger_root=self.root,
            occurred_at=self.stamp(3),
        )
        state = current_failures(ledger_root=self.root)[0]
        self.assertEqual(state["attempt"], 2)
        self.assertEqual(state["attempts"], 2)
        self.assertEqual(state["reasonCode"], "timeout")

    def test_secret_fields_and_url_queries_are_redacted(self) -> None:
        path = record_failure(
            source="pricecharting",
            stage="fetch",
            script="pipelines/pricecharting_cf_session.py",
            item_key="variant:98",
            reason_code="cf_challenge",
            message=(
                "GET https://example.test/card?token=abc "
                "Authorization:Bearer-secret cookie=session-secret"
            ),
            url="https://example.test/card?__cf_chl_rt_tk=secret#fragment",
            context={
                "api_token": "do-not-write",
                "cookies": ["secret"],
                "safe": "https://example.test/x?signed=secret",
            },
            ledger_root=self.root,
            occurred_at=self.stamp(1),
        )
        event = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(event["url"], "https://example.test/card")
        self.assertEqual(event["context"]["api_token"], "[REDACTED]")
        self.assertEqual(event["context"]["cookies"], "[REDACTED]")
        self.assertEqual(event["context"]["safe"], "https://example.test/x")
        serialized = json.dumps(event)
        self.assertNotIn("do-not-write", serialized)
        self.assertNotIn("session-secret", serialized)
        self.assertNotIn("__cf_chl_rt_tk", serialized)

        url_key_path = record_failure(
            source="pricecharting",
            stage="fetch",
            script="pipelines/pricecharting_cf_session.py",
            item_key="https://example.test/card?token=abc#fragment",
            reason_code="cf_challenge",
            ledger_root=self.root,
            occurred_at=self.stamp(2),
        )
        url_key_event = json.loads(url_key_path.read_text(encoding="utf-8"))
        self.assertEqual(url_key_event["itemKey"], "https://example.test/card")

    def test_retry_export_is_sorted_and_excludes_nonretryable_and_resolved(self) -> None:
        record_failure(
            source="z-source",
            stage="collect",
            script="pipelines/z.py",
            item_key="2",
            reason_code="timeout",
            ledger_root=self.root,
            occurred_at=self.stamp(2),
        )
        record_failure(
            source="a-source",
            stage="collect",
            script="pipelines/a.py",
            item_key="1",
            reason_code="identity_conflict",
            retryable=False,
            ledger_root=self.root,
            occurred_at=self.stamp(1),
        )
        record_failure(
            source="z-source",
            stage="collect",
            script="pipelines/z.py",
            item_key="3",
            reason_code="timeout",
            ledger_root=self.root,
            occurred_at=self.stamp(3),
        )
        record_resolution(
            source="z-source",
            stage="collect",
            script="pipelines/z.py",
            item_key="3",
            ledger_root=self.root,
            occurred_at=self.stamp(4),
        )

        first = self.root / "retry-a.json"
        second = self.root / "retry-b.json"
        export_retry_worklist(first, ledger_root=self.root)
        export_retry_worklist(second, ledger_root=self.root)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        document = json.loads(first.read_text(encoding="utf-8"))
        self.assertEqual(document["count"], 1)
        self.assertEqual(document["items"][0]["itemKey"], "2")
        self.assertEqual(summary(ledger_root=self.root)["open"], 2)
        self.assertEqual(summary(ledger_root=self.root)["retryable"], 1)
        self.assertEqual(summary(ledger_root=self.root)["agentReview"], 1)

    def test_agent_can_pull_one_deterministic_review_item(self) -> None:
        for item_key in ("2", "1"):
            record_failure(
                source="pricecharting",
                stage="collect",
                script="pipelines/pc_full_shard_runner.py",
                item_key=item_key,
                reason_code="resolve_mismatch",
                next_action="agent_review",
                context={
                    "name": "Test Card",
                    "set": "Test Set",
                    "collectorNumber": item_key,
                },
                ledger_root=self.root,
                occurred_at=self.stamp(int(item_key)),
            )
        rows = current_failures(
            ledger_root=self.root,
            source="pricecharting",
            retryable_only=True,
            next_action="agent_review",
            limit=1,
        )
        self.assertEqual([row["itemKey"] for row in rows], ["1"])
        self.assertEqual(
            rows[0]["context"]["searchTerms"],
            ["Test Card", "Test Set", "1", "PSA 10", "pricecharting"],
        )

        output = self.root / "agent-next.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "--ledger-root",
                    str(self.root),
                    "export-retry",
                    "--source",
                    "pricecharting",
                    "--next-action",
                    "agent_review",
                    "--limit",
                    "1",
                    "--out",
                    str(output),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["exported"], 1)

    def test_list_and_export_can_filter_exact_stage(self) -> None:
        for stage, item_key in (("psa10_price", "price-card"), ("sales", "sales-card")):
            record_failure(
                source="canonical_db_qc",
                stage=stage,
                script="pipelines/qc_failure_sync.py",
                item_key=item_key,
                reason_code=f"qc_{stage}_blocked",
                next_action="agent_review",
                ledger_root=self.root,
                occurred_at=self.stamp(1 if stage == "psa10_price" else 2),
            )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                main(["--ledger-root", str(self.root), "list", "--stage", "sales"]),
                0,
            )
        listed = json.loads(stdout.getvalue())
        self.assertEqual([row["itemKey"] for row in listed], ["sales-card"])

        output = self.root / "stage-only.json"
        export_retry_worklist(output, ledger_root=self.root, stage="psa10_price")
        exported = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exported["stage"], "psa10_price")
        self.assertEqual([row["itemKey"] for row in exported["items"]], ["price-card"])

    def test_stable_failure_id_ignores_path_spelling_noise(self) -> None:
        one = stable_failure_id(
            source="SNKRDUNK",
            stage="COLLECT",
            script="pipelines/snk_market_data.py",
            item_key=123,
        )
        two = stable_failure_id(
            source="snkrdunk",
            stage="collect",
            script="pipelines/snk_market_data.py",
            item_key="123",
        )
        self.assertEqual(one, two)


if __name__ == "__main__":
    unittest.main()
