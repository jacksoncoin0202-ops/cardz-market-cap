#!/usr/bin/env python3
"""Census harvest and DB-config failures stay loud and classifiable. No network/DB.

Reproduces three repeat failures from the 2026-09-13..26 V2 journal:
  * 09-21: /universal-pop-report answered 403 and the census died as a bare
    "harvest exit=1" (SOURCE_FAILED, no cause on record);
  * 09-22/23: 1 and 4 set pages stayed 403, the harvester still exited 0 and
    a partial census was promoted as refreshed;
  * 09-13..20: six tasks died on a bare "container configuration is
    unavailable" that classified as SOURCE_FAILED instead of MySQL.
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))

try:  # the harvester imports curl_cffi at module level; no request is ever made here
    import curl_cffi  # noqa: F401
except ImportError:  # pragma: no cover - only where curl_cffi is not installed
    _stub = types.ModuleType("curl_cffi")
    _stub.requests = types.ModuleType("curl_cffi.requests")
    sys.modules["curl_cffi"] = _stub
    sys.modules["curl_cffi.requests"] = _stub.requests

import cardz_db_config as db_config  # noqa: E402
import gemrate_brute_harvest as harvest  # noqa: E402
import identity_census_stage as census  # noqa: E402
from daily_chain_v2_contract import classify_error  # noqa: E402

INDEX = harvest.INDEX_URL


def _index_html(sets: list[dict]) -> str:
    pad = "x" * 200_001
    return f"<html><script>var pad='{pad}'; setsData = {json.dumps(sets)};</script></html>"


def _set_html(cards: list[dict]) -> str:
    return f"<script>const rowData = JSON.parse('{json.dumps(cards)}');</script>"


def _sets(count: int) -> list[dict]:
    return [
        {
            "set_id": f"{index:040x}",
            "set_name": f"Fixture Set {index}",
            "set_link": f"/set/{index}",
            "category": "TCG",
        }
        for index in range(1, count + 1)
    ]


def _cards(set_index: int) -> list[dict]:
    return [
        {"psa_id": f"{set_index:02x}{n:038x}", "psa_10": 1500 if n == 1 else 20}
        for n in (1, 2)
    ]


class FakeSite:
    """URL -> scripted responses; the last response repeats."""

    def __init__(self, script: dict[str, list]):
        self.script = {url: list(responses) for url, responses in script.items()}
        self.calls: list[str] = []

    def get(self, url: str, **_kw):
        self.calls.append(url)
        responses = self.script[url]
        response = responses.pop(0) if len(responses) > 1 else responses[0]
        status, text = response
        return SimpleNamespace(status_code=status, text=text)


class HarvestFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.sleeps: list[float] = []
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(harvest, "DATA_DIR", self.data))
        stack.enter_context(patch.object(harvest.time, "sleep", self.sleeps.append))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def site(self, sets: list[dict], index=None, pages=None) -> FakeSite:
        script = {INDEX: index or [(200, _index_html(sets))]}
        for number, entry in enumerate(sets, 1):
            url = f"https://www.gemrate.com{entry['set_link']}"
            script[url] = (pages or {}).get(number) or [(200, _set_html(_cards(number)))]
        fake = FakeSite(script)
        patcher = patch.object(harvest, "_get", fake.get)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def read(self, name: str) -> list[dict]:
        text = (self.data / name).read_bytes().decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]


class IndexPageTests(HarvestFixture):
    def test_index_403_then_200_recovers_and_clears_old_failed_sets(self):
        (self.data / "failed_sets.jsonl").write_bytes(b'{"set_id": "old", "error": "x"}\n')
        self.site(_sets(2), index=[(403, "cf"), (200, _index_html(_sets(2)))])
        harvest.harvest_all_sets()
        self.assertEqual(self.sleeps[0], harvest.INDEX_RETRY_WAITS[0])
        self.assertEqual(len(self.read("all_cards.jsonl")), 4)
        self.assertEqual(len(self.read("psa10_1000_plus.jsonl")), 2)
        self.assertEqual(self.read("failed_sets.jsonl"), [])

    def test_index_403_every_try_is_auth_or_blocked(self):
        fake = self.site(_sets(1), index=[(403, "cf")])
        with self.assertRaises(RuntimeError) as caught:
            harvest.extract_sets_data()
        tries = len(harvest.INDEX_RETRY_WAITS) + 1
        self.assertEqual(fake.calls.count(INDEX), tries)
        message = str(caught.exception)
        self.assertEqual(message, f"universal-pop-report HTTP 403 forbidden after {tries} tries")
        stage_error = f"INCOMPLETE_CENSUS: harvest exit=1: RuntimeError: {message}"
        self.assertEqual(classify_error(stage_error).error_code, "AUTH_OR_BLOCKED")

    def test_index_503_every_try_is_transient(self):
        self.site(_sets(1), index=[(503, "down")])
        with self.assertRaises(RuntimeError) as caught:
            harvest.extract_sets_data()
        stage_error = f"INCOMPLETE_CENSUS: harvest exit=1: RuntimeError: {caught.exception}"
        self.assertEqual(classify_error(stage_error).error_code, "TRANSIENT_SOURCE")


class SetPageTests(HarvestFixture):
    def test_set_403_recovered_by_retry_pass_keeps_set_order(self):
        self.site(_sets(3), pages={2: [(403, "cf"), (403, "cf"), (200, _set_html(_cards(2)))]})
        harvest.harvest_all_sets()
        self.assertIn(harvest.FAILED_SET_COOLDOWN, self.sleeps)
        order = [row["_set_name"] for row in self.read("all_cards.jsonl")]
        self.assertEqual(order, [f"Fixture Set {n}" for n in (1, 1, 2, 2, 3, 3)])
        self.assertEqual(self.read("failed_sets.jsonl"), [])

    def test_set_still_403_does_not_promote_partial_census(self):
        # 2026-09-23 shape: sets stay 403 through every retry.
        old_all = b'{"psa_id": "previous-complete-census"}\n'
        old_psa = b'{"psa_id": "previous-qualified", "psa_10": 1234}\n'
        (self.data / "all_cards.jsonl").write_bytes(old_all)
        (self.data / "psa10_1000_plus.jsonl").write_bytes(old_psa)
        sets = _sets(3)
        self.site(sets, pages={2: [(403, "cf")]})
        with self.assertRaises(harvest.CensusHarvestIncomplete) as caught:
            harvest.harvest_all_sets()
        message = str(caught.exception)
        self.assertTrue(message.startswith("CENSUS_HARVEST_FAILED: census incomplete: 1/3"), message)
        self.assertEqual((self.data / "all_cards.jsonl").read_bytes(), old_all)
        self.assertEqual((self.data / "psa10_1000_plus.jsonl").read_bytes(), old_psa)
        failed = self.read("failed_sets.jsonl")
        self.assertEqual([row["set_id"] for row in failed], [sets[1]["set_id"]])
        self.assertEqual(failed[0]["error"], "set page HTTP 403 forbidden")
        stage_error = f"INCOMPLETE_CENSUS: harvest exit=3: {message}"
        self.assertEqual(classify_error(stage_error).error_code, "AUTH_OR_BLOCKED")

    def test_main_exits_nonzero_with_cause_as_last_stderr_line(self):
        self.site(_sets(2), pages={1: [(403, "cf")]})
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["gemrate_brute_harvest.py", "--all-sets"]), \
                contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                harvest.main()
        self.assertEqual(caught.exception.code, harvest.INCOMPLETE_EXIT_CODE)
        self.assertNotEqual(caught.exception.code, 0)
        last = stderr.getvalue().strip().splitlines()[-1]
        self.assertTrue(last.startswith("CENSUS_HARVEST_FAILED:"), last)

    def test_retry_pass_stops_when_the_block_has_not_cleared(self):
        count = 6
        fake = self.site(_sets(count), pages={n: [(403, "cf")] for n in range(1, count + 1)})
        with self.assertRaises(harvest.CensusHarvestIncomplete):
            harvest.harvest_all_sets()
        set_calls = [url for url in fake.calls if url != INDEX]
        limit = harvest.RETRY_PASS_CONSECUTIVE_FAILURE_LIMIT
        self.assertEqual(len(set_calls), count * 2 + limit)
        self.assertEqual(len(self.read("failed_sets.jsonl")), count)


class CensusStageCauseTests(unittest.TestCase):
    def run_stage(self, exit_code: int, tail: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def runner(command, *, timeout, cwd):
                return {"exitCode": exit_code, "outputTail": tail}

            return census.run_identity_census(
                root=root,
                business_date="2026-09-21",
                runner=runner,
                receipt_path=root / "receipt.json",
            )

    def test_index_403_traceback_reaches_the_journal(self):
        # 2026-09-21 receipt tail, with the harvester's current message.
        fake = FakeSite({INDEX: [(403, "cf")]})
        with patch.object(harvest, "_get", fake.get), \
                patch.object(harvest.time, "sleep", lambda _s: None), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError) as caught:
                harvest.extract_sets_data()
        tail = "\n".join([
            "[11:09:58] Fetching /universal-pop-report ...",
            "Traceback (most recent call last):",
            '  File "pipelines/gemrate_brute_harvest.py", line 135, in harvest_all_sets',
            "    sets_data = extract_sets_data()",
            f"RuntimeError: {caught.exception}",
        ])
        receipt = self.run_stage(1, tail)
        self.assertFalse(receipt["refreshed"])
        self.assertEqual(
            receipt["error"],
            f"harvest exit=1: RuntimeError: {caught.exception}",
        )
        decision = classify_error(f"INCOMPLETE_CENSUS: {receipt['error']}")
        self.assertEqual(decision.error_code, "AUTH_OR_BLOCKED")

    def test_incomplete_census_line_reaches_the_journal(self):
        line = "CENSUS_HARVEST_FAILED: census incomplete: 4/140 TCG sets failed after retry pass (x)"
        receipt = self.run_stage(3, "[11:11:46] Failed sets: 4\n" + line)
        self.assertEqual(receipt["error"], f"harvest exit=3: {line}")

    def test_unexplained_exit_keeps_the_old_prefix(self):
        receipt = self.run_stage(1, "[11:00:00] something odd")
        self.assertEqual(receipt["error"], "harvest exit=1")


COMPOSE = """services:
  db:
    image: mysql:8
    environment:
      MYSQL_DATABASE: fixture_db
      MYSQL_USER: fixture_user
      MYSQL_PASSWORD: fixture-compose-value
    ports:
      - "127.0.0.1:3308:3306"
"""
SECRET = "fixture-container-secret"
ENV_JSON = json.dumps(["MYSQL_DATABASE=fixture_db", "MYSQL_USER=fixture_user", f"MYSQL_PASSWORD={SECRET}"])


class DbConfigInspectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.compose = Path(tmp.name) / "compose.backend.yaml"
        self.compose.write_bytes(COMPOSE.encode("utf-8"))
        self.sleeps: list[float] = []
        patcher = patch.object(db_config.time, "sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_with(self, outcomes: list):
        calls: list[list[str]] = []

        def fake_run(command, **_kw):
            calls.append(list(command))
            outcome = outcomes[min(len(calls), len(outcomes)) - 1]
            if isinstance(outcome, BaseException):
                raise outcome
            code, stdout, stderr = outcome
            return subprocess.CompletedProcess(command, code, stdout, stderr)

        with patch.object(db_config.subprocess, "run", fake_run):
            try:
                return db_config.compose_db_env(self.compose), calls
            except RuntimeError as error:
                return error, calls

    def test_one_failed_inspect_is_retried(self):
        result, calls = self.run_with([(1, "", "error during connect: pipe busy"), (0, ENV_JSON, "")])
        self.assertIsInstance(result, dict)
        self.assertEqual(result["CARDZ_DB_PORT"], "3308")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.sleeps, [db_config.INSPECT_RETRY_WAITS[0]])

    def test_final_failure_classifies_as_mysql_and_names_the_cause(self):
        result, calls = self.run_with([(1, "", "Error: No such object: cardz-market-cap-db-1")])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(len(calls), len(db_config.INSPECT_RETRY_WAITS) + 1)
        self.assertIn("No such object", str(result))
        self.assertEqual(classify_error(str(result)).error_code, "MYSQL_UNAVAILABLE")

    def test_hung_docker_is_a_runtime_error_not_a_crash(self):
        # recover_mysql catches (OSError, RuntimeError); a raw TimeoutExpired escaped it.
        hang = subprocess.TimeoutExpired(["docker.exe"], db_config.INSPECT_TIMEOUT_SECONDS)
        result, calls = self.run_with([hang])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(len(calls), len(db_config.INSPECT_RETRY_WAITS) + 1)
        self.assertEqual(classify_error(str(result)).error_code, "MYSQL_UNAVAILABLE")

    def test_values_never_reach_the_message(self):
        result, _calls = self.run_with([(1, ENV_JSON, f"MYSQL_PASSWORD={SECRET} rejected")])
        self.assertIsInstance(result, RuntimeError)
        self.assertNotIn(SECRET, str(result))


if __name__ == "__main__":
    unittest.main()
