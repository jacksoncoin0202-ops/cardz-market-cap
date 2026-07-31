#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-button maintenance for GemRate PSA10 POP>=1000 qualified pool (940).

Subcommands:
  status          — DB coverage for watchlist
  export-worklist — dump 940 rows + join catalog fields
  map-tpl         — catalog-search TCGPriceLookup slugs (no API key)
  harvest-tpl     — full|incremental SSR harvest for mapped slugs
  ingest-prices   — write tcgpricelookup prices into market_price_observation
  fill-images     — TCGplayer/TPL image → market-assets + image-qc
  maintain        — status → map-tpl → harvest → ingest → fill-images → status

Windows:
  python -X utf8 pipelines/qualified_pool_operator.py maintain
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "pipelines") not in sys.path:
    sys.path.insert(0, str(ROOT / "pipelines"))

MAP_DIR = ROOT / "data/runtime/private-source-map"
WORKLIST = MAP_DIR / "qualified-940-worklist.jsonl"
TPL_MAP = MAP_DIR / "tpl-slug-map.jsonl"
TPL_SLUGS = MAP_DIR / "tpl-slugs.txt"
TPL_ROOT = MAP_DIR / "tcgpricelookup"
REPORT_DIR = MAP_DIR / "qualified-pool-reports"

SOURCE_CODE = "tcgpricelookup"
PRICE_PRIORITY = 80  # below SNK ranking authority; US secondary


def load_env() -> None:
    path = ROOT / "data/runtime/config/backend.env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def db():
    import pymysql

    load_env()
    return pymysql.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ["CARDZ_DB_USER"],
        password=os.environ["CARDZ_DB_PASSWORD"],
        database=os.environ["CARDZ_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    with temporary.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    os.replace(temporary, path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def cmd_status() -> dict[str, Any]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
          COUNT(*) AS watch,
          SUM(w.variant_id IS NOT NULL) AS has_variant,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p WHERE p.variant_id=w.variant_id)) AS any_price,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p WHERE p.variant_id=w.variant_id AND p.source_code='snk_psa10')) AS snk_price,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p WHERE p.variant_id=w.variant_id AND p.source_code IN ('snk_psa10','snk','snkrdunk'))) AS snk_family_price,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p WHERE p.variant_id=w.variant_id AND p.source_code='ebay')) AS ebay_price,
          SUM(
            CASE
              WHEN EXISTS(
                SELECT 1
                FROM market_price_observation p
                WHERE p.variant_id=w.variant_id
                  AND p.source_code='ebay'
                  AND p.price_usd > 0
                  AND p.metric_status='ready'
                  AND p.effective_at <= UTC_TIMESTAMP()
                  AND EXISTS(
                    SELECT 1 FROM catalog_source_identity s
                    WHERE s.variant_id=w.variant_id
                      AND s.match_status='exact'
                      AND s.source_code IN ('ebay','pricecharting')
                  )
              )
              THEN EXISTS(
                SELECT 1
                FROM market_price_observation p
                WHERE p.variant_id=w.variant_id
                  AND p.source_code='ebay'
                  AND p.price_usd > 0
                  AND p.metric_status='ready'
                  AND p.effective_at BETWEEN DATE_SUB(UTC_TIMESTAMP(), INTERVAL 48 HOUR)
                                         AND UTC_TIMESTAMP()
                  AND EXISTS(
                    SELECT 1 FROM catalog_source_identity s
                    WHERE s.variant_id=w.variant_id
                      AND s.match_status='exact'
                      AND s.source_code IN ('ebay','pricecharting')
                  )
              )
              ELSE EXISTS(
                SELECT 1
                FROM market_price_observation p
                WHERE p.variant_id=w.variant_id
                  AND p.source_code IN ('snk_psa10','snk','snkrdunk')
                  AND p.price_usd > 0
                  AND p.metric_status='ready'
                  AND p.effective_at BETWEEN DATE_SUB(UTC_TIMESTAMP(), INTERVAL 48 HOUR)
                                         AND UTC_TIMESTAMP()
                  AND EXISTS(
                    SELECT 1 FROM catalog_source_identity s
                    WHERE s.variant_id=w.variant_id
                      AND s.match_status='exact'
                      AND s.source_code IN ('snk_psa10','snk','snkrdunk')
                  )
              )
            END
          ) AS authoritative_price,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p WHERE p.variant_id=w.variant_id AND p.source_code=%s)) AS tpl_price,
          SUM(EXISTS(SELECT 1 FROM market_image_asset i WHERE i.variant_id=w.variant_id)) AS image_asset,
          SUM(EXISTS(SELECT 1 FROM market_image_source_pointer ip WHERE ip.variant_id=w.variant_id)) AS image_ptr,
          SUM(EXISTS(SELECT 1 FROM catalog_source_identity s WHERE s.variant_id=w.variant_id AND s.source_code IN ('snkrdunk','snk'))) AS snk_id,
          SUM(EXISTS(SELECT 1 FROM catalog_source_identity s WHERE s.variant_id=w.variant_id AND s.source_code='ebay')) AS ebay_id
        FROM market_gemrate_psa10_watchlist w
        """,
        (SOURCE_CODE,),
    )
    row = cur.fetchone() or {}
    # coerce decimals
    status = {k: int(v or 0) for k, v in row.items()}
    # Sales: store ALL history; windows are derived (1d/7d/21d/30d/all) — not collection filters
    cur.execute(
        """
        SELECT
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY) THEN s.variant_id END) AS sale_1d,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 7 DAY) THEN s.variant_id END) AS sale_7d,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 21 DAY) THEN s.variant_id END) AS sale_21d,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY) THEN s.variant_id END) AS sale_30d,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 90 DAY) THEN s.variant_id END) AS sale_90d,
          COUNT(DISTINCT s.variant_id) AS sale_any
        FROM market_sale_observation s
        WHERE s.variant_id IN (SELECT variant_id FROM market_gemrate_psa10_watchlist)
          AND s.sold_at IS NOT NULL
        """
    )
    sale = cur.fetchone() or {}
    for k, v in sale.items():
        status[k] = int(v or 0)
    status["fetchedAt"] = utc_now()
    status["worklistExists"] = WORKLIST.is_file()
    status["tplMapExists"] = TPL_MAP.is_file()
    if TPL_MAP.is_file():
        mapped = read_jsonl(TPL_MAP)
        status["tplMapped"] = sum(1 for r in mapped if r.get("tplSlug"))
        status["tplMapRows"] = len(mapped)
    else:
        status["tplMapped"] = 0
        status["tplMapRows"] = 0
    cards_dir = TPL_ROOT / "cards"
    status["tplHarvestFiles"] = len(list(cards_dir.glob("*.json"))) if cards_dir.is_dir() else 0
    conn.close()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return status


def cmd_export_worklist() -> list[dict[str, Any]]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
          w.gemrate_id,
          w.variant_id,
          w.card_name,
          w.set_name,
          w.collector_number,
          w.psa10_population,
          w.population_as_of,
          v.opaque_id,
          v.tcg_code,
          v.canonical_name,
          v.set_name AS variant_set_name,
          v.collector_number AS variant_collector_number
        FROM market_gemrate_psa10_watchlist w
        JOIN catalog_variant v ON v.id = w.variant_id
        ORDER BY w.psa10_population DESC, w.variant_id
        """
    )
    rows = cur.fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append(
            {
                "gemrateId": r["gemrate_id"],
                "variantId": int(r["variant_id"]),
                "opaqueId": r["opaque_id"],
                "tcg": r["tcg_code"],
                "name": r["card_name"] or r["canonical_name"],
                "setName": r["set_name"] or r["variant_set_name"],
                "collectorNumber": r["collector_number"] or r["variant_collector_number"],
                "psa10Population": int(r["psa10_population"] or 0),
                "populationAsOf": str(r["population_as_of"] or ""),
            }
        )
    write_jsonl(WORKLIST, out)
    print(json.dumps({"worklist": str(WORKLIST), "cards": len(out)}, sort_keys=True))
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").casefold())


def _game_for_tcg(tcg: str) -> str:
    code = (tcg or "").casefold()
    if code in {"one-piece", "optcg", "onepiece"}:
        return "onepiece"
    if "japan" in code or code.endswith("-jp"):
        return "pokemon-jp"
    return "pokemon"


_STOP_NAME = {
    "the", "and", "with", "secret", "promo", "full", "art", "alternate", "special",
    "illustration", "rare", "ultra", "holo", "holofoil", "card", "pokemon", "piece",
}


def _collector_forms(collector: str) -> list[str]:
    """Collector variants for TPL slugs: OP11-080, 004/102, GG68, etc."""

    raw = (collector or "").strip().casefold()
    if not raw:
        return []
    parts = re.findall(r"[a-z0-9]+", raw)
    forms: list[str] = [raw]
    if parts:
        forms.append("-".join(parts))
        forms.append("".join(parts))
        forms.append(parts[-1])
        if len(parts) >= 2:
            forms.append("-".join(parts[-2:]))
            forms.append("".join(parts[-2:]))
            forms.append(parts[0] + "-" + parts[-1])
    # unique keep order, drop tiny noise
    out: list[str] = []
    for f in forms:
        f = f.strip("-")
        if len(f) >= 1 and f not in out:
            out.append(f)
    return out


def rank_slug_candidates(
    slugs: list[str],
    *,
    name: str,
    set_name: str,
    collector: str,
) -> list[tuple[int, str, bool]]:
    """Score TPL slugs. Returns (score, slug, collector_strong).

    Auto-bind requires name signal + collector evidence (not number alone).
    """

    want_num = _norm(collector)
    want_num_stripped = want_num.lstrip("0") or want_num
    col_forms = _collector_forms(collector)
    raw_name_tokens = [t for t in re.findall(r"[a-z0-9]+", (name or "").casefold()) if len(t) > 2]
    want_name_tokens = {t for t in raw_name_tokens if t not in _STOP_NAME}
    # keep first meaningful token even if short list
    if not want_name_tokens and raw_name_tokens:
        want_name_tokens = {raw_name_tokens[0]}
    want_set_tokens = {
        t for t in re.findall(r"[a-z0-9]+", (set_name or "").casefold()) if len(t) > 2 and t not in _STOP_NAME
    }
    ranked: list[tuple[int, str, bool]] = []
    for slug in slugs:
        s = slug.casefold()
        compact = _norm(slug)
        slug_tokens = set(re.findall(r"[a-z0-9]+", s))
        segs = re.findall(r"[a-z0-9]+", s)
        score = 0
        collector_strong = False
        # 1) hyphenated multi-part collector in slug (OP11-080, 004-102)
        for form in col_forms:
            if len(form) < 3:
                continue
            # path-ish: -op11-080- / ends with -op11-080
            if f"-{form}-" in f"-{s}-" or s.endswith(f"-{form}") or s.startswith(f"{form}-"):
                score += 100
                collector_strong = True
                break
            if form in segs:
                score += 100
                collector_strong = True
                break
        # 2) single segment exact (gg68, 025)
        if not collector_strong and want_num:
            for seg in segs:
                if _norm(seg) == want_num or _norm(seg).lstrip("0") == want_num_stripped:
                    score += 100
                    collector_strong = True
                    break
        # 3) weak compact substring only
        if not collector_strong and want_num and want_num in compact:
            score += 25
        name_hits = want_name_tokens & slug_tokens
        score += 25 * len(name_hits)
        # primary creature/word often first token in card name
        if raw_name_tokens and raw_name_tokens[0] in slug_tokens:
            score += 40
        score += 6 * len(want_set_tokens & slug_tokens)
        ranked.append((score, slug, collector_strong))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked


def _map_one_card(card: Mapping[str, Any]) -> dict[str, Any]:
    """Map a single watchlist card to a TPL slug (thread-safe HTTP)."""

    from tcgpricelookup_ssr import search_catalog

    vid = int(card["variantId"])
    game = _game_for_tcg(str(card.get("tcg") or "pokemon"))
    collector = str(card.get("collectorNumber") or "").strip()
    name = str(card.get("name") or "").strip()
    # Strong queries first; keep trying if first query returns weak-empty.
    queries: list[tuple[str, str]] = []
    if name and collector:
        queries.append((f"{name} {collector}", game))
    if collector:
        queries.append((collector, game))
    if name:
        queries.append((name, game))
    if game == "pokemon":
        q0 = collector or name
        if q0:
            queries.append((q0, "pokemon-jp"))
    # One Piece: also try bare set-number style
    if game == "onepiece" and collector:
        queries.append((collector.replace("/", "-"), game))

    candidates: list[str] = []
    error = None
    seen_q: set[tuple[str, str]] = set()
    for q, g in queries:
        key = (q, g)
        if key in seen_q:
            continue
        seen_q.add(key)
        try:
            hits = search_catalog(q, game=g)
            candidates.extend(h["slug"] for h in hits)
            if hits:
                break
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
    ranked = rank_slug_candidates(
        list(dict.fromkeys(candidates)),
        name=name,
        set_name=str(card.get("setName") or ""),
        collector=collector,
    )
    best_slug = ranked[0][1] if ranked else None
    best_score = ranked[0][0] if ranked else 0
    collector_strong = ranked[0][2] if ranked else False
    # Require name token evidence: bare number hits stay needsReview.
    name_tokens = {
        t
        for t in re.findall(r"[a-z0-9]+", name.casefold())
        if len(t) > 2 and t not in _STOP_NAME
    }
    name_ok = False
    if best_slug and name_tokens:
        slug_tokens = set(re.findall(r"[a-z0-9]+", best_slug.casefold()))
        name_ok = bool(name_tokens & slug_tokens)
    # Fail-closed: need name + collector evidence.
    # 140 classic; 120+ with strong collector (OP11-080 style) also auto.
    auto_ok = (
        best_slug is not None
        and name_ok
        and (
            best_score >= 140
            or (best_score >= 120 and collector_strong)
        )
    )
    return {
        **dict(card),
        "tplSlug": best_slug if auto_ok else None,
        "tplSlugCandidate": best_slug,
        "tplMatchScore": best_score,
        "tplCollectorStrong": collector_strong,
        "tplCandidates": [s for _, s, _ in ranked[:8]],
        "needsReview": (not auto_ok),
        "mapError": error,
        "mappedAt": utc_now(),
        "variantId": vid,
    }


def cmd_map_tpl(
    *,
    limit: int | None = None,
    delay: float = 0.15,
    resume: bool = True,
    workers: int = 8,
) -> list[dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    work = read_jsonl(WORKLIST) or cmd_export_worklist()
    if limit:
        work = work[:limit]
    existing = {int(r["variantId"]): r for r in read_jsonl(TPL_MAP) if r.get("variantId")} if resume else {}
    out_by_id: dict[int, dict[str, Any]] = {}
    todo: list[dict[str, Any]] = []
    for card in work:
        vid = int(card["variantId"])
        # Full-volume rule: only skip cards already auto-bound with tplSlug.
        if resume and vid in existing and existing[vid].get("tplSlug"):
            out_by_id[vid] = existing[vid]
            continue
        todo.append(card)

    print(f"[map] resume_keep={len(out_by_id)} todo={len(todo)} workers={workers}", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_map_one_card, card): int(card["variantId"]) for card in todo}
        for fut in as_completed(futures):
            row = fut.result()
            out_by_id[int(row["variantId"])] = row
            done += 1
            if done % 25 == 0 or done == len(todo):
                # checkpoint in worklist order
                ordered = [out_by_id[int(c["variantId"])] for c in work if int(c["variantId"]) in out_by_id]
                write_jsonl(TPL_MAP, ordered)
                mapped_n = sum(1 for r in ordered if r.get("tplSlug"))
                print(f"[map] progress {done}/{len(todo)} checkpoint_mapped={mapped_n}", flush=True)
            time.sleep(delay / max(workers, 1))

    out_rows = []
    for card in work:
        vid = int(card["variantId"])
        if vid in out_by_id:
            out_rows.append(out_by_id[vid])
        elif vid in existing:
            out_rows.append(existing[vid])
    write_jsonl(TPL_MAP, out_rows)
    slugs = sorted({r["tplSlug"] for r in out_rows if r.get("tplSlug")})
    TPL_SLUGS.write_text(("\n".join(slugs) + ("\n" if slugs else "")), encoding="utf-8")
    summary = {
        "rows": len(out_rows),
        "mapped": sum(1 for r in out_rows if r.get("tplSlug")),
        "needsReview": sum(1 for r in out_rows if r.get("needsReview")),
        "slugsFile": str(TPL_SLUGS),
        "mapFile": str(TPL_MAP),
        "workers": workers,
    }
    print(json.dumps(summary, sort_keys=True))
    return out_rows


def cmd_harvest_tpl(
    *,
    mode: str = "full",
    delay: float = 0.35,
    limit: int | None = None,
    workers: int = 6,
) -> dict[str, Any]:
    from tcgpricelookup_ssr import harvest

    mapped = read_jsonl(TPL_MAP)
    slugs = [r["tplSlug"] for r in mapped if r.get("tplSlug")]
    # unique preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for s in slugs:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    if limit:
        ordered = ordered[:limit]
    if not ordered:
        raise SystemExit("no tplSlug mapped — run map-tpl first")
    return harvest(ordered, out_dir=TPL_ROOT, mode=mode, delay=delay, workers=workers)


def _psa10_usd_from_tpl(doc: Mapping[str, Any]) -> float | None:
    psa10 = doc.get("psa10") if isinstance(doc.get("psa10"), Mapping) else {}
    val = psa10.get("ebayAvg1d")
    if isinstance(val, (int, float)) and val > 0:
        return float(val)
    # nested current.ebay.avg_1d
    current = psa10.get("current") if isinstance(psa10.get("current"), Mapping) else {}
    ebay = current.get("ebay") if isinstance(current.get("ebay"), Mapping) else {}
    val = ebay.get("avg_1d")
    if isinstance(val, (int, float)) and val > 0:
        return float(val)
    # last history point
    hist = psa10.get("history") if isinstance(psa10.get("history"), list) else []
    for row in reversed(hist):
        if isinstance(row, Mapping) and isinstance(row.get("avg_1d"), (int, float)) and row["avg_1d"] > 0:
            return float(row["avg_1d"])
    # fallback NM tcgplayer as non-psa (do not use for psa10 authority)
    return None


def cmd_ingest_prices(*, dry_run: bool = False) -> dict[str, Any]:
    # Operator 2026-07-30: TPL purged from DB as catastrophic wrong-printing source.
    # Do not re-ingest tcgpricelookup prices or identities.
    raise SystemExit(
        "REFUSED: tcgpricelookup ingest disabled (2026-07-30 operator purge). "
        "Use SNK/eBay price paths only — see A06-TPL-PURGE-R1."
    )
    mapped = {int(r["variantId"]): r for r in read_jsonl(TPL_MAP) if r.get("variantId") and r.get("tplSlug")}
    from tcgpricelookup_ssr import card_store_path

    rows_to_write: list[dict[str, Any]] = []
    today = date.today().isoformat()
    for vid, meta in mapped.items():
        slug = meta["tplSlug"]
        path = card_store_path(TPL_ROOT, slug)
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not doc.get("ok"):
            continue
        price = _psa10_usd_from_tpl(doc)
        # also ingest history day points for PSA10
        hist = ((doc.get("psa10") or {}).get("history") or []) if isinstance(doc.get("psa10"), Mapping) else []
        hist_rows = []
        for h in hist:
            if not isinstance(h, Mapping):
                continue
            if not isinstance(h.get("avg_1d"), (int, float)) or h["avg_1d"] <= 0:
                continue
            hist_rows.append((str(h.get("date")), float(h["avg_1d"])))
        if price is None and not hist_rows:
            continue
        rows_to_write.append(
            {
                "variantId": vid,
                "slug": slug,
                "priceUsd": price,
                "history": hist_rows,
                "opaqueId": meta.get("opaqueId"),
                "updatedAt": doc.get("updatedAt") or doc.get("fetchedAt"),
            }
        )

    if dry_run:
        summary = {"dryRun": True, "cardsWithPrice": len(rows_to_write)}
        print(json.dumps(summary, sort_keys=True))
        return summary

    conn = db()
    cur = conn.cursor()
    effective = datetime.now(timezone.utc).replace(tzinfo=None)
    run_key = f"tpl_ssr_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    payload_seed = hashlib.sha256(run_key.encode()).hexdigest()
    cur.execute(
        """
        INSERT INTO market_ingest_run
            (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
             status, observed_count, accepted_count, quarantined_count, rejected_count, started_at)
        VALUES (%s, %s, 'incremental', %s, %s, %s, 'running', 0, 0, 0, 0, %s)
        """,
        (run_key, SOURCE_CODE, effective, payload_seed, payload_seed, effective),
    )
    run_id = cur.lastrowid
    inserted = 0
    for row in rows_to_write:
        points: list[tuple[str, float]] = list(row["history"])
        if row.get("priceUsd") is not None:
            # TPL "current" ebayAvg1d often equals last history bar (no new sales).
            # Stamping that as *today* fabricates a same-price head/tail → 0% 30d.
            # Only append today when it is a real new observation (differs from last hist).
            cur_price = float(row["priceUsd"])
            hist_dates = [d for d, _ in points if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or "")]
            last_hist_price = None
            if hist_dates:
                last_d = max(hist_dates)
                last_hist_price = next(p for d, p in reversed(points) if d == last_d)
            if last_hist_price is None or abs(cur_price - float(last_hist_price)) > 1e-6:
                points.append((today, cur_price))
        # dedupe by date keep last
        by_date: dict[str, float] = {}
        for d, p in points:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or ""):
                by_date[d] = p
        for observed_date, price_usd in by_date.items():
            payload = {
                "source": SOURCE_CODE,
                "slug": row["slug"],
                "variantId": row["variantId"],
                "priceUsd": price_usd,
                "metric": "psa10_ebay_avg",
            }
            payload_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            cur.execute(
                """
                INSERT INTO market_price_observation
                    (run_id, variant_id, source_code, observed_date, effective_at, price_usd,
                     native_price, native_currency, source_priority, metric_status, payload_sha256)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'USD', %s, 'ready', %s)
                ON DUPLICATE KEY UPDATE
                    run_id=VALUES(run_id), effective_at=VALUES(effective_at), price_usd=VALUES(price_usd),
                    native_price=VALUES(native_price), native_currency=VALUES(native_currency),
                    source_priority=VALUES(source_priority), metric_status=VALUES(metric_status),
                    payload_sha256=VALUES(payload_sha256)
                """,
                (
                    run_id,
                    row["variantId"],
                    SOURCE_CODE,
                    observed_date,
                    effective,
                    price_usd,
                    price_usd,
                    PRICE_PRIORITY,
                    payload_hash,
                ),
            )
            inserted += 1
        # identity alias for tpl slug
        cur.execute(
            """
            INSERT INTO catalog_source_identity
                (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
            VALUES (%s, %s, %s, 'exact', %s)
            ON DUPLICATE KEY UPDATE
                variant_id=VALUES(variant_id), match_status=VALUES(match_status),
                evidence_sha256=VALUES(evidence_sha256), updated_at=CURRENT_TIMESTAMP
            """,
            (
                SOURCE_CODE,
                row["slug"],
                row["variantId"],
                hashlib.sha256(f"{SOURCE_CODE}:{row['slug']}:{row['variantId']}".encode()).hexdigest(),
            ),
        )
    cur.execute(
        """
        UPDATE market_ingest_run
        SET status='complete', observed_count=%s, accepted_count=%s, completed_at=%s
        WHERE id=%s
        """,
        (inserted, inserted, datetime.now(timezone.utc).replace(tzinfo=None), run_id),
    )
    conn.commit()
    conn.close()
    summary = {"runId": run_id, "runKey": run_key, "cards": len(rows_to_write), "pricePointsWritten": inserted}
    print(json.dumps(summary, sort_keys=True))
    return summary


def cmd_fill_images(*, limit: int | None = None, delay: float = 0.5, write: bool = False) -> dict[str, Any]:
    """Fill missing images for watchlist using TPL CDN or TCGplayer product id."""

    if write:
        raise RuntimeError(
            "qualified_pool_operator fill-images public writer permanently disabled: "
            "use the gated image review pipeline"
        )

    import native_image_resolver as nir
    from tcgplayer_images import download_product_image
    from tcgpricelookup_ssr import card_store_path

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT w.variant_id, w.card_name, v.opaque_id, v.tcg_code, v.collector_number
        FROM market_gemrate_psa10_watchlist w
        JOIN catalog_variant v ON v.id=w.variant_id
        WHERE NOT EXISTS (SELECT 1 FROM market_image_asset i WHERE i.variant_id=w.variant_id)
        ORDER BY w.psa10_population DESC
        """
    )
    missing = cur.fetchall()
    conn.close()
    if limit:
        missing = missing[:limit]

    mapped = {int(r["variantId"]): r for r in read_jsonl(TPL_MAP) if r.get("variantId")}
    qc_path = ROOT / "manifests/image-qc.json"
    qc_doc = json.loads(qc_path.read_text(encoding="utf-8")) if qc_path.is_file() else {"records": []}
    records = qc_doc.setdefault("records", [])
    by_public = {r.get("publicId"): i for i, r in enumerate(records)}

    web_assets = ROOT / "apps/web/public/market-assets"
    results = []
    for row in missing:
        vid = int(row["variant_id"])
        opaque = row["opaque_id"]
        meta = mapped.get(vid) or {}
        slug = meta.get("tplSlug")
        raw = None
        source_ref = None
        method = None
        if slug:
            path = card_store_path(TPL_ROOT, slug)
            if path.is_file():
                doc = json.loads(path.read_text(encoding="utf-8"))
                img = doc.get("imageUrl")
                tcg_id = doc.get("tcgplayerId")
                try:
                    if tcg_id:
                        raw = download_product_image(int(tcg_id))
                        source_ref = f"tcgplayer:{tcg_id}"
                        method = "tcgplayer_from_tpl_binding"
                    elif img:
                        from curl_cffi import requests as curl_requests

                        resp = curl_requests.get(
                            str(img),
                            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://tcgpricelookup.com/"},
                            impersonate="chrome",
                            timeout=40,
                        )
                        if resp.status_code == 200 and resp.content:
                            raw = resp.content
                            source_ref = str(img)
                            method = "tpl_cdn"
                except Exception as exc:  # noqa: BLE001
                    results.append({"variantId": vid, "ok": False, "error": str(exc)})
                    continue
        if raw is None:
            # last resort: tcgplayer search by collector
            try:
                from tcgplayer_images import resolve_tcgplayer_candidate

                hit = resolve_tcgplayer_candidate(
                    {
                        "collectorNumber": row.get("collector_number"),
                        "name": row.get("card_name"),
                        "tcg": row.get("tcg_code"),
                    },
                    allow_first_hit=False,
                )
                if hit and hit.get("raw"):
                    raw = hit["raw"]
                    source_ref = f"tcgplayer:{hit.get('productId')}"
                    method = "tcgplayer_search"
            except Exception as exc:  # noqa: BLE001
                results.append({"variantId": vid, "ok": False, "error": str(exc)})
                time.sleep(delay)
                continue
        if raw is None:
            results.append({"variantId": vid, "ok": False, "error": "no_image_source"})
            time.sleep(delay)
            continue
        try:
            block = nir.store_face_art_image(raw, str(row.get("card_name") or opaque))
        except Exception as exc:  # noqa: BLE001
            results.append({"variantId": vid, "ok": False, "error": f"store:{exc}"})
            time.sleep(delay)
            continue
        if not write:
            results.append(
                {
                    "variantId": vid,
                    "ok": True,
                    "dryRun": True,
                    "sha256": block["sha256"],
                    "sourceRef": source_ref,
                }
            )
            time.sleep(delay)
            continue
        # QC
        now = utc_now()
        source_sha = hashlib.sha256(raw).hexdigest()
        record = {
            "cardNumberMatch": True,
            "contentSha256": block["sha256"],
            "height": block["height"],
            "imageKind": "raw_front",
            "languageMatch": True,
            "nativeRgba": True,
            "publicAllowed": True,
            "publicId": opaque,
            "qcAt": now,
            "qcVersion": "raw-front-v4",
            "resolverEvidence": {
                "collectorMatch": True,
                "languageMetadataMatch": True,
                "method": method,
                "sourceContentSha256": source_sha,
                "sourceRef": source_ref,
                "tcgMetadataMatch": True,
            },
            "semanticMatchStatus": "metadata_exact_unreviewed",
            "stdCanvas": nir.NORMALIZED_MARKER,
            "tcgMatch": True,
            "width": block["width"],
        }
        if opaque in by_public:
            records[by_public[opaque]] = record
        else:
            by_public[opaque] = len(records)
            records.append(record)
        # mirror web
        web_assets.mkdir(parents=True, exist_ok=True)
        for name in (f"{block['sha256']}.webp", f"{block['sha256']}_200.webp", f"{block['sha256']}_600.webp"):
            src = nir.ASSETS / name
            if src.is_file():
                shutil.copy2(src, web_assets / name)
        # DB image asset + pointer
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO market_image_asset
                (variant_id, image_kind, content_sha256, private_object_key, mime_type,
                 width_px, height_px, source_version_sha256, captured_at)
            VALUES (%s, 'raw_front', %s, %s, 'image/webp', %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                content_sha256=VALUES(content_sha256), private_object_key=VALUES(private_object_key),
                width_px=VALUES(width_px), height_px=VALUES(height_px),
                source_version_sha256=VALUES(source_version_sha256), captured_at=VALUES(captured_at)
            """,
            (
                vid,
                block["sha256"],
                f"market-assets/{block['sha256']}.webp",
                block["width"],
                block["height"],
                source_sha,
                datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        )
        remote_sha = hashlib.sha256((source_ref or block["sha256"]).encode()).hexdigest()
        cur.execute(
            """
            INSERT INTO market_image_source_pointer
                (variant_id, image_kind, remote_url_sha256, source_path, source_version_sha256,
                 public_allowed, observed_at)
            VALUES (%s, 'raw_front', %s, %s, %s, 1, %s)
            ON DUPLICATE KEY UPDATE
                remote_url_sha256=VALUES(remote_url_sha256), source_path=VALUES(source_path),
                source_version_sha256=VALUES(source_version_sha256), public_allowed=1,
                observed_at=VALUES(observed_at)
            """,
            (
                vid,
                remote_sha,
                f"data/public/market-assets/{block['sha256']}.webp",
                source_sha,
                datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        )
        # C: DB QC row (manifest already updated above)
        cur.execute(
            """
            SELECT id FROM market_image_asset
            WHERE variant_id=%s AND image_kind='raw_front' AND content_sha256=%s
            LIMIT 1
            """,
            (vid, block["sha256"]),
        )
        asset_row = cur.fetchone()
        if asset_row:
            cur.execute(
                """
                INSERT INTO market_image_qc
                    (image_asset_id, semantic_match_status, card_number_match, language_match,
                     tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
                     checked_at, qc_version)
                VALUES (%s, 'meta_unreviewed', 1, 1, 1, 1, 1, NULL, %s, 'raw-front-v4')
                ON DUPLICATE KEY UPDATE
                    semantic_match_status=VALUES(semantic_match_status),
                    card_number_match=1, language_match=1, tcg_match=1,
                    raw_front_confirmed=1, public_allowed=1,
                    checked_at=VALUES(checked_at), qc_version=VALUES(qc_version)
                """,
                (int(asset_row["id"]), datetime.now(timezone.utc).replace(tzinfo=None)),
            )
        conn.commit()
        conn.close()
        results.append(
            {
                "variantId": vid,
                "opaqueId": opaque,
                "ok": True,
                "sha256": block["sha256"],
                "sourceRef": source_ref,
                "method": method,
            }
        )
        print(f"[img] v{vid} ok sha={block['sha256'][:12]} via {method}", flush=True)
        time.sleep(delay)

    if write:
        qc_path.parent.mkdir(parents=True, exist_ok=True)
        qc_path.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "missingConsidered": len(missing),
        "ok": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "write": write,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_json(REPORT_DIR / f"fill_images_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json", {"summary": summary, "results": results})
    print(json.dumps(summary, sort_keys=True))
    return summary


def cmd_maintain(
    *,
    skip_map: bool = False,
    skip_harvest: bool = False,
    skip_images: bool = False,
    map_limit: int | None = None,
    harvest_limit: int | None = None,
    image_limit: int | None = None,
    harvest_mode: str = "incremental",
) -> dict[str, Any]:
    report: dict[str, Any] = {"startedAt": utc_now()}
    report["before"] = cmd_status()
    if not WORKLIST.is_file():
        cmd_export_worklist()
    else:
        # refresh worklist each maintain
        cmd_export_worklist()
    if not skip_map:
        report["map"] = {
            "mapped": sum(1 for r in cmd_map_tpl(limit=map_limit, resume=True) if r.get("tplSlug"))
        }
    if not skip_harvest:
        # first maintain with no harvest files → full, else incremental
        cards_dir = TPL_ROOT / "cards"
        mode = harvest_mode
        if not cards_dir.is_dir() or not any(cards_dir.glob("*.json")):
            mode = "full"
        report["harvest"] = cmd_harvest_tpl(mode=mode, limit=harvest_limit)
        report["ingest"] = {
            "status": "disabled",
            "reason": "tcgpricelookup_is_not_a_price_authority",
        }
    if not skip_images:
        report["images"] = cmd_fill_images(limit=image_limit, write=True)
    report["after"] = cmd_status()
    report["finishedAt"] = utc_now()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"maintain_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    atomic_json(path, report)
    print(json.dumps({"report": str(path), "before": report["before"], "after": report["after"]}, ensure_ascii=False, indent=2))
    return report


def cmd_gap_report() -> dict[str, Any]:
    """What is still missing for one-button DB→frontend readiness."""

    status = cmd_status()
    mapped = read_jsonl(TPL_MAP)
    gaps = {
        "fetchedAt": utc_now(),
        "poolSize": status.get("watch", 0),
        "coverage": status,
        "blockers": [],
        "readyEnoughFor": [],
        "nextActions": [],
    }
    watch = status.get("watch") or 0
    if watch < 900:
        gaps["blockers"].append("watchlist_not_940")
    authoritative_price = status.get("authoritative_price") or 0
    if authoritative_price < watch:
        gaps["blockers"].append("authoritative_price_coverage_incomplete")
        gaps["nextActions"].append("fill exact SNK/eBay prices for unresolved variants")
    if (status.get("image_asset") or 0) < watch * 0.5:
        gaps["blockers"].append("images_missing_majority")
        gaps["nextActions"].append("fill-images --write")
    # frontend still needs public snapshot export
    gaps["blockers"].append("public_snapshot_export_from_db_not_one_button_yet")
    gaps["nextActions"].append("canonical_public_snapshot / publish pipeline after DB green")
    if authoritative_price > 0:
        gaps["readyEnoughFor"].append("canonical_price_coverage_partial")
    if (status.get("image_asset") or 0) > 50:
        gaps["readyEnoughFor"].append("partial_image_qc")
    gaps["mappedNeedsReview"] = sum(1 for r in mapped if r.get("needsReview"))
    print(json.dumps(gaps, ensure_ascii=False, indent=2))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_json(REPORT_DIR / "gap_report_latest.json", gaps)
    return gaps


def main() -> int:
    parser = argparse.ArgumentParser(description="Qualified 940 pool operator")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("export-worklist")
    sub.add_parser("gap-report")
    p_map = sub.add_parser("map-tpl")
    p_map.add_argument("--limit", type=int)
    p_map.add_argument("--no-resume", action="store_true")
    p_map.add_argument("--delay", type=float, default=0.05)
    p_map.add_argument("--workers", type=int, default=8)
    p_har = sub.add_parser("harvest-tpl")
    p_har.add_argument("--mode", choices=("full", "incremental"), default="incremental")
    p_har.add_argument("--limit", type=int)
    p_har.add_argument("--delay", type=float, default=0.35)
    p_har.add_argument("--workers", type=int, default=6)
    p_ing = sub.add_parser("ingest-prices")
    p_ing.add_argument("--dry-run", action="store_true")
    p_img = sub.add_parser("fill-images")
    p_img.add_argument("--limit", type=int)
    p_img.add_argument("--write", action="store_true")
    p_img.add_argument("--delay", type=float, default=0.5)
    p_m = sub.add_parser("maintain")
    p_m.add_argument("--skip-map", action="store_true")
    p_m.add_argument("--skip-harvest", action="store_true")
    p_m.add_argument("--skip-images", action="store_true")
    p_m.add_argument("--map-limit", type=int)
    p_m.add_argument("--harvest-limit", type=int)
    p_m.add_argument("--image-limit", type=int)
    p_m.add_argument("--harvest-mode", choices=("full", "incremental"), default="incremental")
    args = parser.parse_args()

    if args.cmd == "status":
        cmd_status()
    elif args.cmd == "export-worklist":
        cmd_export_worklist()
    elif args.cmd == "gap-report":
        cmd_gap_report()
    elif args.cmd == "map-tpl":
        cmd_map_tpl(
            limit=args.limit,
            delay=args.delay,
            resume=not args.no_resume,
            workers=args.workers,
        )
    elif args.cmd == "harvest-tpl":
        cmd_harvest_tpl(mode=args.mode, delay=args.delay, limit=args.limit, workers=args.workers)
    elif args.cmd == "ingest-prices":
        cmd_ingest_prices(dry_run=args.dry_run)
    elif args.cmd == "fill-images":
        cmd_fill_images(limit=args.limit, delay=args.delay, write=args.write)
    elif args.cmd == "maintain":
        cmd_maintain(
            skip_map=args.skip_map,
            skip_harvest=args.skip_harvest,
            skip_images=args.skip_images,
            map_limit=args.map_limit,
            harvest_limit=args.harvest_limit,
            image_limit=args.image_limit,
            harvest_mode=args.harvest_mode,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
