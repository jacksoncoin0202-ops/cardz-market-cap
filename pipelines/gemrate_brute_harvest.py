#!/usr/bin/env python3
"""
GemRate Brute-Force Harvester（curl_cffi 版 — 唔使 browser）
=============================================================
用 curl_cffi 模擬 Chrome TLS 指紋，直接 HTTP GET/POST 拎 inline setsData / rowData。
**唔使 Playwright、唔使開 browser**（2026-07-24 突破：Cloudflare 靠 TLS 指紋就過到）。

Usage:
    python gemrate_brute_harvest.py --all-sets
    python gemrate_brute_harvest.py --set-id <40-hex>
    python gemrate_brute_harvest.py --query "rayquaza vmax"

Requires: pip install curl_cffi
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from curl_cffi import requests as cr

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args
from failure_ledger import record_failure, record_resolution

DATA_DIR = REPO_ROOT / "data" / "private" / "gemrate_brute"
DATA_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}
SET_DELAY = 1.0  # 每個 set 之間嘅 delay（秒）
PSA10_MINIMUM_INCLUSIVE = 1000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _get(url: str, **kw):
    kw.setdefault("impersonate", "chrome")
    kw.setdefault("timeout", 40)
    kw.setdefault("headers", HEADERS)
    return cr.get(url, **kw)


def _post(url: str, **kw):
    kw.setdefault("impersonate", "chrome")
    kw.setdefault("timeout", 40)
    h = dict(HEADERS)
    h.update(kw.pop("headers", {}))
    kw["headers"] = h
    return cr.post(url, **kw)


def extract_sets_data() -> list[dict]:
    """由 /universal-pop-report 抽 inline setsData。"""
    log("Fetching /universal-pop-report ...")
    r = _get("https://www.gemrate.com/universal-pop-report")
    if r.status_code != 200:
        raise RuntimeError(f"universal-pop-report status {r.status_code}")
    html = r.text
    big = None
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
        if len(m.group(1)) > 200000:
            big = m.group(1)
            break
    if not big:
        raise RuntimeError("setsData big script not found")
    m = re.search(r"setsData\s*=\s*(\[.*?\]);", big, re.DOTALL)
    if not m:
        raise RuntimeError("setsData regex failed")
    cleaned = m.group(1).replace(": NaN", ":null").replace(":NaN", ":null")
    cleaned = cleaned.replace(": Infinity", ":null").replace(":-Infinity", ":null")
    sets_data = json.loads(cleaned)
    log(f"Extracted {len(sets_data)} sets")
    return sets_data


def extract_row_data(set_link: str) -> list[dict]:
    """由 set 頁抽 inline rowData。"""
    url = set_link if set_link.startswith("http") else f"https://www.gemrate.com{set_link}"
    r = _get(url)
    if r.status_code != 200:
        raise RuntimeError(f"set page status {r.status_code}")
    html = r.text
    m = re.search(r"const rowData = JSON\.parse\('(.*?)'\);", html, re.DOTALL)
    if not m:
        raise RuntimeError("rowData not found on set page")
    js = m.group(1)
    try:
        row_data = json.loads(js)
    except json.JSONDecodeError:
        row_data = json.loads(js.encode("utf-8").decode("unicode_escape"))
    return row_data


def save_jsonl(data: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def qualifying_psa10_cards(cards: list[dict]) -> list[dict]:
    return [
        card
        for card in cards
        if int(card.get("psa_10") or 0) >= PSA10_MINIMUM_INCLUSIVE
        and re.fullmatch(r"[0-9a-f]{40}", str(card.get("psa_id") or ""))
    ]


def tcg_code(card: dict) -> str | None:
    """Return the supported CARDZ game from explicit GemRate set evidence."""

    set_name = str(card.get("set_name") or card.get("_set_name") or "").casefold()
    collector = str(card.get("card_number") or "").strip().upper()
    if "pokemon" in set_name:
        return "pokemon"
    if "one piece" in set_name or re.fullmatch(r"(?:OP|ST|EB|PRB)\d{1,2}-\d{1,3}|P-\d{2,3}", collector):
        return "one-piece"
    return None


def cards_opaque_id(gemrate_id: str) -> str:
    """One immutable CARDZ cards ID per GemRate universal card/version ID."""

    digest = hashlib.sha256(f"gemrate:{gemrate_id.casefold()}".encode("ascii")).hexdigest()
    return f"cmc_{digest[:24]}"


def sync_watchlist(connection, cards: list[dict]) -> dict[str, int]:
    """Upsert qualified GemRate rows and give every supported version a cards ID."""

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    inserted = updated = mapped = created = unsupported = 0
    accepted: list[tuple[int, dict, str]] = []
    quarantined_multi_member = 0
    with connection.cursor() as cursor:
        for card in qualifying_psa10_cards(cards):
            gemrate_id = str(card["psa_id"])
            payload_sha256 = hashlib.sha256(
                json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            cursor.execute(
                """
                SELECT COALESCE(alias.canonical_variant_id, identity.variant_id) AS variant_id
                FROM catalog_source_identity AS identity
                LEFT JOIN catalog_variant_alias AS alias
                  ON alias.duplicate_variant_id=identity.variant_id
                WHERE identity.source_code='gemrate'
                  AND identity.external_entity_id=%s
                """,
                (gemrate_id,),
            )
            identity = cursor.fetchone()
            variant_id = int(identity["variant_id"]) if identity else None
            if variant_id is None:
                game = tcg_code(card)
                if game is None:
                    unsupported += 1
                    continue
                opaque_id = cards_opaque_id(gemrate_id)
                cursor.execute(
                    """
                    INSERT INTO catalog_variant
                        (opaque_id, tcg_code, canonical_name, set_name,
                         collector_number, identity_status)
                    VALUES (%s,%s,%s,%s,%s,'confirmed')
                    ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)
                    """,
                    (
                        opaque_id,
                        game,
                        str(card.get("name") or card.get("card_details") or "").strip()[:255],
                        str(card.get("set_name") or card.get("_set_name") or "").strip()[:255],
                        str(card.get("card_number") or "").strip()[:96],
                    ),
                )
                variant_id = int(cursor.lastrowid)
                cursor.execute(
                    """
                    INSERT INTO catalog_source_identity
                        (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
                    VALUES ('gemrate',%s,%s,'exact',%s)
                    ON DUPLICATE KEY UPDATE
                        variant_id=VALUES(variant_id), match_status='exact',
                        evidence_sha256=VALUES(evidence_sha256)
                    """,
                    (gemrate_id, variant_id, payload_sha256),
                )
                created += 1
            mapped += int(variant_id is not None)
            values = (
                gemrate_id,
                variant_id,
                str(card.get("gemrate_checklist_id") or "") or None,
                str(card.get("name") or card.get("card_details") or "").strip()[:255],
                str(card.get("set_name") or card.get("_set_name") or "").strip()[:255],
                str(card.get("card_number") or "").strip()[:96],
                int(card["year"]) if str(card.get("year") or "").isdigit() else None,
                int(card["psa_10"]),
                int(float(card["psa_card_total_grades"])) if card.get("psa_card_total_grades") is not None else None,
                str(card.get("psa_date") or ""),
                payload_sha256,
                now,
                now,
            )
            cursor.execute(
                """
                INSERT INTO market_gemrate_psa10_watchlist
                    (gemrate_id, variant_id, gemrate_checklist_id, card_name, set_name,
                     collector_number, release_year, psa10_population, psa_total_population,
                     population_as_of, source_payload_sha256, first_seen_at, last_seen_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    variant_id=COALESCE(VALUES(variant_id), variant_id),
                    gemrate_checklist_id=VALUES(gemrate_checklist_id),
                    card_name=VALUES(card_name),
                    set_name=VALUES(set_name),
                    collector_number=VALUES(collector_number),
                    release_year=VALUES(release_year),
                    psa10_population=VALUES(psa10_population),
                    psa_total_population=VALUES(psa_total_population),
                    population_as_of=VALUES(population_as_of),
                    source_payload_sha256=VALUES(source_payload_sha256),
                    last_seen_at=VALUES(last_seen_at)
                """,
                values,
            )
            inserted += int(cursor.rowcount == 1)
            updated += int(cursor.rowcount == 2)
            accepted.append((variant_id, card, payload_sha256))

        if accepted:
            manifest_sha256 = hashlib.sha256(
                "|".join(sorted(payload_hash for _, _, payload_hash in accepted)).encode("ascii")
            ).hexdigest()
            effective_date = max(str(card.get("psa_date") or "")[:10] for _, card, _ in accepted)
            effective_at = f"{effective_date} 00:00:00"
            run_key = hashlib.sha256(
                f"gemrate-psa10-watchlist:{effective_date}:{manifest_sha256}".encode("ascii")
            ).hexdigest()
            cursor.execute(
                """
                INSERT INTO market_ingest_run
                    (run_key, source_code, ingest_mode, effective_at, payload_sha256,
                     manifest_sha256, status, observed_count, accepted_count,
                     quarantined_count, rejected_count, started_at, completed_at)
                VALUES (%s,'gemrate','incremental',%s,%s,%s,'completed',%s,%s,0,0,%s,%s)
                ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id),
                    status='completed', observed_count=VALUES(observed_count),
                    accepted_count=VALUES(accepted_count), completed_at=VALUES(completed_at)
                """,
                (
                    run_key, effective_at, manifest_sha256, manifest_sha256,
                    len(accepted), len(accepted), now, now,
                ),
            )
            run_id = int(cursor.lastrowid)
            # Fail-closed: never merge two distinct GemRate members (parallels) of one
            # canonical variant into a single population observation. The observation
            # unique key has no member dimension, so writing both collapses entity/pop
            # (2026-07-30 audit: systematic pop<->entity swaps on 6 variants caused by
            # GREATEST() + last-writer entity under duplicate-key merge).
            members_by_variant_date: dict[tuple[int, str], set[str]] = {}
            for _vid, _card, _ in accepted:
                key = (_vid, str(_card.get("psa_date") or "")[:10])
                members_by_variant_date.setdefault(key, set()).add(str(_card["psa_id"]))
            collapsed = {key for key, members in members_by_variant_date.items() if len(members) > 1}
            for variant_id, card, payload_sha256 in accepted:
                gemrate_id = str(card["psa_id"])
                observed_date = str(card.get("psa_date") or "")[:10]
                if (variant_id, observed_date) in collapsed:
                    quarantined_multi_member += 1
                    continue
                population = int(card["psa_10"])
                total = (
                    int(float(card["psa_card_total_grades"]))
                    if card.get("psa_card_total_grades") is not None
                    else None
                )
                payload = json.dumps(
                    {"grader": "PSA", "grade": "10", "population": population, "total": total},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                cursor.execute(
                    """
                    INSERT INTO market_source_observation
                        (run_id, source_code, external_entity_id, observation_kind,
                         effective_at, observed_date, payload_sha256, payload_json, observed_at)
                    VALUES (%s,'gemrate',%s,'grader_population_psa10',%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE run_id=VALUES(run_id), observed_at=VALUES(observed_at)
                    """,
                    (
                        run_id, gemrate_id, f"{observed_date} 00:00:00", observed_date,
                        payload_sha256, payload, now,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO market_grader_population_observation
                        (run_id, variant_id, source_code, external_entity_id, grader_code,
                         top_grade_label, total_population, top_grade_population, estimated,
                         effective_at, observed_date, payload_sha256)
                    VALUES (%s,%s,'gemrate',%s,'PSA','10',%s,%s,0,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        run_id=VALUES(run_id), external_entity_id=VALUES(external_entity_id),
                        total_population=VALUES(total_population),
                        top_grade_population=GREATEST(top_grade_population,VALUES(top_grade_population)),
                        effective_at=VALUES(effective_at), payload_sha256=VALUES(payload_sha256)
                    """,
                    (
                        run_id, variant_id, gemrate_id, total, population,
                        f"{observed_date} 00:00:00", observed_date, payload_sha256,
                    ),
                )
    connection.commit()
    return {
        "qualified": len(qualifying_psa10_cards(cards)),
        "inserted": inserted,
        "updated": updated,
        "mapped": mapped,
        "createdCards": created,
        "unsupported": unsupported,
        "unmapped": unsupported,
        "populationObservations": len(accepted) - quarantined_multi_member,
        "quarantinedMultiMember": quarantined_multi_member,
    }


def harvest_all_sets(limit: Optional[int] = None) -> tuple[list[dict], list[dict]]:
    sets_data = extract_sets_data()

    def _is_tcg(s): return (s.get("category") or "").strip().lower() == "tcg"
    def _year_ok(s):
        try:
            return int(s.get("year") or 0) >= 2020
        except (TypeError, ValueError):
            return False

    tcg_sets = [s for s in sets_data if _is_tcg(s) and _year_ok(s)]
    log(f"Found {len(tcg_sets)} TCG sets >= 2020")
    if limit:
        tcg_sets = tcg_sets[:limit]
        log(f"Limited to first {limit} sets")

    all_cards, failed_sets = [], []
    for i, s in enumerate(tcg_sets, 1):
        set_id, set_link = s.get("set_id"), s.get("set_link")
        set_name = s.get("set_name", "unknown")
        log(f"[{i}/{len(tcg_sets)}] {set_name}")
        try:
            cards = extract_row_data(set_link)
            for card in cards:
                card["_set_id"] = set_id
                card["_set_name"] = set_name
                card["_set_link"] = set_link
            all_cards.extend(cards)
            safe = re.sub(r"[^\w\-]+", "_", set_name)[:60]
            save_jsonl(cards, DATA_DIR / f"set_{set_id}_{safe}.jsonl")
            record_resolution(
                source="gemrate",
                stage="harvest_set",
                script=__file__,
                item_key=set_id,
                resolution="set_harvested",
                context={"setName": set_name, "cardCount": len(cards)},
                evidence_paths=[DATA_DIR / f"set_{set_id}_{safe}.jsonl"],
            )
            log(f"    -> {len(cards)} cards")
            time.sleep(SET_DELAY)
        except Exception as e:
            log(f"    ERROR: {e}")
            failed_sets.append({"set_id": set_id, "set_name": set_name, "error": str(e)})
            record_failure(
                source="gemrate",
                stage="harvest_set",
                script=__file__,
                item_key=set_id,
                reason_code="set_harvest_failed",
                message=str(e),
                retryable=True,
                url=str(set_link or "") or None,
                context={"setName": set_name},
                next_action="retry",
                error_type=type(e).__name__,
            )
            time.sleep(SET_DELAY * 2)

    save_jsonl(all_cards, DATA_DIR / "all_cards.jsonl")
    log(f"Total cards harvested: {len(all_cards)}")
    if failed_sets:
        save_jsonl(failed_sets, DATA_DIR / "failed_sets.jsonl")
        log(f"Failed sets: {len(failed_sets)}")
    psa = qualifying_psa10_cards(all_cards)
    log(f"Cards with PSA 10 >= {PSA10_MINIMUM_INCLUSIVE}: {len(psa)}")
    save_jsonl(psa, DATA_DIR / "psa10_1000_plus.jsonl")
    return all_cards, failed_sets


def harvest_single_set(set_id: str) -> None:
    sets_data = extract_sets_data()
    target = next((s for s in sets_data if s.get("set_id") == set_id), None)
    if not target:
        raise ValueError(f"Set ID {set_id} not found")
    log(f"Found: {target.get('set_name')}")
    cards = extract_row_data(target["set_link"])
    save_jsonl(cards, DATA_DIR / f"set_{set_id}.jsonl")
    log(f"Saved {len(cards)} cards")


def search_cards(query: str) -> None:
    log(f"Searching: {query}")
    r = _post("https://www.gemrate.com/universal-search-query",
              json={"query": query},
              headers={"Content-Type": "application/json",
                       "Origin": "https://www.gemrate.com",
                       "Referer": "https://www.gemrate.com/"})
    result = r.json()
    log(f"Found {len(result)} results")
    for x in result[:20]:
        log(f"  {x.get('description','N/A')[:60]} | gemrate_id: {x.get('gemrate_id','')[:16]}...")
    safe_q = re.sub(r"[^\w]+", "_", query)[:40]
    save_jsonl(result, DATA_DIR / f"search_{safe_q}.jsonl")


def main() -> None:
    ap = argparse.ArgumentParser(description="GemRate Brute-Force Harvester (curl_cffi, no browser)")
    ap.add_argument("--all-sets", action="store_true")
    ap.add_argument("--sync-existing", action="store_true")
    ap.add_argument("--set-id", type=str)
    ap.add_argument("--query", type=str)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sync-db", action="store_true")
    add_connection_args(ap)
    args = ap.parse_args()

    if not any([args.all_sets, args.sync_existing, args.set_id, args.query]):
        ap.print_help()
        sys.exit(1)

    if args.all_sets:
        cards, failed_sets = harvest_all_sets(limit=args.limit)
        if args.sync_db:
            if failed_sets:
                raise RuntimeError("watchlist DB sync requires a complete set scan")
            connection = connection_from_args(args)
            try:
                report = sync_watchlist(connection, cards)
            finally:
                connection.close()
            log("DB sync " + json.dumps(report, sort_keys=True))
    elif args.sync_existing:
        source = DATA_DIR / "all_cards.jsonl"
        if not source.is_file():
            raise RuntimeError(f"existing GemRate harvest is missing: {source}")
        cards = [
            json.loads(line)
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        connection = connection_from_args(args)
        try:
            report = sync_watchlist(connection, cards)
        finally:
            connection.close()
        log("DB sync " + json.dumps(report, sort_keys=True))
    elif args.set_id:
        harvest_single_set(args.set_id)
    elif args.query:
        search_cards(args.query)
    log("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        record_failure(
            source="gemrate",
            stage="run",
            script=__file__,
            item_key="gemrate-brute-harvest",
            reason_code="run_failed",
            message="GemRate brute harvest aborted",
            retryable=True,
            next_action="agent_review_then_retry",
            error_type=type(error).__name__,
        )
        raise
    record_resolution(
        source="gemrate",
        stage="run",
        script=__file__,
        item_key="gemrate-brute-harvest",
        resolution="run_completed",
    )
