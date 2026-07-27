"""Guards for the documentation verification-stamp system.

These tests never touch the database.  They cover the two things that must not
regress: the stamp grammar (a stamp that silently fails to parse is a claim
nobody is checking) and the read-only guard (this script runs SQL that was
authored inside a markdown comment).
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cardz_verify_claims", ROOT / "scripts/verify_claims.py")
assert SPEC and SPEC.loader
vc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = vc
SPEC.loader.exec_module(vc)


def parse(markdown: str):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        doc = root / "doc.md"
        doc.write_text(markdown, encoding="utf-8")
        return vc.parse_stamps(doc, root)


class StampGrammarTests(unittest.TestCase):
    def test_inline_stamp_parses_every_field(self) -> None:
        claims = parse(
            "FX table is empty. "
            "<!--@verified 2026-07-26 id=db.fx.rows expect=0 "
            "sql=SELECT COUNT(*) FROM market_fx_rate_observation-->\n"
        )
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertEqual(claim.claim_id, "db.fx.rows")
        self.assertEqual(claim.measured, date(2026, 7, 26))
        self.assertEqual(claim.op, "=")
        self.assertEqual(claim.expected, "0")
        self.assertEqual(claim.ttl, vc.DEFAULT_TTL_DAYS)
        self.assertEqual(claim.sql, "SELECT COUNT(*) FROM market_fx_rate_observation")
        self.assertEqual(claim.parse_error, "")

    def test_multiline_stamp_is_collapsed_to_one_statement(self) -> None:
        claims = parse(
            "eBay coverage.\n"
            "<!--@verified 2026-07-26 id=db.ebay.rows expect>=3824\n"
            "    sql=SELECT COUNT(*) FROM market_price_observation\n"
            "        WHERE source_code='ebay'-->\n"
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(
            claims[0].sql,
            "SELECT COUNT(*) FROM market_price_observation WHERE source_code='ebay'",
        )
        self.assertEqual(claims[0].op, ">=")

    def test_context_points_at_the_sentence_not_the_stamp(self) -> None:
        claims = parse(
            "Quarantine gate has never fired.\n"
            "<!--@verified 2026-07-26 id=q expect=0 sql=SELECT 1-->\n"
        )
        self.assertEqual(claims[0].context, "Quarantine gate has never fired.")

    def test_context_uses_same_line_prose_when_present(self) -> None:
        claims = parse(
            "| table | 0 rows <!--@verified 2026-07-26 id=q expect=0 sql=SELECT 1--> |\n"
        )
        self.assertIn("0 rows", claims[0].context)

    def test_ttl_override_is_honoured(self) -> None:
        claims = parse("<!--@verified 2026-07-26 id=x ttl=90 expect=0 sql=SELECT 1-->")
        self.assertEqual(claims[0].ttl, 90)

    def test_multiple_stamps_in_one_document(self) -> None:
        claims = parse(
            "a <!--@verified 2026-07-26 id=one expect=1 sql=SELECT 1-->\n"
            "b <!--@verified 2026-07-26 id=two expect=2 sql=SELECT 2-->\n"
        )
        self.assertEqual([c.claim_id for c in claims], ["one", "two"])
        self.assertEqual([c.line for c in claims], [1, 2])

    def test_malformed_stamps_surface_as_parse_errors_not_silence(self) -> None:
        cases = {
            "<!--@verified id=x expect=1 sql=SELECT 1-->": "measurement date",
            "<!--@verified 2026-07-26 expect=1 sql=SELECT 1-->": "missing id",
            "<!--@verified 2026-07-26 id=x sql=SELECT 1-->": "missing expect",
            "<!--@verified 2026-07-26 id=x expect=1-->": "missing sql",
            "<!--@verified 2026-13-99 id=x expect=1 sql=SELECT 1-->": "bad date",
            "<!--@verified 2026-07-26 id=x ttl=soon expect=1 sql=SELECT 1-->": "bad ttl",
        }
        for markdown, expected in cases.items():
            with self.subTest(markdown=markdown):
                claims = parse(markdown)
                self.assertEqual(len(claims), 1)
                self.assertIn(expected, claims[0].parse_error)

    def test_ordinary_html_comments_are_ignored(self) -> None:
        self.assertEqual(parse("<!-- just a note -->\n"), [])


class ReadOnlyGuardTests(unittest.TestCase):
    def test_select_and_with_are_allowed(self) -> None:
        for sql in (
            "SELECT COUNT(*) FROM catalog_variant",
            "  select 1  ",
            "WITH x AS (SELECT 1) SELECT * FROM x",
            "SHOW TABLES",
            "EXPLAIN SELECT 1",
            "(SELECT 1)",
        ):
            with self.subTest(sql=sql):
                self.assertEqual(vc.check_read_only(sql), "")

    def test_writes_are_blocked(self) -> None:
        for sql in (
            "UPDATE catalog_variant SET name='x'",
            "DELETE FROM catalog_variant",
            "DROP TABLE catalog_variant",
            "INSERT INTO catalog_variant VALUES (1)",
            "TRUNCATE catalog_variant",
        ):
            with self.subTest(sql=sql):
                self.assertNotEqual(vc.check_read_only(sql), "")

    def test_stacked_statement_is_blocked(self) -> None:
        self.assertIn("multiple statements",
                      vc.check_read_only("SELECT 1; DROP TABLE catalog_variant"))

    def test_write_hidden_behind_a_leading_comment_is_blocked(self) -> None:
        self.assertNotEqual(vc.check_read_only("-- harmless\nDELETE FROM catalog_variant"), "")
        self.assertNotEqual(vc.check_read_only("/* c */ DROP TABLE catalog_variant"), "")

    def test_write_smuggled_after_a_select_is_blocked(self) -> None:
        self.assertNotEqual(
            vc.check_read_only("SELECT 1 FROM x /* */ INTO OUTFILE '/tmp/x'"), ""
        )

    def test_table_rows_estimate_is_refused(self) -> None:
        # Measured 2026-07-26: TABLE_ROWS said 1590, COUNT(*) said 1705.
        message = vc.check_read_only(
            "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_NAME='catalog_variant'"
        )
        self.assertIn("estimate", message)

    def test_empty_statement_is_refused(self) -> None:
        self.assertIn("empty", vc.check_read_only("   -- nothing here\n"))


class ComparisonTests(unittest.TestCase):
    def test_numeric_operators(self) -> None:
        self.assertTrue(vc.compare(10, "=", "10"))
        self.assertFalse(vc.compare(10, "=", "0"))
        self.assertTrue(vc.compare(3824, ">=", "3824"))
        self.assertTrue(vc.compare(3825, ">", "3824"))
        self.assertFalse(vc.compare(3824, ">", "3824"))
        self.assertTrue(vc.compare(5, "<=", "10"))
        self.assertTrue(vc.compare(5, "!=", "6"))

    def test_string_values_compare_as_strings(self) -> None:
        self.assertTrue(vc.compare("2026-07-25", "=", "2026-07-25"))
        self.assertFalse(vc.compare("2026-07-25", "=", "2026-07-24"))

    def test_numeric_expectation_against_text_result_does_not_pass(self) -> None:
        self.assertFalse(vc.compare("ready", "=", "0"))

    def test_null_result_only_matches_an_explicit_null_expectation(self) -> None:
        self.assertTrue(vc.compare(None, "=", "null"))
        self.assertFalse(vc.compare(None, "=", "0"))


class StatusClassificationTests(unittest.TestCase):
    """verify() must separate 'the number changed' from 'nobody re-checked'."""

    class FakeRunner:
        allow_cmd = False
        db_error = ""

        def __init__(self, value):
            self.value = value

        def run_sql(self, sql):
            return self.value

    def claim(self, measured: date, expected: str, ttl: int = vc.DEFAULT_TTL_DAYS):
        return vc.Claim(
            doc="d.md", line=1, claim_id="c", measured=measured, op="=",
            expected=expected, ttl=ttl, sql="SELECT 1",
        )

    def test_fresh_and_holding_is_ok(self) -> None:
        claims = vc.verify([self.claim(date(2026, 7, 26), "5")],
                           self.FakeRunner(5), date(2026, 7, 26))
        self.assertEqual(claims[0].status, vc.OK)

    def test_wrong_number_is_drift_even_when_freshly_stamped(self) -> None:
        claims = vc.verify([self.claim(date(2026, 7, 26), "0")],
                           self.FakeRunner(10), date(2026, 7, 26))
        self.assertEqual(claims[0].status, vc.DRIFT)
        self.assertIn("measured 10", claims[0].detail)

    def test_right_number_but_expired_stamp_is_stale(self) -> None:
        claims = vc.verify([self.claim(date(2026, 7, 20), "5")],
                           self.FakeRunner(5), date(2026, 7, 26))
        self.assertEqual(claims[0].status, vc.STALE)
        self.assertEqual(claims[0].age_days, 6)

    def test_drift_outranks_staleness(self) -> None:
        claims = vc.verify([self.claim(date(2026, 7, 1), "0")],
                           self.FakeRunner(10), date(2026, 7, 26))
        self.assertEqual(claims[0].status, vc.DRIFT)

    def test_ttl_override_keeps_a_slow_moving_fact_fresh(self) -> None:
        claims = vc.verify([self.claim(date(2026, 6, 1), "5", ttl=90)],
                           self.FakeRunner(5), date(2026, 7, 26))
        self.assertEqual(claims[0].status, vc.OK)

    def test_parse_error_reports_as_error_without_running_sql(self) -> None:
        broken = vc.Claim(doc="d.md", line=1, claim_id="?", measured=date.min, op="=",
                          expected="", ttl=3, parse_error="missing id")
        claims = vc.verify([broken], self.FakeRunner(1), date(2026, 7, 26))
        self.assertEqual(claims[0].status, vc.ERROR)


class SecretHandlingTests(unittest.TestCase):
    def test_password_is_scrubbed_from_error_text(self) -> None:
        message = vc.scrub("Access denied using password hunter2", "hunter2")
        self.assertNotIn("hunter2", message)
        self.assertIn("***", message)

    def test_scrub_is_a_noop_without_a_secret(self) -> None:
        self.assertEqual(vc.scrub("plain error", ""), "plain error")


class ProjectDocTests(unittest.TestCase):
    def test_no_stamp_in_the_real_docs_fails_to_parse(self) -> None:
        """A typo'd stamp is a claim silently going unchecked. Catch it in CI."""
        broken = []
        for path in vc.collect_docs([]):
            for claim in vc.parse_stamps(path, ROOT):
                if claim.parse_error:
                    broken.append(f"{claim.doc}:{claim.line} - {claim.parse_error}")
        self.assertEqual(broken, [], "malformed @verified stamps: " + "; ".join(broken))

    def test_claim_ids_are_unique_across_the_docs(self) -> None:
        seen: dict[str, str] = {}
        collisions = []
        for path in vc.collect_docs([]):
            for claim in vc.parse_stamps(path, ROOT):
                if claim.parse_error:
                    continue
                where = f"{claim.doc}:{claim.line}"
                if claim.claim_id in seen:
                    collisions.append(f"{claim.claim_id} ({seen[claim.claim_id]} vs {where})")
                seen[claim.claim_id] = where
        self.assertEqual(collisions, [], "duplicate claim ids: " + "; ".join(collisions))


if __name__ == "__main__":
    unittest.main()
