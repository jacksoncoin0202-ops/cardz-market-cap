from __future__ import annotations

import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bootstrap_archive as subject  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class BootstrapArchiveTests(unittest.TestCase):
    def make_repo(self, root: Path) -> tuple[str, str]:
        lock_hash = "a" * 64
        lock_id = "universe_test_aaaaaaaaaaaa"
        pointer = {
            "payloadSha256": lock_hash,
            "lock": {"lockId": lock_id, "immutableFile": f"{lock_id}.json"},
            "cards": [
                {
                    "canonicalSourceCode": "snkrdunk",
                    "canonicalExternalId": "101",
                    "gemrateId": "g1",
                    "snkItemId": 101,
                },
                {
                    "canonicalSourceCode": "ebay",
                    "canonicalExternalId": "abc",
                    "gemrateId": "g2",
                },
            ],
        }
        source_map = root / "data/runtime/private-source-map"
        write_json(source_map / "active-universe.json", pointer)
        write_json(source_map / "active-universe-locks" / f"{lock_id}.json", pointer)
        (source_map / "active-gemrate-ids.txt").write_text("g1\ng2\n", encoding="utf-8")
        (source_map / "active-snk-ids.txt").write_text("101\n", encoding="utf-8")
        (source_map / "source-crosswalk.json").write_text("must not ship", encoding="utf-8")

        active_observation = {
            "sourceCode": "snkrdunk",
            "externalEntityId": "101",
            "observationKind": "index_constituent",
            "observedDate": "2026-07-20",
            "payload": {"priceUsd": 10},
        }
        inactive_observation = {
            "sourceCode": "snkrdunk",
            "externalEntityId": "999",
            "observationKind": "index_constituent",
            "observedDate": "2026-07-20",
            "payload": {"priceUsd": 99},
        }
        landing = root / "data/runtime/private-landing"
        g10 = landing / "g10/full/current/canonical-batch.json"
        write_json(
            g10,
            {
                "runId": "g10-current",
                "effectiveAt": "2026-07-20T00:00:00Z",
                "mode": "full",
                "observations": [active_observation, inactive_observation],
            },
        )
        write_json(
            landing / "g10/full/undated/canonical-batch.json",
            {
                "runId": "g10-undated",
                "effectiveAt": "2026-07-21T00:00:00Z",
                "mode": "full",
                "observations": [{key: value for key, value in active_observation.items() if key != "observedDate"}],
            },
        )

        current_sources = landing / "sources/current/canonical-batch.json"
        write_json(
            current_sources,
            {
                "runId": "sources-current",
                "effectiveAt": "2026-07-21T00:00:00Z",
                "observations": [active_observation],
            },
        )
        write_json(current_sources.with_name("manifest.json"), {"activeUniverseSha256": lock_hash})
        old_sources = landing / "sources/old/canonical-batch.json"
        write_json(old_sources, {"runId": "old", "observations": [active_observation]})
        write_json(old_sources.with_name("manifest.json"), {"activeUniverseSha256": "b" * 64})

        tag_old = landing / "tag/daily/old/canonical-batch.json"
        tag_new = landing / "tag/daily/new/canonical-batch.json"
        tag_document = {"runId": "tag", "observations": [active_observation]}
        write_json(tag_old, {**tag_document, "fetchedAt": "2026-07-21T00:00:00Z"})
        write_json(tag_new, {**tag_document, "fetchedAt": "2026-07-22T00:00:00Z"})

        (root / ".env").write_text("SECRET=do-not-ship", encoding="utf-8")
        raw = landing / "g10/full/current/payload/images/slab.jpg"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b"not-an-image")
        return lock_id, lock_hash

    def test_build_verify_restore_is_active_only_and_no_overwrite_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repo = base / "repo"
            lock_id, lock_hash = self.make_repo(repo)
            archive = base / "bootstrap.tar.gz"

            manifest = subject.build_archive(repo, archive)
            verified = subject.verify_archive(archive)

            self.assertEqual(manifest["lockId"], lock_id)
            self.assertEqual(verified["lockHash"], lock_hash)
            names = {entry["path"] for entry in manifest["entries"]}
            self.assertNotIn(".env", names)
            self.assertFalse(any("payload/images" in name for name in names))
            self.assertFalse(any("source-crosswalk" in name for name in names))
            self.assertFalse(any("sources/old" in name for name in names))
            self.assertFalse(any("g10/full/undated" in name for name in names))
            self.assertTrue(any("tag/daily/new" in name for name in names))
            self.assertFalse(any("tag/daily/old" in name for name in names))

            with tarfile.open(archive, "r:gz") as handle:
                g10_name = next(name for name in names if "/g10/full/" in f"/{name}")
                batch = json.load(handle.extractfile(g10_name))
            self.assertEqual(len(batch["observations"]), 1)
            self.assertEqual(batch["observations"][0]["externalEntityId"], "101")

            restored = base / "restored"
            subject.restore_archive(archive, restored)
            self.assertTrue((restored / "data/runtime/private-source-map/active-universe.json").is_file())
            with self.assertRaises(subject.ArchiveError):
                subject.restore_archive(archive, restored)
            subject.restore_archive(archive, restored, overwrite=True)

    def test_verify_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                data = b"escape"
                info = tarfile.TarInfo("../escape.txt")
                info.size = len(data)
                handle.addfile(info, io.BytesIO(data))
            with self.assertRaises(subject.ArchiveError):
                subject.verify_archive(archive)

    def test_verify_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "bad-checksum.tar.gz"
            entry_name = "data/runtime/private-source-map/active-universe.json"
            entry_data = b"{}"
            manifest = {
                "schemaVersion": subject.SCHEMA_VERSION,
                "kind": "cardz-active-bootstrap",
                "lockId": "test",
                "lockHash": "a" * 64,
                "activeUniversePath": entry_name,
                "immutableLockPath": entry_name,
                "providerWorklists": [entry_name],
                "canonicalBatches": [entry_name],
                "entries": [{"path": entry_name, "size": len(entry_data), "sha256": "b" * 64}],
            }
            with tarfile.open(archive, "w:gz") as handle:
                for name, data in (
                    (subject.MANIFEST_NAME, subject.canonical_json(manifest)),
                    (entry_name, entry_data),
                ):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    handle.addfile(info, io.BytesIO(data))
            with self.assertRaises(subject.ArchiveError):
                subject.verify_archive(archive)

    def test_build_rejects_provider_worklist_that_does_not_match_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            self.make_repo(repo)
            worklist = repo / "data/runtime/private-source-map/active-gemrate-ids.txt"
            worklist.write_text("api_key=must-not-ship\n", encoding="utf-8")
            with self.assertRaisesRegex(subject.ArchiveError, "worklist does not match"):
                subject.build_archive(repo, Path(temporary) / "bootstrap.tar.gz")

    def test_build_requires_a_dated_current_lock_source_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            self.make_repo(repo)
            batch_path = repo / "data/runtime/private-landing/sources/current/canonical-batch.json"
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            for observation in batch["observations"]:
                observation.pop("observedDate", None)
            write_json(batch_path, batch)
            with self.assertRaisesRegex(subject.ArchiveError, "no canonical source batch"):
                subject.build_archive(repo, Path(temporary) / "bootstrap.tar.gz")


if __name__ == "__main__":
    unittest.main()
