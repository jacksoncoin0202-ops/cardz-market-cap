#!/usr/bin/env python3
"""Capture one exact, active-universe-only TAG population snapshot.

TAG exposes a current population snapshot rather than historical deltas.
CARDZ therefore stores one immutable daily observation and derives 1d/7d/30d
changes from its own replay.  Matching is deliberately fail-closed: Pokémon,
complete collector number, card language and normalized card name must identify
exactly one TAG printing.  No suffix inference or fuzzy first-result fallback is
allowed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from tag_pop_data import dump_fresh
from db_runtime import active_universe_lock_hash
from lang_registry import SUPPORTED_CARD_LANGUAGES
from market_source_sync import collection_cards, load_active_universe as load_market_active_universe


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTIVE = ROOT / "data/runtime/private-source-map/active-universe.json"
DEFAULT_CATALOG = ROOT / "data/tag/pops_pokemon.jsonl"
SUPPORTED_LANGUAGES = SUPPORTED_CARD_LANGUAGES
HANGUL = re.compile(r"[\uac00-\ud7a3]")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def normalize_collector(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).upper()
    text = text.replace("–", "-").replace("—", "-").replace("＿", "-")
    return re.sub(r"[^A-Z0-9]", "", text)


def infer_language(row: Mapping[str, Any]) -> str:
    identity = " ".join(str(row.get(field) or "") for field in ("brandName", "setName"))
    folded = identity.casefold()
    if "traditional chinese" in folded:
        return "zhTW"
    if "simplified chinese" in folded:
        return "zhCN"
    if "korean" in folded or HANGUL.search(identity):
        return "ko"
    if "japanese" in folded:
        return "ja"
    return "en"


def normalize_name(value: Any, language: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    if language == "ko":
        native = "".join(character for character in text if HANGUL.fullmatch(character) or character.isdigit())
        if native:
            return native
    if language in {"zhCN", "zhTW"}:
        native = "".join(
            character
            for character in text
            if "\u3400" <= character <= "\u9fff" or character.isdigit()
        )
        if native:
            return native
    # G10 Japanese identities currently use their English market names while
    # TAG commonly prefixes native text.  Comparing the exact Latin identity
    # is deterministic and avoids treating the native prefix as a mismatch.
    return "".join(re.findall(r"[a-z0-9]+", text))


def row_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(row.get(field) or "")
        for field in ("category", "year", "brandName", "setName", "cardName", "cardNumber", "variation")
    )


def load_tag_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RuntimeError(f"TAG snapshot does not exist: {path}")
    rows: dict[tuple[str, ...], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid TAG JSONL at line {line_number}: {path}") from error
            if not isinstance(row, dict) or row.get("category") != "Pokémon":
                continue
            grades = row.get("grades")
            if not isinstance(grades, Mapping) or not isinstance(row.get("cardNumber"), str):
                raise RuntimeError(f"invalid TAG population row at line {line_number}: {path}")
            if any(not isinstance(value, int) or value < 0 for value in grades.values()):
                raise RuntimeError(f"invalid TAG grade count at line {line_number}: {path}")
            rows[row_identity(row)] = row
    if not rows:
        raise RuntimeError(f"TAG snapshot contains no Pokémon rows: {path}")
    return list(rows.values())


def load_active_universe(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"active universe does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("active universe contract is invalid")
    if value.get("schemaVersion") == "5.0.0":
        # Reuse the canonical collection validator so TAG observes the same
        # formal plus 971--999 pre-entry universe as GemRate and SNK.
        return load_market_active_universe(path)
    if value.get("schemaVersion") != "1.0.0" or not isinstance(value.get("cards"), list):
        raise RuntimeError("active universe contract is invalid")
    return value


def graded_total(row: Mapping[str, Any]) -> int:
    grades = row["grades"]
    return sum(int(value) for grade, value in grades.items() if str(grade).upper() != "VA")


def top_grade_population(row: Mapping[str, Any]) -> int:
    grades = row["grades"]
    return int(grades.get("10", 0)) + int(grades.get("10P", 0))


def match_active_cards(
    active_universe: Mapping[str, Any], tag_rows: Iterable[Mapping[str, Any]], observed: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    index: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in tag_rows:
        language = infer_language(row)
        collector = normalize_collector(row.get("cardNumber"))
        name = normalize_name(row.get("cardName"), language)
        if collector and name:
            index[(collector, language, name)].append(row)

    matched: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    counts = Counter({"active": 0, "pokemon": 0, "matched": 0, "ambiguous": 0, "unmatched": 0, "unsupportedTcg": 0})
    cards = (
        collection_cards(active_universe)
        if active_universe.get("schemaVersion") == "5.0.0"
        else active_universe.get("cards", [])
    )
    for card in cards:
        if not isinstance(card, Mapping):
            continue
        counts["active"] += 1
        if card.get("tcg") != "pokemon":
            counts["unsupportedTcg"] += 1
            continue
        counts["pokemon"] += 1
        language = str(card.get("language") or "")
        collector = normalize_collector(card.get("collectorNumber"))
        name = normalize_name(card.get("name"), language)
        candidates = index.get((collector, language, name), []) if language in SUPPORTED_LANGUAGES else []
        source_code = str(card.get("canonicalSourceCode") or "").casefold()
        external_id = str(card.get("canonicalExternalId") or "")
        review_base = {
            "canonicalSourceCode": source_code,
            "canonicalExternalId": external_id,
            "pokedexId": str(card.get("pokedexId") or ""),
            "collectorNumber": str(card.get("collectorNumber") or ""),
            "language": language,
        }
        if len(candidates) != 1:
            reason = "ambiguous_exact_identity" if len(candidates) > 1 else "no_exact_identity"
            counts["ambiguous" if len(candidates) > 1 else "unmatched"] += 1
            review.append({**review_base, "reason": reason, "candidateCount": len(candidates)})
            continue
        row = candidates[0]
        if not source_code or not external_id or card.get("pokedexStatus") != "confirmed":
            raise RuntimeError("active universe contains an invalid canonical identity")
        identity_hash = sha256(
            {
                "year": row.get("year"),
                "brandName": row.get("brandName"),
                "setName": row.get("setName"),
                "cardName": row.get("cardName"),
                "cardNumber": row.get("cardNumber"),
                "variation": row.get("variation"),
            }
        )
        matched.append(
            {
                "schemaVersion": "1.0.0",
                "observedDate": observed.isoformat(),
                "canonicalSourceCode": source_code,
                "canonicalExternalId": external_id,
                "pokedexId": str(card.get("pokedexId") or ""),
                "tagIdentitySha256": identity_hash,
                "topGradePopulation": top_grade_population(row),
                "total": graded_total(row),
            }
        )
        counts["matched"] += 1
    matched.sort(key=lambda row: (row["canonicalSourceCode"], row["canonicalExternalId"]))
    review.sort(key=lambda row: (row["reason"], row["canonicalSourceCode"], row["canonicalExternalId"]))
    return matched, review, dict(counts)


def write_immutable_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> bool:
    payload = b"".join(canonical_json(row) + b"\n" for row in rows)
    if path.is_file():
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable TAG run mismatch: {path}")
        return True
    atomic_write(path, payload)
    return False


def self_test() -> dict[str, Any]:
    active = {
        "schemaVersion": "1.0.0",
        "cards": [
            {"canonicalSourceCode": "scope", "canonicalExternalId": "jp", "pokedexId": "jp", "pokedexStatus": "confirmed", "tcg": "pokemon", "language": "ja", "collectorNumber": "207/XY-P", "name": "Poncho-Wearing Pikachu"},
            {"canonicalSourceCode": "scope", "canonicalExternalId": "suffix", "pokedexId": "suffix", "pokedexStatus": "confirmed", "tcg": "pokemon", "language": "en", "collectorNumber": "085/SVP", "name": "Pikachu with Grey Felt Hat"},
            {"canonicalSourceCode": "scope", "canonicalExternalId": "ambiguous", "pokedexId": "ambiguous", "pokedexStatus": "confirmed", "tcg": "pokemon", "language": "en", "collectorNumber": "001/100", "name": "Pikachu"},
            {"canonicalSourceCode": "scope", "canonicalExternalId": "op", "pokedexId": "op", "pokedexStatus": "confirmed", "tcg": "one-piece", "language": "ja", "collectorNumber": "OP01-001", "name": "Luffy"},
        ],
    }
    rows = [
        {"category": "Pokémon", "year": "2016", "brandName": "Pokémon XY Japanese", "setName": "Promo", "cardName": "ポンチョを着たピカチュウ Poncho-Wearing Pikachu", "cardNumber": "207/XY-P", "variation": "Full Art", "grades": {"10": 13, "10P": 1, "VA": 2}},
        {"category": "Pokémon", "year": "2023", "brandName": "Pokémon Scarlet & Violet Black Star Promos", "setName": "Van Gogh", "cardName": "Pikachu with Grey Felt Hat", "cardNumber": "085", "variation": "", "grades": {"10": 10}},
        {"category": "Pokémon", "year": "2020", "brandName": "Pokémon Sword & Shield", "setName": "Fixture", "cardName": "Pikachu", "cardNumber": "001/100", "variation": "Holo", "grades": {"10": 2}},
        {"category": "Pokémon", "year": "2020", "brandName": "Pokémon Sword & Shield", "setName": "Fixture", "cardName": "Pikachu", "cardNumber": "001/100", "variation": "Reverse Holo", "grades": {"10": 3}},
    ]
    matched, review, counts = match_active_cards(active, rows, date(2026, 7, 23))
    with tempfile.TemporaryDirectory(prefix="cardz-tag-capture-") as directory:
        output = Path(directory) / "tag.jsonl"
        first_replay = write_immutable_jsonl(output, matched)
        second_replay = write_immutable_jsonl(output, matched)
    return {
        "counts": counts,
        "matchedTopGrade": matched[0]["topGradePopulation"],
        "matchedTotalExcludesVa": matched[0]["total"],
        "reviewReasons": sorted(row["reason"] for row in review),
        "suffixWasNotGuessed": any(row["canonicalExternalId"] == "suffix" for row in review),
        "ambiguousWasQuarantined": any(row["canonicalExternalId"] == "ambiguous" for row in review),
        "firstReplay": first_replay,
        "secondReplay": second_replay,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture exact active TAG grader populations")
    parser.add_argument("--active-universe", type=Path, default=DEFAULT_ACTIVE)
    parser.add_argument("--from-file", type=Path, help="offline/replay TAG catalog; production omits this")
    parser.add_argument("--catalog-out", type=Path, help="run-local complete catalog path for live capture")
    parser.add_argument("--out", type=Path, required=False)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--review-out", type=Path)
    parser.add_argument("--observed-date")
    parser.add_argument(
        "--captured-at",
        help="UTC acquisition timestamp preserved when replaying a bounded last-good catalog",
    )
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    if args.out is None:
        raise RuntimeError("--out is required")

    observed = date.fromisoformat(args.observed_date) if args.observed_date else datetime.now(timezone.utc).date()
    if args.captured_at:
        captured_at = datetime.fromisoformat(args.captured_at.replace("Z", "+00:00"))
        if captured_at.tzinfo is None:
            raise RuntimeError("--captured-at must include a timezone")
        captured_at = captured_at.astimezone(timezone.utc)
    else:
        captured_at = datetime.now(timezone.utc)
    manifest_out = args.manifest_out or args.out.with_suffix(args.out.suffix + ".manifest.json")
    review_out = args.review_out or args.out.with_suffix(args.out.suffix + ".review.json")
    if args.out.is_file() and manifest_out.is_file() and review_out.is_file():
        manifest = json.loads(manifest_out.read_text(encoding="utf-8"))
        print(json.dumps({"status": "replayed", **manifest.get("counts", {})}, sort_keys=True))
        return 0
    if any(path.is_file() for path in (args.out, manifest_out, review_out)):
        raise RuntimeError("incomplete immutable TAG generation exists")

    total_graded: int | None = None
    if args.from_file:
        catalog = args.from_file.resolve()
        acquisition = {"rows": None, "totalGraded": 0, "replayed": 1, "totalSets": 0, "unnamedSets": 0, "unusableSets": 0}
    else:
        if args.catalog_out is None:
            raise RuntimeError("live TAG capture requires --catalog-out")
        catalog = args.catalog_out.resolve()
        acquisition = dump_fresh("Pokémon", catalog, args.delay)
        total_graded = acquisition["totalGraded"] or None
    tag_rows = load_tag_rows(catalog)
    active = load_active_universe(args.active_universe.resolve())
    matched, review, counts = match_active_cards(active, tag_rows, observed)
    if not matched:
        raise RuntimeError("TAG exact matcher produced no active observations")
    replayed = write_immutable_jsonl(args.out.resolve(), matched)
    review_document = {
        "schemaVersion": "1.0.0",
        "observedDate": observed.isoformat(),
        "capturedAt": captured_at.isoformat().replace("+00:00", "Z"),
        "counts": counts,
        "rows": review,
    }
    manifest = {
        "schemaVersion": "1.0.0",
        "observedDate": observed.isoformat(),
        "activeUniverseSha256": active_universe_lock_hash(active),
        "catalogSha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
        "outputSha256": hashlib.sha256(args.out.resolve().read_bytes()).hexdigest(),
        "catalogRows": len(tag_rows),
        "tagMarketTotal": total_graded,
        "catalogAcquisition": {
            key: acquisition.get(key)
            for key in ("replayed", "rows", "totalSets", "unnamedSets", "unusableSets")
        },
        "counts": counts,
    }
    atomic_write(review_out.resolve(), json.dumps(review_document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    atomic_write(manifest_out.resolve(), json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    print(json.dumps({"status": "replayed" if replayed else "ready", **counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
