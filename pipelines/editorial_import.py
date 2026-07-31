#!/usr/bin/env python3
"""Classify cohort editorial cells: names, sets, stories, provenance, locales.

QC-A09-EDITORIAL-LOCALES / role A09.

This module is a **read-only classifier + candidate-shard writer**. It never:

* fabricates missing locale text
* writes English into story locales to fake coverage
* mutates MySQL, public pointers, timers, or production publish surfaces

Terminal states (every required cell must reach one):

* ``ready`` — content present and contract-valid
* ``missing`` — content absent; stays missing (terminal, not fabricated)
* ``review_required`` — present evidence is insufficient or pack-flagged

Producer note (not classification invention):
``pipelines/editorial_localization.py`` may *export* English fallback for
names/sets when a translation is missing. Stories never fall back. This
classifier records the truth of source coverage; export policy is separate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_QC_REPORT = (
    ROOT
    / "data"
    / "runtime"
    / "private-reports"
    / "canonical-db-qc"
    / "qc_20260729_sale_contract_01"
    / "report.json"
)
DEFAULT_IDENTITY = (
    ROOT / "data" / "runtime" / "private-source-map" / "qualified-940-identity.jsonl"
)
DEFAULT_CARD_NAMES = ROOT / "data" / "editorial" / "card-names.json"
DEFAULT_SET_NAMES = ROOT / "data" / "editorial" / "set-names.json"
DEFAULT_STORIES = ROOT / "data" / "editorial" / "top100-stories.json"
DEFAULT_COHORT = (
    ROOT
    / "data"
    / "runtime"
    / "private-reports"
    / "baselines"
    / "baseline_qc_20260729_wave0_c1_v1"
    / "COHORT_C1.json"
)

# Canonical public LocalizedText locales (schema.ts). FE may add `ko`; never invent it here.
REQUIRED_LOCALES: tuple[str, ...] = ("en", "zhTW", "zhCN", "ja")
TRANSLATED_LOCALES: tuple[str, ...] = ("zhTW", "zhCN", "ja")
STORY_MIN_CHARS = 80

# validate.ts generic template ban (same family as editorial_locale_sync / story_merge).
BANNED_STORY = re.compile(
    r"trader market view|炒家市場觀察|受到市場關注，重點不只是單張報價",
    re.IGNORECASE,
)

ALLOWED_STORY_REVIEW_REASONS = frozenset(
    {
        "summary_printing_conflict",
        "summary_empty",
        "collector_number_unresolved",
        "language_conflict",
        "identity_conflict",
        "evidence_too_thin",
    }
)

TERMINAL_STATUSES = frozenset({"ready", "missing", "review_required"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _nonempty_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _entries_map(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    document = read_json(path)
    raw = document.get("entries") if isinstance(document, Mapping) else None
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def load_story_index(path: Path) -> dict[str, dict[str, Any]]:
    """Index top100-stories.json by public opaque id."""
    if not path.is_file():
        return {}
    document = read_json(path)
    index: dict[str, dict[str, Any]] = {}
    for entry in document.get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        key = _nonempty_str(entry.get("id"))
        if key:
            index[key] = dict(entry)
    return index


def load_identity_by_variant(path: Path) -> dict[int, dict[str, Any]]:
    table: dict[int, dict[str, Any]] = {}
    if not path.is_file():
        return table
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            variant_id = int(row["variantId"])
            table[variant_id] = row
    return table


def load_cohort_cards(qc_report_path: Path) -> list[dict[str, Any]]:
    report = read_json(qc_report_path)
    cards = report.get("cards") or []
    if not isinstance(cards, list):
        raise ValueError(f"QC report cards missing: {qc_report_path}")
    return [dict(card) for card in cards if isinstance(card, Mapping)]


@dataclass(frozen=True)
class LocaleCell:
    locale: str
    status: str
    value: str | None
    reason: str | None
    provenance: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "locale": self.locale,
            "status": self.status,
            "value": self.value,
            "reason": self.reason,
            "provenance": self.provenance,
        }


def _assert_terminal(status: str) -> str:
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"non-terminal status: {status}")
    return status


def classify_name_locale(
    locale: str,
    english_name: str | None,
    pack_row: Mapping[str, Any] | None,
) -> LocaleCell:
    """Classify one name locale. Missing stays missing; never invent text."""

    if locale == "en":
        if english_name:
            return LocaleCell(
                locale="en",
                status=_assert_terminal("ready"),
                value=english_name,
                reason=None,
                provenance="catalog_identity.name",
            )
        return LocaleCell(
            locale="en",
            status=_assert_terminal("missing"),
            value=None,
            reason="en_name_missing",
            provenance=None,
        )

    if not english_name:
        return LocaleCell(
            locale=locale,
            status=_assert_terminal("missing"),
            value=None,
            reason="en_name_missing",
            provenance=None,
        )

    pack_row = pack_row or {}
    candidate = _nonempty_str(pack_row.get(locale))
    if candidate is None:
        return LocaleCell(
            locale=locale,
            status=_assert_terminal("missing"),
            value=None,
            reason="translation_missing",
            provenance=None,
        )
    if candidate == english_name:
        # Pack placeholder equal to English is not a true translation.
        return LocaleCell(
            locale=locale,
            status=_assert_terminal("missing"),
            value=None,
            reason="english_placeholder_not_translation",
            provenance="card_names_pack",
        )
    return LocaleCell(
        locale=locale,
        status=_assert_terminal("ready"),
        value=candidate,
        reason=None,
        provenance="card_names_pack",
    )


def classify_set_locale(
    locale: str,
    english_set: str | None,
    pack_row: Mapping[str, Any] | None,
    *,
    en_provenance: str,
) -> LocaleCell:
    if locale == "en":
        if english_set:
            return LocaleCell(
                locale="en",
                status=_assert_terminal("ready"),
                value=english_set,
                reason=None,
                provenance=en_provenance,
            )
        return LocaleCell(
            locale="en",
            status=_assert_terminal("missing"),
            value=None,
            reason="en_set_missing",
            provenance=None,
        )

    if not english_set:
        return LocaleCell(
            locale=locale,
            status=_assert_terminal("missing"),
            value=None,
            reason="en_set_missing",
            provenance=None,
        )

    pack_row = pack_row or {}
    candidate = _nonempty_str(pack_row.get(locale))
    if candidate is None:
        return LocaleCell(
            locale=locale,
            status=_assert_terminal("missing"),
            value=None,
            reason="translation_missing",
            provenance=None,
        )
    if candidate == english_set:
        return LocaleCell(
            locale=locale,
            status=_assert_terminal("missing"),
            value=None,
            reason="english_placeholder_not_translation",
            provenance="set_names_pack",
        )
    return LocaleCell(
        locale=locale,
        status=_assert_terminal("ready"),
        value=candidate,
        reason=None,
        provenance="set_names_pack",
    )


def _story_locale_defect(text: str | None, locale: str) -> str | None:
    if text is None or not str(text).strip():
        return f"{locale}_missing"
    stripped = str(text).strip()
    if len(stripped) < STORY_MIN_CHARS:
        return f"{locale}_too_short"
    if BANNED_STORY.search(stripped):
        return f"{locale}_banned_template"
    return None


def classify_stories(
    pack_entry: Mapping[str, Any] | None,
    *,
    db_stories: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Classify four story locales. Missing stays null; never English-fill."""

    # Prefer DB rows when provided (non-drifting variant_id join done by caller).
    source = "none"
    raw: dict[str, Any] = {}
    status_hint: str | None = None
    review_reason: str | None = None
    evidence: list[str] = []
    evidence_sha: str | None = None

    if db_stories:
        raw = {locale: db_stories.get(locale) for locale in REQUIRED_LOCALES}
        source = "catalog_variant_locale"
        status_hint = "ready"
    elif pack_entry:
        stories = pack_entry.get("stories") or {}
        raw = {locale: stories.get(locale) if isinstance(stories, Mapping) else None for locale in REQUIRED_LOCALES}
        source = "top100_stories_json"
        status_hint = _nonempty_str(pack_entry.get("status"))
        review_reason = _nonempty_str(pack_entry.get("reviewReason"))
        evidence = list(pack_entry.get("evidence") or [])
        evidence_sha = _nonempty_str(pack_entry.get("evidenceSha256"))

    cells: list[LocaleCell] = []
    values: dict[str, str | None] = {}
    reasons: list[str] = []

    for locale in REQUIRED_LOCALES:
        candidate = _nonempty_str(raw.get(locale))
        defect = _story_locale_defect(candidate, locale)
        if defect:
            cells.append(
                LocaleCell(
                    locale=locale,
                    status=_assert_terminal("missing" if defect.endswith("_missing") else "review_required"),
                    value=None,
                    reason=defect,
                    provenance=source if source != "none" else None,
                )
            )
            values[locale] = None
            reasons.append(defect)
        else:
            cells.append(
                LocaleCell(
                    locale=locale,
                    status=_assert_terminal("ready"),
                    value=candidate,
                    reason=None,
                    provenance=source,
                )
            )
            values[locale] = candidate

    ready_values = [values[locale] for locale in REQUIRED_LOCALES if values[locale] is not None]
    if len(ready_values) == len(REQUIRED_LOCALES):
        if len(set(ready_values)) != len(REQUIRED_LOCALES):
            # Collapse to review — not distinct (validate.ts).
            cells = [
                LocaleCell(
                    locale=locale,
                    status=_assert_terminal("review_required"),
                    value=values[locale],
                    reason="stories_not_independently_localized",
                    provenance=source,
                )
                for locale in REQUIRED_LOCALES
            ]
            reasons = ["stories_not_independently_localized"]
            overall = "review_required"
        elif status_hint == "review_required":
            overall = "review_required"
            if review_reason and review_reason not in ALLOWED_STORY_REVIEW_REASONS:
                reasons.append("invalid_review_reason")
            elif review_reason:
                reasons.append(review_reason)
            else:
                reasons.append("pack_review_required")
        else:
            overall = "ready"
    elif len(ready_values) == 0 and source == "none":
        overall = "missing"
        reasons = ["story_missing"]
    elif len(ready_values) == 0 and status_hint == "review_required":
        overall = "review_required"
        reasons = [review_reason or "pack_review_required"]
        if review_reason and review_reason not in ALLOWED_STORY_REVIEW_REASONS:
            reasons.append("invalid_review_reason")
    else:
        overall = "review_required"
        reasons.append("story_incomplete_locales")

    return {
        "status": _assert_terminal(overall),
        "source": source,
        "reviewReason": review_reason,
        "evidence": evidence,
        "evidenceSha256": evidence_sha,
        "locales": [cell.as_dict() for cell in cells],
        "values": values,
        "reasons": sorted(set(reasons)),
        "producerEnglishFallbackEligible": False,
    }


def resolve_english_set(
    identity_set: str | None,
    qc_set: str | None,
    set_pack: Mapping[str, Any],
) -> tuple[str | None, str, Mapping[str, Any] | None]:
    """Pick English set label and pack row.

    Prefer identity setName when present (cohort identity file). Pack lookup
    tries identity key first, then QC set string — never invents a third label.
    """

    if identity_set:
        row = set_pack.get(identity_set)
        if isinstance(row, Mapping):
            return identity_set, "catalog_identity.setName", row
        if qc_set and isinstance(set_pack.get(qc_set), Mapping):
            # English label still identity; translations found under QC key only
            # when keys differ — report pack under qc key without renaming en.
            return identity_set, "catalog_identity.setName", set_pack.get(qc_set)  # type: ignore[return-value]
        return identity_set, "catalog_identity.setName", None
    if qc_set:
        row = set_pack.get(qc_set)
        return qc_set, "qc_report.facts.identity.set", row if isinstance(row, Mapping) else None
    return None, "none", None


def classify_card(
    card: Mapping[str, Any],
    *,
    identity: Mapping[str, Any] | None,
    name_pack: Mapping[str, Any],
    set_pack: Mapping[str, Any],
    story_index: Mapping[str, Mapping[str, Any]],
    db_story_by_variant: Mapping[int, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    variant_id = int(card["variantId"])
    opaque_id = str(card.get("id") or (identity or {}).get("opaqueId") or "")
    identity = identity or {}

    english_name = _nonempty_str(identity.get("name"))
    identity_set = _nonempty_str(identity.get("setName"))
    qc_identity = (card.get("facts") or {}).get("identity") or {}
    qc_set = _nonempty_str(qc_identity.get("set"))

    name_row = name_pack.get(english_name) if english_name else None
    name_row = name_row if isinstance(name_row, Mapping) else None
    english_set, set_en_prov, set_row = resolve_english_set(identity_set, qc_set, set_pack)

    name_cells = [
        classify_name_locale(locale, english_name, name_row) for locale in REQUIRED_LOCALES
    ]
    set_cells = [
        classify_set_locale(locale, english_set, set_row, en_provenance=set_en_prov)
        for locale in REQUIRED_LOCALES
    ]

    pack_story = story_index.get(opaque_id)
    db_stories = None
    if db_story_by_variant is not None:
        db_stories = db_story_by_variant.get(variant_id)

    stories = classify_stories(pack_story, db_stories=db_stories)

    name_status = _field_status(name_cells)
    set_status = _field_status(set_cells)

    provenance = {
        "name": {
            "en": "catalog_identity.name" if english_name else None,
            "translations": "card_names_pack" if name_row else None,
        },
        "set": {
            "en": set_en_prov if english_set else None,
            "translations": "set_names_pack" if set_row else None,
            "qcSetName": qc_set,
            "identitySetName": identity_set,
            "setNameKeyMismatch": bool(
                identity_set and qc_set and identity_set != qc_set
            ),
        },
        "stories": {
            "source": stories["source"],
            "evidence": stories["evidence"],
            "evidenceSha256": stories["evidenceSha256"],
        },
        "identityGuards": {
            "opaqueId": opaque_id,
            "variantId": variant_id,
            "tcg": card.get("tcg") or identity.get("tcg"),
            "collectorNumber": _nonempty_str(qc_identity.get("collectorNumber"))
            or _nonempty_str(identity.get("collectorNumber")),
        },
    }

    reasons: list[str] = []
    for cell in name_cells:
        if cell.reason:
            reasons.append(f"names.{cell.locale}:{cell.reason}")
    for cell in set_cells:
        if cell.reason:
            reasons.append(f"sets.{cell.locale}:{cell.reason}")
    for reason in stories.get("reasons") or []:
        reasons.append(f"stories:{reason}")

    # Overall: all locale cells ready → ready; any review_required → review; else missing.
    all_cells = name_cells + set_cells + [
        LocaleCell(
            locale=loc["locale"],
            status=loc["status"],
            value=loc["value"],
            reason=loc["reason"],
            provenance=loc["provenance"],
        )
        for loc in stories["locales"]
    ]
    if all(cell.status == "ready" for cell in all_cells):
        overall = "ready"
    elif any(cell.status == "review_required" for cell in all_cells):
        overall = "review_required"
    else:
        overall = "missing"

    return {
        "id": opaque_id,
        "variantId": variant_id,
        "marketRank": card.get("marketRank"),
        "tcg": card.get("tcg") or identity.get("tcg"),
        "segment": card.get("segment"),
        "status": _assert_terminal(overall),
        "names": {
            "status": name_status,
            "locales": [cell.as_dict() for cell in name_cells],
            "values": {cell.locale: cell.value for cell in name_cells},
            "producerEnglishFallbackEligible": True,
        },
        "sets": {
            "status": set_status,
            "locales": [cell.as_dict() for cell in set_cells],
            "values": {cell.locale: cell.value for cell in set_cells},
            "producerEnglishFallbackEligible": True,
        },
        "stories": stories,
        "provenance": provenance,
        "requiredLocales": list(REQUIRED_LOCALES),
        "outOfContractLocales": {
            "ko": {
                "status": "missing",
                "reason": "canonical_schema_has_no_ko",
                "note": "FE interface may use ko; public LocalizedText does not.",
            }
        },
        "missingReviewReasons": sorted(set(reasons)),
        "terminal": True,
    }


def _field_status(cells: Sequence[LocaleCell]) -> str:
    if all(cell.status == "ready" for cell in cells):
        return "ready"
    if any(cell.status == "review_required" for cell in cells):
        return "review_required"
    return "missing"


def classify_cohort(
    cards: Sequence[Mapping[str, Any]],
    *,
    identity_by_variant: Mapping[int, Mapping[str, Any]],
    name_pack: Mapping[str, Any],
    set_pack: Mapping[str, Any],
    story_index: Mapping[str, Mapping[str, Any]],
    db_story_by_variant: Mapping[int, Mapping[str, str]] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for card in cards:
        variant_id = int(card["variantId"])
        out.append(
            classify_card(
                card,
                identity=identity_by_variant.get(variant_id),
                name_pack=name_pack,
                set_pack=set_pack,
                story_index=story_index,
                db_story_by_variant=db_story_by_variant,
            )
        )
    return out


def aggregate_missing_review_reasons(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    reason_counts: Counter[str] = Counter()
    by_field: dict[str, Counter[str]] = {
        "names": Counter(),
        "sets": Counter(),
        "stories": Counter(),
    }
    for record in records:
        for code in record.get("missingReviewReasons") or []:
            reason_counts[str(code)] += 1
            if code.startswith("names."):
                by_field["names"][str(code).split(":", 1)[-1]] += 1
            elif code.startswith("sets."):
                by_field["sets"][str(code).split(":", 1)[-1]] += 1
            elif code.startswith("stories:"):
                by_field["stories"][str(code).split(":", 1)[-1]] += 1

    return {
        "schemaVersion": "1.0.0",
        "kind": "cardz-editorial-missing-review-reasons",
        "totalCards": len(records),
        "reasonCounts": dict(sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "byField": {field: dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))) for field, counter in by_field.items()},
        "allowedStoryReviewReasons": sorted(ALLOWED_STORY_REVIEW_REASONS),
        "invariants": [
            "missing locale content stays missing",
            "never fabricate translation or story text",
            "stories never English-fallback",
            "names/sets producer may English-fallback at export; classification records source truth",
            "canonical required locales are en,zhTW,zhCN,ja only (no ko)",
        ],
    }


def summarize_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    overall = Counter(str(r.get("status")) for r in records)
    name = Counter(str(r["names"]["status"]) for r in records)
    sets = Counter(str(r["sets"]["status"]) for r in records)
    stories = Counter(str(r["stories"]["status"]) for r in records)

    def locale_ready(field: str, locale: str) -> int:
        count = 0
        for record in records:
            for cell in record[field]["locales"]:
                if cell["locale"] == locale and cell["status"] == "ready":
                    count += 1
        return count

    locale_matrix = {
        field: {locale: locale_ready(field, locale) for locale in REQUIRED_LOCALES}
        for field in ("names", "sets", "stories")
    }

    terminal_count = sum(1 for r in records if r.get("terminal") is True)
    fabricated = 0
    for record in records:
        for field in ("names", "sets", "stories"):
            for cell in record[field]["locales"]:
                if cell["status"] == "missing" and cell.get("value") not in (None, ""):
                    fabricated += 1

    return {
        "cardCount": len(records),
        "terminalCount": terminal_count,
        "allTerminal": terminal_count == len(records) and len(records) > 0,
        "fabricatedMissingLocaleValues": fabricated,
        "overallStatus": dict(overall),
        "namesStatus": dict(name),
        "setsStatus": dict(sets),
        "storiesStatus": dict(stories),
        "localeReadyCounts": locale_matrix,
        "requiredLocales": list(REQUIRED_LOCALES),
    }


def render_summary_md(
    *,
    meta: Mapping[str, Any],
    summary: Mapping[str, Any],
    reasons: Mapping[str, Any],
) -> str:
    lines = [
        "# A09 Editorial Locales — C1 Classification",
        "",
        f"- workItemId: `{meta.get('workItemId')}`",
        f"- agentId: `{meta.get('agentId')}`",
        f"- baselineId: `{meta.get('baselineId')}`",
        f"- cohortSha256: `{meta.get('cohortSha256')}`",
        f"- asOf: `{meta.get('asOf')}`",
        f"- classifiedAt: `{meta.get('classifiedAt')}`",
        f"- cardCount: **{summary.get('cardCount')}** (source: QC report)",
        f"- allTerminal: **{summary.get('allTerminal')}**",
        f"- fabricatedMissingLocaleValues: **{summary.get('fabricatedMissingLocaleValues')}**",
        "",
        "## Overall status",
        "",
        "```json",
        json.dumps(summary.get("overallStatus"), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Field status",
        "",
        f"- names: `{summary.get('namesStatus')}`",
        f"- sets: `{summary.get('setsStatus')}`",
        f"- stories: `{summary.get('storiesStatus')}`",
        "",
        "## Locale ready counts (ready only)",
        "",
        "```json",
        json.dumps(summary.get("localeReadyCounts"), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Top missing / review reasons",
        "",
    ]
    reason_counts = reasons.get("reasonCounts") or {}
    if not reason_counts:
        lines.append("_none_")
    else:
        for code, count in list(reason_counts.items())[:40]:
            lines.append(f"- `{code}`: {count}")
    lines.extend(
        [
            "",
            "## Invariants",
            "",
        ]
    )
    for inv in reasons.get("invariants") or []:
        lines.append(f"- {inv}")
    lines.append("")
    return "\n".join(lines)


def build_shard_document(
    records: Sequence[Mapping[str, Any]],
    *,
    meta: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "kind": "cardz-editorial-candidate-shard",
        **dict(meta),
        "summary": summary,
        "cards": list(records),
    }


def write_outputs(
    output_root: Path,
    *,
    records: Sequence[Mapping[str, Any]],
    meta: Mapping[str, Any],
) -> dict[str, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    summary = summarize_records(records)
    reasons = aggregate_missing_review_reasons(records)
    shard = build_shard_document(records, meta=meta, summary=summary)

    paths = {
        "shard_json": output_root / "editorial-candidate-shard.json",
        "shard_jsonl": output_root / "editorial-candidate-shard.jsonl",
        "reasons": output_root / "missing-review-reasons.json",
        "summary_md": output_root / "summary.md",
    }

    write_json(paths["shard_json"], shard)
    with paths["shard_jsonl"].open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    write_json(paths["reasons"], {**reasons, **{k: meta[k] for k in meta if k in {
        "workItemId", "agentId", "waveId", "baselineId", "cohortSha256", "asOf", "classifiedAt"
    }}})
    paths["summary_md"].write_text(
        render_summary_md(meta=meta, summary=summary, reasons=reasons),
        encoding="utf-8",
    )
    return paths


def load_db_stories_by_variant(connection: Any) -> dict[int, dict[str, str]]:
    """Optional read-only DB join. Not required for offline C1 classification."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT variant_id, locale_code, market_story
            FROM catalog_variant_locale
            WHERE market_story IS NOT NULL
              AND locale_code IN ('en', 'zhTW', 'zhCN', 'ja')
            """
        )
        rows = cursor.fetchall()

    grouped: dict[int, dict[str, str]] = {}
    for row in rows:
        if isinstance(row, Mapping):
            variant_id, locale, story = int(row["variant_id"]), str(row["locale_code"]), str(row["market_story"])
        else:
            variant_id, locale, story = int(row[0]), str(row[1]), str(row[2])
        if _nonempty_str(story):
            grouped.setdefault(variant_id, {})[locale] = story.strip()
    return grouped


def run_classification(
    *,
    qc_report: Path,
    identity_path: Path,
    card_names_path: Path,
    set_names_path: Path,
    stories_path: Path,
    connection: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cards = load_cohort_cards(qc_report)
    identity = load_identity_by_variant(identity_path)
    name_pack = _entries_map(card_names_path)
    set_pack = _entries_map(set_names_path)
    story_index = load_story_index(stories_path)
    db_stories = load_db_stories_by_variant(connection) if connection is not None else None

    records = classify_cohort(
        cards,
        identity_by_variant=identity,
        name_pack=name_pack,
        set_pack=set_pack,
        story_index=story_index,
        db_story_by_variant=db_stories,
    )

    source_meta = {
        "qcReportPath": str(qc_report.as_posix()),
        "identityPath": str(identity_path.as_posix()),
        "cardNamesPath": str(card_names_path.as_posix()),
        "setNamesPath": str(set_names_path.as_posix()),
        "storiesPath": str(stories_path.as_posix()),
        "dbStoriesLoaded": db_stories is not None,
        "identityMatched": sum(1 for c in cards if int(c["variantId"]) in identity),
        "namePackEntries": len(name_pack),
        "setPackEntries": len(set_pack),
        "storyPackEntries": len(story_index),
    }
    return records, source_meta


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qc-report", type=Path, default=DEFAULT_QC_REPORT)
    parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--card-names", type=Path, default=DEFAULT_CARD_NAMES)
    parser.add_argument("--set-names", type=Path, default=DEFAULT_SET_NAMES)
    parser.add_argument("--stories", type=Path, default=DEFAULT_STORIES)
    parser.add_argument("--cohort-meta", type=Path, default=DEFAULT_COHORT)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT
        / "data"
        / "runtime"
        / "private-reports"
        / "wave3"
        / "A09-QC-A09-EDITORIAL-LOCALES",
    )
    parser.add_argument(
        "--use-db",
        action="store_true",
        help="Optional read-only catalog_variant_locale stories (requires CARDZ_DB_PASSWORD).",
    )
    parser.add_argument("--work-item-id", default="QC-A09-EDITORIAL-LOCALES")
    parser.add_argument("--agent-id", default="A09")
    parser.add_argument("--wave-id", default="wave-3-domains")
    parser.add_argument("--baseline-id", default="baseline_qc_20260729_wave0_c1_v1")
    parser.add_argument(
        "--cohort-sha256",
        default="2f2fbb1cac643e1580897fc0af2baf4ce746a361f32ee7d68ebb86ce3ebdde41",
    )
    parser.add_argument("--as-of", default="2026-07-29T09:02:59Z")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    connection = None
    if args.use_db:
        sys.path.insert(0, str(ROOT / "pipelines"))
        from db_runtime import add_connection_args, connection_from_args  # noqa: WPS433

        db_parser = argparse.ArgumentParser(add_help=False)
        add_connection_args(db_parser)
        db_args, _ = db_parser.parse_known_args(list(argv or []))
        connection = connection_from_args(db_args)

    try:
        records, source_meta = run_classification(
            qc_report=args.qc_report,
            identity_path=args.identity,
            card_names_path=args.card_names,
            set_names_path=args.set_names,
            stories_path=args.stories,
            connection=connection,
        )
    finally:
        if connection is not None:
            connection.close()

    cohort_meta = {}
    if args.cohort_meta.is_file():
        cohort_meta = read_json(args.cohort_meta)

    meta = {
        "workItemId": args.work_item_id,
        "agentId": args.agent_id,
        "waveId": args.wave_id,
        "baselineId": args.baseline_id,
        "cohortSha256": args.cohort_sha256,
        "asOf": args.as_of,
        "classifiedAt": utc_now_iso(),
        "optionId": cohort_meta.get("optionId", "C1"),
        "qualifiedCountExpected": cohort_meta.get("qualifiedCount"),
        "sources": source_meta,
    }

    paths = write_outputs(args.output_root, records=records, meta=meta)
    summary = summarize_records(records)
    print(
        json.dumps(
            {
                "ok": True,
                "cardCount": summary["cardCount"],
                "allTerminal": summary["allTerminal"],
                "fabricatedMissingLocaleValues": summary["fabricatedMissingLocaleValues"],
                "overallStatus": summary["overallStatus"],
                "outputs": {key: str(path.as_posix()) for key, path in paths.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if summary["allTerminal"] and summary["fabricatedMissingLocaleValues"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
