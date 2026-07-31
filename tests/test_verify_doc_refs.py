"""Tests for scripts/verify_doc_refs.py.

Every case below is either a real reference shape taken out of this repo's docs
or a real false positive the first version produced.  The false-positive tests
are the load-bearing ones: a reference checker that cries wolf gets switched
off, and a switched-off checker is indistinguishable from no checker.

`ReverseTests` exists because of a measured failure in a sibling script:
`verify_handoff.py` asked git for the *index* rather than HEAD, so files that
were merely `git add`-ed reported as tracked and the check went green while 30
of 51 required files were absent from HEAD.  The logic was fine; the wiring
asked the wrong question.  Only an input that must fail catches that class of
bug, so each verifier here is fed one.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_doc_refs.py"

spec = importlib.util.spec_from_file_location("verify_doc_refs", SCRIPT)
assert spec and spec.loader
vdr = importlib.util.module_from_spec(spec)
# Register before executing: @dataclass resolves annotations through
# sys.modules[cls.__module__], and an unregistered module makes that None.
sys.modules[spec.name] = vdr
spec.loader.exec_module(vdr)


class Sandbox:
    """A throwaway repo: docs plus source, so references have something to hit."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(dir=str(ROOT / "temp") if (ROOT / "temp").is_dir() else None)
        self.root = Path(self._tmp.name)

    def write(self, relative: str, body: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def report(self, docs: list[str]) -> dict:
        return vdr.build_report(self.root, [self.root / d for d in docs], skip_tasks=True)

    def close(self) -> None:
        self._tmp.cleanup()


class SandboxCase(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Sandbox()
        self.addCleanup(self.box.close)


class ReferenceExtractionTests(SandboxCase):
    def test_link_label_is_not_a_second_reference(self) -> None:
        """`[a.py:12](src/a.py:12)` is one reference.

        Counting the label separately reported "file does not exist" for every
        correctly written link in the repo - 28 false alarms on the first run.
        """
        self.box.write("src/a.py", "\n" * 40)
        self.box.write("DOC.md", "see [a.py:12](src/a.py:12) here\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["references"], 1)
        self.assertEqual(report["failures"], [])

    def test_github_anchor_spelling_is_caught_too(self) -> None:
        self.box.write("src/a.py", "\n" * 5)
        self.box.write("DOC.md", "see `src/a.py#L900`\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["failures"][0]["status"], vdr.BROKEN)

    def test_bare_basename_resolves_when_unique(self) -> None:
        self.box.write("pipelines/run_daily.py", "\n" * 100)
        self.box.write("DOC.md", "see `run_daily.py:72`\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["failures"], [])
        self.assertEqual(len(report["bare"]), 1)

    def test_bare_basename_with_two_matches_is_ambiguous_not_broken(self) -> None:
        self.box.write("a/dup.py", "\n" * 100)
        self.box.write("b/dup.py", "\n" * 100)
        self.box.write("DOC.md", "see `dup.py:5`\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["warnings"][0]["status"], vdr.AMBIGUOUS)
        self.assertEqual(report["failures"], [])

    def test_document_relative_path_resolves(self) -> None:
        self.box.write("pipelines/thing.py", "\n" * 300)
        self.box.write("docs/DOC.md", "see [thing.py:219](../pipelines/thing.py:219)\n")
        report = self.box.report(["docs/DOC.md"])
        self.assertEqual(report["failures"], [])


class BrokenReferenceTests(SandboxCase):
    def test_missing_file_is_broken(self) -> None:
        self.box.write("DOC.md", "see [gone.py:10](src/gone.py:10)\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["failures"][0]["status"], vdr.BROKEN)
        self.assertFalse(report["ok"])

    def test_line_past_end_of_file_is_broken(self) -> None:
        self.box.write("src/short.py", "one\ntwo\n")
        self.box.write("DOC.md", "see [short.py:900](src/short.py:900)\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["failures"][0]["status"], vdr.BROKEN)
        self.assertIn("2 lines", report["failures"][0]["detail"])


class DocumentRouteTests(SandboxCase):
    def _authority(self, **statuses: str) -> dict[str, dict]:
        return {
            path: {"path": path, "status": status}
            for path, status in statuses.items()
        }

    def test_active_document_cannot_route_to_blocked_document(self) -> None:
        source = self.box.write("docs/ACTIVE.md", "[old](OLD.md)\n")
        self.box.write("docs/OLD.md", "historical\n")
        authority = self._authority(
            **{
                "docs/ACTIVE.md": "active",
                "docs/OLD.md": "superseded",
            }
        )
        report = vdr.build_report(
            self.box.root,
            [source],
            skip_tasks=True,
            document_authority=authority,
        )
        self.assertEqual(report["failures"][0]["status"], vdr.DOCROUTE)
        self.assertFalse(report["ok"])

    def test_active_document_missing_markdown_link_is_broken(self) -> None:
        source = self.box.write("docs/ACTIVE.md", "[missing](MISSING.md)\n")
        authority = self._authority(**{"docs/ACTIVE.md": "active"})
        report = vdr.build_report(
            self.box.root,
            [source],
            skip_tasks=True,
            document_authority=authority,
        )
        self.assertEqual(report["failures"][0]["status"], vdr.BROKENLINK)

    def test_historical_source_is_not_an_execution_route(self) -> None:
        source = self.box.write("docs/HISTORY.md", "[old](MISSING.md)\n")
        authority = self._authority(**{"docs/HISTORY.md": "historical"})
        report = vdr.build_report(
            self.box.root,
            [source],
            skip_tasks=True,
            document_authority=authority,
        )
        self.assertEqual(report["references"], 0)
        self.assertEqual(report["failures"], [])

    def test_active_to_active_document_route_is_counted_and_clean(self) -> None:
        source = self.box.write("docs/ACTIVE.md", "[next](NEXT.md)\n")
        self.box.write("docs/NEXT.md", "active\n")
        authority = self._authority(
            **{
                "docs/ACTIVE.md": "active",
                "docs/NEXT.md": "generated",
            }
        )
        report = vdr.build_report(
            self.box.root,
            [source],
            skip_tasks=True,
            document_authority=authority,
        )
        self.assertEqual(report["references"], 1)
        self.assertEqual(report["counts"], {vdr.OK: 1})
        self.assertTrue(report["clean"])


class DriftTests(SandboxCase):
    def test_symbol_that_moved_is_reported_with_its_real_line(self) -> None:
        """The measured failure: 80 lines inserted, `localize_cards()` moved."""
        body = "\n" * 40 + "def localize_cards(cards):\n    return cards\n"
        self.box.write("pipelines/snap.py", body)
        self.box.write("DOC.md", "[snap.py:12](pipelines/snap.py:12) is `localize_cards()`\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(len(report["drift"]), 1)
        self.assertIn("41", report["drift"][0]["detail"])

    def test_drift_does_not_fail_the_run_by_default(self) -> None:
        """Measured one-in-four false positives. Loud, but not blocking."""
        body = "\n" * 40 + "def moved(x):\n    return x\n"
        self.box.write("pipelines/snap.py", body)
        self.box.write("DOC.md", "[snap.py:12](pipelines/snap.py:12) is `moved()`\n")
        report = self.box.report(["DOC.md"])
        self.assertTrue(report["ok"], "drift alone must not fail the run")
        self.assertFalse(report["clean"], "but it must not read as clean either")

    def test_a_second_unrelated_symbol_does_not_condemn_a_correct_reference(self) -> None:
        """Real false positive.

        PROJECT_STATE.md: "`singleton_lock` acquired at the top of `main()`
        (`run_daily.py:72`)".  Line 72 is `singleton_lock`; `main()` is 500
        lines away.  Pairing the reference with every symbol on the line called
        a correct reference drifted.
        """
        body = ["\n"] * 71 + ["def singleton_lock(path):\n", "    pass\n"] + ["\n"] * 500 + ["def main():\n"]
        self.box.write("pipelines/run_daily.py", "".join(body))
        self.box.write("DOC.md", "`singleton_lock` taken at the top of `main()` (`run_daily.py:72`)\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["drift"], [], "a matching symbol on the line vindicates the reference")

    def test_module_level_constant_counts_as_a_definition(self) -> None:
        """`scripts/backend.py:33` is `SECRETS_PATH = ...`, not a def."""
        body = "\n" * 32 + "SECRETS_PATH = ROOT / 'x'\n"
        self.box.write("scripts/backend.py", body)
        self.box.write("DOC.md", "`scripts/backend.py:33` `SECRETS_PATH` feeds `daily_environment()`\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["drift"], [])

    def test_call_site_counts_not_only_the_definition(self) -> None:
        """Docs cite call sites: "KadoRawResolver calls normalize_collector here"."""
        body = "def normalize_collector(x):\n    return x\n" + "\n" * 20 + "    v = normalize_collector(n)\n"
        self.box.write("pipelines/g10.py", body)
        self.box.write("DOC.md", "`pipelines/g10.py:23` `normalize_collector`\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["drift"], [])

    def test_label_range_is_honoured_over_the_bare_anchor(self) -> None:
        """`[snapshot.ts:37-61](.../snapshot.ts:37)` claims 37-61, not just 37."""
        body = "\n" * 37 + "function localised(v) {\n  return v;\n}\n"
        self.box.write("apps/web/src/lib/snapshot.ts", body)
        self.box.write(
            "DOC.md",
            "[snapshot.ts:37-61](apps/web/src/lib/snapshot.ts:37) `localised()` wires it\n",
        )
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["drift"], [])


class RenameTests(SandboxCase):
    def test_missing_call_form_symbol_warns(self) -> None:
        self.box.write("pipelines/a.py", "def kept():\n    pass\n")
        self.box.write("DOC.md", "[a.py](pipelines/a.py) `vanished()` does the thing\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["warnings"][0]["status"], vdr.RENAMED)
        self.assertTrue(report["ok"], "a rename warning must not block delivery")

    def test_bare_backticked_word_is_not_a_rename_claim(self) -> None:
        """`CARDZ_ALERT_WEBHOOK`, `python3`, `exec` are not missing symbols.

        Treating every backticked word as a symbol produced 22 warnings, all of
        them wrong.
        """
        self.box.write("scripts/notify_alert.py", "def send():\n    pass\n")
        self.box.write("DOC.md", "[notify_alert.py](scripts/notify_alert.py) reads `CARDZ_ALERT_WEBHOOK`\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(report["warnings"], [])


class TempDependencyTests(SandboxCase):
    def test_absolute_windows_path_into_temp_is_detected(self) -> None:
        """The live case.

        `CARDZ-Freeze-Sweep-Guard` ran
        `...\\cardz-market-cap\\temp\\freeze-sweep-guard.ps1`.  An anchor that
        only accepted quote/space/equals before `temp` missed it, because the
        real string has a backslash there.
        """
        blob = r'powershell.exe -File "C:\repo\cardz-market-cap\temp\freeze-sweep-guard.ps1"'
        self.assertIsNotNone(vdr.TEMP_EXEC_RE.search(blob))

    def test_installer_referencing_temp_is_reported(self) -> None:
        self.box.write("deploy/windows/install.ps1", 'Register-ScheduledTask -Execute "temp/guard.ps1"\n')
        self.box.write("DOC.md", "nothing here\n")
        report = self.box.report(["DOC.md"])
        self.assertEqual(len(report["temp_dependencies"]), 1)
        self.assertFalse(report["ok"], "a scheduled temp/ dependency is a hard failure")

    def test_a_clean_deploy_tree_reports_nothing(self) -> None:
        self.box.write("deploy/windows/install.ps1", 'Register-ScheduledTask -Execute "deploy/windows/guard.ps1"\n')
        self.box.write("DOC.md", "nothing here\n")
        self.assertEqual(self.box.report(["DOC.md"])["temp_dependencies"], [])


class ExampleMarkerTests(SandboxCase):
    """The doc that bans bare line numbers must be able to quote one."""

    def test_marked_line_is_not_reported(self) -> None:
        self.box.write("pipelines/thing.py", "def go():\n    return 1\n")
        self.box.write(
            "RULES.md",
            "banned form looks like `thing.py:2` <!--docref:example-->\n",
        )
        report = self.box.report(["RULES.md"])
        self.assertEqual(report["references"], 0, report)
        self.assertEqual(report["bare"], [], report)

    def test_marker_only_silences_its_own_line(self) -> None:
        """Reverse: a marker must not mute the rest of the file.

        A file-level opt-out would let one example hide every real rotted
        reference below it - the checker would go quiet exactly where it
        matters most.
        """
        self.box.write("pipelines/thing.py", "def go():\n    return 1\n")
        self.box.write(
            "RULES.md",
            "banned form looks like `thing.py:2` <!--docref:example-->\n"
            "but this one is a real reference: `pipelines/thing.py:2`\n",
        )
        report = self.box.report(["RULES.md"])
        self.assertEqual(report["references"], 1, report)
        self.assertEqual([b["doc_line"] for b in report["bare"]], [2], report)

    def test_marker_does_not_hide_a_broken_file(self) -> None:
        """Reverse: the marker must not become a way to launder a dead path."""
        self.box.write(
            "RULES.md",
            "see [gone.py:1](pipelines/gone.py:1) <!--docref:example-->\n"
            "see [also_gone.py:1](pipelines/also_gone.py:1)\n",
        )
        report = self.box.report(["RULES.md"])
        self.assertEqual(len(report["failures"]), 1, report)
        self.assertIn("also_gone", report["failures"][0]["ref"])


class ReverseTests(unittest.TestCase):
    """Feed each verifier something that MUST fail, and prove it does.

    Without this, a green run only proves the script ran.
    """

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-X", "utf8", *args],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
        )

    def test_doc_ref_checker_exits_nonzero_on_a_broken_reference(self) -> None:
        with tempfile.TemporaryDirectory(dir=str(ROOT / "temp")) as tmp:
            doc = Path(tmp) / "BAD.md"
            doc.write_text("see [nope.py:1](pipelines/definitely_not_here.py:1)\n", encoding="utf-8")
            result = self._run([str(SCRIPT), str(doc.relative_to(ROOT)), "--skip-tasks"])
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("BROKEN", result.stdout)

    def test_doc_ref_checker_exits_zero_when_the_reference_is_good(self) -> None:
        with tempfile.TemporaryDirectory(dir=str(ROOT / "temp")) as tmp:
            doc = Path(tmp) / "GOOD.md"
            doc.write_text("see [verify_doc_refs.py](scripts/verify_doc_refs.py) `main()`\n", encoding="utf-8")
            result = self._run([str(SCRIPT), str(doc.relative_to(ROOT)), "--skip-tasks"])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_authority_cli_exits_nonzero_on_active_to_blocked_route(self) -> None:
        with tempfile.TemporaryDirectory(dir=str(ROOT / "temp")) as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "docs").mkdir()
            (root / "docs" / "ACTIVE.md").write_text(
                "[old](OLD.md)\n",
                encoding="utf-8",
            )
            (root / "docs" / "OLD.md").write_text("old\n", encoding="utf-8")
            (root / "config" / "data-routing.json").write_text(
                '{"documentAuthority":{"documents":['
                '{"path":"docs/ACTIVE.md","status":"active"},'
                '{"path":"docs/OLD.md","status":"historical"}'
                "]}}",
                encoding="utf-8",
            )
            result = self._run(
                [
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "--authority-active-only",
                    "--skip-tasks",
                ]
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("DOCROUTE", result.stdout)

    def test_claim_checker_exits_nonzero_on_a_number_that_is_wrong(self) -> None:
        """End-to-end proof that verify_claims wires DRIFT through to exit 1.

        The unit tests prove the classifier; this proves main() acts on it.
        Skipped rather than failed when the database is unreachable - a missing
        credential is not a drifted number and must not be reported as one.
        """
        checker = ROOT / "scripts" / "verify_claims.py"
        with tempfile.TemporaryDirectory(dir=str(ROOT / "temp")) as tmp:
            doc = Path(tmp) / "BAD_CLAIM.md"
            doc.write_text(
                "The catalog holds exactly one variant.\n"
                "<!--@verified 2026-07-26 id=reverse.test.impossible expect=1 "
                "sql=SELECT COUNT(*) FROM catalog_variant-->\n",
                encoding="utf-8",
            )
            result = self._run([str(checker), str(doc.relative_to(ROOT))])
            if result.returncode == 2:
                self.skipTest("database unreachable; nothing to reverse-test")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("DRIFT", result.stdout)


class ProjectDocTests(unittest.TestCase):
    """Guards over the real documents, not synthetic ones."""

    def test_document_authority_selects_only_active_or_generated_markdown(self) -> None:
        selected = {
            path.relative_to(ROOT).as_posix()
            for path in vdr.collect_authority_docs(ROOT)
        }
        self.assertIn("AGENTS.md", selected)
        self.assertIn("docs/generated/DOCUMENT_AUTHORITY.md", selected)
        self.assertNotIn("docs/PROJECT_MAP.md", selected)
        self.assertNotIn("docs/DATA_GAPS.md", selected)
        self.assertNotIn("docs/archive/PROJECT_STATE_PRE_QC_20260729.md", selected)

    def test_active_document_routes_do_not_target_blocked_documents(self) -> None:
        authority = vdr.load_document_authority(ROOT)
        report = vdr.build_report(
            ROOT,
            vdr.collect_authority_docs(ROOT),
            skip_tasks=True,
            document_authority=authority,
        )
        routed_failures = [
            failure
            for failure in report["failures"]
            if failure["status"] in {vdr.BROKENLINK, vdr.DOCROUTE}
        ]
        self.assertEqual(routed_failures, [])
        self.assertGreater(report["references"], 0)

    def test_the_repo_docs_have_no_broken_references(self) -> None:
        report = vdr.build_report(ROOT, vdr.collect_docs([], ROOT), skip_tasks=True)
        broken = [f for f in report["failures"] if f["status"] == vdr.BROKEN]
        self.assertEqual(broken, [], f"{len(broken)} documentation references point at nothing")

    def test_nothing_scheduled_or_installed_runs_out_of_temp(self) -> None:
        """temp/ is swept. Anything a scheduler needs must live somewhere kept.

        Repo-side only: the OS scheduler is machine state, not repo state, and
        failing a test suite on another machine's task list would be noise.
        """
        self.assertEqual(vdr.repo_temp_deps(ROOT), [])


if __name__ == "__main__":
    unittest.main()
