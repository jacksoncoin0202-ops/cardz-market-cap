#!/usr/bin/env python3
"""Backfill catalog_variant.card_language + catalog_printing_identity.card_language.

Fail-closed: never invent. Only write when evidence converges to one of:
  en | ja | ko | zhCN | zhTW

Evidence priority (highest first):
  1. G10 asset_info.language for bound snkrdunk/ebay ids
  2. GemRate route-verified local public-card receipts with an explicit
     language cue in canonicalUrl or identity.set_name
  3. TPL slug language cues (japanese-, -jp-, chinese-, korean-)
  4. Set-name heuristics (explicit JP/EN markers only)

market_image_source_pointer is diagnostic-only.  An image source family is not
physical-print identity and must never vote or override canonical language.

Conflicts across strong sources → leave NULL + report.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402
from g10_ingest import normalize_language  # noqa: E402
from card_identity import opaque_id_from_row, printing_identity_sha256_from_row  # noqa: E402

# load CARDZ_DB_* from project env before argparse connect
try:
    from qualified_pool_operator import load_env as _load_env

    _load_env()
except Exception:
    pass

G10_ROOTS = [
    ROOT / "integrations" / "grade10" / "data" / "cards",
    ROOT.parent / "grade10-scraper" / "data" / "cards",
]
GEMRATE_RUNS_ROOT = ROOT / "data" / "private" / "gemrate" / "runs"
REPORT_DEFAULT = ROOT / "temp" / "card-language-backfill-report.json"
CANON = frozenset({"en", "ja", "ko", "zhCN", "zhTW"})

# Strong set-name markers only (no weak "Pokemon" alone)
SET_JA = re.compile(
    r"(?i)\b(?:japanese|jp\b|jpn\b|sv\d|s\d{1,2}[a-z]?[:\s]|m\d[:\s]|sm\d|xy[-\s]|cp\d|"
    r"pokemon card 151|sv2a|sword shield promotional|s-p\b|m2[:\s]|m1[sl]?\b)"
)
SET_EN = re.compile(
    r"(?i)\b(?:english|en\b|scarlet and violet|sword and shield|sun and moon|"
    r"mega evolution phantasma|prismatic|destined rivals|black bolt|white flare|"
    r"crown zenith|shining fates|paldean|obsidian|mew\b.*151 mew)"
)
SET_KO = re.compile(r"(?i)\b(?:korean|kr\b|korea)\b")
SET_ZHTW = re.compile(r"(?i)\b(?:traditional chinese|zh-tw|taiwan|cht)\b")
SET_ZHCN = re.compile(r"(?i)\b(?:simplified chinese|zh-cn|mainland|chs)\b")


def canon(value: Any) -> str | None:
    if value is None:
        return None
    # g10 uses "jp" often
    n = normalize_language(value)
    if n in CANON:
        return n
    # also accept already-canonical
    s = str(value).strip()
    if s in CANON:
        return s
    # uppercase JA → ja
    low = s.casefold().replace("_", "-")
    aliases = {
        "ja": "ja",
        "jp": "ja",
        "jpn": "ja",
        "japanese": "ja",
        "en": "en",
        "eng": "en",
        "english": "en",
        "ko": "ko",
        "kr": "ko",
        "korean": "ko",
        "zhcn": "zhCN",
        "zh-cn": "zhCN",
        "zh-hans": "zhCN",
        "zhtw": "zhTW",
        "zh-tw": "zhTW",
        "zh-hant": "zhTW",
    }
    return aliases.get(low)


def find_g10_root() -> Path | None:
    for p in G10_ROOTS:
        if (p / "snkrdunk").is_dir() or (p / "altxyz").is_dir():
            return p
    return None


def g10_language(g10_root: Path, source_code: str, external_id: str) -> str | None:
    provider = "snkrdunk" if source_code == "snkrdunk" else ("altxyz" if source_code in ("ebay", "altxyz") else None)
    if not provider:
        return None
    path = g10_root / provider / str(external_id) / "asset_info.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return canon(data.get("language") or data.get("lang"))


def tpl_language(slug: str) -> str | None:
    """TPL is primarily the EN market catalog.

    Region markers force ja/ko/zh*; otherwise pokemon-* / onepiece-* slugs
    without a region token resolve to en (not a guess from card name).
    """
    s = (slug or "").casefold()
    if not s:
        return None
    if "japanese" in s or "-jp-" in s or s.endswith("-jp") or "jpn" in s:
        return "ja"
    if "korean" in s or "-kr-" in s:
        return "ko"
    if "chinese" in s and ("traditional" in s or "taiwan" in s):
        return "zhTW"
    if "chinese" in s and ("simplified" in s or "mainland" in s):
        return "zhCN"
    if "english" in s or "-en-" in s:
        return "en"
    # EN catalog default (TCGPriceLookup US product keys)
    if s.startswith("pokemon-") or s.startswith("onepiece-"):
        return "en"
    return None


def set_language(set_name: str) -> str | None:
    s = set_name or ""
    hits: list[str] = []
    if SET_KO.search(s):
        hits.append("ko")
    if SET_ZHTW.search(s):
        hits.append("zhTW")
    if SET_ZHCN.search(s):
        hits.append("zhCN")
    # JP set codes like SV2a:, S10a:, m1S: are strong JA signals
    if re.search(r"(?i)\b(?:sv\d+[a-z]?|s\d{1,2}[a-z]?|m\d[a-z]?|sm\d+|xy\d*|cp\d+)\s*:", s):
        hits.append("ja")
    if re.search(r"(?i)pokemon card \d+|sv2a|japanese", s):
        hits.append("ja")
    if SET_EN.search(s) and "japanese" not in s.casefold():
        hits.append("en")
    # One Piece EN set names
    if re.search(r"(?i)one piece (awakening|emperors|romance|wings|future|best)", s):
        hits.append("en")
    uniq = list(dict.fromkeys(hits))
    if len(uniq) == 1:
        return uniq[0]
    return None


def pointer_language(source_path: str) -> str | None:
    p = (source_path or "").casefold().replace("\\", "/")
    if "pkmn-tcg-en" in p or "/en/" in p or "_en.webp" in p:
        return "en"
    if "pkmn-tcg-jp" in p or "pkmn-tcg-ja" in p or "_jp.webp" in p:
        return "ja"
    return None


def _receipt_explicit_language(value: str) -> str | None:
    """Read an explicit language cue only; card names never participate."""
    return set_language(value)


@lru_cache(maxsize=4)
def gemrate_receipt_index(gemrate_runs_root: Path) -> dict[str, tuple[Path, ...]]:
    """Index local receipt paths once; a full DB dry-run must not rescan runs per card."""
    by_id: dict[str, list[Path]] = {}
    if gemrate_runs_root.is_dir():
        for path in gemrate_runs_root.glob("*/cards/*/card_details.json"):
            by_id.setdefault(path.parent.name, []).append(path)
    return {
        external_id: tuple(sorted(paths, key=lambda path: path.parents[2].name, reverse=True))
        for external_id, paths in by_id.items()
    }


def gemrate_language(gemrate_runs_root: Path, external_id: str) -> tuple[str | None, str | None]:
    """Return language from the newest valid, route-verified GemRate receipt.

    The canonical URL and GemRate identity set name are provider-owned evidence.
    They must agree when both provide a language.  A receipt without an explicit
    cue is deliberately no vote.
    """
    if not external_id or not gemrate_runs_root.is_dir():
        return None, None
    paths = gemrate_receipt_index(gemrate_runs_root).get(str(external_id), ())
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        page = data.get("publicCardPage")
        if not isinstance(page, dict) or page.get("routeVerified") is not True:
            continue
        canonical_url = str(page.get("canonicalUrl") or "")
        identity = page.get("identity")
        set_name = str(identity.get("set_name") or "") if isinstance(identity, dict) else ""
        cues = [
            lang
            for lang in (_receipt_explicit_language(canonical_url), _receipt_explicit_language(set_name))
            if lang
        ]
        if not cues:
            continue
        if len(set(cues)) != 1:
            return None, f"gemrate:{external_id}:receipt_conflict:{path.parents[2].name}"
        return cues[0], f"gemrate:{external_id}:receipt:{path.parents[2].name}"
    return None, None


def resolve_language(
    *,
    set_name: str,
    sources: list[dict[str, str]],
    g10_root: Path | None,
    pointer_paths: list[str],
    gemrate_runs_root: Path = GEMRATE_RUNS_ROOT,
) -> tuple[str | None, list[str], str]:
    """Return (lang|None, evidence[], status)."""
    votes: list[tuple[str, str, int]] = []  # lang, source, weight

    if g10_root:
        for s in sources:
            lang = g10_language(g10_root, s["source_code"], s["external_entity_id"])
            if lang:
                votes.append((lang, f"g10:{s['source_code']}/{s['external_entity_id']}", 100))

    for s in sources:
        if s["source_code"] == "gemrate":
            lang, receipt = gemrate_language(gemrate_runs_root, s["external_entity_id"])
            if receipt and ":receipt_conflict:" in receipt:
                return None, [receipt], "conflict"
            if lang and receipt:
                votes.append((lang, receipt, 100))

    for s in sources:
        if s["source_code"] == "tcgpricelookup":
            lang = tpl_language(s["external_entity_id"])
            if lang:
                votes.append((lang, f"tpl:{s['external_entity_id'][:60]}", 80))

    diagnostics = [
        f"diagnostic:ptr:{pointer_language(path)}:{path[:80]}"
        for path in pointer_paths
        if pointer_language(path)
    ]

    set_lang = set_language(set_name)
    if set_lang:
        votes.append((set_lang, f"set:{set_name[:60]}", 40))

    if not votes:
        return None, diagnostics, "no_evidence"

    # weight-sum by language
    by: dict[str, int] = Counter()
    evidence: list[str] = []
    for lang, ev, w in votes:
        by[lang] += w
        evidence.append(f"{lang}@{w}:{ev}")

    ranked = sorted(by.items(), key=lambda x: -x[1])
    best_lang, _best_w = ranked[0]
    # Strong-provider disagreement is an identity conflict even when two
    # providers outvote one.  Language is not a confidence average.
    strong_languages = {lang for lang, _source, weight in votes if weight >= 60}
    if len(strong_languages) > 1:
        return None, evidence, "conflict"
    return best_lang, [*evidence, *diagnostics], "ok"


def build_identity_update_plan(
    rows: list[dict[str, Any]], language_by_variant: dict[int, str],
) -> list[dict[str, Any]]:
    """Compute final IDs, then prove their global uniqueness before any write.

    ``rows`` must include every catalog variant, not merely rows being changed:
    an update is invalid if it would take an identity currently owned by an
    unchanged row.  Printing rows are mandatory for a language mutation;
    silently leaving an old hash behind would break the identity invariant.
    """
    planned: list[dict[str, Any]] = []
    seen_opaque: dict[str, int] = {}
    seen_printing: dict[str, int] = {}
    ids = {int(row["id"]) for row in rows}
    missing = sorted(set(language_by_variant) - ids)
    if missing:
        raise ValueError(f"language update has missing catalog variants: {missing[:10]}")

    for row in rows:
        variant_id = int(row["id"])
        final_language = language_by_variant.get(variant_id, row.get("card_language"))
        target = dict(row)
        target["card_language"] = final_language
        if variant_id in language_by_variant:
            if not row.get("printing_variant_id"):
                raise ValueError(f"variant_id={variant_id} has no catalog_printing_identity")
            target["edition_code"] = row.get("edition_code") or ""
            target["parallel_code"] = row.get("parallel_code") or ""
            target["finish_code"] = row.get("finish_code") or ""
            next_opaque = opaque_id_from_row(target)
            next_printing = printing_identity_sha256_from_row(target)
            planned.append(
                {
                    "variant_id": variant_id,
                    "old_language": row.get("card_language"),
                    "old_opaque": str(row.get("opaque_id") or ""),
                    "old_printing_language": row.get("printing_card_language"),
                    "old_printing_sha256": str(row.get("canonical_printing_sha256") or ""),
                    "language": final_language,
                    "opaque_id": next_opaque,
                    "printing_sha256": next_printing,
                }
            )
        else:
            next_opaque = str(row.get("opaque_id") or "")
            next_printing = str(row.get("canonical_printing_sha256") or "")

        if not next_opaque:
            raise ValueError(f"variant_id={variant_id} has empty opaque_id")
        prior = seen_opaque.setdefault(next_opaque, variant_id)
        if prior != variant_id:
            raise ValueError(f"opaque_id collision: variant_id={variant_id} with {prior}: {next_opaque}")
        # A NULL printing row is permitted only for an unchanged legacy variant.
        if next_printing:
            prior = seen_printing.setdefault(next_printing, variant_id)
            if prior != variant_id:
                raise ValueError(f"canonical_printing_sha256 collision: variant_id={variant_id} with {prior}")

    # Unique temporary values let a valid language swap pass MySQL's immediate
    # UNIQUE checks without exposing a partial state (the transaction rolls back
    # as one unit on any failed rowcount precondition).
    import hashlib
    for item in planned:
        variant_id = item["variant_id"]
        item["temp_opaque"] = f"cmc_language_backfill_tmp_{variant_id}"
        item["temp_printing_sha256"] = hashlib.sha256(
            f"card-language-backfill-temp|{variant_id}".encode("utf-8")
        ).hexdigest()
    temp_opaque = {item["temp_opaque"] for item in planned}
    temp_printing = {item["temp_printing_sha256"] for item in planned}
    if len(temp_opaque) != len(planned) or len(temp_printing) != len(planned):
        raise ValueError("temporary identity collision")
    if temp_opaque & set(seen_opaque):
        raise ValueError("temporary opaque_id collides with final/current identity")
    if temp_printing & set(seen_printing):
        raise ValueError("temporary printing hash collides with final/current identity")
    return planned


def apply_identity_update_plan(cur: Any, plan: list[dict[str, Any]]) -> None:
    """Apply a preflighted plan within the caller's transaction, or raise."""
    for item in plan:
        cur.execute(
            """
            UPDATE catalog_variant SET opaque_id=%s
            WHERE id=%s AND opaque_id=%s AND card_language <=> %s
            """,
            (item["temp_opaque"], item["variant_id"], item["old_opaque"], item["old_language"]),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"catalog_variant temporary rowcount precondition failed: {item['variant_id']}")
        cur.execute(
            """
            UPDATE catalog_printing_identity SET canonical_printing_sha256=%s
            WHERE variant_id=%s AND canonical_printing_sha256=%s AND card_language <=> %s
            """,
            (item["temp_printing_sha256"], item["variant_id"], item["old_printing_sha256"], item["old_printing_language"]),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"catalog_printing_identity temporary rowcount precondition failed: {item['variant_id']}")

    for item in plan:
        cur.execute(
            """
            UPDATE catalog_variant SET card_language=%s, opaque_id=%s
            WHERE id=%s AND opaque_id=%s
            """,
            (item["language"], item["opaque_id"], item["variant_id"], item["temp_opaque"]),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"catalog_variant final rowcount precondition failed: {item['variant_id']}")
        cur.execute(
            """
            UPDATE catalog_printing_identity
            SET card_language=%s, canonical_printing_sha256=%s
            WHERE variant_id=%s AND canonical_printing_sha256=%s
            """,
            (item["language"], item["printing_sha256"], item["variant_id"], item["temp_printing_sha256"]),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"catalog_printing_identity final rowcount precondition failed: {item['variant_id']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    add_connection_args(parser)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report-out", type=Path, default=REPORT_DEFAULT)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    g10_root = find_g10_root()
    conn = connection_from_args(args)
    stats: Counter = Counter()
    samples: list[dict[str, Any]] = []
    would_write_samples: list[dict[str, Any]] = []

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT variant.id, variant.opaque_id, variant.tcg_code,
                       variant.canonical_name, variant.set_name,
                       variant.collector_number, variant.card_language,
                       printing.variant_id AS printing_variant_id,
                       printing.card_language AS printing_card_language,
                       printing.edition_code, printing.parallel_code,
                       printing.finish_code, printing.canonical_printing_sha256
                FROM catalog_variant AS variant
                LEFT JOIN catalog_printing_identity AS printing
                  ON printing.variant_id=variant.id
                ORDER BY variant.id
                """
            )
            all_variants = list(cur.fetchall())
            # --limit bounds evidence work only.  Collision preflight always
            # sees every current identity, including untouched rows.
            variants = all_variants[: args.limit] if args.limit else all_variants

            # Evidence is read in two bounded bulk queries.  Full and delta
            # backfills share this path; do not turn a full dry-run into one
            # source/pointer query pair per variant.
            variant_ids = [int(v["id"]) for v in variants]
            source_by_variant: dict[int, list[dict[str, str]]] = {}
            pointer_by_variant: dict[int, list[str]] = {}
            if variant_ids:
                placeholders = ",".join(["%s"] * len(variant_ids))
                cur.execute(
                    f"""
                    SELECT variant_id, source_code, external_entity_id
                    FROM catalog_source_identity WHERE variant_id IN ({placeholders})
                    """,
                    variant_ids,
                )
                for row in cur.fetchall():
                    source_by_variant.setdefault(int(row["variant_id"]), []).append(
                        {"source_code": str(row["source_code"]), "external_entity_id": str(row["external_entity_id"])}
                    )
                cur.execute(
                    f"""
                    SELECT variant_id, source_path
                    FROM market_image_source_pointer WHERE variant_id IN ({placeholders})
                    """,
                    variant_ids,
                )
                for row in cur.fetchall():
                    pointer_by_variant.setdefault(int(row["variant_id"]), []).append(str(row["source_path"] or ""))

            language_by_variant: dict[int, str] = {}
            for v in variants:
                vid = int(v["id"])
                sources = source_by_variant.get(vid, [])
                pointers = pointer_by_variant.get(vid, [])[:20]

                lang, evidence, status = resolve_language(
                    set_name=str(v["set_name"] or ""),
                    sources=sources,
                    g10_root=g10_root,
                    pointer_paths=pointers,
                )
                stats[status] += 1
                if lang:
                    stats[f"lang_{lang}"] += 1
                    if v.get("card_language") != lang:
                        language_by_variant[vid] = lang
                        stats["would_write"] += 1
                        if len(would_write_samples) < 100:
                            would_write_samples.append(
                                {
                                    "variantId": vid,
                                    "name": v["canonical_name"],
                                    "set": v["set_name"],
                                    "collector": v["collector_number"],
                                    "from": v.get("card_language"),
                                    "to": lang,
                                    "evidence": evidence,
                                }
                            )
                    else:
                        stats["already_correct"] += 1
                else:
                    stats["null"] += 1
                if len(samples) < 40 and (status != "ok" or not lang):
                    samples.append(
                        {
                            "variantId": vid,
                            "name": v["canonical_name"],
                            "set": v["set_name"],
                            "collector": v["collector_number"],
                            "status": status,
                            "lang": lang,
                            "evidence": evidence[:6],
                        }
                    )

            plan = build_identity_update_plan(all_variants, language_by_variant)
            stats["identity_preflighted"] = len(plan)
            if args.write and plan:
                try:
                    apply_identity_update_plan(cur, plan)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                stats["written"] = len(plan)
            elif args.write:
                stats["written"] = 0
            else:
                stats["written"] = 0
                stats["dry_run"] = 1

            cur.execute(
                """
                SELECT card_language, COUNT(*) n FROM catalog_variant
                GROUP BY card_language ORDER BY n DESC
                """
            )
            distribution = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    report = {
        "g10Root": str(g10_root) if g10_root else None,
        "write": bool(args.write),
        "stats": dict(stats),
        "distribution": distribution,
        "samples": samples,
        "wouldWriteSamples": would_write_samples,
    }
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stats": dict(stats), "distribution": distribution}, ensure_ascii=False, indent=2))
    print(f"report {args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
