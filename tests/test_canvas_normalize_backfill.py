from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipelines"))
import canvas_normalize_backfill as mod  # noqa: E402


SOURCE_SHA = "a" * 64
SOURCE_VERSION = "b" * 64


def candidate(path: Path) -> str:
    image = Image.new("RGBA", (429, 600), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (423, 589), "white"), (3, 5))
    image.save(path, format="WEBP", lossless=True, method=6)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ledger(tmp_path: Path, *, traversal: bool = False, profile: str = "v2") -> tuple[Path, str, dict]:
    if profile == "v3":
        input_count, success_count, unresolved_count = 7, 5, 2
        run_id, ledger_schema = mod.V3_LEDGER_RUN_ID, mod.V3_LEDGER_SCHEMA
    else:
        input_count, success_count, unresolved_count = 380, 373, 7
        run_id, ledger_schema = mod.LEDGER_RUN_ID, None
    master = tmp_path / "candidate.webp"
    after_sha = candidate(master)
    success = {
        "variantId": 1,
        "assetId": 10,
        "reportContentSha256": SOURCE_SHA,
        "beforeSha256": SOURCE_SHA,
        "sourceVersionSha256": SOURCE_VERSION,
        "afterSha256": after_sha,
        "afterDimensions": [429, 600],
        "candidateMaster": "../outside.webp" if traversal else master.name,
        "postNormalizationGeometryClassification": "pass_geometry",
        "status": "success",
    }
    records = [dict(success, variantId=index + 1, assetId=index + 10) for index in range(success_count)]
    records += [
        {
            "variantId": index + success_count + 1,
            "assetId": index + success_count + 10,
            "status": "unresolved",
            "unresolvedReason": "post_normalization_geometry_failed",
            "postNormalizationGeometryClassification": "reject_geometry",
        }
        for index in range(unresolved_count)
    ]
    document = {
        "schemaVersion": 1,
        "runId": run_id,
        "inputDecision": "reject_geometry",
        "exactInputCount": input_count,
        "uniqueVariantAssetPairs": input_count,
        "counts": {
            "inputRejectGeometry": input_count,
            "candidateSuccess": success_count,
            "unresolved": unresolved_count,
            "postNormalizationRejectGeometry": unresolved_count,
        },
        "records": records,
    }
    if ledger_schema:
        document["ledgerSchema"] = ledger_schema
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), success


class Cursor:
    def __init__(self, connection: "Connection") -> None:
        self.connection = connection
        self.lastrowid = 99
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.connection.commands.append((sql, params))
        if "asset.id = %s AND asset.variant_id = %s" in sql:
            self.row = {
                **self.connection.current,
                "asset_id": int(params[0]),
                "variant_id": int(params[1]),
            }
        elif "asset.content_sha256 = %s" in sql:
            self.row = dict(self.connection.derived) if self.connection.derived else None
        elif "INSERT INTO market_image_asset" in sql:
            self.connection.derived = {
                "asset_id": 99,
                "private_object_key": params[2],
                "mime_type": "image/webp",
                "width_px": 429,
                "height_px": 600,
                "source_version_sha256": params[3],
            }
        elif "INSERT INTO market_image_qc" in sql:
            self.connection.derived.update(
                {
                    "qc_id": 199,
                    "semantic_match_status": params[1],
                    "card_number_match": params[2],
                    "language_match": params[3],
                    "tcg_match": params[4],
                    "raw_front_confirmed": params[5],
                    "public_allowed": 0,
                    "rejection_reason": params[6],
                }
            )
        elif "WHERE asset.id = %s AND qc.qc_version" in sql:
            self.row = {
                **self.connection.derived,
                "variant_id": 1,
                "content_sha256": self.connection.after_sha,
            }

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, after_sha: str, *, drift: str | None = None) -> None:
        self.after_sha = after_sha
        self.commands = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.derived = None
        self.human_qc = {
            "qc_version": "human-review-v1",
            "semantic_match_status": "human_or_vision_confirmed",
            "public_allowed": 1,
            "checked_at": "later-human-review",
        }
        self.current = {
            "asset_id": 10,
            "variant_id": 1,
            "content_sha256": SOURCE_SHA if drift != "before" else "c" * 64,
            "source_version_sha256": SOURCE_VERSION if drift != "version" else "d" * 64,
            "card_number_match": 1,
            "language_match": 1,
            "tcg_match": 1,
            "raw_front_confirmed": 1,
        }

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def args(path: Path, digest: str, failure_root: Path, *, write: bool = False):
    return SimpleNamespace(
        ledger=path,
        ledger_sha256=digest,
        write=write,
        failure_ledger_root=failure_root,
        host="127.0.0.1",
        port=3308,
        database="cardz_market_cap",
        user="cardz",
        password="unused",
    )


def test_rejects_ledger_hash_mismatch(tmp_path: Path) -> None:
    path, _digest, _record = ledger(tmp_path)
    try:
        mod.validate_ledger(path, "0" * 64)
    except mod.LedgerError as error:
        assert str(error) == "ledger_sha256_mismatch"
    else:
        raise AssertionError("hash mismatch accepted")


def test_rejects_candidate_path_traversal(tmp_path: Path) -> None:
    path, digest, _record = ledger(tmp_path, traversal=True)
    try:
        mod.validate_ledger(path, digest)
    except mod.LedgerError as error:
        assert str(error).startswith("candidate_path_escapes_ledger_root")
    else:
        raise AssertionError("traversal accepted")


def test_accepts_named_v3_fixed_cohort(tmp_path: Path) -> None:
    path, digest, _record = ledger(tmp_path, profile="v3")
    document, success, unresolved = mod.validate_ledger(path, digest)
    assert document["runId"] == mod.V3_LEDGER_RUN_ID
    assert len(success) == 5
    assert len(unresolved) == 2


def test_rejects_v3_wrong_counts(tmp_path: Path) -> None:
    path, _digest, _record = ledger(tmp_path, profile="v3")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["counts"]["candidateSuccess"] = 4
    path.write_text(json.dumps(document), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        mod.validate_ledger(path, digest)
    except mod.LedgerError as error:
        assert str(error) == "ledger_fixed_cohort_counts_invalid"
    else:
        raise AssertionError("wrong v3 counts accepted")


def test_dry_run_rolls_back_and_keeps_public_approval_off(tmp_path: Path, monkeypatch) -> None:
    path, digest, record = ledger(tmp_path)
    connection = Connection(record["afterSha256"])
    monkeypatch.setattr(mod.db_runtime, "connection_from_args", lambda _args: connection)
    monkeypatch.setattr(mod, "load_backend_env", lambda: None)
    result = mod.run(args(path, digest, tmp_path / "failures"))
    assert result["candidateReady"] == 373
    assert result["unresolved"] == 7
    assert result["transaction"] == "rolled_back"
    assert connection.commits == 0 and connection.rollbacks == 1
    assert not (tmp_path / "private-source-map").exists()


def test_drift_blocks_and_rolls_back(tmp_path: Path, monkeypatch) -> None:
    path, digest, record = ledger(tmp_path)
    connection = Connection(record["afterSha256"], drift="version")
    monkeypatch.setattr(mod.db_runtime, "connection_from_args", lambda _args: connection)
    monkeypatch.setattr(mod, "load_backend_env", lambda: None)
    result = mod.run(args(path, digest, tmp_path / "failures"))
    assert result["status"] == "blocked_drift"
    assert result["drifted"] == 373
    assert result["transaction"] == "rolled_back"
    assert connection.commits == 0 and connection.rollbacks == 1


def test_write_is_idempotent_and_never_promotes(tmp_path: Path, monkeypatch) -> None:
    path, _digest, record = ledger(tmp_path)
    durable = tmp_path / "private-source-map/image-derived/geometry"
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DERIVED_ROOT", durable)
    connection = Connection(record["afterSha256"])
    current = connection.current
    first = mod.apply_record(connection, record, current, path.parent)
    first_mutations = [
        (sql, params)
        for sql, params in connection.commands
        if sql.lstrip().startswith(("INSERT ", "UPDATE ", "DELETE "))
    ]
    human_before = dict(connection.human_qc)
    second = mod.apply_record(connection, record, current, path.parent)
    assert first["derivedAssetId"] == second["derivedAssetId"] == 99
    assert first == {
        "derivedAssetId": 99,
        "privateObjectKey": (
            f"private-source-map/image-derived/geometry/{record['afterSha256']}.webp"
        ),
        "write": True,
        "noOp": False,
    }
    assert second == {
        "derivedAssetId": 99,
        "privateObjectKey": (
            f"private-source-map/image-derived/geometry/{record['afterSha256']}.webp"
        ),
        "write": False,
        "noOp": True,
    }
    destination = durable / f"{record['afterSha256']}.webp"
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == record["afterSha256"]
    all_mutations = [
        (sql, params)
        for sql, params in connection.commands
        if sql.lstrip().startswith(("INSERT ", "UPDATE ", "DELETE "))
    ]
    assert all_mutations == first_mutations
    assert len([sql for sql, _params in all_mutations if "market_image_asset" in sql]) == 1
    assert len([sql for sql, _params in all_mutations if "market_image_qc" in sql]) == 1
    assert connection.human_qc == human_before
