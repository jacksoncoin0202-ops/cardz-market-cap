#!/usr/bin/env python3
"""Export the web snapshot from validated canonical MySQL observations.

Ranking and market metrics come only from the canonical database.  The checked
public snapshot is used solely as a presentation pack for already-QC'd images,
localized identity text and editorial stories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args
from canonical_db_qc import BLOCKER_CATEGORY
from data_routing import DEFAULT_RELEASE_PROFILE, load_registry, load_release_profile
from editorial_localization import coverage_summary, localize_cards
from g10_public_snapshot import normalize_collector
from image_geometry_qc import inspect_path
from image_source_qc import classify_content_sha256, classify_source


WINDOWS = ("1d", "7d", "30d")
MIN_PURE_PSA10_SALES_30D = 10
LOCALES = ("en", "zhTW", "zhCN", "ja")
IMAGE_QC_PATH = ROOT / "manifests/image-qc.json"
PUBLIC_ASSET_DIR = ROOT / "data/public/market-assets"
DEFAULT_ROUTING_CONFIG = ROOT / "config" / "data-routing.json"
GRADERS = ("PSA", "BGS", "CGC", "SGC", "TAG")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OPAQUE_PUBLIC_ID_RE = re.compile(r"^cmc_[0-9a-f]{24}$")
CURRENCIES = ("USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW")
TOP_GRADE = {"PSA": "10", "BGS": "10", "CGC": "10", "SGC": "10", "TAG": "10"}
# GemRate 每張卡嘅 3 年週線 POP 史，由 `gemrate_source.py` 落地（gitignore）。
# 缺檔／缺目錄唔係錯 —— 冇歷史就退返 DB 每日觀測，窗口自然報 accumulating。
POPULATION_HISTORY_DIR = ROOT / "data/private/gemrate/cards"
# 直連 API history payload 嘅正宗 top-grade key，鏡返 gemrate_source.py TOP_GRADE。
# TAG 唔喺 per-card population API 入面，所以冇歷史來源。
POPULATION_HISTORY_KEYS = {
    "PSA": ("psa", "psa_10"),
    "BGS": ("beckett", "beckett_10_pristine"),
    "CGC": ("cgc", "cgc_10_perfect"),
    "SGC": ("sgc", "sgc_10_pristine"),
}
POPULATION_WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30}
# 評級 POP 觀測超過 168 小時（7 日）當過期：成個 grader block fail-closed 出
# unavailable，唔准舊數扮新。基準係快照嘅 effective_at，唔係跑機當刻，
# 令同一個 generation 喺任何時間重跑都出一樣結果。
POPULATION_STALE_HOURS = 168
# 同 g10_ingest.derive_price_windows 一模一樣嘅容差，唔另立第二套標準：
# 錨點要真係落喺窗口附近，唔可以攞 35 日前嘅點當「30 日變動」。
POPULATION_WINDOW_TOLERANCE = {"1d": 1, "7d": 2, "30d": 3}
# 價格窗口：candidate daily 缺 change_* 時由 historyDaily 回補（heatmap = 表同一源）。
# 計法就係頭尾：而家價 ÷ ~N 日前價。容差細少少——搵唔到目標日就 ± 幾日，唔玩花巧。
PRICE_WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30}
PRICE_WINDOW_TOLERANCE = {"1d": 2, "7d": 3, "30d": 3}
# The public schema remains top100 + watchlist.  These selectors only control
# how many ordered canonical ranks are materialized into that stable shape.
PRESENTATION_VIEW_LIMITS = {
    "top100": 100,
    "top300": 300,
    "top350": 350,
    "top100_plus_200": 300,
    # combined Top300 ∪ 同日分榜（one-piece / pokemon）成員。分榜有卡跌出
    # combined 300 名以外（實測 07-26：3 張 One Piece，rank 304/312/336），
    # 冇聯集嘅話 /one-piece 分榜頁出唔齊自己榜嘅卡。limit 只管 core 部分。
    "top300_boards": 300,
    # One full, release-profile-filtered public snapshot.  It has no raw rank
    # truncation; the profile capacity is checked after all per-card gates.
    "all_eligible": None,
}
# Minimum constituents a combined index snapshot must hold before it may back
# a given public view. The full Top 300 export requires complete coverage;
# smaller public views tolerate a partially-covered snapshot as long as the
# materialized rows stay rank-contiguous from 1.
PRESENTATION_VIEW_MIN_COVERAGE = {
    "top100": 100,
    "top300": 300,
    "top350": 350,
    "top100_plus_200": 300,
    "top300_boards": 300,
    "all_eligible": 100,
}
QC_REPORT_SCHEMA_VERSION = 1
IMAGE_QC_CATEGORIES = frozenset(
    {"imageIdentity", "imageSemantic", "imageSample", "imageGeometry", "imageDuplicate"}
)


class SnapshotExportError(RuntimeError):
    """Raised before an invalid canonical generation can replace a snapshot."""


def resolved_release_profile(
    release_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the exact policy envelope bound into one public generation."""

    if release_profile is None:
        # Direct unit callers retain the historic strict path.  The CLI supplies
        # the configured effective profile (relaxed-launch-v1).
        return load_release_profile(load_registry(DEFAULT_ROUTING_CONFIG), "strict-v1")
    profile_id = str(release_profile.get("releaseProfile") or "").strip()
    policy = release_profile.get("policy")
    policy_sha256 = str(release_profile.get("policySha256") or "").strip().casefold()
    if (
        not profile_id
        or not isinstance(policy, Mapping)
        or not SHA256_RE.fullmatch(policy_sha256)
    ):
        raise SnapshotExportError("release profile envelope is invalid")
    return {
        "releaseProfile": profile_id,
        "policy": dict(policy),
        "policySha256": policy_sha256,
    }


def _policy_int(policy: Mapping[str, Any], key: str) -> int:
    value = policy.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SnapshotExportError(f"release profile has invalid {key}")
    return value


def qc_gate_current_lock_sha256(connection: Any, evaluation_id: int) -> str:
    """Return the current lock bound to this exact evaluation, or fail closed."""

    row = fetchone(
        connection,
        """
        SELECT l.lock_sha256
        FROM market_alert_evaluation e
        JOIN market_universe_lock l ON l.id=e.universe_lock_id
        WHERE e.id=%s AND l.is_current=1
        """,
        (evaluation_id,),
    )
    lock_sha256 = str((row or {}).get("lock_sha256") or "").casefold()
    if not SHA256_RE.fullmatch(lock_sha256):
        raise SnapshotExportError(
            f"evaluation {evaluation_id} has no current formal universe lock"
        )
    return lock_sha256


def qc_gate_allowed_opaque_ids(
    report_path: Path,
    generation: Mapping[str, Any],
    current_lock_sha256: str,
    release_profile: Mapping[str, Any] | None = None,
) -> set[str]:
    """Accept only exact-report cards without any non-image QC blocker.

    Images intentionally stay outside this gate: their independent pipeline may
    finish later, but identity, price, sales, population, market-cap and time
    evidence must already be authoritative before presentation resolution.
    """

    try:
        document = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SnapshotExportError(f"cannot read authoritative DB QC report: {error}") from error
    if not isinstance(document, Mapping):
        raise SnapshotExportError("authoritative DB QC report must be an object")
    if document.get("schemaVersion") != QC_REPORT_SCHEMA_VERSION:
        raise SnapshotExportError("authoritative DB QC report schema is unsupported")
    if release_profile is not None:
        expected = resolved_release_profile(release_profile)
        if (
            document.get("releaseProfile") != expected["releaseProfile"]
            or document.get("policySha256") != expected["policySha256"]
        ):
            raise SnapshotExportError("authoritative DB QC report release profile/hash mismatch")
    database = document.get("database")
    if not isinstance(database, Mapping) or (
        database.get("authority") != "canonical_mysql"
        or database.get("name") != "cardz_market_cap"
    ):
        raise SnapshotExportError("authoritative DB QC report database is not canonical cardz_market_cap")
    as_of = document.get("asOf")
    try:
        report_day = datetime.fromisoformat(str(as_of).replace("Z", "+00:00")).date()
        generation_day = date.fromisoformat(str(generation["effective_date"]))
    except (KeyError, TypeError, ValueError) as error:
        raise SnapshotExportError("authoritative DB QC report or generation has invalid as-of date") from error
    if report_day != generation_day:
        raise SnapshotExportError("authoritative DB QC report as-of date does not match evaluation")
    if database.get("marketEvaluationId") != generation.get("evaluation_id"):
        raise SnapshotExportError("authoritative DB QC report evaluation does not match generation")
    universe = document.get("universe")
    report_lock = str((universe or {}).get("formalUniverseLockSha256") or "").casefold()
    if not SHA256_RE.fullmatch(report_lock) or report_lock != current_lock_sha256:
        raise SnapshotExportError("authoritative DB QC report formal universe lock does not match evaluation")
    cards = document.get("cards")
    if not isinstance(cards, list):
        raise SnapshotExportError("authoritative DB QC report cards must be a list")

    allowed: set[str] = set()
    for card in cards:
        if not isinstance(card, Mapping):
            continue
        opaque_id = str(card.get("id") or "")
        if not OPAQUE_PUBLIC_ID_RE.fullmatch(opaque_id):
            continue
        blockers = card.get("blockers")
        if not isinstance(blockers, list):
            continue
        categories = {BLOCKER_CATEGORY.get(str(blocker)) for blocker in blockers}
        if categories - IMAGE_QC_CATEGORIES:
            continue
        if None in categories:
            continue
        allowed.add(opaque_id)
    return allowed


def qc_gate_release_image_receipts(
    report_path: Path,
    release_profile: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    """Return the exact relaxed image choices already bound by DB QC.

    Export never reruns the confidence selector: it consumes the immutable
    receipt chosen by the same card-level DB QC evaluation.
    """

    expected = resolved_release_profile(release_profile)
    if expected["releaseProfile"] != "relaxed-launch-v1":
        return {}
    try:
        document = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SnapshotExportError(f"cannot read relaxed image receipt report: {error}") from error
    if (
        not isinstance(document, Mapping)
        or document.get("releaseProfile") != expected["releaseProfile"]
        or document.get("policySha256") != expected["policySha256"]
    ):
        raise SnapshotExportError("relaxed image receipt profile/hash mismatch")
    result: dict[str, Mapping[str, Any]] = {}
    cards = document.get("cards")
    if not isinstance(cards, list):
        raise SnapshotExportError("relaxed image receipt report has no cards")
    for card in cards:
        if not isinstance(card, Mapping):
            continue
        card_id = str(card.get("id") or "")
        image = (card.get("facts") or {}).get("image") if isinstance(card.get("facts"), Mapping) else None
        receipt = image.get("releaseImageReceipt") if isinstance(image, Mapping) else None
        if not OPAQUE_PUBLIC_ID_RE.fullmatch(card_id) or not isinstance(receipt, Mapping):
            continue
        payload = dict(receipt)
        declared = str(payload.pop("receiptSha256", "")).casefold()
        calculated = hashlib.sha256(
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        ).hexdigest()
        chosen = receipt.get("chosen")
        if (
            not SHA256_RE.fullmatch(declared)
            or declared != calculated
            or receipt.get("releaseProfile") != expected["releaseProfile"]
            or str(receipt.get("cardId") or "") != card_id
            or not isinstance(chosen, Mapping)
            or chosen.get("hardBlockers") != []
            or str(receipt.get("chosenContentSha256") or "") != str(chosen.get("contentSha256") or "")
        ):
            raise SnapshotExportError(f"relaxed image receipt is invalid for {card_id}")
        result[card_id] = receipt
    return result


def qc_gate_identity_statuses(
    report_path: Path,
    release_profile: Mapping[str, Any],
) -> dict[str, str]:
    """Read DB-QC identity outcomes without exposing its private evidence."""

    expected = resolved_release_profile(release_profile)
    policy = expected["policy"]
    allowed = policy.get("allowedIdentityStatuses") if isinstance(policy, Mapping) else None
    if not isinstance(allowed, list) or not all(isinstance(value, str) for value in allowed):
        raise SnapshotExportError("release profile has invalid allowedIdentityStatuses")
    try:
        document = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SnapshotExportError(f"cannot read DB-QC identity report: {error}") from error
    if (
        not isinstance(document, Mapping)
        or document.get("releaseProfile") != expected["releaseProfile"]
        or document.get("policySha256") != expected["policySha256"]
    ):
        raise SnapshotExportError("DB-QC identity profile/hash mismatch")
    result: dict[str, str] = {}
    for card in document.get("cards") or []:
        if not isinstance(card, Mapping):
            continue
        card_id = str(card.get("id") or "")
        status = str(card.get("identityStatus") or "")
        if OPAQUE_PUBLIC_ID_RE.fullmatch(card_id) and status in allowed:
            result[card_id] = status
    return result


def presentation_view_limit(name: str) -> int | None:
    normalized = str(name).strip()
    try:
        return PRESENTATION_VIEW_LIMITS[normalized]
    except KeyError as error:
        choices = ", ".join(PRESENTATION_VIEW_LIMITS)
        raise SnapshotExportError(f"unknown public presentation view {normalized!r}; expected one of {choices}") from error


def presentation_view_min_coverage(name: str) -> int:
    return PRESENTATION_VIEW_MIN_COVERAGE[str(name).strip()]


def iso(value: Any) -> str:
    if isinstance(value, datetime):
        current = value
    elif isinstance(value, date):
        current = datetime.combine(value, time.min)
    else:
        current = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def earlier(left: str, right: str) -> str:
    """兩個 ISO 時戳返舊嗰個。

    唔可以直接 `min()` 字串：微秒位會令 `...:00.123456Z` 排喺 `...:00Z` 前面，
    新舊啱啱倒轉。
    """

    def moment(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return left if moment(left) <= moment(right) else right


def number(value: Any) -> int | float | None:
    if value is None:
        return None
    converted = float(value)
    return int(converted) if converted.is_integer() else converted


def integer(value: Any) -> int | None:
    return None if value is None else int(value)


def js_safe_numbers(value: Any) -> Any:
    """數值正規化：令 Python 序列化文字 == JS `JSON.stringify(JSON.parse(text))`。

    validate.ts 嘅 `publicSnapshotContentSha256` 係 parse 完再 restringify 先 hash，
    所以任何「Python 寫法 ≠ JS 重寫法」嘅數值都會令 contentSha256 對唔上。
    實測出現過嘅只有整數值 float（Python `0.0` / JS `0`），呢度轉 int；
    指數寫法（Python `1e-05` / JS `0.00001`）一出現即 fail-closed，唔准靜靜出街。
    """

    if isinstance(value, float):
        if not isfinite(value):
            raise SnapshotExportError(f"snapshot contains non-finite number: {value!r}")
        if value.is_integer() and abs(value) < 2**53:
            return int(value)
        if "e" in repr(value):
            raise SnapshotExportError(
                f"float {value!r} serializes with exponent notation; Python/JS texts diverge"
            )
        return value
    if isinstance(value, dict):
        return {key: js_safe_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [js_safe_numbers(item) for item in value]
    return value


def stable_json(value: Any) -> bytes:
    return json.dumps(
        js_safe_numbers(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def snapshot_content_sha256(snapshot: Mapping[str, Any]) -> str:
    """`generation.contentSha256` 嘅唯一計法：自身欄位當空字串再 hash 全份。

    對面 `packages/market-data/src/validate.ts` 嘅 `publicSnapshotContentSha256`
    會用同一條規則重算再比對，唔啱就 reject 成份 snapshot。所以任何改完 snapshot
    內容再寫返落磁碟嘅腳本（例如 ensure_std_card_images.py 換卡圖 block）都必須
    經呢度重算——改咗內容但唔重算，出嚟嘅 snapshot 喺 validator 眼中係壞檔。
    數值經 `js_safe_numbers()` 正規化（`stable_json` 入面），寫檔嗰邊 `atomic_json`
    用同一份正規化，hash 同磁碟文字唔會分家。
    """

    payload = dict(snapshot)
    payload["generation"] = {**snapshot.get("generation", {}), "contentSha256": ""}
    return hashlib.sha256(stable_json(payload)).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                js_safe_numbers(value), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def metric(value: float | int | None, status: str, as_of: str | None, **extra: Any) -> dict[str, Any]:
    if status not in {"ready", "stale", "accumulating", "unavailable"}:
        status = "unavailable"
    if status in {"accumulating", "unavailable"}:
        value = None
    return {"value": value, "status": status, "asOf": as_of if value is not None else None, **extra}


def load_presentation(path: Path) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise SnapshotExportError("presentation snapshot is invalid")
    cards = document.get("top100", []) + document.get("watchlist", [])
    indexed = {
        str(card["id"]): card
        for card in cards
        if isinstance(card, Mapping) and isinstance(card.get("id"), str)
    }
    if len(indexed) != len(cards):
        raise SnapshotExportError("presentation snapshot has duplicate or invalid card IDs")
    return dict(document), indexed


class PublicImages(NamedTuple):
    """QC-approved public artwork, indexed for both lookup directions."""

    by_public_id: dict[str, dict[str, Any]]
    allowed_sha: set[str]


def public_derivatives_ready(asset_dir: Path, sha: str) -> bool:
    """Require both responsive WEBP derivatives before an image can publish."""

    for suffix, expected_size in (("200", (200, 280)), ("600", (429, 600))):
        path = asset_dir / f"{sha}_{suffix}.webp"
        if not path.is_file():
            return False
        try:
            with Image.open(path) as image:
                image.load()
                if image.format != "WEBP" or image.size != expected_size:
                    return False
        except (OSError, ValueError):
            return False
    return True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_public_images(
    qc_path: Path = IMAGE_QC_PATH,
    asset_dir: Path = PUBLIC_ASSET_DIR,
    connection: Any | None = None,
    release_image_receipts: Mapping[str, Mapping[str, Any]] | None = None,
) -> PublicImages:
    """Index raw-front artwork that already passed public QC and exists on disk.

    Prefer DB (variant → current opaque_id) so opaque_id rehashes do not orphan
    images. Manifest is a fallback for offline/demo paths.
    """

    by_public_id: dict[str, dict[str, Any]] = {}
    allowed_sha: set[str] = set()

    def add_image(public_id: str, sha: str, width: int, height: int, qc_at: str | None) -> None:
        if not public_id or public_id in by_public_id:
            return
        if len(sha) != 64 or not width or not height:
            return
        if not (asset_dir / f"{sha}.webp").is_file():
            return
        if not public_derivatives_ready(asset_dir, sha):
            return
        allowed_sha.add(sha)
        by_public_id[public_id] = {
            "height": height,
            "kind": "raw_front",
            "qcAt": qc_at,
            "sha256": sha,
            "src": f"/market-assets/{sha}.webp",
            "variants": {
                size: f"/market-assets/{sha}_{size}.webp"
                for size in ("200", "600")
                if (asset_dir / f"{sha}_{size}.webp").is_file()
            },
            "width": width,
        }

    if connection is not None:
        # Only current CAS-bound human review may enter production.
        rows = fetchall(
            connection,
            """
            SELECT v.opaque_id, a.content_sha256, a.width_px, a.height_px,
                   a.source_version_sha256, q.checked_at, p.source_path, v.tcg_code
            FROM catalog_variant v
            JOIN market_image_asset a ON a.variant_id = v.id AND a.image_kind = 'raw_front'
            JOIN market_image_qc q ON q.image_asset_id = a.id
              AND q.public_allowed = 1
              AND q.qc_version = 'human-review-v2'
              AND q.semantic_match_status = 'human_or_vision_confirmed'
              AND q.card_number_match = 1 AND q.language_match = 1
              AND q.tcg_match = 1 AND q.raw_front_confirmed = 1
            JOIN market_image_review_approval b
              ON b.image_asset_id = a.id
              AND b.variant_id = a.variant_id
              AND b.content_sha256 = a.content_sha256
              AND b.source_version_sha256 = a.source_version_sha256
              AND b.binding_sha256 = SHA2(CONCAT_WS('|',a.id,a.variant_id,
                  a.content_sha256,a.source_version_sha256,
                  b.canonical_printing_sha256,b.expected_language),256)
            JOIN catalog_printing_identity pi
              ON pi.variant_id = v.id
              AND pi.identity_status = 'canonical'
              AND pi.canonical_printing_sha256 = b.canonical_printing_sha256
              AND pi.card_language = b.expected_language
              AND v.card_language = b.expected_language
            JOIN market_image_source_pointer p
              ON p.variant_id = v.id AND p.image_kind = 'raw_front'
              AND p.source_version_sha256 = a.source_version_sha256
              AND p.public_allowed = 1
            LEFT JOIN market_image_rejection_registry rejected
              ON rejected.variant_id = a.variant_id
              AND rejected.content_sha256 = a.content_sha256
            WHERE rejected.variant_id IS NULL
            ORDER BY v.id, q.checked_at DESC, a.id DESC
            """,
        )
        seen_opaque: set[str] = set()
        for row in rows:
            oid = str(row["opaque_id"] or "")
            if not oid or oid in seen_opaque:
                continue
            seen_opaque.add(oid)
            sha = str(row["content_sha256"] or "")
            if classify_content_sha256(sha).get("status") == "reject":
                continue
            source = classify_source(
                str(row.get("source_path") or ""),
                tcg_code=str(row.get("tcg_code") or ""),
                width_px=row.get("width_px"),
                height_px=row.get("height_px"),
            )
            if source.get("status") == "reject":
                continue
            master = asset_dir / f"{sha}.webp"
            if not master.is_file() or inspect_path(master).get("status") != "passed":
                continue
            width = integer(row.get("width_px")) or 429
            height = integer(row.get("height_px")) or 600
            qc_at = iso(row["checked_at"]) if row.get("checked_at") else None
            add_image(oid, sha, width, height, qc_at)

    # Production always supplies canonical MySQL. An empty DB result is a hard
    # no-image state, never permission to revive an old manifest entry.
    if connection is not None and release_image_receipts is not None:
        if not release_image_receipts:
            return PublicImages({}, set())
        public_ids = sorted(release_image_receipts)
        marks = ",".join(["%s"] * len(public_ids))
        rows = fetchall(
            connection,
            f"""
            SELECT v.opaque_id, a.id AS asset_id, a.content_sha256,
                   a.width_px, a.height_px
            FROM catalog_variant AS v
            JOIN market_image_asset AS a
              ON a.variant_id=v.id AND a.image_kind='raw_front'
            WHERE v.opaque_id IN ({marks})
            ORDER BY v.opaque_id, a.id
            """,
            public_ids,
        )
        for row in rows:
            oid = str(row.get("opaque_id") or "")
            receipt = release_image_receipts.get(oid)
            chosen = receipt.get("chosen") if isinstance(receipt, Mapping) else None
            if not isinstance(chosen, Mapping):
                continue
            chosen_asset_id = chosen.get("assetId")
            chosen_sha = str(chosen.get("contentSha256") or "").casefold()
            if row.get("asset_id") != chosen_asset_id or str(row.get("content_sha256") or "").casefold() != chosen_sha:
                continue
            if not SHA256_RE.fullmatch(chosen_sha):
                continue
            master = asset_dir / f"{chosen_sha}.webp"
            if (
                not master.is_file()
                or sha256_file(master) != chosen_sha
                or inspect_path(master).get("status") != "passed"
            ):
                continue
            width = integer(row.get("width_px")) or 429
            height = integer(row.get("height_px")) or 600
            add_image(oid, chosen_sha, width, height, None)
        return PublicImages(by_public_id, allowed_sha)

    if connection is not None:
        return PublicImages(by_public_id, allowed_sha)

    try:
        document = json.loads(qc_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return PublicImages({}, set())
    for record in document.get("records", []) if isinstance(document, Mapping) else []:
        if not isinstance(record, Mapping) or not record.get("publicAllowed"):
            continue
        if str(record.get("imageKind") or "") != "raw_front":
            continue
        sha = str(record.get("contentSha256") or "")
        if classify_content_sha256(sha).get("status") == "reject":
            continue
        master = asset_dir / f"{sha}.webp"
        if not master.is_file() or inspect_path(master).get("status") != "passed":
            continue
        width = integer(record.get("width"))
        height = integer(record.get("height"))
        if not width or not height:
            continue
        public_id = str(record.get("publicId") or "")
        add_image(
            public_id,
            sha,
            width,
            height,
            str(record["qcAt"]) if record.get("qcAt") else None,
        )
    return PublicImages(by_public_id, allowed_sha)


# 7-part: tcg | language | set | collector | edition | parallel | finish
PrintingKey = tuple[str, str, str, str, str, str, str]


def printing_key(
    tcg: Any,
    set_name: Any,
    collector_number: Any,
    edition_code: Any,
    parallel_code: Any,
    finish_code: Any,
    language: Any = "",
) -> PrintingKey:
    """Return the canonical seven-part printing key (language inclusive)."""

    from card_identity import printing_key7

    return printing_key7(
        tcg, language, set_name, collector_number, edition_code, parallel_code, finish_code
    )


def printing_key_sha256(key: PrintingKey) -> str:
    return hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()


def public_printing_identity(
    row: Mapping[str, Any], *, allow_provisional: bool = False
) -> dict[str, str] | None:
    """Validate and expose the DB-owned printing tuple for a public card.

    Empty edition/parallel/finish values are missing evidence, not defaults.
    Only a canonical row whose base identity agrees with ``catalog_variant``
    and whose stored hash recomputes may enter the presentation resolver.

    Hash is 7-part (includes card_language) after rehash_identity_language.
    """

    lang = row.get("printing_card_language") or row.get("card_language") or ""
    values = (
        row.get("printing_tcg_code"),
        row.get("printing_set_name"),
        row.get("printing_collector_number"),
        row.get("edition_code"),
        row.get("parallel_code"),
        row.get("finish_code"),
        lang,
    )
    key = printing_key(
        values[0], values[1], values[2], values[3], values[4], values[5], language=values[6]
    )
    printing_status = str(row.get("printing_identity_status") or "").strip().casefold()
    allowed_statuses = {"canonical", "candidate"} if allow_provisional else {"canonical"}
    if printing_status not in allowed_statuses:
        return None
    # Every component is evidence-bearing. Missing language, edition, parallel,
    # or finish must never be interpreted as a default printing.
    if any(not part for part in key):
        return None
    base_key = printing_key(
        row.get("tcg_code"),
        row.get("set_name"),
        row.get("collector_number"),
        "",
        "",
        "",
        language=row.get("card_language") or "",
    )
    # tcg, language, set, collector must agree between printing row and variant
    if key[:4] != base_key[:4]:
        return None
    canonical_hash = str(row.get("canonical_printing_sha256") or "").strip().casefold()
    evidence_hash = str(row.get("printing_evidence_sha256") or "").strip().casefold()
    if (
        not SHA256_RE.fullmatch(canonical_hash)
        or canonical_hash != printing_key_sha256(key)
        or not SHA256_RE.fullmatch(evidence_hash)
    ):
        return None
    return {
        "setName": str(values[1]).strip(),
        "collectorNumber": str(values[2]).strip(),
        "editionCode": str(values[3]).strip(),
        "parallelCode": str(values[4]).strip(),
        "finishCode": str(values[5]).strip(),
        "cardLanguage": str(lang).strip() or None,
        "canonicalPrintingSha256": canonical_hash,
        "evidenceSha256": evidence_hash,
    }


def row_printing_key(
    row: Mapping[str, Any], *, allow_provisional: bool = False
) -> PrintingKey | None:
    identity = public_printing_identity(row, allow_provisional=allow_provisional)
    if identity is None:
        return None
    return printing_key(
        row.get("printing_tcg_code"),
        identity["setName"],
        identity["collectorNumber"],
        identity["editionCode"],
        identity["parallelCode"],
        identity["finishCode"],
        language=identity.get("cardLanguage") or row.get("card_language") or "",
    )


def card_printing_key(card: Mapping[str, Any]) -> PrintingKey | None:
    identity = card.get("printingIdentity")
    if not isinstance(identity, Mapping):
        return None
    key = printing_key(
        card.get("tcg"),
        identity.get("setName"),
        identity.get("collectorNumber"),
        identity.get("editionCode"),
        identity.get("parallelCode"),
        identity.get("finishCode"),
        language=identity.get("cardLanguage") or card.get("cardLanguage") or "",
    )
    canonical_hash = str(identity.get("canonicalPrintingSha256") or "").strip().casefold()
    evidence_hash = str(identity.get("evidenceSha256") or "").strip().casefold()
    if (
        any(not part for part in key)
        or not SHA256_RE.fullmatch(canonical_hash)
        or canonical_hash != printing_key_sha256(key)
        or not SHA256_RE.fullmatch(evidence_hash)
    ):
        return None
    return key


def catalog_printing_key_counts(
    connection: Any, *, allow_provisional: bool = False
) -> Counter:
    rows = fetchall(
        connection,
        """
        SELECT v.tcg_code,v.set_name,v.collector_number,v.card_language,
               pi.tcg_code AS printing_tcg_code,
               pi.card_language AS printing_card_language,
               pi.set_name AS printing_set_name,
               pi.collector_number AS printing_collector_number,
               pi.edition_code,pi.parallel_code,pi.finish_code,
               pi.canonical_printing_sha256,
               pi.identity_status AS printing_identity_status,
               pi.evidence_sha256 AS printing_evidence_sha256
        FROM catalog_variant v
        LEFT JOIN catalog_printing_identity pi ON pi.variant_id=v.id
        """,
    )
    counts: Counter = Counter()
    for row in rows:
        key = row_printing_key(row, allow_provisional=allow_provisional)
        if key is not None:
            counts[key] += 1
    return counts


def base_identity_complete(row: Mapping[str, Any]) -> bool:
    name = str(row.get("canonical_name") or "").strip()
    set_name = str(row.get("set_name") or "").strip()
    tcg = str(row.get("tcg_code") or "").strip()
    language = str(row.get("card_language") or "").strip()
    collector = normalize_collector(row.get("collector_number"), None, set_name)
    return bool(
        OPAQUE_PUBLIC_ID_RE.fullmatch(str(row.get("opaque_id") or ""))
        and
        name
        and set_name
        and tcg
        and language in {"en", "ja", "ko", "zhCN", "zhTW"}
        and str(row.get("identity_status") or "") == "confirmed"
        and collector.complete
    )


def presentation_from_identity(
    row: Mapping[str, Any], image: Mapping[str, Any], *, allow_provisional: bool = False
) -> dict[str, Any] | None:
    """Build a presentation entry for a ranked card the pack has never seen.

    Returns None when the canonical catalog cannot supply a complete identity;
    callers must skip such cards instead of filling the gaps with placeholders.
    """

    name = str(row.get("canonical_name") or "").strip()
    set_name = str(row.get("set_name") or "").strip()
    tcg = str(row.get("tcg_code") or "").strip().lower()
    if not base_identity_complete(row):
        return None
    printing_identity = public_printing_identity(row, allow_provisional=allow_provisional)
    if printing_identity is None:
        return None
    collector = normalize_collector(row.get("collector_number"), None, set_name)
    if not collector.complete:
        return None
    localized = {locale: (name if locale == "en" else None) for locale in LOCALES}
    card_language = str(row.get("card_language") or "").strip() or None
    if card_language not in {None, "en", "ja", "ko", "zhCN", "zhTW"}:
        card_language = None
    return {
        "cardLanguage": card_language,
        "collectorNumber": {
            "complete": True,
            "display": collector.display,
            "normalized": collector.normalized,
        },
        "graderPopulations": {},
        "historyDaily": [],
        "id": str(row["opaque_id"]),
        "identityStatus": "confirmed",
        "image": {**image, "alt": dict(localized)},
        "marketCap": metric(None, "unavailable", None),
        "names": dict(localized),
        "populationPsa10": metric(None, "unavailable", None, estimated=False),
        "printingIdentity": printing_identity,
        "pricePsa10": metric(None, "unavailable", None),
        "rank": 0,
        "sets": {locale: (set_name if locale == "en" else None) for locale in LOCALES},
        "stories": {locale: None for locale in LOCALES},
        "tcg": tcg,
        "windows": {window: {} for window in WINDOWS},
    }


def resolve_presentation_entries(
    rows: Iterable[Mapping[str, Any]],
    cards_by_id: Mapping[str, Mapping[str, Any]],
    images: PublicImages,
    catalog_key_counts: Mapping[PrintingKey, int],
    *,
    allow_provisional: bool = False,
) -> tuple[dict[str, Mapping[str, Any]], list[tuple[str, str]], Counter]:
    """Pair every ranked row with a presentation entry it may legally publish.

    Cards the canonical database ranks but the pack never carried used to abort
    the whole export.  They are now resolved card by card, and only the ones
    that still cannot show real artwork and a complete identity are skipped.
    """

    pack_key_counts: Counter = Counter()
    pack_by_key: dict[PrintingKey, Mapping[str, Any]] = {}
    for card in cards_by_id.values():
        key = card_printing_key(card)
        if key is None:
            continue
        pack_key_counts[key] += 1
        pack_by_key.setdefault(key, card)

    def image_usable(entry: Mapping[str, Any]) -> bool:
        return str((entry.get("image") or {}).get("sha256") or "") in images.allowed_sha

    def refreshed(entry: Mapping[str, Any], row: Mapping[str, Any]) -> Mapping[str, Any]:
        """Bind a reused presentation row to the current canonical identity."""

        printing_identity = public_printing_identity(row, allow_provisional=allow_provisional)
        if printing_identity is None:
            raise AssertionError("refreshed() received an invalid printing identity")
        current = entry.get("collectorNumber") or {}
        collector = normalize_collector(
            row.get("collector_number"), None, row.get("set_name")
        )
        sets = entry.get("sets") if isinstance(entry.get("sets"), Mapping) else {}
        card_language = str(row.get("card_language") or "").strip() or None
        if card_language not in {None, "en", "ja", "ko", "zhCN", "zhTW"}:
            card_language = None
        return {
            **entry,
            "id": str(row["opaque_id"]),
            "tcg": str(row.get("tcg_code") or "").strip().lower(),
            "cardLanguage": card_language,
            "identityStatus": "confirmed",
            "printingIdentity": printing_identity,
            "collectorNumber": {
                **current,
                "display": collector.display,
                "normalized": collector.normalized,
                "complete": collector.complete,
            },
            "sets": {**sets, "en": str(row.get("set_name") or "").strip()},
        }

    resolved: dict[str, Mapping[str, Any]] = {}
    skipped: list[tuple[str, str]] = []
    tiers: Counter = Counter()
    claimed: set[str] = set()
    for row in rows:
        opaque_id = str(row["opaque_id"])
        if not OPAQUE_PUBLIC_ID_RE.fullmatch(opaque_id):
            skipped.append((opaque_id, "opaque_id_invalid"))
            continue
        if not base_identity_complete(row):
            skipped.append((opaque_id, "identity_incomplete"))
            continue
        key = row_printing_key(row, allow_provisional=allow_provisional)
        if key is None:
            skipped.append((opaque_id, "printing_identity_incomplete"))
            continue
        entry = cards_by_id.get(opaque_id)
        if entry is not None and opaque_id not in claimed and image_usable(entry):
            claimed.add(opaque_id)
            resolved[opaque_id] = refreshed(entry, row)
            tiers["pack_id"] += 1
            continue
        candidate = pack_by_key.get(key)
        unique = pack_key_counts.get(key) == 1 and catalog_key_counts.get(key, 0) == 1
        if candidate is not None and unique and str(candidate["id"]) not in claimed and image_usable(candidate):
            claimed.add(str(candidate["id"]))
            resolved[opaque_id] = refreshed(candidate, row)
            tiers["relinked_printing_key"] += 1
            continue
        image = images.by_public_id.get(opaque_id)
        if image is None:
            collides = candidate is not None and (
                pack_key_counts.get(key, 0) > 1 or catalog_key_counts.get(key, 0) > 1
            )
            skipped.append((opaque_id, "ambiguous_printing_key" if collides else "image_unavailable"))
            continue
        rebuilt = presentation_from_identity(
            row, image, allow_provisional=allow_provisional
        )
        if rebuilt is None:
            skipped.append((opaque_id, "identity_incomplete"))
            continue
        resolved[opaque_id] = rebuilt
        tiers["new_from_catalog"] += 1
    return resolved, skipped, tiers


def resolution_summary(ranked: int, tiers: Mapping[str, int], skipped: list[tuple[str, str]]) -> str:
    reasons = Counter(reason for _, reason in skipped)
    published = sum(tiers.values())
    return (
        f"canonical export: {ranked} ranked, {published} published "
        f"(pack_id={tiers.get('pack_id', 0)}, relinked_printing_key={tiers.get('relinked_printing_key', 0)}, "
        f"new_from_catalog={tiers.get('new_from_catalog', 0)}), {len(skipped)} skipped"
        + (f" ({', '.join(f'{reason}={count}' for reason, count in sorted(reasons.items()))})" if skipped else "")
    )


def fetchall(connection: Any, query: str, args: Iterable[Any] = ()) -> list[Mapping[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(args))
        return list(cursor.fetchall())


def fetchone(connection: Any, query: str, args: Iterable[Any] = ()) -> Mapping[str, Any] | None:
    rows = fetchall(connection, query, args)
    return rows[0] if rows else None


def latest_generation(connection: Any, min_constituents: int) -> Mapping[str, Any]:
    rows = fetchall(
        connection,
        """
        SELECT s.id,s.evaluation_id,s.effective_at,s.effective_date,
               s.constituent_count,s.snapshot_sha256
        FROM market_index_snapshot s
        JOIN market_alert_evaluation e ON e.id=s.evaluation_id
        WHERE s.index_code='tcg-combined' AND e.publish_gate_status='passed'
        ORDER BY s.effective_date DESC,s.evaluation_id DESC,s.id DESC
        """,
    )
    for row in rows:
        count = int(fetchone(
            connection,
            "SELECT COUNT(*) AS n FROM market_index_constituent WHERE index_snapshot_id=%s",
            (row["id"],),
        )["n"])
        if count >= min_constituents:
            return row
    raise SnapshotExportError(
        f"no canonical combined ranking snapshot covers at least {min_constituents} constituents"
    )


def staging_generation(
    connection: Any,
    evaluation_id: int,
    min_constituents: int,
) -> Mapping[str, Any]:
    row = fetchone(
        connection,
        """
        SELECT s.id,s.evaluation_id,s.effective_at,s.effective_date,
               s.constituent_count,s.snapshot_sha256
        FROM market_index_snapshot s
        WHERE s.index_code='tcg-combined' AND s.evaluation_id=%s
        ORDER BY s.id DESC LIMIT 1
        """,
        (evaluation_id,),
    )
    if not row:
        raise SnapshotExportError(f"staging evaluation {evaluation_id} has no combined snapshot")
    count = int(fetchone(
        connection,
        "SELECT COUNT(*) AS n FROM market_index_constituent WHERE index_snapshot_id=%s",
        (row["id"],),
    )["n"])
    if count < min_constituents:
        raise SnapshotExportError(
            f"staging evaluation {evaluation_id} covers only {count} constituents"
        )
    return row


BOARD_UNION_INDEX_CODES = ("one-piece", "pokemon")


def board_member_variant_ids(
    connection: Any,
    effective_date: Any,
    evaluation_id: int | None = None,
) -> set[int]:
    """同日（或最近一日）分榜成員 variant_id，top300_boards 聯集 view 用。"""
    members: set[int] = set()
    for index_code in BOARD_UNION_INDEX_CODES:
        if evaluation_id is not None:
            board = fetchone(
                connection,
                """
                SELECT id FROM market_index_snapshot
                WHERE index_code=%s AND evaluation_id=%s
                ORDER BY id DESC LIMIT 1
                """,
                (index_code, evaluation_id),
            )
        else:
            board = fetchone(
                connection,
                """
                SELECT id FROM market_index_snapshot
                WHERE index_code=%s AND effective_date<=%s
                ORDER BY effective_date DESC,id DESC LIMIT 1
                """,
                (index_code, effective_date),
            )
        if not board:
            continue
        rows = fetchall(
            connection,
            """
            SELECT variant_id FROM market_index_constituent
            WHERE index_snapshot_id=%s AND rank_position<=100
            """,
            (int(board["id"]),),
        )
        members.update(int(row["variant_id"]) for row in rows)
    return members


def generation_evaluation_id(connection: Any, generation: Mapping[str, Any]) -> int:
    """Use the exact evaluation bound to a revision; query only for legacy rows."""

    if generation.get("evaluation_id") is not None:
        return int(generation["evaluation_id"])
    evaluation = fetchone(
        connection,
        """
        SELECT id FROM market_alert_evaluation
        WHERE index_code='tcg-combined' AND effective_date<=%s
        ORDER BY effective_date DESC,id DESC LIMIT 1
        """,
        (generation["effective_date"],),
    )
    return int(evaluation["id"]) if evaluation else -1


def market_rows(
    connection: Any,
    generation: Mapping[str, Any],
    *,
    required_count: int | None,
    include_board_extras: bool = False,
) -> list[dict[str, Any]]:
    evaluation_id = generation_evaluation_id(connection, generation)
    row_query = """
        SELECT v.id AS variant_id,v.opaque_id,v.identity_status,
               v.canonical_name,v.set_name,v.collector_number,v.tcg_code,
               v.card_language AS card_language,
               pi.tcg_code AS printing_tcg_code,
               pi.card_language AS printing_card_language,
               pi.set_name AS printing_set_name,
               pi.collector_number AS printing_collector_number,
               pi.edition_code,pi.parallel_code,pi.finish_code,
               pi.canonical_printing_sha256,
               pi.identity_status AS printing_identity_status,
               pi.evidence_sha256 AS printing_evidence_sha256,
               c.rank_position AS rank_position,c.reference_price_usd,c.psa10_population,
               c.market_cap_usd,c.metric_status,
               d.change_1d_pct,d.change_7d_pct,d.change_30d_pct
        FROM market_index_constituent c
        JOIN catalog_variant v ON v.id=c.variant_id
        LEFT JOIN catalog_printing_identity pi ON pi.variant_id=v.id
        LEFT JOIN market_candidate_daily_snapshot d
          ON d.variant_id=c.variant_id AND d.evaluation_id=%s
        WHERE c.index_snapshot_id=%s AND c.rank_position{rank_cond}
        ORDER BY c.rank_position
    """
    if required_count is None:
        top = fetchall(
            connection,
            row_query.format(rank_cond=">=1"),
            (evaluation_id, generation["id"]),
        )
    else:
        top = fetchall(
            connection,
            row_query.format(rank_cond="<=%s"),
            (evaluation_id, generation["id"], required_count),
        )
    if required_count is not None and len(top) != required_count:
        raise SnapshotExportError(
            f"canonical combined ranking has {len(top)} rows for requested Top {required_count} view"
        )
    if [int(row["rank_position"]) for row in top] != list(range(1, len(top) + 1)):
        raise SnapshotExportError("canonical combined ranking has non-contiguous ranks")
    if not include_board_extras:
        return [dict(row) for row in top]
    # 聯集尾巴：combined 榜 required_count 名以外、但屬於同日分榜嘅卡，
    # 照 combined rank 排喺 core 後面。出版前 build_snapshot 會重排 1..N，
    # 所以呢度唔使（亦唔應該）連續。
    members = board_member_variant_ids(
        connection,
        generation["effective_date"],
        evaluation_id if evaluation_id >= 0 else None,
    )
    if not members:
        return [dict(row) for row in top]
    placeholders = ",".join(["%s"] * len(members))
    extras = fetchall(
        connection,
        row_query.format(rank_cond=f">%s AND c.variant_id IN ({placeholders})"),
        (evaluation_id, generation["id"], required_count, *sorted(members)),
    )
    return [dict(row) for row in [*top, *extras]]


SALES_WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30}


def ebay_psa10_daily_rows(
    connection: Any,
    variant_ids: list[int],
    earliest: date | None = None,
    anchor: date | None = None,
) -> list[Mapping[str, Any]]:
    """eBay PSA 10 日成交，由逐筆表 market_sale_observation 即場滾出嚟。

    點解唔讀 market_daily_sales_aggregate 嘅 ebay 行：嗰批係 g10_ebay_ingest
    將全部 grade（PSA10+PSA9+CGC10+BGS10+BGS BL）滾埋一齊嘅總量，日表冇 grade
    欄分唔返開，直接用會令 PSA9/BGS/CGC 成交混入 PSA10 序列。逐筆表有
    grader_code/grade_label，先篩得出純 PSA 10（8,558 行 / 505 variant /
    2026-04-25 起，2026-07-27 實測）。
    口徑照抄 ingest 嘅日 rollup（daily_sales_rows）：sales_count = 筆數
    （quantity 全部係 1，實測），sales_value_usd = SUM(unit_price_usd)。
    coverage_status 逐筆行全部 'partial'；用 MAX() 係為咗第日混入其他值時
    揀字母序最大嗰個（'quarantined' > 'partial'），出錯方向係 fail-closed。
    """
    if not variant_ids:
        return []
    placeholders = ",".join(["%s"] * len(variant_ids))
    params: list[Any] = [*variant_ids]
    date_cond = ""
    if earliest is not None and anchor is not None:
        date_cond = "AND DATE(sold_at) BETWEEN %s AND %s"
        params.extend([earliest, anchor])
    return fetchall(
        connection,
        f"""
        SELECT variant_id, DATE(sold_at) AS observed_date,
               COUNT(*) AS sales_count, SUM(unit_price_usd) AS sales_value_usd,
               MAX(coverage_status) AS coverage_status
        FROM market_sale_observation
        WHERE variant_id IN ({placeholders})
          AND source_code='ebay' AND grader_code='psa' AND grade_label='10'
          AND sold_at IS NOT NULL {date_cond}
        GROUP BY variant_id, DATE(sold_at)
        """,
        params,
    )


def approved_psa10_daily_rows(
    connection: Any,
    variant_ids: list[int],
    earliest: date | None = None,
    anchor: date | None = None,
) -> list[Mapping[str, Any]]:
    """Roll up only canonical-QC-equivalent, exact-bound PSA10 observations.

    The daily aggregate cannot prove grade, exact identity, or fingerprint, so
    it is deliberately not merged here; SNK and eBay/PriceCharting sales share
    this one transaction-level path.
    """
    if not variant_ids:
        return []
    placeholders = ",".join(["%s"] * len(variant_ids))
    params: list[Any] = [*variant_ids]
    date_cond = ""
    if earliest is not None and anchor is not None:
        date_cond = "AND DATE(sale.sold_at) BETWEEN %s AND %s"
        params.extend([earliest, anchor])
    rows = fetchall(
        connection,
        f"""
        SELECT sale.variant_id, DATE(sale.sold_at) AS observed_date,
               sale.source_code, sale.external_entity_id,
               sale.transaction_fingerprint, sale.grader_code, sale.grade_label,
               sale.timestamp_quality, sale.unit_price_usd, sale.quantity,
               sale.transaction_value_usd, sale.coverage_status,
               sale.source_payload_sha256, 1 AS identity_confirmed
        FROM market_sale_observation AS sale
        WHERE sale.variant_id IN ({placeholders})
          AND sale.sold_at IS NOT NULL {date_cond}
          AND UPPER(sale.grader_code)='PSA' AND UPPER(sale.grade_label) IN ('10', 'PSA 10', 'PSA10')
          AND sale.quantity=1 AND sale.unit_price_usd > 0
          AND sale.transaction_value_usd = sale.unit_price_usd
          AND sale.timestamp_quality IN ('exact', 'date', 'timestamp', 'exact_date', 'relative_resolved', 'relative_subday')
          AND sale.coverage_status IN ('partial', 'complete', 'certified')
          AND LOWER(sale.source_payload_sha256) REGEXP '^[0-9a-f]{{64}}$'
          AND (
            (sale.source_code IN ('snk_psa10', 'snk', 'snkrdunk', 'snk_grade')
             AND sale.external_entity_id REGEXP '^[0-9]+$'
             AND CAST(sale.external_entity_id AS UNSIGNED) > 0
             AND EXISTS (
               SELECT 1 FROM catalog_source_identity AS identity
               LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=identity.variant_id
               WHERE COALESCE(alias.canonical_variant_id, identity.variant_id)=sale.variant_id
                 AND identity.source_code IN ('snk', 'snkrdunk') AND identity.match_status='exact'
                 AND identity.external_entity_id REGEXP '^[0-9]+$'
                 AND CAST(identity.external_entity_id AS UNSIGNED)=CAST(sale.external_entity_id AS UNSIGNED)
             ))
            OR (sale.source_code='ebay' AND (
              (sale.external_entity_id REGEXP '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
               AND EXISTS (
                 SELECT 1 FROM catalog_source_identity AS identity
                 LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=identity.variant_id
                 WHERE COALESCE(alias.canonical_variant_id, identity.variant_id)=sale.variant_id
                   AND identity.source_code='ebay' AND identity.match_status='exact'
                   AND LOWER(identity.external_entity_id)=LOWER(sale.external_entity_id)
               ))
              OR (sale.external_entity_id REGEXP '^[0-9]+$' AND CAST(sale.external_entity_id AS UNSIGNED)>0
               AND EXISTS (
                 SELECT 1 FROM catalog_source_identity AS identity
                 LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=identity.variant_id
                 WHERE COALESCE(alias.canonical_variant_id, identity.variant_id)=sale.variant_id
                   AND identity.source_code IN ('snk', 'snkrdunk') AND identity.match_status='exact'
                   AND identity.external_entity_id REGEXP '^[0-9]+$'
                   AND CAST(identity.external_entity_id AS UNSIGNED)=CAST(sale.external_entity_id AS UNSIGNED)
               ))
              OR (sale.external_entity_id REGEXP '^pc:[0-9]+$'
               AND EXISTS (
                 SELECT 1 FROM catalog_source_identity AS identity
                 LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=identity.variant_id
                 WHERE COALESCE(alias.canonical_variant_id, identity.variant_id)=sale.variant_id
                   AND identity.source_code='pricecharting' AND identity.match_status='exact'
                   AND identity.external_entity_id REGEXP '^[0-9]+$'
                   AND CAST(identity.external_entity_id AS UNSIGNED)=CAST(SUBSTRING(sale.external_entity_id, 4) AS UNSIGNED)
               ))
            ))
          )
        """,
        params,
    )
    accepted: list[Mapping[str, Any]] = []
    seen_fingerprints: set[tuple[int, str, str, str]] = set()
    for row in rows:
        fingerprint = str(row.get("transaction_fingerprint") or "").strip()
        key = (int(row["variant_id"]), str(row.get("source_code") or "").casefold(), str(row.get("external_entity_id") or "").casefold(), fingerprint)
        if (
            not row.get("identity_confirmed") or not fingerprint or key in seen_fingerprints
            or str(row.get("grader_code") or "").upper() != "PSA"
            or str(row.get("grade_label") or "").upper() not in {"10", "PSA 10", "PSA10"}
            or int(row.get("quantity") or 0) != 1
            or float(row.get("unit_price_usd") or 0) <= 0
            or float(row.get("unit_price_usd") or 0) != float(row.get("transaction_value_usd") or 0)
        ):
            continue
        seen_fingerprints.add(key)
        accepted.append(row)
    daily: dict[tuple[int, date], dict[str, Any]] = {}
    for row in accepted:
        observed = row.get("observed_date")
        if isinstance(observed, datetime):
            observed = observed.date()
        if not isinstance(observed, date):
            continue
        bucket = daily.setdefault(
            (int(row["variant_id"]), observed),
            {"variant_id": int(row["variant_id"]), "observed_date": observed, "sales_count": 0, "sales_value_usd": 0.0, "coverage_status": "partial"},
        )
        bucket["sales_count"] += 1
        bucket["sales_value_usd"] += float(row["unit_price_usd"])
    return list(daily.values())


def latest_sales(
    connection: Any, variant_ids: list[int], anchor: date | datetime
) -> dict[tuple[int, str], Mapping[str, Any]]:
    """Aggregate approved canonical observations against the generation date."""
    if not variant_ids:
        return {}
    if isinstance(anchor, datetime):
        anchor = anchor.date()
    placeholders = ",".join(["%s"] * len(variant_ids))
    # 兩倍窗口長度：除咗當前窗口，仲要罩住緊貼前面嗰個同長度窗口，
    # 先至砌得出真嘅成交額環比（見下面 prev_* 欄）。
    earliest = anchor - timedelta(days=max(SALES_WINDOW_DAYS.values()) * 2 - 1)
    rows = approved_psa10_daily_rows(connection, variant_ids, earliest, anchor)
    result: dict[tuple[int, str], Mapping[str, Any]] = {}
    # 桶內係純累加（交換律），兩源行 concat 就得，唔使 pre-merge 同日行。
    # coverage_status/window_end_at 跟窗口內最新一日行走；同日兩源都係
    # 'partial'，邊個 last-wins 都一樣。
    for row in rows:
        variant_id = int(row["variant_id"])
        observed = row["observed_date"]
        for window, span in SALES_WINDOW_DAYS.items():
            if observed <= anchor - timedelta(days=span * 2):
                continue
            bucket = result.setdefault(
                (variant_id, window),
                {
                    "sales_count": 0,
                    "sales_value_usd": 0.0,
                    "coverage_status": "unavailable",
                    "window_end_at": None,
                    # 前一個同長度窗口 (anchor-2*span, anchor-span]，淨係用嚟計環比。
                    "prev_sales_count": 0,
                    "prev_sales_value_usd": 0.0,
                    "prev_days": 0,
                },
            )
            if observed <= anchor - timedelta(days=span):
                bucket["prev_sales_count"] += int(row["sales_count"] or 0)
                bucket["prev_sales_value_usd"] += float(row["sales_value_usd"] or 0)
                bucket["prev_days"] += 1
                continue
            bucket["sales_count"] += int(row["sales_count"] or 0)
            bucket["sales_value_usd"] += float(row["sales_value_usd"] or 0)
            # coverage 同 window_end_at 一齊跟窗口內最新嗰日走。
            if bucket["window_end_at"] is None or observed >= bucket["window_end_at"]:
                bucket["window_end_at"] = observed
                bucket["coverage_status"] = str(row["coverage_status"])
    # 只有前窗口有成交、當前窗口冇嘅卡，會喺上面開咗個空 bucket。嗰個唔算
    # 「有成交」，收返走，唔好令 trackedSales 由 unavailable 變 ready/0。
    return {key: bucket for key, bucket in result.items() if bucket["window_end_at"] is not None}


def latest_populations(connection: Any, variant_ids: list[int]) -> dict[tuple[int, str], Mapping[str, Any]]:
    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    rows = fetchall(
        connection,
        f"""
        SELECT variant_id,grader_code,top_grade_label,total_population,top_grade_population,
               estimated,effective_at,observed_date
        FROM market_grader_population_observation
        WHERE variant_id IN ({placeholders})
        ORDER BY observed_date DESC,effective_at DESC,id DESC
        """,
        variant_ids,
    )
    result: dict[tuple[int, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (int(row["variant_id"]), str(row["grader_code"]).upper())
        # Latest row wins for top-grade population, but a fresh row may carry a
        # null total_population (the GemRate pipeline only ships top-grade
        # counts). Fall back to the most recent non-null total so the public
        # snapshot never emits a ready/null metric.
        existing = result.get(key)
        if existing is None:
            result[key] = row
        elif existing["total_population"] is None and row["total_population"] is not None:
            merged = dict(existing)
            merged["total_population"] = row["total_population"]
            result[key] = merged
    return result


def population_series(
    connection: Any, variant_ids: list[int]
) -> dict[tuple[int, str], dict[date, int]]:
    """每張卡每個評級廠嘅 top-grade POP 日線／週線序列。

    兩個來源疊埋：DB 逐日觀測（起 2026-07-21，得日線但短）＋ GemRate 每張卡嘅
    3 年週線史（`data/private/gemrate/cards/<gemrate_id>/history_full.json`，由
    `gemrate_source.py` 落地）。DB 係當日權威，同日撞到就 DB 贏。

    冇私有歷史目錄（CI／新 clone）唔係錯，淨用 DB 就得。
    """

    if not variant_ids:
        return {}
    series: dict[tuple[int, str], dict[date, int]] = defaultdict(dict)
    placeholders = ",".join(["%s"] * len(variant_ids))
    if POPULATION_HISTORY_DIR.is_dir():
        gemrate_ids = fetchall(
            connection,
            f"""
            SELECT variant_id,external_entity_id
            FROM catalog_source_identity
            WHERE source_code='gemrate' AND variant_id IN ({placeholders})
            """,
            variant_ids,
        )
        for identity in gemrate_ids:
            path = POPULATION_HISTORY_DIR / str(identity["external_entity_id"]) / "history_full.json"
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                by_grader = payload["data"]["population"]["population_data"]["by_grader"]
            except (OSError, ValueError, KeyError, TypeError):
                # 私有落地檔壞咗唔可以拉冧成個 snapshot；冇歷史當冇歷史。
                continue
            if not isinstance(by_grader, Mapping):
                continue
            variant_id = int(identity["variant_id"])
            for grader, (source_key, grade_key) in POPULATION_HISTORY_KEYS.items():
                row = by_grader.get(source_key)
                points = row.get("history") if isinstance(row, Mapping) else None
                if not isinstance(points, list):
                    continue
                for point in points:
                    if not isinstance(point, Mapping):
                        continue
                    value = (point.get("grades") or {}).get(grade_key)
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        continue
                    try:
                        observed = date.fromisoformat(str(point["date"]))
                    except (KeyError, ValueError):
                        continue
                    series[(variant_id, grader)][observed] = value
    observations = fetchall(
        connection,
        f"""
        SELECT variant_id,grader_code,observed_date,top_grade_population,estimated
        FROM market_grader_population_observation
        WHERE variant_id IN ({placeholders})
        ORDER BY id
        """,
        variant_ids,
    )
    for row in observations:
        value = row["top_grade_population"]
        if value is None or row["estimated"]:
            continue
        observed = row["observed_date"]
        if isinstance(observed, datetime):
            observed = observed.date()
        series[(int(row["variant_id"]), str(row["grader_code"]).upper())][observed] = int(value)
    return dict(series)


def population_is_stale(observation: Mapping[str, Any], effective_at: str) -> bool:
    """POP 觀測老過 POPULATION_STALE_HOURS 就係過期，唔准入快照。

    冇 effective_at 或者 parse 唔到，一律當過期——寧願 unavailable 都唔好
    stamp 個假時間出街。
    """

    observed_at = observation.get("effective_at")
    if not isinstance(observed_at, datetime):
        return True
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    try:
        generated = datetime.fromisoformat(str(effective_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return (generated - observed_at) > timedelta(hours=POPULATION_STALE_HOURS)


def top_grade_label(observation: Mapping[str, Any] | None, grader: str) -> str:
    """DB 而家 9,862 行 top_grade_label 全部係字面 'top'（ingest 層 bug，
    Part② 先修根）。快照層防守：literal 'top'／空值一律回退 TOP_GRADE 表，
    真 label（將來 ingest 修好後）原样直出。"""

    label = str(observation.get("top_grade_label") or "").strip() if observation else ""
    if not label or label.lower() == "top":
        return TOP_GRADE[grader]
    return label


def observed_day(observation: Mapping[str, Any] | None) -> date | None:
    if not observation:
        return None
    value = observation.get("observed_date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _history_price_map(history_points: list[dict[str, Any]] | None) -> dict[date, float]:
    positive: dict[date, float] = {}
    for point in history_points or []:
        price = number(point.get("priceUsd"))
        if price is None or price <= 0:
            continue
        raw_at = str(point.get("at") or "")
        try:
            day = date.fromisoformat(raw_at[:10])
        except (TypeError, ValueError):
            continue
        positive[day] = float(price)
    return positive


def price_change_from_history(
    history_points: list[dict[str, Any]] | None, window: str
) -> dict[str, Any]:
    """頭尾計 %：而家價 vs 同一窗口 ~N 日前價。

    只接受目標日 ± 容差內嘅同卡價格。歷史短過窗口係 accumulating；
    歷史跨過窗口但目標附近缺 observation 係 unavailable。禁止用較短／較長
    窗口或任意最早價格填補，因為嗰個百分比唔代表 requested window。
    """

    days = PRICE_WINDOW_DAYS[window]
    tolerance = PRICE_WINDOW_TOLERANCE[window]
    positive = _history_price_map(history_points)
    if not positive:
        return metric(None, "unavailable", None)
    latest_date = max(positive)
    current = positive[latest_date]
    earlier = {key: value for key, value in positive.items() if key < latest_date and value > 0}
    if not earlier:
        return metric(None, "accumulating", None)

    target = latest_date - timedelta(days=days)
    as_of = iso(datetime.combine(latest_date, time.min, tzinfo=timezone.utc))

    # 目標日 ± 容差內：最貼；同距離寧取 target 或之前（窗口略長好過略短）
    near = [
        (abs((key - target).days), key, value)
        for key, value in earlier.items()
        if abs((key - target).days) <= tolerance
    ]
    if near:
        _, anchor_date, anchor = min(near, key=lambda item: (item[0], item[1] > target, item[1]))
        status = "ready"
    else:
        earliest = min(earlier)
        missing_status = "accumulating" if earliest > target + timedelta(days=tolerance) else "unavailable"
        return metric(None, missing_status, None)

    if anchor <= 0:
        return metric(None, "unavailable", None)
    change = ((current / anchor) - 1) * 100
    if not isfinite(change):
        return metric(None, "unavailable", None)
    return metric(
        round(change, 6),
        status,
        as_of,
        anchorAt=anchor_date.isoformat(),
    )


def resolve_price_change_metric(
    index_change: float | None,
    history_points: list[dict[str, Any]] | None,
    window: str,
    effective_at: str,
) -> dict[str, Any]:
    """Resolve a window without presenting an unproved index zero as fact.

    A real zero needs two distinct valid history dates whose canonical daily
    prices are equal.  A zero-shaped index fallback without that evidence is
    missing data and remains accumulating.
    """

    derived = price_change_from_history(history_points, window)
    derived_ok = derived.get("status") in {"ready", "stale"} and derived.get("value") is not None

    if index_change is None:
        return derived

    if abs(float(index_change)) > 1e-9:
        return metric(index_change, "ready", effective_at)

    if derived_ok:
        return derived
    missing_status = str(derived.get("status") or "unavailable")
    if missing_status not in {"accumulating", "unavailable"}:
        missing_status = "unavailable"
    return metric(None, missing_status, None)


def population_change_windows(
    points: Mapping[date, int], current_value: int | None, current_day: date | None, status: str
) -> dict[str, dict[str, Any]]:
    """POP 三個窗口嘅百分比變動。

    Population 係**存量**指標，只升唔跌（CLAUDE.md 數據語義）。錨點高過現值即係
    數據異常（某廠當日冇入到數就寫 0），一律當 `unavailable` 收起，**永遠唔會**
    出負 delta 或者跌箭嘴。錨點係 0 亦冇得計百分比，同樣收起。
    """

    if current_value is None or current_day is None or not points:
        return {window: metric(None, "unavailable", None) for window in WINDOWS}
    as_of = iso(datetime.combine(current_day, time.min, tzinfo=timezone.utc))
    earliest = min(points)
    result: dict[str, dict[str, Any]] = {}
    for window in WINDOWS:
        target = current_day - timedelta(days=POPULATION_WINDOW_DAYS[window])
        tolerance = POPULATION_WINDOW_TOLERANCE[window]
        candidates = [
            (abs((day - target).days), day, value)
            for day, value in points.items()
            if day < current_day and abs((day - target).days) <= tolerance
        ]
        if not candidates:
            # 有現值但歷史未夠長 = accumulating（中性「—」）；完全冇歷史先叫 unavailable。
            result[window] = metric(None, "accumulating" if earliest > target else "unavailable", None)
            continue
        # 同 derive_price_windows 一致：先揀最貼目標日，同距離下寧取目標日或之前
        # 嗰點（寧可窗口略長，唔好偷短），最後取最早嗰個穩定 tie-break。
        _, anchor_day, anchor = min(candidates, key=lambda item: (item[0], item[1] > target, item[1]))
        if anchor <= 0 or current_value < anchor:
            result[window] = metric(None, "unavailable", None)
            continue
        result[window] = metric(
            round(((current_value / anchor) - 1) * 100, 6),
            "stale" if status == "stale" else "ready",
            as_of,
            anchorAt=anchor_day.isoformat(),
        )
    return result


def compose_change_pct(base: Mapping[str, Any], factor: Mapping[str, Any]) -> dict[str, Any]:
    """市值變動 = (1+Δ價/100)(1+ΔPOP/100)−1，百分比單位。

    市值 = 價 × POP，所以市值嘅變動率**唔係**價格嘅變動率。舊版前端直接攞
    `changePct`（純價格）當市值變動用，漏咗 POP 嗰截：POP 只升唔跌，所以幅度
    永遠低估；而當價格跌、POP 升到蓋得過，乘出嚟由負變正，箭嘴會**指錯方向**。

    兩個輸入有一個唔係 ready/stale 就出 null。**唔准**退返去單用 Δ價頂替 ——
    頂替就係原本嗰個 bug 本身（CLAUDE.md 數據語義：冇對應窗口歷史數據就直話
    用戶，唔准攞另一個指標嘅 changePct 頂替）。呢個係 fail-closed，寧願空白。

    `derive.ts` 嘅 `composeChangePct()` 係同一條式，兩邊要一齊改。
    """

    live = {"ready", "stale"}
    base_ok = base.get("value") is not None and base.get("status") in live
    factor_ok = factor.get("value") is not None and factor.get("status") in live
    if not base_ok or not factor_ok:
        unavailable = base.get("status") == "unavailable" or factor.get("status") == "unavailable"
        return metric(None, "unavailable" if unavailable else "accumulating", None)
    composed = ((1 + float(base["value"]) / 100) * (1 + float(factor["value"]) / 100) - 1) * 100
    if not isfinite(composed):
        return metric(None, "unavailable", None)
    # 複合指標唔可以扮到新過佢最舊嗰個輸入。
    base_at, factor_at = base.get("asOf"), factor.get("asOf")
    as_of = earlier(base_at, factor_at) if base_at and factor_at else (base_at or factor_at)
    return metric(
        round(composed, 6),
        "stale" if "stale" in {base.get("status"), factor.get("status")} else "ready",
        as_of,
    )


def ratio_change_pct(current: float | None, previous: float | None, status: str, as_of: str | None) -> dict[str, Any]:
    """環比百分比。前期係 0 或者缺數就出 null —— 除唔到就係計唔到。"""

    if current is None or previous is None or previous <= 0:
        return metric(None, "accumulating", None)
    change = ((current / previous) - 1) * 100
    if not isfinite(change):
        return metric(None, "unavailable", None)
    return metric(round(change, 6), status, as_of)


def latest_price_at(connection: Any, variant_ids: list[int]) -> dict[int, str]:
    """每張卡最近一次**真實**價格觀測嘅 effective_at。

    公開 snapshot 嘅 `pricePsa10.asOf` 一定要用呢個，唔可以用 snapshot 自己嘅
    `generation.effectiveAt`：`validate.ts` 個 48h 新鮮度閘量度嘅正正係兩者之差，
    如果 stamp 咗 snapshot 自己嘅時間，age 由構造上永遠係 0，爬蟲死咗個閘都唔會響。

    Prefer eBay then SNK timestamps (real market). Never any legacy G10-derived
    source: it is analytics/index data, not a bindable market observation.
    """
    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    # Priority: ebay (0) > SNK (1) > rest (2). Legacy G10 sources never egress.
    rows = fetchall(
        connection,
        f"""
        SELECT variant_id, effective_at, source_code, source_priority, observed_date, id
        FROM market_price_observation
        WHERE variant_id IN ({placeholders})
          AND price_usd IS NOT NULL
          AND price_usd > 0
          AND LOWER(source_code) NOT LIKE 'g10%'
        ORDER BY
          CASE
            WHEN source_code IN ('ebay') THEN 0
            WHEN source_code IN ('snk_psa10', 'snk', 'snkrdunk') THEN 1
            ELSE 2
          END,
          effective_at DESC,
          source_priority ASC,
          id DESC
        """,
        tuple(variant_ids),
    )
    result: dict[int, str] = {}
    for row in rows:
        if row.get("effective_at") is None:
            continue
        result.setdefault(int(row["variant_id"]), iso(row["effective_at"]))
    return result


def latest_ungraded_reference_prices(
    connection: Any, variant_ids: list[int]
) -> dict[int, Mapping[str, Any]]:
    """Latest positive RAW reference per variant, kept outside PSA10 metrics."""

    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    rows = fetchall(
        connection,
        f"""
        SELECT variant_id,price_usd,observed_at,id
        FROM market_ungraded_reference_price
        WHERE variant_id IN ({placeholders})
          AND price_usd > 0
        ORDER BY observed_at DESC,id DESC
        """,
        tuple(variant_ids),
    )
    latest: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        if row.get("observed_at") is None:
            continue
        latest.setdefault(int(row["variant_id"]), row)
    return latest


def daily_history(connection: Any, variant_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    prices = fetchall(
        connection,
        f"""
        SELECT variant_id,observed_date,price_usd,metric_status,source_priority,effective_at
        FROM market_price_observation
        WHERE variant_id IN ({placeholders})
          AND LOWER(source_code) NOT LIKE 'g10%'
        ORDER BY observed_date DESC,source_priority ASC,effective_at DESC,id DESC
        """,
        variant_ids,
    )
    # Same approved observation path as latest_sales(); never merge the daily
    # table, because it cannot establish pure PSA10 transaction eligibility.
    sales = approved_psa10_daily_rows(connection, variant_ids)
    sales_by_day: dict[tuple[int, str], dict[str, Any]] = {}
    for row in sales:
        sale_key = (int(row["variant_id"]), str(row["observed_date"]))
        merged = sales_by_day.get(sale_key)
        if merged is None:
            sales_by_day[sale_key] = {
                "sales_count": int(row["sales_count"] or 0),
                "sales_value_usd": float(row["sales_value_usd"] or 0),
                "coverage_status": str(row["coverage_status"]),
            }
        else:
            merged["sales_count"] += int(row["sales_count"] or 0)
            merged["sales_value_usd"] += float(row["sales_value_usd"] or 0)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[int, str]] = set()
    for row in prices:
        variant_id = int(row["variant_id"])
        day = str(row["observed_date"])
        key = (variant_id, day)
        if key in seen:
            continue
        seen.add(key)
        sale = sales_by_day.get(key)
        grouped[variant_id].append(
            {
                "at": f"{day}T00:00:00Z",
                "priceUsd": number(row["price_usd"]),
                "priceStatus": str(row["metric_status"]),
                "trackedSalesValueUsd": number(sale["sales_value_usd"]) if sale else None,
                "trackedSalesCount": integer(sale["sales_count"]) if sale else None,
                "salesCoverage": str(sale["coverage_status"]) if sale else "unavailable",
            }
        )
    for variant_id, points in grouped.items():
        grouped[variant_id] = sorted(points[:90], key=lambda point: point["at"])
    return grouped


def currency_block(connection: Any, effective_at: str) -> dict[str, Any]:
    rows = fetchall(
        connection,
        """
        SELECT quote_currency,rate,effective_at
        FROM market_fx_rate_observation
        WHERE base_currency='USD'
        ORDER BY effective_at DESC,id DESC
        """,
    )
    latest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        latest.setdefault(str(row["quote_currency"]).upper(), row)
    rates: dict[str, Any] = {"USD": metric(1, "ready", effective_at)}
    for currency in CURRENCIES[1:]:
        row = latest.get(currency)
        rates[currency] = (
            metric(number(row["rate"]), "ready", iso(row["effective_at"]))
            if row
            else metric(None, "unavailable", None)
        )
    dates = [value["asOf"] for value in rates.values() if value["asOf"]]
    return {"base": "USD", "supported": list(CURRENCIES), "rates": rates, "asOf": min(dates) if dates else None}


def card_from_row(
    row: Mapping[str, Any],
    presentation: Mapping[str, Any],
    effective_at: str,
    sales: Mapping[tuple[int, str], Mapping[str, Any]],
    populations: Mapping[tuple[int, str], Mapping[str, Any]],
    history: Mapping[int, list[dict[str, Any]]],
    price_at: Mapping[int, str],
    pop_series: Mapping[tuple[int, str], Mapping[date, int]] | None = None,
    ungraded_references: Mapping[int, Mapping[str, Any]] | None = None,
    identity_status: str = "confirmed",
) -> dict[str, Any]:
    card = json.loads(json.dumps(presentation))
    variant_id = int(row["variant_id"])
    status = str(row.get("metric_status") or "unavailable")
    if status not in {"ready", "stale"}:
        raise SnapshotExportError(f"ranked card {row['opaque_id']} has unavailable market metrics")
    # 三個對外指標都要 stamp 返真實觀測時間。冇觀測就係數據鏈斷咗，
    # fail-closed 喺呢度嘈出嚟，好過 stamp 個假時間令 48h 閘一世唔會響。
    price_observed_at = price_at.get(variant_id)
    if price_observed_at is None:
        raise SnapshotExportError(
            f"ranked card {row['opaque_id']} has no backing price observation to date pricePsa10"
        )
    psa_population = populations.get((variant_id, "PSA"))
    if psa_population is None:
        raise SnapshotExportError(
            f"ranked card {row['opaque_id']} has no backing PSA population observation to date populationPsa10"
        )
    population_observed_at = iso(psa_population["effective_at"])
    # ``marketRank`` survives view-specific filtering; ``viewRank`` is made
    # contiguous after unusable presentation/image rows are removed.  Keep the
    # legacy ``rank`` equal to ``viewRank`` for existing consumers.
    card["marketRank"] = int(row["rank_position"])
    card["viewRank"] = int(row["rank_position"])
    card["rank"] = int(row["rank_position"])
    card["identityStatus"] = identity_status
    card["pricePsa10"] = metric(number(row["reference_price_usd"]), status, price_observed_at)
    ungraded = (ungraded_references or {}).get(variant_id)
    card["priceUngradedReference"] = (
        metric(number(ungraded["price_usd"]), "ready", iso(ungraded["observed_at"]))
        if ungraded is not None
        else metric(None, "unavailable", None)
    )
    card["populationPsa10"] = metric(
        integer(row["psa10_population"]), "ready", population_observed_at, estimated=False
    )
    # market cap = 價 × POP，兩個輸入邊個舊就跟邊個，唔可以扮返新。
    card["marketCap"] = metric(
        number(row["market_cap_usd"]), status, earlier(price_observed_at, population_observed_at)
    )
    # 市值 changePct 要 PSA top-grade POP 嘅同窗口變動做第二個因子。
    # 呢度同下面 grader loop 嘅 PSA 分支傳一模一樣嘅參數，
    # `population_change_windows()` 係純函數，兩邊必然出同一個數。
    psa_population_change = population_change_windows(
        (pop_series or {}).get((variant_id, "PSA")) or {},
        integer(psa_population["top_grade_population"]),
        observed_day(psa_population),
        "ready",
    )
    variant_history = history.get(variant_id) or []
    for window, column in (("1d", "change_1d_pct"), ("7d", "change_7d_pct"), ("30d", "change_30d_pct")):
        change = number(row.get(column))
        change_metric = resolve_price_change_metric(change, variant_history, window, effective_at)
        aggregate = sales.get((variant_id, window))
        coverage = str(aggregate["coverage_status"]) if aggregate else "unavailable"
        aggregate_at = iso(aggregate["window_end_at"]) if aggregate else None
        sales_status = "ready" if aggregate and coverage == "partial" else "unavailable"
        sales_value = number(aggregate["sales_value_usd"]) if aggregate else None
        card["windows"][window] = {
            "changePct": change_metric,
            "marketCapChangePct": compose_change_pct(change_metric, psa_population_change[window]),
            # 成交額環比：呢個窗口 vs 緊貼前面同長度嗰個窗口。舊版前端攞
            # `changePct`（價格變動）當成交額變動用 —— 兩個量冇任何數學關係。
            "trackedSalesChangePct": ratio_change_pct(
                sales_value if sales_status == "ready" else None,
                number(aggregate.get("prev_sales_value_usd")) if aggregate else None,
                sales_status,
                aggregate_at,
            ),
            "trackedSales": {
                "valueUsd": metric(number(aggregate["sales_value_usd"]) if aggregate else None, sales_status, aggregate_at),
                "count": metric(integer(aggregate["sales_count"]) if aggregate else None, sales_status, aggregate_at),
                "coverage": coverage,
                "asOf": aggregate_at,
            },
        }
    for grader in GRADERS:
        observed = populations.get((variant_id, grader))
        # Date floor：過期觀測直接當冇——label／total／POP／changePct 四樣
        # 全部跟住自動歸 unavailable，唔會留低半新半舊嘅卡片。
        # （TAG 07-29 起最先觸發：freeze 後冇新觀測，就係應該熄。）
        if observed is not None and population_is_stale(observed, effective_at):
            observed = None
        observed_at = iso(observed["effective_at"]) if observed else None
        observed_status = "ready" if observed else "unavailable"
        total_value = integer(observed["total_population"]) if observed else None
        top_value = integer(observed["top_grade_population"]) if observed else None
        card["graderPopulations"][grader] = {
            "topGrade": top_grade_label(observed, grader),
            # total population is not shipped by the GemRate top-grade pipeline;
            # emit unavailable instead of ready/null so public validation holds.
            "total": metric(
                total_value,
                observed_status if total_value is not None else "unavailable",
                observed_at if total_value is not None else None,
                estimated=False,
            ),
            "topGradePopulation": metric(
                top_value,
                observed_status if top_value is not None else "unavailable",
                observed_at if top_value is not None else None,
                estimated=bool(observed["estimated"]) if observed else False,
            ),
            "topGradePopulationChangePct": population_change_windows(
                (pop_series or {}).get((variant_id, grader)) or {},
                top_value,
                observed_day(observed),
                observed_status,
            ),
        }
    card["historyDaily"] = history.get(variant_id, [])
    return card


def is_frontend_liquid(card: Mapping[str, Any]) -> bool:
    """Return whether the DATA_CONTRACT 30d pure-PSA10 listing gate is met."""
    window = (card.get("windows") or {}).get("30d") or {}
    sales = window.get("trackedSales") or {}
    count = sales.get("count") or {}
    value = count.get("value") if isinstance(count, Mapping) else count
    return has_frontend_sales_30d(value)


def has_frontend_sales_30d(
    value: Any, minimum: int = MIN_PURE_PSA10_SALES_30D
) -> bool:
    """Missing sales are unavailable, never a fabricated zero."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= minimum
    )


def frontend_liquid_cards(
    cards: Sequence[dict[str, Any]], *, sales_minimum: int = MIN_PURE_PSA10_SALES_30D
) -> list[dict[str, Any]]:
    """Delist cards without enough 30d pure-PSA10 sales, then rank the public view."""
    liquid_cards: list[dict[str, Any]] = []
    for card in cards:
        window = (card.get("windows") or {}).get("30d") or {}
        sales = window.get("trackedSales") or {}
        count = sales.get("count") or {}
        value = count.get("value") if isinstance(count, Mapping) else count
        liquid = has_frontend_sales_30d(value, sales_minimum)
        card["feTop100LiquidityOk"] = liquid
        if liquid:
            liquid_cards.append(card)

    def market_cap_value(card: Mapping[str, Any]) -> float:
        raw = card.get("marketCap")
        if isinstance(raw, Mapping):
            raw = raw.get("value")
        return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0

    ordered = sorted(liquid_cards, key=market_cap_value, reverse=True)
    for position, card in enumerate(ordered, start=1):
        card["marketRank"] = position
        card["viewRank"] = position
        card["rank"] = position
    return ordered


def build_snapshot(
    connection: Any,
    presentation_path: Path,
    *,
    production: bool,
    presentation_view: str = "top300",
    staging_evaluation_id: int | None = None,
    db_qc_report: Path | None = None,
    release_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    release = resolved_release_profile(release_profile)
    profile_bound = release_profile is not None
    release_profile_id = str(release["releaseProfile"])
    policy = release["policy"]
    if not isinstance(policy, Mapping):
        raise SnapshotExportError("release profile policy is invalid")
    sales_minimum = _policy_int(policy, "trackedPsa10Sales30dMinimumInclusive")
    card_capacity = _policy_int(policy, "publicCardsMaximum")
    asset_capacity = _policy_int(policy, "publicImageAssetsMaximum")
    allow_provisional = release_profile_id == "relaxed-launch-v1"
    template, cards_by_id = load_presentation(presentation_path)
    required_count = presentation_view_limit(presentation_view)
    minimum = presentation_view_min_coverage(presentation_view)
    generation = (
        staging_generation(connection, staging_evaluation_id, minimum)
        if staging_evaluation_id is not None
        else latest_generation(connection, minimum)
    )
    effective_at = iso(generation["effective_at"])
    rows = market_rows(
        connection,
        generation,
        required_count=required_count,
        include_board_extras=(presentation_view == "top300_boards"),
    )
    if required_count is not None and required_count > card_capacity:
        raise SnapshotExportError(
            f"{presentation_view} exceeds {release_profile_id} public card capacity {card_capacity}"
        )
    release_image_receipts: dict[str, Mapping[str, Any]] = {}
    identity_status_by_id: dict[str, str] = {}
    if db_qc_report is not None:
        allowed_opaque_ids = qc_gate_allowed_opaque_ids(
            db_qc_report,
            generation,
            qc_gate_current_lock_sha256(connection, int(generation["evaluation_id"])),
            release if profile_bound else None,
        )
        if profile_bound:
            identity_status_by_id = qc_gate_identity_statuses(db_qc_report, release)
            release_image_receipts = qc_gate_release_image_receipts(db_qc_report, release)
        before_qc_gate = len(rows)
        rows = [row for row in rows if str(row["opaque_id"]) in allowed_opaque_ids]
        print(
            f"qc_gate_excluded={before_qc_gate - len(rows)} "
            f"qc_gate_allowed={len(rows)}",
            file=sys.stderr,
        )
    all_variant_ids = [int(row["variant_id"]) for row in rows]
    sales = latest_sales(connection, all_variant_ids, generation["effective_at"])
    liquid_rows = [
        row for row in rows
        if has_frontend_sales_30d(
            (sales.get((int(row["variant_id"]), "30d")) or {}).get("sales_count"),
            sales_minimum,
        )
    ]
    # Delisted rows do not need public images or presentation text, so they
    # cannot create an assets blocker before their liquidity returns.
    resolved, skipped, tiers = resolve_presentation_entries(
        liquid_rows,
        cards_by_id,
        load_public_images(
            connection=connection,
            release_image_receipts=(
                release_image_receipts if allow_provisional and profile_bound else None
            ),
        ),
        catalog_printing_key_counts(connection, allow_provisional=allow_provisional),
        allow_provisional=allow_provisional,
    )
    publishable = [row for row in liquid_rows if str(row["opaque_id"]) in resolved]
    variant_ids = [int(row["variant_id"]) for row in publishable]
    populations = latest_populations(connection, variant_ids)
    history = daily_history(connection, variant_ids)
    price_at = latest_price_at(connection, variant_ids)
    ungraded_references = latest_ungraded_reference_prices(connection, variant_ids)
    pop_series = population_series(connection, variant_ids)
    # Fail-closed per card: skip ranked rows still missing price or PSA pop observation
    # rather than aborting the whole FE export (index can lag a single observation).
    usable_publishable: list[Mapping[str, Any]] = []
    for row in publishable:
        vid = int(row["variant_id"])
        if vid not in price_at:
            skipped.append((str(row["opaque_id"]), "price_observation_missing"))
            continue
        if (vid, "PSA") not in populations and (vid, "psa") not in populations:
            # populations keys use grader codes from loader — check PSA
            if not any(k[0] == vid and str(k[1]).upper() == "PSA" for k in populations):
                skipped.append((str(row["opaque_id"]), "psa_population_missing"))
                continue
        usable_publishable.append(row)
    publishable = usable_publishable
    cards = [
        card_from_row(
            row,
            resolved[str(row["opaque_id"])],
            effective_at,
            sales,
            populations,
            history,
            price_at,
            pop_series,
            ungraded_references,
            identity_status_by_id.get(str(row["opaque_id"]), "confirmed"),
        )
        for row in publishable
    ]
    # Skipped cards leave holes in canonical ``marketRank``.  The public view
    # remains contiguous via ``viewRank``/legacy ``rank`` without pretending
    # that a lower-ranked verified card had a different market position.
    for position, card in enumerate(cards, start=1):
        card["viewRank"] = position
        card["rank"] = position
    # 譯名／譯文要喺呢度貼，唔可以喺 presentation_from_identity ——
    # 嗰個 function 淨係砌 tier-3 卡，tier-1/2 由 presentation pack 直接抬過嚟，
    # 帶住同一批 null。呢度係唯一見到齊全 cards list 嘅地方。
    #
    # 故事查表要用 DB 嘅 `opaque_id` 而唔係 `card["id"]`：tier-1/2 卡個 id 係由
    # presentation pack 凍住抬過嚟，而 `opaque_id` = sha256(name/set/collector)
    # —— 執過一次卡名就重新 hash 過，pack 入面嗰個舊 id 即刻查唔返
    # `catalog_variant`。實測 251 張出版卡得 75 張仲對得返，其餘 176 張明明
    # 入咗庫但出唔到街。呢度即場砌返「出版 id → 當日 opaque_id」嘅對照，
    # 公開 id 一個 bit 都唔郁。
    localization = localize_cards(
        cards,
        connection=connection,
        story_keys={
            str(card["id"]): str(row["opaque_id"]) for card, row in zip(cards, publishable)
        },
    )
    print(resolution_summary(len(rows), tiers, skipped), file=sys.stderr)
    print(coverage_summary(localization), file=sys.stderr)
    # DATA_CONTRACT liquidity gate: cards with fewer than 10, or unavailable,
    # 30d pure-PSA10 sales are delisted from every public collection.
    cards_by_mcap = frontend_liquid_cards(cards, sales_minimum=sales_minimum)
    if len(cards_by_mcap) > card_capacity:
        raise SnapshotExportError(
            f"{release_profile_id} public card capacity exceeded: "
            f"{len(cards_by_mcap)} > {card_capacity}"
        )
    image_hashes = {
        str((card.get("image") or {}).get("sha256") or "")
        for card in cards_by_mcap
        if str((card.get("image") or {}).get("sha256") or "")
    }
    if len(image_hashes) * 3 > asset_capacity:
        raise SnapshotExportError(
            f"{release_profile_id} public image asset capacity exceeded: "
            f"more than {asset_capacity} derivatives required"
        )
    top = cards_by_mcap[:100]
    watch = cards_by_mcap[100:]
    # viewRank for watchlist must start at 101 per validate.ts
    for position, card in enumerate(watch, start=101):
        card["viewRank"] = position
        card["rank"] = position
    blockers: list[str] = []
    if len(top) != 100:
        blockers.append("combined_top100_incomplete")
    if len(liquid_rows) < 100:
        blockers.append("top100_liquidity_shortfall")
    if len(cards) != len(liquid_rows):
        blockers.append("presentation_assets_incomplete")
    generated_now = datetime.now(timezone.utc)
    generated_at = iso(generated_now)
    generation_id = (
        f"canonical_{str(generation['effective_date']).replace('-', '')}"
        f"_e{int(generation['evaluation_id'])}_{str(generation['snapshot_sha256'])[:12]}"
        f"_{release_profile_id}"
        f"_{generated_now.strftime('%Y%m%dT%H%M%S%fZ')}"
    )
    snapshot = {
        "schemaVersion": "2.0.0",
        "generation": {
            "id": generation_id,
            "releaseProfile": release_profile_id,
            "policySha256": release["policySha256"],
            "dbFingerprint": (
                str((json.loads(db_qc_report.read_text(encoding="utf-8")) if db_qc_report else {}).get("database", {}).get("fingerprint") or "")
            ),
            "evaluationId": int(generation["evaluation_id"]),
            "generatedAt": generated_at,
            "effectiveAt": effective_at,
            "contentSha256": "",
            # Export is candidate-only.  ``public_snapshot_qc.py finalize`` is
            # the sole step allowed to bind a strict receipt and turn these
            # fields into a production-eligible generation.
            "qcReceiptSha256": "",
            "mode": "demo",
            "productionEligible": False,
            "blockers": sorted(set([*blockers, "strict_qc_receipt_missing"])),
        },
        "universe": {
            "populationMin": 1000,
            "grade": "PSA 10",
            "rankingMetric": "psa10_market_cap_usd",
            "windows": list(WINDOWS),
            "salesCoverage": "partial",
        },
        "coverage": {
            "claim": "verified-top-n",
            "requestedCount": 100,
            "verifiedCount": 0,
            "requestedView": presentation_view,
            "top100Count": len(top),
            "watchlistCount": len(watch),
            "publicTop300Count": len(cards),
            "publicCardCount": len(cards_by_mcap),
            "privateReserveExcludedCount": max(0, int(generation["constituent_count"]) - len(cards)),
            "changeReady": {window: sum(card["windows"][window]["changePct"]["status"] == "ready" for card in cards) for window in WINDOWS},
            "salesReady": {window: sum(card["windows"][window]["trackedSales"]["coverage"] == "partial" for card in cards) for window in WINDOWS},
            "graderPopulationReady": {
                grader: sum(card["graderPopulations"][grader]["topGradePopulation"]["status"] == "ready" for card in cards)
                for grader in GRADERS
            },
            "graderPopulationChangeReady": {
                grader: {window: 0 for window in WINDOWS} for grader in GRADERS
            },
            "completeIdentityCount": sum(card["collectorNumber"]["complete"] for card in cards),
            "localizedStoryCount": {
                locale: sum(bool(card["stories"].get(locale)) for card in cards)
                for locale in ("en", "zhTW", "zhCN", "ja")
            },
        },
        "currencies": currency_block(connection, effective_at),
        "top100": top,
        "watchlist": watch,
    }
    snapshot["generation"]["contentSha256"] = snapshot_content_sha256(snapshot)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presentation", type=Path, default=ROOT / "data/public/seed-snapshot.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/public/seed-snapshot.json")
    parser.add_argument("--view", choices=tuple(PRESENTATION_VIEW_LIMITS), default="all_eligible")
    parser.add_argument("--routing-config", type=Path, default=DEFAULT_ROUTING_CONFIG)
    parser.add_argument("--release-profile", default=DEFAULT_RELEASE_PROFILE)
    parser.add_argument("--staging-evaluation-id", type=int)
    parser.add_argument(
        "--db-qc-report",
        type=Path,
        help="authoritative canonical DB QC report bound to this exact evaluation",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="deprecated compatibility flag; export remains blocked until strict QC finalization",
    )
    add_connection_args(parser)
    args = parser.parse_args()
    release = load_release_profile(
        load_registry(args.routing_config.resolve()), args.release_profile
    )
    connection = connection_from_args(args)
    try:
        snapshot = build_snapshot(
            connection,
            args.presentation.resolve(),
            production=args.production,
            presentation_view=args.view,
            staging_evaluation_id=args.staging_evaluation_id,
            db_qc_report=args.db_qc_report.resolve() if args.db_qc_report else None,
            release_profile=release,
        )
    finally:
        connection.close()
    atomic_json(args.output.resolve(), snapshot)
    print(
        json.dumps(
            {
                "generation": snapshot["generation"]["id"],
                "releaseProfile": snapshot["generation"]["releaseProfile"],
                "policySha256": snapshot["generation"]["policySha256"],
                "top100": len(snapshot["top100"]),
                "watchlist": len(snapshot["watchlist"]),
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SnapshotExportError, RuntimeError, ValueError) as error:
        print(str(error), file=os.sys.stderr)
        raise SystemExit(1) from None
