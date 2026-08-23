#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gemrate_source.py — single-file GemRate population data source for CARDZ market-cap.

SOURCE LEVEL: GemRate is the population authority. The current transport chain is
direct API -> public exact card page -> Grade10 GemRate mirror. Grade10 is
bootstrap/discovery evidence only and the mirror never supplies population history.
SNK supplies exact PSA 10 price/trade evidence. All sources pass canonical data
gates before any sanitized public snapshot.

Daily updater + standalone scraper. Direct API uses stdlib; public GemRate routes
use the pinned Playwright dependency with a portable browser fallback.

GemRate is an AGGREGATOR of the four grading companies' public population reports:
  PSA (psacard.com/pop) · CGC (cgccards.com/population-report)
  Beckett/BGS (beckett.com/grading/pop-report) · SGC (gosgc.com/pop-report)

Two acquisition modes:
  WITH key   -> official API  api.gemrate.com   (authoritative card population/history)
  WITHOUT key-> exact public card page (current POP only) or public search

USAGE
  # Daily incremental update for the cards CARDZ already tracks (resumable):
  python pipelines/gemrate_source.py daily --ids-file pipelines/gemrate_ids.txt

  # One-off bulk harvest of a big id list (key window — use it before it dies):
  python pipelines/gemrate_source.py api-dump --ids-file pipelines/gemrate_ids.txt --speed medium --resume

  # Grow the id list for free (no key) via a real Chrome + internal search API:
  python pipelines/gemrate_source.py collect --queries charizard pikachu lugia --limit 40

  # Free per-set four-grader snapshot (research evidence, not per-card PSA 10 POP):
  python pipelines/gemrate_source.py pop-report --out data/private/gemrate/pop_report.json

  # Flatten everything harvested into one analysis CSV:
  python pipelines/gemrate_source.py export-csv --out data/private/gemrate/population_history.csv

SCHEDULER — this file is NOT scheduled on its own.
  `pipelines/run_daily.py` calls `gemrate_source.py daily` inside the canonical
  daily run (see `gemrate_daily_command`), so GemRate follows the daily trigger:
    Linux (production) : cardz-market-cap-daily.timer, 00:30 UTC = 09:30 JST
    Windows (legacy)   : CARDZ-Market-Cap-Daily, 09:30 local
  There is no standalone GemRate task registered on either host. Register
  `deploy\\windows\\install_gemrate_task.ps1` (bottom of this file) only if you need a
  detached Windows run; do NOT reuse 06:45 — that slot belongs to
  CARDZ-TAG-Daily-Capture.

ENV
  GEMRATE_API_KEY   injected secret for direct API. Never logged or written out.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

API = "https://api.gemrate.com/v1"
WEB = "https://www.gemrate.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "private" / "gemrate"
CARDS_DIR = OUT_DIR / "cards"
IDS_FILE = ROOT / "pipelines" / "gemrate_ids.txt"
DEFAULT_TRACKED_IDENTITIES = ROOT / "data" / "runtime" / "private-source-map" / "tracked-universe.json"
DEFAULT_G10_MIRROR_ROOT = ROOT / "integrations" / "grade10" / "data"

# Delay between API calls (seconds).
SPEEDS = {"slow": 3.0, "medium": 1.0, "fast": 0.15}

# Public card-page 429 backoff ladder (seconds). Each 429 on the same card waits
# the next rung and retries; running past the last rung records a
# rate_limited_429_exhausted failure receipt for that card.
RATE_LIMIT_LADDER = (30, 60, 120, 300, 600)

# Session warm-up backoff (seconds). audit P0-2: the one navigation that gates
# all ~400 cards of a shard had no retry, so a transient Cloudflare/Chrome
# failure ~1.5s into the session killed 399/399 cards twice (2026-08-20,
# 2026-08-23) while three sibling shards launched Chrome in the same second.
WEBSITE_SESSION_RETRY_BACKOFF = (5.0, 15.0, 30.0)

# audit P1-6: give up on the first pass only after this many consecutive
# session-level errors that harvested nothing, instead of breaking on the first.
WEBSITE_SESSION_ERROR_LIMIT = 2

# audit item 10 (2026-08-24 repair): a SIGTERM used to discard every card the
# website pass had already captured because the manifest is only written at the
# end of cmd_daily. This event lets the pass stop dispatching and fall through
# to a declared-partial manifest, so the ~600 cards already fetched are ingested
# instead of re-fetched from zero on the next attempt.
_SHUTDOWN_EVENT = threading.Event()


def shutdown_requested() -> bool:
    return _SHUTDOWN_EVENT.is_set()


def request_shutdown() -> None:
    _SHUTDOWN_EVENT.set()


def _install_shutdown_handlers() -> None:
    """Stop dispatching on SIGTERM/SIGINT; never abandon captured payloads."""

    def _on_signal(signum, _frame) -> None:
        _SHUTDOWN_EVENT.set()
        print(f"[daily] signal {signum}: finishing partial run", file=sys.stderr)

    for name in ("SIGTERM", "SIGINT"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            signal.signal(number, _on_signal)
        except (ValueError, OSError):
            # Not the main thread / unsupported platform: the pass simply keeps
            # its previous all-or-nothing behaviour rather than failing here.
            continue


# Cards per browser batch on the public-card-page pass. Small enough that the
# wall-clock budget is checked often, large enough to amortise browser startup.
WEBSITE_CHUNK = 25
# Default wall clock for the whole public-card-page pass. run_daily wraps this
# process in --pipeline-timeout-seconds (7200 by default) and the direct API
# leg plus mirror pass have to fit alongside it, so leave generous headroom.
DEFAULT_WEBSITE_BUDGET_SECONDS = 3600

# Graders GemRate tracks in per-card population (verified 2026-07: API enum is
# psa/cgc/sgc/beckett/csg; TAG is NOT yet in per-card population — it only appears
# in the grader-level weekly/monthly volume reports). We support TAG anyway so
# that the moment GemRate opens it in the API the pipeline picks it up with no
# code change. Missing graders in a card's by_grader are simply absent, not zero.
GRADERS = ("psa", "beckett", "sgc", "cgc", "tag")
API_GRADERS = ("psa", "beckett", "sgc", "cgc")  # what the per-card API returns today
TOP_GRADE = {"psa": "psa_10", "beckett": "beckett_10_pristine",
             "sgc": "sgc_10_pristine", "cgc": "cgc_10_perfect", "tag": "tag_10"}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


# --------------------------------------------------------------------------
# HTTP (stdlib; the API key never appears in a child-process command line)
# --------------------------------------------------------------------------

def _request(url: str, key: str | None = None, method: str = "GET",
             body: dict | None = None, timeout: int = 45) -> tuple[int, str, dict[str, str]]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if key:
        headers["x-api-key"] = key
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
            return int(response.status), text, {key.casefold(): value for key, value in response.headers.items()}
    except HTTPError as error:
        text = error.read().decode("utf-8", "replace")
        return int(error.code), text, {key.casefold(): value for key, value in error.headers.items()}
    except (URLError, TimeoutError, OSError) as error:
        return 0, json.dumps({"error": type(error).__name__}), {}


def _api_get(path: str, key: str, base_delay: float, retries: int = 4):
    """Polite GET with bounded retry for rate limits and transient failures."""
    for attempt in range(retries):
        st, txt, headers = _request(f"{API}{path}", key=key)
        if st == 429 or st == 0 or 500 <= st < 600:
            retry_after = headers.get("retry-after")
            try:
                wait = max(float(retry_after), base_delay) if retry_after else base_delay * (2 ** attempt)
            except ValueError:
                wait = base_delay * (2 ** attempt)
            print(f"    [{st or 'network'}] retry in {wait:.1f}s", file=sys.stderr)
            if attempt + 1 >= retries:
                break
            time.sleep(wait)
            continue
        try:
            return st, json.loads(txt)
        except ValueError:
            return st, txt
    try:
        return st, json.loads(txt)
    except ValueError:
        return st, txt


# --------------------------------------------------------------------------
# id list
# --------------------------------------------------------------------------

def load_ids(ids_file: Path | None) -> list[str]:
    src = ids_file or IDS_FILE
    ids: list[str] = []
    if src.exists():
        for ln in src.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if HEX40.match(ln):
                ids.append(ln)
    return list(dict.fromkeys(ids))


def append_ids(ids: list[str], ids_file: Path | None = None) -> int:
    src = ids_file or IDS_FILE
    existing = set(load_ids(src))
    new = [i for i in ids if i not in existing]
    if new:
        src.parent.mkdir(parents=True, exist_ok=True)
        with src.open("a", encoding="utf-8") as f:
            for i in new:
                f.write(i + "\n")
    return len(new)


# --------------------------------------------------------------------------
# Acquisition (official API)
# --------------------------------------------------------------------------

def _save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


PRIVATE_PAGE_JSON_FIELD = "_privatePageInitiatedJson"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_direct_identity_receipt(
    requested_gid: str,
    payload: Mapping[str, Any],
    fetched_at: str,
    source_pointer: str = "population.json",
) -> dict[str, Any]:
    """Build a private, fail-closed identity receipt for one direct response.

    The requested, entity, universal, and grader-member IDs remain distinct
    evidence.  This function deliberately does not return a canonical ID.
    """
    if not isinstance(requested_gid, str) or not HEX40.fullmatch(requested_gid):
        raise ValueError("direct_identity_requested_gid_invalid")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
        raise ValueError("direct_identity_payload_invalid")
    if not isinstance(fetched_at, str) or not fetched_at.strip():
        raise ValueError("direct_identity_fetched_at_invalid")
    if (
        not isinstance(source_pointer, str)
        or not source_pointer
        or source_pointer.startswith(("/", "\\"))
        or ".." in Path(source_pointer).parts
    ):
        raise ValueError("direct_identity_source_pointer_invalid")

    entity = payload["data"]
    entity_gid = entity.get("gemrate_id")
    if not isinstance(entity_gid, str) or not HEX40.fullmatch(entity_gid) or entity_gid != requested_gid:
        raise ValueError("direct_identity_entity_gid_mismatch")

    universal_gid = entity.get("universal_gemrate_id")
    universal_match = entity.get("is_universal_match")
    if universal_gid is not None and (not isinstance(universal_gid, str) or not HEX40.fullmatch(universal_gid)):
        raise ValueError("direct_identity_universal_gid_invalid")
    if not isinstance(universal_match, bool):
        raise ValueError("direct_identity_universal_match_invalid")
    # The official contract defines the boolean as exactly equivalent to a
    # non-null universal ID.  A grader-member entity may legitimately point to
    # a different universal ID, so equality with ``entity_gid`` is not required.
    if universal_match != (universal_gid is not None):
        raise ValueError("direct_identity_universal_null_mismatch")

    population = entity.get("population")
    graders = population.get("graders") if isinstance(population, Mapping) else None
    if not isinstance(graders, Mapping):
        raise ValueError("direct_identity_graders_invalid")
    receipt_graders: dict[str, dict[str, Any]] = {}
    member_ids: set[str] = set()
    for grader, member in graders.items():
        if not isinstance(grader, str) or not grader or not isinstance(member, Mapping):
            raise ValueError("direct_identity_grader_invalid")
        member_gid = member.get("gemrate_id")
        if not isinstance(member_gid, str) or not HEX40.fullmatch(member_gid):
            raise ValueError("direct_identity_member_gid_invalid")
        if member_gid in member_ids:
            raise ValueError("direct_identity_member_gid_duplicate")
        member_ids.add(member_gid)
        spec_id = member.get("spec_id")
        if spec_id is not None:
            if isinstance(spec_id, bool) or not re.fullmatch(r"[0-9]+", str(spec_id)):
                raise ValueError("direct_identity_spec_id_invalid")
            spec_id = str(spec_id)
        receipt_graders[grader] = {
            "gemrateId": member_gid,
            "specId": spec_id,
            "parsedDescription": member.get("parsed_description"),
        }

    return {
        "schemaVersion": "1.0.0",
        "requestedGemrateId": requested_gid,
        "entityGemrateId": entity_gid,
        "universalGemrateId": universal_gid,
        "isUniversalMatch": universal_match,
        "parsedDescription": entity.get("parsed_description"),
        "graders": receipt_graders,
        "payloadSha256": hashlib.sha256(_canonical_json_bytes(payload)).hexdigest(),
        "sourcePointer": source_pointer,
        "fetchedAt": fetched_at,
    }


def _persist_public_card_capture(cards_dir: Path, gemrate_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Split one public-page capture into immutable private raw and normalized receipt.

    Raw page-initiated JSON stays beneath the private landing path.  The normal
    ``card_details.json`` only receives a hash and relative pointer, keeping raw
    provider bodies out of canonical importers and public exports.
    """

    normalized = dict(payload)
    raw = normalized.pop(PRIVATE_PAGE_JSON_FIELD, None)
    population_data = normalized.get("population_data")
    if isinstance(population_data, list):
        normalized_rows = []
        for row in population_data:
            if not isinstance(row, Mapping):
                continue
            key = str(row.get("grader") or "").casefold()
            if GRADER_CODE_BY_KEY.get(key) is None:
                continue
            grades = row.get("grades")
            value = _top_grade_value(key, grades) if isinstance(grades, Mapping) else None
            if value is None:
                continue
            row = dict(row)
            row["grades"] = {**grades, "g10": value}
            normalized_rows.append(row)
        normalized["population_data"] = normalized_rows
    card_dir = cards_dir / gemrate_id
    page_meta = normalized.get("publicCardPage")
    mode = page_meta.get("populationMode") if isinstance(page_meta, Mapping) else None
    receipt: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "gemrateId": gemrate_id,
        "transport": WEBSITE_TRANSPORT,
        "populationMode": mode,
        "fetchedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    if isinstance(page_meta, Mapping):
        provider_entity_id = str(page_meta.get("providerEntityGemrateId") or "")
        if HEX40.fullmatch(provider_entity_id):
            receipt["providerEntityGemrateId"] = provider_entity_id
    if isinstance(raw, Mapping):
        raw_bytes = _canonical_json_bytes(raw)
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        raw_relative = f"raw/{content_hash}.json"
        raw_path = card_dir / raw_relative
        existing_hash = None
        if raw_path.is_file():
            try:
                existing_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            except OSError:
                existing_hash = None
        if existing_hash != content_hash:
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = raw_path.with_name(f".{raw_path.name}.{os.getpid()}.next")
            temporary.write_bytes(raw_bytes)
            os.replace(temporary, raw_path)
        receipt.update({
            "rawStatus": "captured",
            "contentSha256": content_hash,
            "sourcePointer": raw_relative,
        })
    elif mode == "dom_labelled_fallback" and isinstance(page_meta, Mapping) and HEX64.match(str(page_meta.get("domSha256") or "")):
        receipt.update({
            "rawStatus": "dom_evidence_only",
            "contentSha256": page_meta["domSha256"],
            "sourcePointer": None,
        })
    else:
        receipt.update({"rawStatus": "not_captured", "contentSha256": None, "sourcePointer": None})
    normalized["privateSourceReceipt"] = receipt
    _save(card_dir / "card_details.json", normalized)
    _save(card_dir / "card_details.raw.receipt.json", receipt)
    return normalized


def _has_complete_public_card_capture(cards_dir: Path, gemrate_id: str) -> bool:
    """Only let --resume skip cards with normalized data and verifiable raw receipt."""

    card_dir = cards_dir / gemrate_id
    normalized_path = card_dir / "card_details.json"
    receipt_path = card_dir / "card_details.raw.receipt.json"
    if not normalized_path.is_file() or not receipt_path.is_file():
        return False
    try:
        normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(normalized, Mapping) or not isinstance(receipt, Mapping):
        return False
    source_receipt = normalized.get("privateSourceReceipt")
    if source_receipt != receipt:
        return False
    if receipt.get("rawStatus") == "captured":
        pointer = receipt.get("sourcePointer")
        digest = receipt.get("contentSha256")
        if not isinstance(pointer, str) or not HEX64.match(str(digest or "")):
            return False
        try:
            raw_path = (card_dir / pointer).resolve()
            if card_dir.resolve() not in raw_path.parents:
                return False
            return hashlib.sha256(raw_path.read_bytes()).hexdigest() == digest
        except OSError:
            return False
    # dom_evidence_only has no replayable raw payload, so resume must refetch it.
    return False


# --------------------------------------------------------------------------
# Current PSA 10 population transport resolver
# --------------------------------------------------------------------------

# GemRate remains the population authority regardless of how a current payload
# arrives. Grade10's price.getGradingPopulations response is only a transport
# mirror of that GemRate data and cannot manufacture historical observations.
DIRECT_TRANSPORT = "direct_api"
WEBSITE_TRANSPORT = "gemrate_public_card_page"
MIRROR_TRANSPORT = "grade10_gemrate_mirror"


class PopulationResolutionError(RuntimeError):
    """A current-population run cannot safely advance its checkpoint."""

    def __init__(self, message: str, manifest: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.manifest = dict(manifest or {})


def _iso_date(value: object, fallback: str) -> tuple[str, str]:
    """Return a date-only effective value plus a truthful source label."""

    if isinstance(value, str) and value.strip():
        candidate = value.strip().replace("Z", "+00:00")
        try:
            return date.fromisoformat(candidate[:10]).isoformat(), "source_effective_date"
        except ValueError:
            try:
                return datetime.fromisoformat(candidate).date().isoformat(), "source_effective_date"
            except ValueError:
                pass
    return date.fromisoformat(fallback[:10]).isoformat(), "run_fetched_date"


GRADER_CODE_BY_KEY: dict[str, str] = {
    "psa": "PSA",
    "cgc": "CGC",
    "beckett": "BGS",
    "sgc": "SGC",
}


def _top_grade_value(grader_key: str, grades: Mapping[str, Any]) -> int | None:
    """Top-grade population for one normalized grader row.

    PSA/CGC/SGC rows carry a flat ``g10``. Beckett splits its top grade into
    pristine (``g10p``) and black label (``g10b``); BGS Pristine is the
    canonical top-grade population, matching ``beckett_10_pristine`` on the
    direct API. A Beckett row without a pristine count contributes no BGS
    point (never fall back to black label).
    """

    if grader_key == "beckett":
        value = grades.get("g10p")
    else:
        value = grades.get("g10")
    return value if isinstance(value, int) and value >= 0 else None


def _direct_population(payload: Mapping[str, Any], fetched_at: str) -> dict[str, Any] | None:
    """Extract current per-grader top-grade points from one direct GemRate response."""

    try:
        population_data = payload["data"]["population"]["population_data"]
        by_grader = population_data["by_grader"]
        psa_value = by_grader["psa"]["grades"]["psa_10"]
    except (KeyError, TypeError):
        return None
    if not isinstance(psa_value, int) or psa_value < 0:
        return None
    if not isinstance(by_grader, Mapping):
        return None
    effective_date, effective_source = _iso_date(population_data.get("data_last_updated"), fetched_at)
    grader_populations: dict[str, int] = {}
    for key, code in GRADER_CODE_BY_KEY.items():
        row = by_grader.get(key)
        grades = row.get("grades") if isinstance(row, Mapping) else None
        if not isinstance(grades, Mapping):
            continue
        # Direct API rows carry per-grader top-grade keys (psa_10,
        # beckett_10_pristine, ...) rather than the website g10 naming.
        value = grades.get(TOP_GRADE[key])
        if isinstance(value, int) and value >= 0:
            grader_populations[code] = value
    return {
        "populationPsa10": psa_value,
        "graderPopulations": grader_populations,
        "effectiveDate": effective_date,
        "effectiveDateSource": effective_source,
        "transport": DIRECT_TRANSPORT,
    }


def _mirror_population(payload: Mapping[str, Any], fetched_at: str) -> dict[str, Any] | None:
    """Extract current PSA 10 from Grade10's GemRate mirror payload only."""

    rows = payload.get("population")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("gradeName") or "").upper() != "PSA":
            continue
        value = row.get("topGrade")
        if not isinstance(value, int) or value < 0:
            continue
        effective_date, effective_source = _iso_date(
            payload.get("effectiveDate") or payload.get("effective_at") or payload.get("fetchedAt") or payload.get("fetched_at"),
            fetched_at,
        )
        return {
            "populationPsa10": value,
            "effectiveDate": effective_date,
            "effectiveDateSource": effective_source,
            "transport": MIRROR_TRANSPORT,
        }
    return None


def _website_population(payload: Mapping[str, Any], fetched_at: str) -> dict[str, Any] | None:
    """Extract current per-grader points from the exact public GemRate card-page receipt."""

    rows = payload.get("population_data")
    if not isinstance(rows, list):
        return None
    grader_rows: dict[str, Mapping[str, Any]] = {}
    grader_values: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        key = str(row.get("grader") or "").casefold()
        code = GRADER_CODE_BY_KEY.get(key)
        if code is None:
            continue
        grades = row.get("grades")
        value = _top_grade_value(key, grades) if isinstance(grades, Mapping) else None
        if value is None:
            continue
        grader_rows[code] = row
        grader_values[code] = value
    psa_row = grader_rows.get("PSA")
    if psa_row is None:
        return None
    value = grader_values["PSA"]
    page_metadata = payload.get("publicCardPage")
    is_live_page_json = isinstance(page_metadata, Mapping) and page_metadata.get("populationMode") == "page_initiated_json"
    if is_live_page_json:
        # GemRate's public JSON `date`/`last_population_change` describes
        # the last reported population change, not proof that the page was
        # stale.  Its current POP was observed live during this run.
        point = {
            "populationPsa10": value,
            "graderPopulations": dict(grader_values),
            "effectiveDate": date.fromisoformat(fetched_at[:10]).isoformat(),
            "effectiveDateSource": "live_public_snapshot_fetch",
            "sourceDate": payload.get("date"),
            "lastPopulationChange": psa_row.get("last_population_change") or payload.get("last_population_change"),
            "transport": WEBSITE_TRANSPORT,
        }
        return point
    effective_date, effective_source = _iso_date(psa_row.get("last_population_change") or payload.get("date"), fetched_at)
    return {
        "populationPsa10": value,
        "graderPopulations": dict(grader_values),
        "effectiveDate": effective_date,
        "effectiveDateSource": effective_source,
        "transport": WEBSITE_TRANSPORT,
    }


def build_public_card_page_payload(
    gemrate_id: str,
    *,
    headers: list[object],
    psa_row: list[object],
    canonical_url: object,
    title: object,
    dom_sha256: object,
    route_verified: object,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a labelled public card-page table and normalize its current PSA10.

    This is intentionally independent of Playwright so fixture tests pin the
    page contract.  The browser only extracts labels/cells and a DOM hash; it
    never supplies a fixed-index population value directly to canonical logic.
    """

    normalize = lambda value: " ".join(str(value or "").split()).upper()
    normalized_headers = [normalize(value) for value in headers]
    if "POP" not in normalized_headers or "GEM MINT" not in normalized_headers:
        return None, "population_table_missing"
    gem_mint_index = normalized_headers.index("GEM MINT")
    if not psa_row or normalize(psa_row[0]) != "PSA" or gem_mint_index >= len(psa_row):
        return None, "psa_gem_mint_missing"
    raw_population = str(psa_row[gem_mint_index] or "")
    compact_population = re.sub(r"[^0-9]", "", raw_population)
    if not compact_population:
        return None, "psa_gem_mint_invalid"
    population = int(compact_population)
    if population < 0:
        return None, "psa_gem_mint_invalid"
    resolved_url = str(canonical_url or "")
    if not bool(route_verified) or not resolved_url.startswith(f"{WEB}/card/"):
        return None, "canonical_route_unverified"
    content_hash = str(dom_sha256 or "")
    if not HEX64.match(content_hash):
        return None, "dom_hash_missing"
    normalized_title = " ".join(str(title or "").split())
    if not normalized_title:
        return None, "canonical_title_missing"
    return {
        "gemrate_id": gemrate_id,
        "population_data": [{"grader": "psa", "grades": {"g10": population}}],
        "publicCardPage": {
            "canonicalUrl": resolved_url,
            "title": normalized_title,
            "domSha256": content_hash,
            "routeVerified": True,
            "populationMode": "dom_labelled_fallback",
        },
    }, None


def build_public_card_page_json_payload(
    gemrate_id: str,
    *,
    response_url: object,
    response_status: object,
    payload: object,
    canonical_url: object,
    title: object,
    dom_sha256: object,
    route_verified: object,
) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize only the exact page-initiated card-details JSON contract.

    A direct fetch of this endpoint is not a supported transport.  The browser
    must first navigate to ``/card/{gemrate_id}`` and this helper verifies that
    the captured JSON request belongs to that same opaque ID.  It deliberately
    returns a small normalized receipt, never the upstream response body.
    """

    parsed = urlparse(str(response_url or ""))
    request_ids = parse_qs(parsed.query).get("gemrate_id", [])
    provider_entity_id = request_ids[0] if len(request_ids) == 1 else ""
    canonical_parts = str(canonical_url or "").split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.gemrate.com"
        or parsed.path != "/card-details"
        or not HEX40.fullmatch(provider_entity_id)
        or provider_entity_id not in canonical_parts
    ):
        return None, "page_initiated_json_route_unverified"
    if response_status != 200:
        return None, f"page_initiated_json_http_{response_status}"
    if not isinstance(payload, Mapping) or str(payload.get("gemrate_id") or "") != provider_entity_id:
        return None, "page_initiated_json_identity_unverified"

    identity_fields = ("year", "set_name", "card_number")
    identity = {field: str(payload.get(field) or "").strip() for field in identity_fields}
    # Some PSA DON!! entries are deliberately unnumbered. Preserve that empty
    # provider value; year and set namespace remain mandatory.
    if not identity["year"] or not identity["set_name"] or "card_number" not in payload:
        return None, "page_initiated_json_identity_incomplete"
    source_date = payload.get("date")
    if not isinstance(source_date, str) or not source_date.strip():
        return None, "page_initiated_json_effective_date_missing"

    rows = payload.get("population_data")
    if not isinstance(rows, list):
        return None, "page_initiated_json_population_missing"
    psa_row: Mapping[str, Any] | None = None
    other_grader_rows: dict[str, tuple[Mapping[str, Any], int]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        key = str(row.get("grader") or "").casefold()
        code = GRADER_CODE_BY_KEY.get(key)
        if code is None:
            continue
        grades = row.get("grades")
        value = _top_grade_value(key, grades) if isinstance(grades, Mapping) else None
        if value is None:
            continue
        if code == "PSA":
            if psa_row is None:
                psa_row = row
        elif code not in other_grader_rows:
            other_grader_rows[code] = (row, value)
    if psa_row is None:
        return None, "page_initiated_json_psa_g10_missing"

    resolved_url = str(canonical_url or "")
    content_hash = str(dom_sha256 or "")
    normalized_title = " ".join(str(title or "").split())
    if not bool(route_verified) or not resolved_url.startswith(f"{WEB}/card/"):
        return None, "canonical_route_unverified"
    if not HEX64.match(content_hash):
        return None, "dom_hash_missing"
    if not normalized_title:
        return None, "canonical_title_missing"

    normalized_psa = {
        "grader": "psa",
        "grades": {"g10": psa_row["grades"]["g10"]},
    }
    last_change = psa_row.get("last_population_change") or payload.get("last_population_change")
    if isinstance(last_change, str) and last_change.strip():
        normalized_psa["last_population_change"] = last_change.strip()
    normalized_rows: list[dict[str, Any]] = [normalized_psa]
    for code in ("CGC", "BGS", "SGC"):
        entry = other_grader_rows.get(code)
        if entry is None:
            continue
        row, value = entry
        key = "beckett" if code == "BGS" else code.casefold()
        normalized_rows.append({
            "grader": str(row.get("grader") or key),
            "grades": {"g10": value},
        })
    return {
        "gemrate_id": gemrate_id,
        "date": source_date.strip(),
        "population_data": normalized_rows,
        PRIVATE_PAGE_JSON_FIELD: dict(payload),
        "publicCardPage": {
            "canonicalUrl": resolved_url,
            "title": normalized_title,
            "domSha256": content_hash,
            "routeVerified": True,
            "populationMode": "page_initiated_json",
            "providerEntityGemrateId": provider_entity_id,
            "jsonStatus": 200,
            "sourceDate": source_date.strip(),
            "lastPopulationChange": last_change.strip() if isinstance(last_change, str) and last_change.strip() else None,
            "identity": {
                **identity,
                "parallel": str(payload.get("parallel") or "").strip() or None,
            },
        },
    }, None


def _transport_counts() -> dict[str, dict[str, int]]:
    return {
        DIRECT_TRANSPORT: {"attempted": 0, "succeeded": 0, "failed": 0},
        WEBSITE_TRANSPORT: {"attempted": 0, "succeeded": 0, "failed": 0},
        MIRROR_TRANSPORT: {"attempted": 0, "succeeded": 0, "failed": 0},
    }


def build_population_transport_run(
    cards: list[Mapping[str, Any]],
    *,
    direct_payloads: Mapping[str, Mapping[str, Any]],
    website_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    mirror_payloads: Mapping[str, Mapping[str, Any]],
    fetched_at: str,
    direct_attempted_ids: set[str] | None = None,
    website_attempted_ids: set[str] | None = None,
    mirror_attempted_ids: set[str] | None = None,
    website_failure_reasons: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve a complete current-population run without doing network I/O.

    ``direct_payloads`` contains only successfully acquired direct API responses.
    ``website_payloads`` contains exact GemRate public card-page receipts and
    ``mirror_payloads`` contains current Grade10 mirror responses. Every
    transport observation is retained. A same-effective-date mismatch is a hard
    failure; a later date wins only after all observations have been preserved.
    """

    website_payloads = website_payloads or {}
    # audit P2-9: "no_current_population" is an assertion about GemRate's data.
    # When the browser died before it issued a request the truthful reason is
    # the transport receipt, not a data verdict, so the caller hands the
    # per-card receipts in and they win over the generic fallback.
    website_failure_reasons = website_failure_reasons or {}
    cards_by_gid: dict[str, Mapping[str, Any]] = {}
    for card in cards:
        gid = str(card.get("gemrateId") or "")
        if not HEX40.match(gid):
            raise PopulationResolutionError("current population run requires exact GemRate IDs")
        if gid in cards_by_gid:
            raise PopulationResolutionError(f"duplicate GemRate ID in population run: {gid}")
        cards_by_gid[gid] = card

    transports = _transport_counts()
    observations: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    mismatch_rows: list[dict[str, Any]] = []

    for gid in sorted(cards_by_gid):
        direct_point = None
        website_point = None
        mirror_point = None
        if gid in direct_payloads or (direct_attempted_ids is not None and gid in direct_attempted_ids):
            transports[DIRECT_TRANSPORT]["attempted"] += 1
            direct_payload = direct_payloads.get(gid)
            direct_point = _direct_population(direct_payload, fetched_at) if direct_payload else None
            if direct_point is None:
                transports[DIRECT_TRANSPORT]["failed"] += 1
            else:
                transports[DIRECT_TRANSPORT]["succeeded"] += 1
        if gid in website_payloads or (website_attempted_ids is not None and gid in website_attempted_ids):
            transports[WEBSITE_TRANSPORT]["attempted"] += 1
            website_payload = website_payloads.get(gid)
            website_point = _website_population(website_payload, fetched_at) if website_payload else None
            if website_point is None:
                transports[WEBSITE_TRANSPORT]["failed"] += 1
            else:
                transports[WEBSITE_TRANSPORT]["succeeded"] += 1
        if gid in mirror_payloads or (mirror_attempted_ids is not None and gid in mirror_attempted_ids):
            transports[MIRROR_TRANSPORT]["attempted"] += 1
            mirror_payload = mirror_payloads.get(gid)
            mirror_point = _mirror_population(mirror_payload, fetched_at) if mirror_payload else None
            if mirror_point is None:
                transports[MIRROR_TRANSPORT]["failed"] += 1
            else:
                transports[MIRROR_TRANSPORT]["succeeded"] += 1

        points = [point for point in (direct_point, website_point, mirror_point) if point is not None]
        for point in points:
            observation = {
                "gemrateId": gid,
                "authority": "gemrate",
                "transport": point["transport"],
                "populationPsa10": point["populationPsa10"],
                "effectiveDate": point["effectiveDate"],
                "effectiveDateSource": point["effectiveDateSource"],
                "sourceDate": point.get("sourceDate"),
                "lastPopulationChange": point.get("lastPopulationChange"),
                "fetchedAt": fetched_at,
                "historyStatus": "unavailable" if point["transport"] == MIRROR_TRANSPORT else "not_collected",
            }
            grader_populations = point.get("graderPopulations")
            if isinstance(grader_populations, Mapping) and grader_populations:
                observation["graderPopulations"] = dict(grader_populations)
            observations.append(observation)

        comparable_points = [point for point in points if point["transport"] != WEBSITE_TRANSPORT]
        for left_index, left in enumerate(comparable_points):
            for right in comparable_points[left_index + 1:]:
                if (
                    left["effectiveDate"] == right["effectiveDate"]
                    and left["populationPsa10"] != right["populationPsa10"]
                ):
                    mismatch_rows.append(
                        {
                            "gemrateId": gid,
                            "effectiveDate": left["effectiveDate"],
                            "leftTransport": left["transport"],
                            "leftPsa10": left["populationPsa10"],
                            "rightTransport": right["transport"],
                            "rightPsa10": right["populationPsa10"],
                        }
                    )
                    break
            else:
                continue
            break
        if any(row["gemrateId"] == gid for row in mismatch_rows):
            continue

        if not points:
            unresolved.append({
                "gemrateId": gid,
                "reason": website_failure_reasons.get(gid, "no_current_population"),
            })
            continue
        selected = max(
            points,
            key=lambda point: (
                {DIRECT_TRANSPORT: 2, WEBSITE_TRANSPORT: 1, MIRROR_TRANSPORT: 0}[point["transport"]],
                point["effectiveDate"],
            ),
        )
        resolved_row = {
            "gemrateId": gid,
            "populationPsa10": selected["populationPsa10"],
            "effectiveDate": selected["effectiveDate"],
            "effectiveDateSource": selected["effectiveDateSource"],
            "authority": "gemrate",
            "transport": selected["transport"],
        }
        selected_graders = selected.get("graderPopulations")
        if isinstance(selected_graders, Mapping) and selected_graders:
            resolved_row["graderPopulations"] = dict(selected_graders)
        resolved.append(resolved_row)

    manifest = {
        "schemaVersion": "2.0.0",
        "fetchedAt": fetched_at,
        "attempted": len(cards_by_gid),
        "succeeded": len(resolved),
        "failed": len(unresolved) + len(mismatch_rows),
        "partial": bool(unresolved or mismatch_rows),
        "promotable": not unresolved and not mismatch_rows,
        "transports": transports,
        "observations": sorted(observations, key=lambda row: (row["gemrateId"], row["effectiveDate"], row["transport"])),
        "resolved": sorted(resolved, key=lambda row: row["gemrateId"]),
        "unresolved": sorted(unresolved, key=lambda row: row["gemrateId"]),
        "history": {
            "status": "unavailable",
            "reason": "current_transports_have_no_population_history" if not direct_payloads else "population_history_not_collected",
        },
    }
    if mismatch_rows:
        manifest["mismatches"] = mismatch_rows
        raise PopulationResolutionError("GemRate direct and mirror disagree on the same effective date", manifest)
    return manifest


def load_tracked_identity_cards(path: Path, ids: list[str]) -> list[dict[str, Any]]:
    """Load exact tracked cards for mirror routing; never infer a source path."""

    cards_by_gid: dict[str, dict[str, Any]] = {}
    if path.is_file():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            source_cards = document.get("cards") if isinstance(document, Mapping) else None
            if isinstance(source_cards, list):
                for raw in source_cards:
                    if not isinstance(raw, Mapping):
                        continue
                    gid = str(raw.get("gemrateId") or "")
                    if HEX40.match(gid):
                        cards_by_gid[gid] = dict(raw)
        except (OSError, ValueError):
            pass
    return [cards_by_gid.get(gid, {"gemrateId": gid}) for gid in ids]


def read_grade10_mirror(root: Path, card: Mapping[str, Any], fetched_at: str) -> dict[str, Any] | None:
    """Read only an exact Grade10 mirror path for one known canonical card."""

    scope = str(card.get("storageScope") or card.get("canonicalSourceCode") or "").casefold()
    external_id = str(card.get("canonicalExternalId") or "")
    if not scope or not external_id:
        return None
    path = root / "cards" / scope / external_id / "populations.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    # Grade10's payload has no historical population series. Its file timestamp
    # is deliberately not promoted to a historical source date; only a current
    # run fetched date may act as the effective current observation date.
    copy = dict(payload)
    copy.setdefault("effectiveDate", fetched_at[:10])
    return copy


def fetch_card(gid: str, key: str, delay: float, history: bool = True,
               cards_dir: Path = CARDS_DIR) -> tuple[bool, str]:
    """Fetch one card's population (+ optional full history). Returns (ok, note)."""
    st, pop = _api_get(f"/cards/{gid}/population?parsed_description=true", key, delay)
    if st in (401, 403):
        return False, f"KEY_DEAD_{st}"
    if st != 200 or not isinstance(pop, dict):
        return False, f"POP_HTTP_{st}"
    hist = None
    if history:
        # Official API history is sampled at week/two_week intervals (default
        # two_week) over its available trailing history.  Keep the highest
        # documented public sampling here; raw daily-grade population is not
        # silently inferred from those weekly points.
        st, hist = _api_get(f"/cards/{gid}/population/history?interval=week", key, delay)
        if st in (401, 403):
            return False, f"KEY_DEAD_{st}"
        if st != 200 or not isinstance(hist, dict):
            return False, f"HISTORY_HTTP_{st}"
    try:
        receipt = build_direct_identity_receipt(
            gid,
            pop,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )
    except ValueError:
        return False, "IDENTITY_RECEIPT_INVALID"
    cdir = cards_dir / gid
    _save(cdir / "population.json", pop)
    if history and hist is not None:
        _save(cdir / "history_full.json", hist)
    _save(cdir / "identity.receipt.json", receipt)
    return True, "ok"


def fetch_current_population(gid: str, key: str, delay: float) -> tuple[dict[str, Any] | None, str]:
    """Fetch only the current direct population payload for one exact GemRate ID."""

    st, payload = _api_get(f"/cards/{gid}/population?parsed_description=true", key, delay)
    if st in (401, 403):
        return None, f"KEY_DEAD_{st}"
    if st != 200 or not isinstance(payload, dict):
        return None, f"POP_HTTP_{st}"
    return payload, "ok"


def _has_verified_direct_identity_receipt(
    receipt_path: Path,
    requested_gid: str,
    payload: Mapping[str, Any],
) -> bool:
    """Return true only when a receipt still proves this exact raw payload."""
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(receipt, Mapping) or not isinstance(receipt.get("fetchedAt"), str) or not receipt["fetchedAt"]:
            return False
        expected = build_direct_identity_receipt(
            requested_gid, payload, receipt["fetchedAt"], source_pointer="population.json",
        )
    except (OSError, ValueError, TypeError):
        return False
    return all(receipt.get(field) == expected[field] for field in (
        "schemaVersion", "requestedGemrateId", "entityGemrateId", "universalGemrateId",
        "isUniversalMatch", "parsedDescription", "graders", "payloadSha256", "sourcePointer",
    ))


def rebuild_direct_identity_receipts(cards_dir: Path = CARDS_DIR, *, resume: bool = False) -> dict[str, Any]:
    """Rebuild direct identity receipts from private population cache without HTTP."""
    result: dict[str, Any] = {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "cached": 0,
        "failures": [],
    }
    if not cards_dir.is_dir():
        return result
    for population_path in sorted(cards_dir.glob("*/population.json"), key=lambda path: path.parent.name):
        card_dir = population_path.parent
        requested_gid = card_dir.name
        result["attempted"] += 1
        if not HEX40.fullmatch(requested_gid):
            result["failed"] += 1
            result["failures"].append({"gemrateId": requested_gid, "reason": "identity_receipt_parent_gid_invalid"})
            continue
        try:
            payload = json.loads(population_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("direct_identity_payload_invalid")
            fetched_at = datetime.fromtimestamp(
                population_path.stat().st_mtime, timezone.utc,
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if resume and _has_verified_direct_identity_receipt(
                card_dir / "identity.receipt.json", requested_gid, payload,
            ):
                result["cached"] += 1
                continue
            receipt = build_direct_identity_receipt(
                requested_gid, payload, fetched_at, source_pointer="population.json",
            )
        except FileNotFoundError:
            reason = "identity_receipt_population_missing"
        except json.JSONDecodeError:
            reason = "identity_receipt_population_json_invalid"
        except OSError:
            reason = "identity_receipt_population_read_failed"
        except ValueError as error:
            reason = str(error)
        else:
            _save(card_dir / "identity.receipt.json", receipt)
            result["succeeded"] += 1
            continue
        result["failed"] += 1
        result["failures"].append({"gemrateId": requested_gid, "reason": reason})
    return result


def cmd_identity_receipts(args) -> int:
    """Offline repair for direct identity receipts; never calls the GemRate API."""
    cards_dir = Path(args.cards_dir) if args.cards_dir else CARDS_DIR
    result = rebuild_direct_identity_receipts(cards_dir, resume=args.resume)
    for failure in result["failures"][:3]:
        print(f"[identity-receipts] {failure['gemrateId']}: {failure['reason']}", file=sys.stderr)
    if len(result["failures"]) > 3:
        print(f"[identity-receipts] {len(result['failures']) - 3} additional failures retained", file=sys.stderr)
    print(
        f"[identity-receipts] attempted={result['attempted']} succeeded={result['succeeded']} "
        f"failed={result['failed']} cached={result['cached']}",
    )
    return 0 if result["failed"] == 0 else 1


def _run_harvest(ids: list[str], key: str, delay: float, resume: bool,
                 history: bool, limit: int | None, cards_dir: Path = CARDS_DIR) -> int:
    cards_dir.mkdir(parents=True, exist_ok=True)
    done = fail = 0
    for gid in ids:
        if resume and (cards_dir / gid / ("history_full.json" if history else "population.json")).exists():
            card_dir = cards_dir / gid
            try:
                cached_payload = json.loads((card_dir / "population.json").read_text(encoding="utf-8"))
                if not isinstance(cached_payload, Mapping):
                    raise ValueError("direct_identity_payload_invalid")
                if _has_verified_direct_identity_receipt(
                    card_dir / "identity.receipt.json", gid, cached_payload,
                ):
                    done += 1
                    continue
                cached_fetched_at = datetime.fromtimestamp(
                    (card_dir / "population.json").stat().st_mtime, timezone.utc,
                ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                _save(
                    card_dir / "identity.receipt.json",
                    build_direct_identity_receipt(gid, cached_payload, cached_fetched_at),
                )
                done += 1
                continue
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        ok, note = fetch_card(gid, key, delay, history=history, cards_dir=cards_dir)
        if note.startswith("KEY_DEAD"):
            print(f"\nKEY LOST ACCESS ({note}). {done} done, {fail} failed. "
                  f"Re-run with --resume after fixing the key.", file=sys.stderr)
            return 3
        if note.endswith("_429"):
            # The daily request quota is spent. _api_get has already burned its
            # retry ladder on this card; every remaining card would do the same
            # for nothing. Stop instead of hammering a quota that cannot serve.
            print(f"\nQUOTA SPENT ({note}) after {done} cards, {fail} failed. "
                  f"Re-run with --resume when the quota window reopens.", file=sys.stderr)
            return 4
        if ok:
            done += 1
            if done % 10 == 0:
                print(f"  …{done} cards", file=sys.stderr)
        else:
            fail += 1
        time.sleep(delay)
        if limit and done >= limit:
            break
    print(f"Harvest ok={done} fail={fail} of {len(ids)}")
    return 0


def cmd_daily(args) -> int:
    """Refresh current POP through direct API, then exact Grade10 mirror fallback.

    The private run directory is immutable staging. It is copied to the durable
    direct cache only when every tracked card resolves and no same-day direct /
    mirror disagreement exists. Grade10 can fill current observations without a
    key, but is never allowed to generate a population history file.
    """
    key = os.environ.get("GEMRATE_API_KEY", "")
    ids = load_ids(Path(args.ids_file) if args.ids_file else None)
    if not ids:
        print(f"No ids in {args.ids_file or IDS_FILE}. Run `collect` first.", file=sys.stderr)
        return 2
    delay = SPEEDS[args.speed]
    run_id = datetime.now(timezone.utc).strftime("daily_%Y%m%dT%H%M%S%fZ")
    run_suffix = str(getattr(args, "run_suffix", "") or "").strip()
    if run_suffix:
        if not re.fullmatch(r"[a-z0-9-]{1,32}", run_suffix):
            raise ValueError("--run-suffix must be lowercase alphanumeric/hyphen")
        run_id = f"{run_id}_{run_suffix}"
    run_root = OUT_DIR / "runs" / run_id
    run_cards = run_root / "cards"
    selected_ids = ids[:args.limit] if args.limit else ids
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    identity_file = Path(args.identity_file) if args.identity_file else DEFAULT_TRACKED_IDENTITIES
    cards = load_tracked_identity_cards(identity_file, selected_ids)
    mirror_root = Path(args.mirror_root) if args.mirror_root else DEFAULT_G10_MIRROR_ROOT
    direct_payloads: dict[str, Mapping[str, Any]] = {}
    website_payloads: dict[str, Mapping[str, Any]] = {}
    mirror_payloads: dict[str, Mapping[str, Any]] = {}
    direct_receipts: dict[str, dict[str, Any]] = {}
    direct_identity_failures: list[dict[str, str]] = []
    direct_attempted: set[str] = set()
    website_attempted: set[str] = set()
    mirror_attempted: set[str] = set()
    # audit P2-9 / P2-10: keep the real per-card transport verdicts instead of
    # letting them die inside collect_public_card_details.
    website_failure_receipts: list[dict[str, Any]] = []
    # audit item 10: a tick-deadline SIGTERM must not discard the cards this
    # run already fetched; stop dispatching and fall through to a partial run.
    _install_shutdown_handlers()

    print(
        f"[daily] {len(cards)} cards, direct={'enabled' if key else 'disabled'}, "
        f"public-card-page={'standby' if key else 'enabled'}, "
        f"mirror={'enabled' if mirror_root.is_dir() else 'unavailable'}",
        file=sys.stderr,
    )
    if key:
        for gid in selected_ids:
            if _SHUTDOWN_EVENT.is_set():
                break
            direct_attempted.add(gid)
            payload, note = fetch_current_population(gid, key, delay)
            if payload is not None:
                try:
                    receipt = build_direct_identity_receipt(gid, payload, fetched_at)
                except ValueError as error:
                    direct_identity_failures.append({"gemrateId": gid, "reason": str(error)})
                    print(f"[daily] direct {gid}: {error}", file=sys.stderr)
                else:
                    direct_payloads[gid] = payload
                    direct_receipts[gid] = receipt
                    _save(run_cards / gid / "population.json", payload)
                    _save(run_cards / gid / "identity.receipt.json", receipt)
            else:
                print(f"[daily] direct {gid}: {note}", file=sys.stderr)
                # A 429 means the key's daily request quota is spent, not that this one
                # card blipped: every later call returns 429 too. Measured 2026-07-26 --
                # a clean cut at request ~1000 of a 1468-card roster, so the roster can
                # never be covered by the direct API alone. Burning the rest of the loop
                # on calls that are all guaranteed to 429 just delays the fallback, so
                # stop and let the public card page take the remainder.
                if note == "POP_HTTP_429":
                    print(
                        f"[daily] direct quota spent after {len(direct_payloads)} cards; "
                        f"{len(selected_ids) - len(direct_attempted)} remaining cards fall to "
                        "the public card page",
                        file=sys.stderr,
                    )
                    break
            time.sleep(delay)
    website_ids = list(dict.fromkeys(gid for gid in selected_ids if gid not in direct_payloads))
    if website_ids:
        # The browser pass is the slowest transport and the caller wraps this
        # process in a hard --pipeline-timeout-seconds. Overrunning it kills
        # the process and discards every result already written this run, which
        # is strictly worse than returning a partial run: a partial run at
        # least leaves its staged payloads on disk and lets the rest of the
        # daily chain proceed. collect_public_card_details enforces the budget
        # as a monotonic deadline and turns undispatched cards into
        # budget_exhausted receipts instead of silently dropping them.
        budget = getattr(args, "website_budget_seconds", None) or DEFAULT_WEBSITE_BUDGET_SECONDS
        deadline = time.monotonic() + budget
        website_attempted.update(website_ids)
        workers = max(1, int(getattr(args, "workers", 0) or 4))
        pending = list(website_ids)
        session_errors = 0
        for pass_workers, pass_delay, label in (
            # audit P2-11: the caller already picked a delay (--speed fast is
            # 0.15s); the old max(0.3, delay) floor silently doubled it with no
            # measurement behind it and no 429 ever observed.
            (workers, delay, "first"),
            (1, SPEEDS["slow"], "slow retry"),
        ):
            if not pending or time.monotonic() >= deadline or _SHUTDOWN_EVENT.is_set():
                break
            if label != "first":
                print(
                    f"[daily] public card page {label} for {len(pending)} cards",
                    file=sys.stderr,
                )
            outcome = collect_public_card_details(
                pending,
                cards_dir=run_cards,
                delay=pass_delay,
                resume=False,
                chunk_size=WEBSITE_CHUNK,
                workers=pass_workers,
                deadline=deadline,
                payload_sink=website_payloads,
            )
            website_failure_receipts.extend(outcome.get("failureReceipts") or [])
            if outcome["error"]:
                print(
                    f"[daily] public card page unavailable: {outcome['error']}",
                    file=sys.stderr,
                )
                # audit P1-6: one session-level exception used to break out of
                # the pass loop, which disabled the very slow-retry pass that
                # exists to rescue these cards (2026-08-24: 399/399 lost).
                # A session that harvested nothing counts towards the give-up
                # limit; one that harvested something resets it.
                session_errors = session_errors + 1 if not outcome["succeeded"] else 0
                if session_errors >= WEBSITE_SESSION_ERROR_LIMIT:
                    break
            # A per-page failure (CF challenge timing, browser evaluation) usually
            # clears on one slower retry, so whatever the fast pass missed gets a
            # second, gentler attempt inside the same budget.
            pending = [gid for gid in pending if gid not in website_payloads]
        if pending and time.monotonic() >= deadline:
            print(
                f"[daily] public card page budget spent; {len(pending)} cards unresolved",
                file=sys.stderr,
            )
    if mirror_root.is_dir():
        for card in cards:
            gid = str(card["gemrateId"])
            mirror_attempted.add(gid)
            payload = read_grade10_mirror(mirror_root, card, fetched_at)
            if payload is not None:
                mirror_payloads[gid] = payload
                _save(run_cards / gid / "population_mirror.json", payload)

    # audit P2-10: the run dir keeps the exact per-card transport verdicts,
    # including the browser exception class name, so the next outage does not
    # have to be diagnosed by back-inference. Last verdict per card wins.
    website_failure_reasons = {
        str(row.get("gemrateId") or ""): str(row.get("reason") or "unknown")
        for row in website_failure_receipts
        if str(row.get("gemrateId") or "")
    }
    if website_failure_receipts:
        _save(
            run_root / "website_transport_receipts.json",
            {
                "schemaVersion": "1.0.0",
                "runId": run_id,
                "fetchedAt": fetched_at,
                "interrupted": _SHUTDOWN_EVENT.is_set(),
                "reasons": website_failure_reasons,
            },
        )
    try:
        manifest = build_population_transport_run(
            cards,
            direct_payloads=direct_payloads,
            website_payloads=website_payloads,
            mirror_payloads=mirror_payloads,
            fetched_at=fetched_at,
            direct_attempted_ids=direct_attempted,
            website_attempted_ids=website_attempted,
            mirror_attempted_ids=mirror_attempted,
            website_failure_reasons=website_failure_reasons,
        )
    except PopulationResolutionError as error:
        manifest = dict(error.manifest)
        manifest.update({"runId": run_id, "historyIncluded": False, "promoted": False})
        if website_failure_reasons:
            manifest["websiteFailureReceipts"] = website_failure_reasons
        if _SHUTDOWN_EVENT.is_set():
            manifest["interrupted"] = True
        if direct_identity_failures:
            manifest["directIdentityReceiptFailures"] = direct_identity_failures
        _save(run_root / "manifest.json", manifest)
        print(f"[daily] rejected: {error}", file=sys.stderr)
        return 1

    manifest.update({"runId": run_id, "historyIncluded": False, "promoted": False})
    if website_failure_reasons:
        manifest["websiteFailureReceipts"] = website_failure_reasons
    if _SHUTDOWN_EVENT.is_set():
        # audit item 10: a declared-partial manifest is what lets the caller
        # ingest the cards this run did fetch. The flag keeps the lane failing
        # -- an interrupted run is never a clean verdict.
        manifest.update({"partial": True, "promotable": False, "interrupted": True})
    if direct_identity_failures:
        manifest.update({"partial": True, "promotable": False, "promoted": False})
        manifest["directIdentityReceiptFailures"] = direct_identity_failures
    _save(run_root / "manifest.json", manifest)
    if not manifest["promotable"]:
        print("[daily] partial current-population run; durable cache/checkpoint unchanged", file=sys.stderr)
        return 1

    rc = 0
    if args.with_history and not args.no_history and key:
        # Full history is explicitly a direct-API backfill. The Grade10 mirror
        # never fills this gap and daily current POP remains promotable without it.
        history_rc = _run_harvest(selected_ids, key, delay, resume=False, history=True,
                                  limit=args.limit, cards_dir=run_cards)
        if history_rc == 0:
            manifest["history"] = {"status": "ready", "transport": DIRECT_TRANSPORT}
            manifest["historyIncluded"] = True
        else:
            manifest.update({"partial": True, "promotable": False, "promoted": False})
            manifest["history"] = {"status": "unavailable", "reason": "direct_history_refresh_failed"}
            _save(run_root / "manifest.json", manifest)
            rc = history_rc
    if rc != 0:
        return rc

    # Promote only after every requested transport has completed. Direct API
    # payloads retain their legacy cache path; mirror evidence remains separate.
    for gid, payload in direct_payloads.items():
        _save(CARDS_DIR / gid / "population.json", payload)
        _save(CARDS_DIR / gid / "identity.receipt.json", direct_receipts[gid])
    for gid, payload in website_payloads.items():
        _persist_public_card_capture(CARDS_DIR, gid, payload)
    for gid, payload in mirror_payloads.items():
        _save(CARDS_DIR / gid / "population_mirror.json", payload)
    for resolved in manifest["resolved"]:
        gid = str(resolved["gemrateId"])
        current = {
            "schemaVersion": "1.0.0",
            "authority": "gemrate",
            "transport": resolved["transport"],
            "populationPsa10": resolved["populationPsa10"],
            "effectiveDate": resolved["effectiveDate"],
            "effectiveDateSource": resolved["effectiveDateSource"],
            "fetchedAt": fetched_at,
        }
        resolved_graders = resolved.get("graderPopulations")
        if isinstance(resolved_graders, Mapping) and resolved_graders:
            current["graderPopulations"] = dict(resolved_graders)
        _save(CARDS_DIR / gid / "current.json", current)
    if manifest.get("historyIncluded"):
        for gid in selected_ids:
            history_file = run_cards / gid / "history_full.json"
            if history_file.is_file():
                _save(CARDS_DIR / gid / "history_full.json", json.loads(history_file.read_text(encoding="utf-8")))
        export_csv_to(OUT_DIR / "population_history.csv")
    manifest["promoted"] = True
    _save(run_root / "manifest.json", manifest)
    if rc == 0 and not bool(getattr(args, "skip_grader_volume", False)):
        # Also refresh the grader-level volume (incl TAG) alongside population.
        # Never let this break the population run. Recap pages need a real Chrome
        # (Cloudflare); skip quietly if playwright/Chrome isn't available.
        try:
            slug = args.volume_slug
            if not slug:
                slugs = _discover_recap_slugs(limit=1)
                slug = slugs[0] if slugs else None
            rec = None
            if slug:
                try:
                    texts = _chrome_pages_eval([(f"/{slug}", "document.body.innerText")])
                    rec = fetch_monthly_recap(slug, live_text=texts[0] if texts else None)
                except RuntimeError:
                    rec = fetch_monthly_recap(slug)  # fallback (may 403)
            if rec:
                _save(OUT_DIR / "grader_volume.json",
                      {"period": "monthly", "runId": run_id,
                       "generated_at": manifest["fetchedAt"], "months": [rec]})
                print(f"[daily] grader volume ({rec['month']}): " + ", ".join(
                    f"{g}={d['items_graded']:,}" for g, d in sorted(rec["graders"].items())))
        except Exception as e:
            print(f"[daily] grader volume capture skipped: {e}", file=sys.stderr)
        print(f"[daily] done -> {run_root}")
    return rc


def cmd_api_dump(args) -> int:
    key = os.environ.get("GEMRATE_API_KEY", "")
    if not key:
        print("GEMRATE_API_KEY not set", file=sys.stderr)
        return 2
    ids = load_ids(Path(args.ids_file) if args.ids_file else None)
    if not ids:
        print("No ids to dump.", file=sys.stderr)
        return 2
    delay = SPEEDS[args.speed]
    print(f"[api-dump] {len(ids)} cards, speed={args.speed}")
    return _run_harvest(ids, key, delay, resume=args.resume, history=True, limit=args.limit)


# --------------------------------------------------------------------------
# Free (no key) acquisition via a real Chrome — bypasses Cloudflare
# --------------------------------------------------------------------------

_CHROME_CANDIDATES = [
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]
_BROWSER_EXECUTABLE_ENV = "CARDZ_BROWSER_EXECUTABLE"

_STEALTH = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "window.chrome={runtime:{}};"
)


def _find_chrome() -> str | None:
    configured = os.environ.get(_BROWSER_EXECUTABLE_ENV, "").strip()
    if configured:
        return configured
    for p in _CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        resolved = shutil.which(command)
        if resolved:
            return resolved
    return None


def _launch_chromium(pw):
    """Launch an explicit system browser when configured, else Playwright Chromium."""

    executable = _find_chrome()
    launch_args: dict[str, Any] = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    }
    if executable:
        launch_args["executable_path"] = executable
    try:
        return pw.chromium.launch(**launch_args)
    except Exception as error:
        selected = f"{_BROWSER_EXECUTABLE_ENV}={executable}" if os.environ.get(_BROWSER_EXECUTABLE_ENV) else (
            f"system browser {executable}" if executable else "Playwright managed Chromium"
        )
        raise RuntimeError(
            f"GemRate browser launch failed ({selected}). Install the pinned browser with "
            "`python -m playwright install chromium`, or set CARDZ_BROWSER_EXECUTABLE."
        ) from error


def _chrome_eval(js: str, timeout: int = 90):
    """Run JS inside a real (headless) Chrome so Cloudflare clears us.

    Requires the pinned Playwright package. Uses an operator-supplied/system
    browser when available and Playwright-managed Chromium otherwise.
    Returns the JS value (JSON-serialisable).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("collect/pop-report need the pinned Playwright dependency")
    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900})
        ctx.add_init_script(_STEALTH)
        ctx.route("**/*", _abort_heavy_resources)
        page = ctx.new_page()
        # warm up on a real page to acquire the CF clearance cookie
        page.goto(WEB + "/universal-pop-report", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        result = page.evaluate(js)
        browser.close()
        return result


def _chrome_pages_eval(paths_and_js: list[tuple[str, str]], timeout: int = 120) -> list:
    """Open several pages in ONE real-Chrome session (Cloudflare-cleared) and run a
    JS expression on each. Returns a list of results in the same order.
    Falls back to direct _request for any page that returns 200 without CF."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("grader-volume needs the pinned Playwright dependency")
    results: list = []
    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900})
        ctx.add_init_script(_STEALTH)
        ctx.route("**/*", _abort_heavy_resources)
        page = ctx.new_page()
        page.goto(WEB + "/universal-pop-report", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)  # acquire CF clearance once
        for path, js in paths_and_js:
            try:
                page.goto(WEB + path, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1200)
                results.append(page.evaluate(js))
            except Exception:
                results.append(None)
        browser.close()
    return results


def _public_failure_receipt(gid: str, *, http_status: int | None, reason: str) -> dict[str, Any]:
    """Return a safe, private per-ID transport receipt without response bodies."""

    return {
        "gemrateId": gid,
        "httpStatus": http_status,
        "reason": reason,
    }


def _remove_page_listener(page: Any, event: str, callback: Any) -> None:
    """Use the Playwright Python listener API, not the Node-only ``page.off``."""

    page.remove_listener(event, callback)


def _fetch_card_once(
    page: Any, gid: str,
) -> tuple[Mapping[str, Any] | None, dict[str, Any] | None, bool]:
    """One capture attempt for one card page.

    Returns ``(payload, failure_receipt, rate_limited)``. ``rate_limited`` is
    True when the page itself or its ``/card-details`` JSON came back 429 and
    no valid payload was captured, so the caller can climb the backoff ladder
    instead of recording a misleading DOM failure.
    """

    initiated_json: list[dict[str, Any]] = []

    def capture_page_json(response: Any) -> None:
        """Capture only the response requested by this exact card page."""

        parsed = urlparse(response.url)
        request_ids = parse_qs(parsed.query).get("gemrate_id", [])
        if (
            parsed.path != "/card-details"
            or len(request_ids) != 1
            or not HEX40.fullmatch(request_ids[0])
        ):
            return
        try:
            body = response.json() if response.status == 200 else None
        except Exception:
            body = None
        initiated_json.append({
            "url": response.url,
            "status": response.status,
            "body": body,
        })

    page.on("response", capture_page_json)
    try:
        response = page.goto(
            f"{WEB}/card/{gid}", wait_until="domcontentloaded", timeout=60000,
        )
        if response is not None:
            status = int(response.status)
            if status == 429:
                # Rate-limited before render; skip the DOM poll entirely.
                return None, None, True
            if status in (404, 410):
                # Dead/retired card page: the SPA shell renders with no table
                # and no /card-details JSON ever fires. Waiting the JSON + DOM
                # windows burned ~25s per dead id for a verdict already decided
                # by the document status. 403/5xx stay on the slow path — a CF
                # challenge page self-resolves and the JSON can still arrive.
                return None, _public_failure_receipt(
                    gid, http_status=status, reason=f"page_http_{status}",
                ), False
        # Primary transport first: the page-initiated /card-details JSON
        # usually settles within seconds. Only when it never arrives do we pay
        # for the DOM-table poll (fail-closed fallback, unchanged semantics).
        json_deadline = time.monotonic() + 10.0
        while time.monotonic() < json_deadline:
            if any(
                entry.get("status") == 200 and isinstance(entry.get("body"), Mapping)
                for entry in initiated_json
            ) or any(entry.get("status") == 429 for entry in initiated_json):
                break
            # audit P2-11: a 200ms poll overshoots the arrival of the
            # /card-details response by ~100ms on average; at ~400 cards a
            # shard that is ~40s of pure sleep per shard.
            page.wait_for_timeout(50)
        has_page_json = any(
            entry.get("status") == 200 and isinstance(entry.get("body"), Mapping)
            for entry in initiated_json
        )
        if not has_page_json and any(
            entry.get("status") == 429 for entry in initiated_json
        ):
            # Rate-limited JSON and nothing usable; climb the ladder now
            # instead of paying for a DOM poll whose verdict would be a lie.
            return None, None, True
        page_data = page.evaluate(
            """async (args) => {
                const norm = (value) => String(value || "")
                  .replace(/\\s+/g, " ").trim().toUpperCase();
                const findTable = () => Array.from(document.querySelectorAll("table")).find((candidate) => {
                  const headers = Array.from(candidate.querySelectorAll("thead th"))
                    .map((cell) => norm(cell.textContent));
                  return headers.includes("POP") && headers.includes("GEM MINT");
                });
                const hasPsaRow = (table) => table && Array.from(table.querySelectorAll("tbody tr")).some((row) => {
                  const cells = Array.from(row.querySelectorAll("th,td"));
                  return norm(cells[0] && cells[0].textContent) === "PSA";
                });
                const deadline = Date.now() + args.waitMs;
                // The population table can render well after domcontentloaded.
                // Poll (bounded) before declaring it missing so slow cards are
                // not misclassified; with page JSON in hand one attempt is enough.
                let table = findTable();
                while (!hasPsaRow(table) && Date.now() < deadline) {
                  await new Promise((resolve) => setTimeout(resolve, 500));
                  table = findTable();
                }
                const html = document.documentElement.outerHTML;
                const digest = await crypto.subtle.digest(
                  "SHA-256", new TextEncoder().encode(html)
                );
                const htmlSha256 = Array.from(new Uint8Array(digest))
                  .map((byte) => byte.toString(16).padStart(2, "0")).join("");
                const h1 = document.querySelector("h1");
                const routeVerified = location.origin === "https://www.gemrate.com"
                  && location.pathname.startsWith("/card/") && !!h1;
                const meta = {
                  canonicalUrl: location.href,
                  title: h1 ? String(h1.textContent || "").trim() : "",
                  domSha256: htmlSha256,
                  routeVerified,
                };
                if (!routeVerified) return {__failureReason: "canonical_route_unverified", ...meta};
                if (!table) return {__failureReason: "population_table_missing", ...meta};
                const headers = Array.from(table.querySelectorAll("thead th"))
                  .map((cell) => norm(cell.textContent));
                const gemMintIndex = headers.indexOf("GEM MINT");
                const psaRow = Array.from(table.querySelectorAll("tbody tr")).find((row) => {
                  const cells = Array.from(row.querySelectorAll("th,td"));
                  return norm(cells[0] && cells[0].textContent) === "PSA";
                });
                if (!psaRow || gemMintIndex < 0) return {__failureReason: "psa_gem_mint_missing", ...meta};
                const cells = Array.from(psaRow.querySelectorAll("th,td"));
                return {
                  ...meta,
                  headers,
                  psaRow: cells.map((cell) => String(cell.textContent || "").trim()),
                };
            }""",
            {"gemrateId": gid, "waitMs": 0 if has_page_json else 15000},
        )
        dom_payload: dict[str, Any] | None = None
        dom_reason: str | None = None
        if isinstance(page_data, Mapping) and not page_data.get("__failureReason"):
            headers = page_data.get("headers")
            psa_row = page_data.get("psaRow")
            dom_payload, dom_reason = build_public_card_page_payload(
                gid,
                headers=list(headers) if isinstance(headers, list) else [],
                psa_row=list(psa_row) if isinstance(psa_row, list) else [],
                canonical_url=page_data.get("canonicalUrl"),
                title=page_data.get("title"),
                dom_sha256=page_data.get("domSha256"),
                route_verified=page_data.get("routeVerified"),
            )
        page_json = next(
            (entry for entry in initiated_json if entry.get("status") == 200 and isinstance(entry.get("body"), Mapping)),
            None,
        )
        if page_json is None and any(
            entry.get("status") == 429 for entry in initiated_json
        ):
            # The page-initiated JSON was rate-limited and nothing usable
            # arrived; a DOM "table missing" verdict here would be a lie.
            return None, None, True
        if isinstance(page_json, Mapping) and isinstance(page_data, Mapping):
            payload, reason = build_public_card_page_json_payload(
                gid,
                response_url=page_json.get("url"),
                response_status=page_json.get("status"),
                payload=page_json.get("body"),
                canonical_url=page_data.get("canonicalUrl"),
                title=page_data.get("title"),
                dom_sha256=page_data.get("domSha256"),
                route_verified=page_data.get("routeVerified"),
            )
            page_data = payload or dom_payload or {
                "__failureReason": reason or dom_reason or "page_initiated_json_invalid",
            }
        else:
            page_data = dom_payload or {
                "__failureReason": dom_reason or (
                    page_data.get("__failureReason") if isinstance(page_data, Mapping) else "invalid_payload"
                ) or "invalid_payload",
            }
        if (
            isinstance(page_data, Mapping)
            and page_data.get("gemrate_id") == gid
            and _website_population(page_data, datetime.now(timezone.utc).isoformat()) is not None
        ):
            return page_data, None, False
        if isinstance(page_data, Mapping):
            return None, _public_failure_receipt(
                gid,
                http_status=int(response.status) if response is not None else None,
                reason=str(page_data.get("__failureReason") or "invalid_payload"),
            ), False
        return None, _public_failure_receipt(
            gid, http_status=None, reason="invalid_payload",
        ), False
    except Exception:
        return None, _public_failure_receipt(
            gid, http_status=None, reason="browser_evaluation_failed",
        ), False
    finally:
        _remove_page_listener(page, "response", capture_page_json)


_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


def _abort_heavy_resources(route: Any) -> None:
    """Skip bytes that never influence the DOM markup we hash or parse."""

    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        route.abort()
    else:
        route.continue_()


@contextmanager
def _gemrate_public_page() -> Iterator[Any]:
    """One headed-capable Chrome session for the whole public-page shard.

    Relaunching Playwright Chrome every 25 cards hangs on this host
    (2026-08-19/20 nightly and catch-up): first chunk succeeds, the next
    ``chromium.launch`` never returns. Keep the browser open; persist in
    the caller after each chunk so a kill still keeps finished cards.
    """

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("public-card-dump needs the pinned Playwright dependency")
    with sync_playwright() as pw:
        # audit P0-2: this is the only network call in the whole website
        # transport without a retry, and it gates every card in the shard. Two
        # whole-shard 399/399 outages (2026-08-20, 2026-08-23) died here ~1.5s
        # into the session while sibling shards launched Chrome in the same
        # second. Retry the launch+context+warm-up as one unit, closing the
        # half-dead browser in between, and only raise on the last attempt.
        browser = None
        for attempt, backoff in enumerate(
            (*WEBSITE_SESSION_RETRY_BACKOFF, None), start=1
        ):
            try:
                browser = _launch_chromium(pw)
                ctx = browser.new_context(
                    user_agent=UA, viewport={"width": 1366, "height": 900}
                )
                ctx.add_init_script(_STEALTH)
                ctx.route("**/*", _abort_heavy_resources)
                page = ctx.new_page()
                page.goto(
                    WEB + "/universal-pop-report",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.wait_for_timeout(3000)
                break
            except Exception as error:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser = None
                if backoff is None:
                    # Keep the original exception class: _safe_browser_error
                    # turns it into the receipt reason (audit P2-10).
                    raise
                print(
                    f"[daily] public card page session attempt {attempt} failed "
                    f"({type(error).__name__}); retrying in {backoff:.0f}s",
                    file=sys.stderr,
                )
                if _SHUTDOWN_EVENT.wait(backoff):
                    raise
        try:
            yield page
        finally:
            browser.close()


def _fetch_card_pages_on_page(
    page: Any,
    ids: list[str],
    delay: float = 0.3,
    label: str = "",
    deadline: float | None = None,
    payload_callback: Callable[[str, Mapping[str, Any]], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, Any]]]:
    """Fetch exact public GemRate card pages on an already-open session.

    ``payload_callback`` runs as soon as one exact card succeeds, before the
    worker advances to the next card.  The discovery lane uses it to make every
    card an immediate durable checkpoint instead of waiting for a whole chunk.
    ``cancel_event`` lets a terminating orchestrator stop workers between page
    requests and during long 429 waits without discarding completed captures.
    """

    def _raise_if_cancelled() -> None:
        # audit item 10: a process-level SIGTERM stops dispatch the same way a
        # caller cancellation does; the caller turns it into a partial run.
        if _SHUTDOWN_EVENT.is_set():
            raise InterruptedError("public card-page worker shutting down")
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("public card-page worker interrupted")

    def _interruptible_page_wait(seconds: float) -> None:
        remaining_ms = max(0, int(seconds * 1000))
        while remaining_ms > 0:
            _raise_if_cancelled()
            step_ms = min(1000, remaining_ms)
            page.wait_for_timeout(step_ms)
            remaining_ms -= step_ms

    results: dict[str, Mapping[str, Any]] = {}
    receipts: list[dict[str, Any]] = []
    for index, gid in enumerate(ids, start=1):
        _raise_if_cancelled()
        if deadline is not None and time.monotonic() >= deadline:
            receipts.extend(
                _public_failure_receipt(left, http_status=None, reason="budget_exhausted")
                for left in ids[index - 1:]
            )
            break
        ladder_step = 0
        while True:
            payload, failure, rate_limited = _fetch_card_once(page, gid)
            if rate_limited:
                if ladder_step >= len(RATE_LIMIT_LADDER):
                    receipts.append(_public_failure_receipt(
                        gid, http_status=429, reason="rate_limited_429_exhausted",
                    ))
                    break
                wait_seconds = RATE_LIMIT_LADDER[ladder_step]
                if deadline is not None and time.monotonic() + wait_seconds >= deadline:
                    receipts.append(_public_failure_receipt(
                        gid, http_status=429, reason="rate_limited_429_exhausted",
                    ))
                    break
                ladder_step += 1
                print(
                    f"  {label}429 on {gid}; waiting {wait_seconds}s "
                    f"(ladder {ladder_step}/{len(RATE_LIMIT_LADDER)})",
                    file=sys.stderr,
                )
                _interruptible_page_wait(wait_seconds)
                continue
            if payload is not None:
                if payload_callback is not None:
                    payload_callback(gid, payload)
                results[gid] = payload
            elif failure is not None:
                receipts.append(failure)
            break
        if index % 25 == 0:
            print(
                f"  {label}public card pages {index}/{len(ids)} ok={len(results)}",
                file=sys.stderr,
            )
        _interruptible_page_wait(delay)
    return results, receipts


def _chrome_card_pages_with_receipts(
    ids: list[str], delay: float = 0.3, label: str = "",
    deadline: float | None = None,
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, Any]]]:
    """Fetch exact public GemRate card pages with per-ID failure receipts.

    The requested URL contains the opaque GemRate ID. GemRate may redirect it to
    a canonical card slug, so the receipt records the settled canonical URL and
    a DOM hash. The primary transport is the ``/card-details`` JSON response
    initiated by that exact page; a direct JSON fetch is intentionally never
    attempted. The labelled PSA/`GEM MINT` DOM parser is a fail-closed fallback.
    A 429 on the page or its JSON climbs RATE_LIMIT_LADDER before retrying the
    same card; exhausting the ladder records ``rate_limited_429_exhausted``.
    ``deadline`` is a ``time.monotonic()`` cutoff: cards not started before it
    get ``budget_exhausted`` receipts, and a ladder rung that cannot finish
    before it records ``rate_limited_429_exhausted`` instead of sleeping.
    """

    with _gemrate_public_page() as page:
        return _fetch_card_pages_on_page(
            page, ids, delay=delay, label=label, deadline=deadline,
        )


def _safe_browser_error(error: Exception) -> str:
    """Classify a browser failure without storing exception text or secrets.

    audit P2-10: the exception class name is not a secret and it is the only
    thing that separates "Chrome never launched" from "Cloudflare closed the
    context". Two whole-shard outages had to be diagnosed by back-inference
    because this collapsed every failure into one opaque string.
    """

    if isinstance(error, RuntimeError):
        return "browser_unavailable"
    return f"browser_collection_failed:{type(error).__name__}"


def collect_public_card_details(
    ids: list[str],
    *,
    cards_dir: Path,
    delay: float = 0.3,
    resume: bool = False,
    chunk_size: int = 25,
    workers: int = 1,
    deadline: float | None = None,
    payload_sink: dict[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Collect exact public card-page receipts into a private cache.

    This helper deliberately accepts GemRate IDs only.  It is shared by the
    standalone command and candidate backfill so neither path can accidentally
    promote a search-result population into canonical data.

    ``workers`` > 1 shards the pending list round-robin across that many
    threads, each with one browser for the whole shard and its own 429 ladder.
    Every successful capture persists before the worker advances to the next
    card, so a crash or kill resumes from only IDs without a complete capture.
    Do not relaunch Chrome per chunk — that hangs after the first 25.

    ``deadline`` (``time.monotonic()`` cutoff) turns undispatched work into
    ``budget_exhausted`` receipts instead of silent ``missing_response`` fill.
    ``payload_sink`` receives the captured payloads keyed by GemRate ID; the
    return value stays counts-only because callers spread it into manifests.
    """

    unique_ids = list(dict.fromkeys(str(gid) for gid in ids if str(gid)))
    if len(unique_ids) != len(ids):
        raise ValueError("public card-page collection requires unique exact IDs")
    if chunk_size < 1:
        raise ValueError("public card-page collection chunk_size must be positive")
    pending = [gid for gid in unique_ids if not resume or not _has_complete_public_card_capture(cards_dir, gid)]
    payloads: dict[str, Mapping[str, Any]] = {}
    error: str | None = None
    failure_receipts: list[dict[str, Any]] = []
    attempted = 0
    cancel_event = threading.Event()

    def _run_shard(shard: list[str], label: str, stagger: float) -> tuple[
        dict[str, Mapping[str, Any]], list[dict[str, Any]], int, str | None,
    ]:
        shard_payloads: dict[str, Mapping[str, Any]] = {}
        shard_receipts: list[dict[str, Any]] = []
        shard_attempted = 0
        shard_error: str | None = None
        try:
            if stagger > 0 and cancel_event.wait(stagger):
                raise InterruptedError("public card-page worker interrupted")
            with _gemrate_public_page() as page:
                for start in range(0, len(shard), chunk_size):
                    if cancel_event.is_set() or _SHUTDOWN_EVENT.is_set():
                        raise InterruptedError("public card-page worker interrupted")
                    if deadline is not None and time.monotonic() >= deadline:
                        shard_receipts.extend(
                            _public_failure_receipt(gid, http_status=None, reason="budget_exhausted")
                            for gid in shard[start:]
                        )
                        break
                    chunk = shard[start:start + chunk_size]
                    shard_attempted += len(chunk)

                    def _persist_immediately(gid: str, payload: Mapping[str, Any]) -> None:
                        _persist_public_card_capture(cards_dir, gid, payload)
                        shard_payloads[gid] = payload

                    chunk_payloads, chunk_receipts = _fetch_card_pages_on_page(
                        page, chunk, delay=delay, label=label, deadline=deadline,
                        payload_callback=_persist_immediately,
                        cancel_event=cancel_event,
                    )
                    shard_receipts.extend(chunk_receipts)
                    resolved_chunk_ids = set(chunk_payloads)
                    receipt_chunk_ids = {str(row.get("gemrateId")) for row in chunk_receipts}
                    for gid in chunk:
                        if gid not in resolved_chunk_ids and gid not in receipt_chunk_ids:
                            shard_receipts.append(_public_failure_receipt(
                                gid, http_status=None, reason="missing_response",
                            ))
        except InterruptedError:
            # audit item 10: an in-flight shutdown is not a lost shard. Return
            # what this worker already captured (each card was persisted the
            # moment it succeeded) so cmd_daily can still build a declared
            # partial manifest and the chain ingests it instead of re-fetching.
            if not _SHUTDOWN_EVENT.is_set():
                raise
            shard_error = "interrupted_by_signal"
            leftover = [gid for gid in shard if gid not in shard_payloads]
            leftover = [gid for gid in leftover if gid not in {str(row.get("gemrateId")) for row in shard_receipts}]
            shard_receipts.extend(
                _public_failure_receipt(gid, http_status=None, reason=shard_error)
                for gid in leftover
            )
        except Exception as caught:
            shard_error = _safe_browser_error(caught)
            leftover = [gid for gid in shard if gid not in shard_payloads]
            leftover = [gid for gid in leftover if gid not in {str(row.get("gemrateId")) for row in shard_receipts}]
            shard_receipts.extend(
                _public_failure_receipt(gid, http_status=None, reason=shard_error)
                for gid in leftover
            )
        return shard_payloads, shard_receipts, shard_attempted, shard_error

    if pending:
        worker_count = max(1, min(int(workers), len(pending)))
        if worker_count == 1:
            payloads, failure_receipts, attempted, error = _run_shard(pending, "", 0.0)
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            shards = [pending[offset::worker_count] for offset in range(worker_count)]
            pool = ThreadPoolExecutor(max_workers=worker_count)
            try:
                futures = [
                    pool.submit(_run_shard, shard, f"w{offset + 1} ", offset * 1.5)
                    for offset, shard in enumerate(shards)
                ]
                for future in as_completed(futures):
                    shard_payloads, shard_receipts, shard_attempted, shard_error = future.result()
                    payloads.update(shard_payloads)
                    failure_receipts.extend(shard_receipts)
                    attempted += shard_attempted
                    if shard_error and error is None:
                        error = shard_error
            except BaseException:
                cancel_event.set()
                for future in futures:
                    future.cancel()
                pool.shutdown(wait=True, cancel_futures=True)
                raise
            else:
                pool.shutdown(wait=True)
    resolved_ids = set(payloads)
    receipt_ids = {str(row.get("gemrateId")) for row in failure_receipts}
    for gid in pending:
        if gid not in resolved_ids and gid not in receipt_ids:
            failure_receipts.append(_public_failure_receipt(
                gid, http_status=None, reason="missing_response",
            ))
    if payload_sink is not None:
        payload_sink.update(payloads)
    failed = len(pending) - len(payloads)
    failure_receipts.sort(key=lambda row: str(row["gemrateId"]))
    return {
        "transport": WEBSITE_TRANSPORT,
        "requested": len(unique_ids),
        "attempted": attempted,
        "notAttempted": len(pending) - attempted,
        "succeeded": len(payloads),
        "failed": failed,
        "cached": len(unique_ids) - len(pending),
        "partial": failed > 0,
        "promotable": failed == 0,
        "error": error,
        "failureReceiptCount": len(failure_receipts),
        "failureReceipts": failure_receipts,
    }


def cmd_public_card_dump(args) -> int:
    """Keyless current POP harvest from exact GemRate public card pages."""

    ids = load_ids(Path(args.ids_file) if args.ids_file else None)
    if args.limit:
        ids = ids[:args.limit]
    if not ids:
        print(f"No ids in {args.ids_file or IDS_FILE}. Run `collect` first.", file=sys.stderr)
        return 2
    cards_dir = Path(args.out) if args.out else CARDS_DIR
    run_id = datetime.now(timezone.utc).strftime("public_%Y%m%dT%H%M%SZ")
    manifest_path = Path(args.manifest_out) if getattr(args, "manifest_out", None) else (
        OUT_DIR / "runs" / run_id / "manifest.json"
    )
    result = collect_public_card_details(
        ids,
        cards_dir=cards_dir,
        delay=args.delay,
        resume=args.resume,
        chunk_size=args.chunk_size,
        workers=max(1, int(getattr(args, "workers", 1) or 1)),
    )
    print(f"[public-card-dump] requested={result['requested']} pending={result['attempted']}", file=sys.stderr)
    if result["error"]:
        print(result["error"], file=sys.stderr)
    _save(manifest_path, {
        "schemaVersion": "1.1.0",
        "runId": run_id,
        "runStatus": "complete" if result["promotable"] else "partial",
        **result,
    })
    print(
        f"[public-card-dump] ok={result['succeeded']} failed={result['failed']} "
        f"cached={result['cached']}"
    )
    return 0 if result["promotable"] else 1


def cmd_collect(args) -> int:
    """Grow gemrate_ids.txt for free using the internal search API (no key)."""
    queries = args.queries or []
    if args.queries_file:
        queries += [q.strip() for q in Path(args.queries_file).read_text(encoding="utf-8").splitlines()
                    if q.strip() and not q.startswith("#")]
    if not queries:
        print("pass --queries or --queries-file", file=sys.stderr)
        return 2
    found: list[str] = []
    discovered: dict[str, dict] = {}
    for q in queries:
        js = ("fetch('/universal-search-query',{method:'POST',"
              "headers:{'Content-Type':'application/json'},"
              f"body:JSON.stringify({{query:{json.dumps(q)},limit:{args.limit}}})}})"
              ".then(r=>r.json())")
        try:
            res = _chrome_eval(js)
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 1
        if isinstance(res, list):
            for item in res:
                if not isinstance(item, dict):
                    continue
                gemrate_id = str(item.get("gemrate_id", ""))
                if not HEX40.match(gemrate_id):
                    continue
                found.append(gemrate_id)
                evidence = dict(item)
                evidence["matched_query"] = q
                discovered.setdefault(gemrate_id, evidence)
        print(f"  search '{q}' -> {len(res) if isinstance(res, list) else 0} hits "
              f"(cumulative ids {len(set(found))})", file=sys.stderr)
        time.sleep(1.0)
    added = append_ids(sorted(set(found)), Path(args.ids_file) if args.ids_file else None)
    if args.results_out:
        results_out = Path(args.results_out)
        _save(
            results_out,
            {
                "schemaVersion": 1,
                "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "queries": queries,
                "resultCount": len(discovered),
                "results": [discovered[key] for key in sorted(discovered)],
            },
        )
    print(f"[collect] +{added} new ids -> {args.ids_file or IDS_FILE}")
    return 0


def cmd_pop_report(args) -> int:
    """Free per-set four-grader snapshot (400 sets) — no key, via Chrome."""
    js = ("(typeof setsData!=='undefined') ? setsData : null")
    try:
        rows = _chrome_eval(js)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not isinstance(rows, list):
        print("setsData not found on pop-report page", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else OUT_DIR / "pop_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[pop-report] {len(rows)} sets -> {out}")
    return 0


# --------------------------------------------------------------------------
# Flatten to CSV (feeds analysis / the market-cap chart data prep)
# --------------------------------------------------------------------------

def _history_rows(gid: str, hist: dict) -> list[dict]:
    rows: list[dict] = []
    try:
        bg = hist["data"]["population"]["population_data"]["by_grader"]
        dates: dict[str, dict] = {}
        for grader, gv in bg.items():
            for pt in gv.get("history", []):
                d = pt.get("date")
                dates.setdefault(d, {})[grader] = pt
        for d in sorted(dates):
            row = {"gemrate_id": gid, "date": d}
            for g in GRADERS:
                pt = dates[d].get(g)
                if pt:
                    row[f"{g}_total"] = pt.get("total")
                    row[f"{g}_gem_rate"] = pt.get("gem_rate")
                    row[f"{g}_top_grade_pop"] = (pt.get("grades") or {}).get(TOP_GRADE[g])
            rows.append(row)
    except (KeyError, TypeError):
        pass
    return rows


def export_csv_to(out: Path) -> tuple[int, int]:
    import csv
    fields = ["gemrate_id", "date"]
    for g in GRADERS:
        fields += [f"{g}_total", f"{g}_gem_rate", f"{g}_top_grade_pop"]
    nrows = nfiles = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        if CARDS_DIR.exists():
            for gid in sorted(os.listdir(CARDS_DIR)):
                hf = CARDS_DIR / gid / "history_full.json"
                if not hf.exists():
                    continue
                try:
                    body = json.loads(hf.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                for r in _history_rows(gid, body):
                    w.writerow(r)
                    nrows += 1
                nfiles += 1
    return nfiles, nrows


def cmd_export_csv(args) -> int:
    out = Path(args.out) if args.out else OUT_DIR / "population_history.csv"
    nfiles, nrows = export_csv_to(out)
    print(f"[export-csv] {nfiles} cards, {nrows} date-rows -> {out}")
    return 0


# --------------------------------------------------------------------------
# Grader-level volume (PSA/CGC/TAG/Beckett/SGC weekly + monthly items-graded)
# This is the "Items Graded" report — DISTINCT from per-card population.
# TAG exists here even though it is not yet in the per-card API.
# --------------------------------------------------------------------------

_VOLUME_MONTH_RE = re.compile(
    r"(?P<grader>PSA|CGC|TAG|Beckett|BGS|SGC)\s*\*?\s+graded\s+~?"
    r"(?P<num>[\d.,]+)\s*(?P<unit>[mkMK]?)\s+cards?\s*-\s*"
    r"(?P<dir>[⬆⬇↑↓→]?)\s*(?P<pct>[\d.]+)%\s*vs\s*(?P<ref>\w+)",
    re.IGNORECASE)


def _to_count(num: str, unit: str) -> int:
    v = float(num.replace(",", ""))
    u = unit.lower()
    if u == "m":
        v *= 1_000_000
    elif u == "k":
        v *= 1_000
    return int(round(v))


def _norm_grader(g: str) -> str:
    g = g.lower()
    return "beckett" if g == "bgs" else g


def _strip_html(html_text: str) -> str:
    import html as _html
    t = re.sub(r"<script.*?</script>", " ", html_text, flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t)
    return re.sub(r"\s+", " ", t)


def _parse_recap_text(body: str, slug: str) -> dict | None:
    """Parse grader volumes out of stripped recap text."""
    graders: dict[str, dict] = {}
    for m in _VOLUME_MONTH_RE.finditer(body):
        g = _norm_grader(m.group("grader"))
        entry = {
            "items_graded": _to_count(m.group("num"), m.group("unit")),
            "mom_change_pct": (float(m.group("pct"))
                               if m.group("dir") in ("⬆", "↑", "") else -float(m.group("pct"))),
            "direction": {"⬆": "up", "↑": "up", "⬇": "down", "↓": "down"}.get(m.group("dir"), "up"),
        }
        if g not in graders or entry["items_graded"] > graders[g]["items_graded"]:
            graders[g] = entry
    if not graders:
        return None
    mm = re.match(r"([a-z]+)-(\d{4})-recap", slug)
    month = None
    if mm:
        try:
            month = datetime.strptime(f"{mm.group(1)} {mm.group(2)}", "%B %Y").strftime("%Y-%m")
        except ValueError:
            month = None
    return {"period": "monthly", "month": month, "slug": slug,
            "source_url": f"{WEB}/{slug}", "graders": graders}


def fetch_monthly_recap(slug: str, live_text: str | None = None) -> dict | None:
    """Parse one monthly recap page (e.g. 'june-2026-recap') into grader volumes.

    The page is server-rendered; grader volumes appear as sentences like
    'TAG graded 54k cards - ⬆ 4% vs May, ⬆ 45% YoY'. Returns None if no volumes.
    Pass live_text (stripped page text from a CF-cleared browser) when Cloudflare
    blocks direct requests.
    """
    body = live_text
    if body is None:
        st, txt, _ = _request(f"{WEB}/{slug}")
        if st != 200 or not txt:
            return None
        body = _strip_html(txt)
    return _parse_recap_text(body, slug)


def _discover_recap_slugs(limit: int = 24) -> list[str]:
    """Find monthly-recap slugs from the blog index. Uses a real Chrome because
    /blog sits behind Cloudflare; falls back to a direct request."""
    html_text = None
    try:
        res = _chrome_pages_eval([("/blog", "document.documentElement.outerHTML")])
        html_text = res[0] if res else None
    except RuntimeError:
        st, txt, _ = _request(f"{WEB}/blog")
        if st == 200 and txt:
            html_text = txt
    if not html_text:
        return []
    slugs = re.findall(r'href="/([a-z]+-\d{4}-recap)"', html_text)
    seen: list[str] = []
    for s in slugs:
        if s not in seen:
            seen.append(s)
    return seen[:limit]


def cmd_grader_volume(args) -> int:
    """Fetch grader-level items-graded volumes (incl TAG).

    --period monthly : scrape the monthly recap page(s) (server-rendered, reliable).
    --period weekly  : the weekly 'Items Graded - Last Week' graphic is only published
                       on GemRate social/newsletter (no site endpoint); we record the
                       latest known weekly figures when provided via --weekly-json,
                       otherwise report that weekly is social-image-only.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else OUT_DIR / "grader_volume.json"

    if args.period == "weekly":
        if args.weekly_json:
            data = json.loads(Path(args.weekly_json).read_text(encoding="utf-8"))
            data.setdefault("period", "weekly")
            data.setdefault("note", "weekly figures are published only as a social/newsletter "
                                    "image by GemRate; entered manually or via OCR")
            _save(out_path, data)
            print(f"[grader-volume weekly] recorded -> {out_path}")
            return 0
        # --set allows one-shot entry without a JSON file:
        #   --set psa=492900 cgc=140600 tag=19100 beckett=18800 sgc=8500 --week 2026-07-06
        if args.set:
            graders = {}
            for kv in args.set:
                if "=" not in kv:
                    continue
                g, v = kv.split("=", 1)
                g = _norm_grader(g.strip())
                pct = None
                if ":" in v:
                    v, p = v.split(":", 1)
                    try:
                        pct = float(p)
                    except ValueError:
                        pct = None
                try:
                    graders[g] = {"items_graded": int(v)}
                    if pct is not None:
                        graders[g]["wow_change_pct"] = pct
                except ValueError:
                    pass
            if graders:
                data = {"period": "weekly", "week_ending": args.week,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "note": "weekly figures published by GemRate as a social/newsletter "
                                "image; recorded manually or via OCR",
                        "graders": graders}
                _save(out_path, data)
                print(f"[grader-volume weekly] recorded -> {out_path}")
                return 0
        print("Weekly 'Items Graded - Last Week' is published by GemRate as a social/"
              "newsletter IMAGE only — there is no site/API endpoint for it.\n"
              "Options: (1) --weekly-json <file>, (2) --set psa=N cgc=N tag=N ... [--week DATE], or\n"
              "         (3) use monthly volumes (reliable, scrapable).", file=sys.stderr)
        return 1

    # monthly
    slugs = [args.slug] if args.slug else _discover_recap_slugs()
    if not slugs:
        print("no recap slugs found (Cloudflare?) — try --slug june-2026-recap", file=sys.stderr)
        return 1

    # Recap pages sit behind Cloudflare on www.gemrate.com — pull their stripped
    # text through a real-Chrome session, then parse locally.
    texts: dict[str, str | None] = {}
    try:
        js = "document.body.innerText"
        results = _chrome_pages_eval([(f"/{s}", js) for s in slugs])
        texts = {s: r for s, r in zip(slugs, results)}
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        print("falling back to direct requests (may hit Cloudflare)", file=sys.stderr)

    records = []
    for slug in slugs:
        rec = fetch_monthly_recap(slug, live_text=texts.get(slug))
        if rec:
            records.append(rec)
            print(f"  {slug}: " + ", ".join(
                f"{g}={d['items_graded']:,}" for g, d in sorted(rec["graders"].items())),
                file=sys.stderr)
        time.sleep(0.2)
    if not records:
        print("no grader volumes parsed", file=sys.stderr)
        return 1
    payload = {"period": "monthly", "generated_at": datetime.now(timezone.utc).isoformat(),
               "months": records}
    _save(out_path, payload)
    print(f"[grader-volume monthly] {len(records)} months -> {out_path}")
    return 0


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="GemRate population source for CARDZ (with/without key)")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daily", help="Daily incremental update of tracked cards")
    d.add_argument("--ids-file")
    d.add_argument("--speed", choices=list(SPEEDS), default="medium")
    d.add_argument("--with-history", action="store_true", help="also refresh full history; daily default is population only")
    d.add_argument("--no-history", action="store_true", help=argparse.SUPPRESS)
    d.add_argument("--limit", type=int)
    d.add_argument("--identity-file", help="private tracked-universe JSON used for exact mirror routing")
    d.add_argument("--mirror-root", help="private Grade10 data root containing cards/<scope>/<id>/populations.json")
    d.add_argument("--website-budget-seconds", type=int, default=DEFAULT_WEBSITE_BUDGET_SECONDS,
                   help="wall-clock cap for the public-card-page pass; it stops starting new "
                        "batches past this so the run returns partial instead of being killed")
    d.add_argument("--workers", type=int, default=4,
                   help="parallel browsers for the public-card-page pass; the slow retry "
                        "pass always runs single-worker")
    d.add_argument("--run-suffix", help="unique lowercase suffix for parallel V2 shards")
    d.add_argument("--skip-grader-volume", action="store_true",
                   help="parallel shard: leave the one shared grader-volume capture to shard 0")
    d.add_argument("--volume-slug", help="monthly recap slug for grader volume, e.g. june-2026-recap")
    d.set_defaults(fn=cmd_daily)

    a = sub.add_parser("api-dump", help="Bulk harvest (key window)")
    a.add_argument("--ids-file")
    a.add_argument("--speed", choices=list(SPEEDS), default="medium")
    a.add_argument("--resume", action="store_true")
    a.add_argument("--limit", type=int)
    a.set_defaults(fn=cmd_api_dump)

    ir = sub.add_parser("identity-receipts", help="Offline rebuild of direct API identity receipts")
    ir.add_argument("--cards-dir", help="private GemRate cards directory; defaults to data/private/gemrate/cards")
    ir.add_argument("--resume", action="store_true", help="skip only receipts whose raw hash, pointer, and identity still verify")
    ir.set_defaults(fn=cmd_identity_receipts)

    c = sub.add_parser("collect", help="Grow id list for free via Chrome search (no key)")
    c.add_argument("--queries", nargs="*", default=[])
    c.add_argument("--queries-file")
    c.add_argument("--limit", type=int, default=40)
    c.add_argument("--ids-file")
    c.add_argument("--results-out", help="private JSON manifest with search identity evidence")
    c.set_defaults(fn=cmd_collect)

    pc = sub.add_parser("public-card-dump", help="Keyless exact current POP via GemRate public card pages")
    pc.add_argument("--ids-file")
    pc.add_argument("--out", help="private cards cache root")
    pc.add_argument("--resume", action="store_true")
    pc.add_argument("--limit", type=int)
    pc.add_argument("--delay", type=float, default=0.3)
    pc.add_argument("--chunk-size", type=int, default=WEBSITE_CHUNK,
                    help="cards per browser batch (browser restarts between batches)")
    pc.add_argument("--workers", type=int, default=1,
                    help="parallel browsers, each with its own 429 ladder; captures "
                         "persist per chunk so kills stay cheap")
    pc.add_argument("--manifest-out", help="immutable private run manifest output path")
    pc.set_defaults(fn=cmd_public_card_dump)

    pr = sub.add_parser("pop-report", help="Free per-set four-grader snapshot (no key)")
    pr.add_argument("--out")
    pr.set_defaults(fn=cmd_pop_report)

    e = sub.add_parser("export-csv", help="Flatten harvested history -> CSV")
    e.add_argument("--out")
    e.set_defaults(fn=cmd_export_csv)

    gv = sub.add_parser("grader-volume", help="Grader-level items-graded (incl TAG, weekly+monthly)")
    gv.add_argument("--period", choices=["weekly", "monthly"], default="monthly")
    gv.add_argument("--slug", help="one monthly recap slug, e.g. june-2026-recap")
    gv.add_argument("--weekly-json", help="JSON file with weekly figures (weekly is social-image-only)")
    gv.add_argument("--set", nargs="*", metavar="GRADER=N[:PCT]",
                    help="weekly one-shot: psa=492900:-18 cgc=140600:-32 tag=19100:55 ...")
    gv.add_argument("--week", help="week-ending date for --set, e.g. 2026-07-06")
    gv.add_argument("--out")
    gv.set_defaults(fn=cmd_grader_volume)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# LEGACY / OPTIONAL — Windows Task Scheduler registration (run once, PowerShell).
#
# Not part of production. Production is a Linux server where GemRate runs inside
# cardz-market-cap-daily.service (00:30 UTC = 09:30 JST) via run_daily.py, and no
# CARDZ-GemRate-Daily task is registered on the legacy Windows host either. Only
# use this for a detached manual run, and pick a slot that collides with neither
# CARDZ-TAG-Daily-Capture (06:45) nor CARDZ-Market-Cap-Daily (09:30).
#
#   $py   = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
#   $root = "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"
#   $act  = New-ScheduledTaskAction -Execute $py `
#           -Argument "pipelines\gemrate_source.py daily --ids-file pipelines\gemrate_ids.txt" `
#           -WorkingDirectory $root
#   $trg  = New-ScheduledTaskTrigger -Daily -At 07:30
#   Register-ScheduledTask -TaskName "CARDZ-GemRate-Daily" -Action $act -Trigger $trg `
#           -Description "Daily GemRate 4-grader population update"
#
# (Set GEMRATE_API_KEY as a user env var, or hardcode via a wrapper .cmd that sets it.)
# ---------------------------------------------------------------------------
