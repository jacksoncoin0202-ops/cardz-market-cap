#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sealed (原盒) operator commands, delegated from operator_control.py.

Commands:
  sealed-status          coverage / freshness / freeze KPIs
  sealed-gaps            per-SKU missing bind/freeze/price/image
  sealed-accept-binding  human freeze (single --sku or bulk --all-resolved)
  sealed-daily           incr collect (optional) + compose + status + gaps
  export-sealed-subset   sealed[] block for the product snapshot / FE fixture

Artifacts live in data/runtime/operator/sealed/.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from sealed_runtime import (  # noqa: E402
    OUT_DIR,
    SEALED_ADAPTERS,
    db,
    ensure_sealed_fullname_columns,
    load_env,
    sha,
    utc_naive,
    utc_now,
)
from sealed_price_compose import (  # noqa: E402
    compose_current,
    item_key,
    load_asks,
    load_market,
    load_sales,
    trim_outliers,
)
from sealed_discover_lib import insert_candidate_bind, same_item_ids, yahoo_closedsearch_url, yahoo_jp_query  # noqa: E402

EDITORIAL_STORIES = ROOT / "data" / "editorial" / "sealed-stories.json"
COLLISION_BASELINE = ROOT / "data" / "policy" / "box-image-collision-baseline.json"
SEALED_FREEZE_KINDS = ("identity", "source", "image")
HISTORY_MAX_POINTS = 400


def _slug_lang(slug: str) -> str:
    parts = slug.split("-")
    return parts[1] if len(parts) > 1 else ""


def _collision_baseline() -> dict[str, list[str]]:
    if not COLLISION_BASELINE.is_file():
        return {}
    doc = json.loads(COLLISION_BASELINE.read_text(encoding="utf-8"))
    if doc.get("contract") != "cardz-box-image-collision-baseline-v1":
        raise RuntimeError("box image collision baseline contract missing/unsupported")
    return {str(k): sorted(v) for k, v in (doc.get("collisions") or {}).items()}


def assert_image_identity(entries: list[dict]) -> dict[str, Any]:
    """Fail loud: one image sha <-> one sealed id. Known collisions live in the
    baseline (ratchet: may only shrink); a new collision aborts the export."""
    by_sha: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        image = entry.get("image")
        if image and image.get("sha256"):
            by_sha[str(image["sha256"])].append(str(entry["id"]))
    collisions = {s: sorted(ids) for s, ids in by_sha.items() if len(ids) > 1}
    baseline = _collision_baseline()
    new = {s: ids for s, ids in collisions.items() if baseline.get(s) != ids}
    cross_lang_new = {s: ids for s, ids in new.items() if len({_slug_lang(i) for i in ids}) > 1}
    if new or len(collisions) > len(baseline):
        raise RuntimeError(
            "BOX image identity violated: "
            f"{len(new)} collision(s) not in baseline ({len(cross_lang_new)} cross-language), "
            f"total {len(collisions)} > baseline {len(baseline)}: "
            f"{json.dumps(dict(list(new.items())[:10]), ensure_ascii=False)}"
        )
    return {"collisions": len(collisions), "baseline": len(baseline), "known": collisions}


# --- shared loads -------------------------------------------------------------


def _products(cur, *, include_no_box: bool = False) -> list[dict]:
    cur.execute(
        """
        SELECT p.*, 
          EXISTS(SELECT 1 FROM operator_sealed_binding_freeze f WHERE f.sealed_id=p.id AND f.freeze_kind='identity' AND f.acceptance_status='accepted') AS identity_frozen,
          EXISTS(SELECT 1 FROM operator_sealed_binding_freeze f WHERE f.sealed_id=p.id AND f.freeze_kind='source' AND f.acceptance_status='accepted') AS source_frozen,
          EXISTS(SELECT 1 FROM operator_sealed_binding_freeze f WHERE f.sealed_id=p.id AND f.freeze_kind='image' AND f.acceptance_status='accepted') AS image_frozen
        FROM catalog_sealed_product p
        ORDER BY p.id
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not include_no_box:
        rows = [r for r in rows if r["status"] != "no-box"]
    return rows


def _bind_counts(cur) -> dict[int, dict[str, int]]:
    cur.execute(
        """
        SELECT sealed_id, source_code, resolved, match_status
        FROM catalog_sealed_source_identity
        WHERE match_status <> 'rejected'
        """
    )
    out: dict[int, dict[str, int]] = defaultdict(
        lambda: {"binds": 0, "resolved": 0, "exact": 0, "pc": 0, "snk": 0}
    )
    for r in cur.fetchall():
        entry = out[int(r["sealed_id"])]
        entry["binds"] += 1
        if int(r["resolved"] or 0):
            entry["resolved"] += 1
        if r["match_status"] == "exact":
            entry["exact"] += 1
        if r["source_code"] == "pricecharting":
            entry["pc"] += 1
        if r["source_code"] == "snkrdunk":
            entry["snk"] += 1
    return out


def _aggregates(cur) -> dict[int, list[dict]]:
    cur.execute(
        """
        SELECT sealed_id, observed_date, sold_count, sold_value_usd, vwap_usd,
               composed_price_usd, composed_kind, composed_source
        FROM market_sealed_daily_aggregate
        ORDER BY observed_date
        """
    )
    grouped: dict[int, list[dict]] = defaultdict(list)
    for r in cur.fetchall():
        grouped[int(r["sealed_id"])].append(dict(r))
    return grouped


# --- status / gaps --------------------------------------------------------------


def cmd_sealed_status() -> dict:
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        ensure_sealed_fullname_columns(cur)
        conn.commit()
        products = _products(cur)
        binds = _bind_counts(cur)
        aggregates = _aggregates(cur)
        cur.execute("SELECT DISTINCT sealed_id FROM market_sealed_image_asset WHERE image_kind='box_front'")
        imaged_ids = {int(r["sealed_id"]) for r in cur.fetchall()}
        today = datetime.now(timezone.utc).date()
        by_group: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        totals = defaultdict(int)
        for p in products:
            sealed_id = int(p["id"])
            group = str(p["group_code"])
            by_group[group]["total"] += 1
            totals["total"] += 1
            bind = binds.get(sealed_id, {})
            if bind.get("binds"):
                by_group[group]["bound"] += 1
                totals["bound"] += 1
            if bind.get("pc"):
                totals["bindPc"] += 1
                by_group[group]["bindPc"] += 1
            if bind.get("snk"):
                totals["bindSnk"] += 1
                by_group[group]["bindSnk"] += 1
            if int(p["source_frozen"] or 0):
                by_group[group]["sourceFrozen"] += 1
                totals["sourceFrozen"] += 1
            if int(p["image_frozen"] or 0):
                totals["imageFrozen"] += 1
            if sealed_id in imaged_ids:
                totals["imaged"] += 1
                by_group[group]["imaged"] += 1
            if p.get("full_name_en"):
                totals["fullName"] += 1
                by_group[group]["fullName"] += 1
            line = aggregates.get(sealed_id) or []
            recent = [r for r in line if r["composed_price_usd"] is not None and (today - r["observed_date"]).days <= 45]
            if recent:
                by_group[group]["priced"] += 1
                totals["priced"] += 1
        for key in ("bindPc", "bindSnk", "imaged", "priced", "fullName"):
            denom = totals.get("total") or 0
            totals[f"{key}Pct"] = round(100.0 * totals.get(key, 0) / denom, 1) if denom else 0.0
        placeholders = ",".join(["%s"] * len(SEALED_ADAPTERS))
        cur.execute(
            f"SELECT source_code, COUNT(*) AS n, MAX(updated_at) AS latest "
            f"FROM market_ingest_checkpoint WHERE source_code IN ({placeholders}) GROUP BY source_code",
            SEALED_ADAPTERS,
        )
        adapters = {str(r["source_code"]): {"checkpoints": int(r["n"]), "latest": str(r["latest"])} for r in cur.fetchall()}
        doc = {
            "asOf": utc_now(),
            "action": "sealed-status",
            "totals": dict(totals),
            "byGroup": {g: dict(v) for g, v in sorted(by_group.items())},
            "adapters": adapters,
        }
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "status.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(doc, ensure_ascii=False, indent=2, default=str))
        return doc
    finally:
        conn.close()


def cmd_sealed_gaps(limit: int | None = None) -> dict:
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        ensure_sealed_fullname_columns(cur)
        conn.commit()
        products = _products(cur)
        binds = _bind_counts(cur)
        aggregates = _aggregates(cur)
        cur.execute("SELECT DISTINCT sealed_id FROM market_sealed_image_asset WHERE image_kind='box_front'")
        imaged_ids = {int(r["sealed_id"]) for r in cur.fetchall()}
        today = datetime.now(timezone.utc).date()
        gaps = []
        for p in products:
            if p["status"] == "unreleased":
                continue
            sealed_id = int(p["id"])
            reasons = []
            bind = binds.get(sealed_id, {})
            if not bind.get("pc"):
                reasons.append("no_pc_bind")
            if not bind.get("snk"):
                reasons.append("no_snk_bind")
            if not bind.get("binds"):
                reasons.append("no_source_bind")
            elif not bind.get("resolved"):
                reasons.append("bind_unresolved")
            if not int(p["source_frozen"] or 0):
                reasons.append("source_not_frozen")
            if not int(p["identity_frozen"] or 0):
                reasons.append("identity_not_frozen")
            if not int(p["image_frozen"] or 0):
                reasons.append("image_not_frozen")
            line = aggregates.get(sealed_id) or []
            recent = [r for r in line if r["composed_price_usd"] is not None and (today - r["observed_date"]).days <= 45]
            if not recent:
                reasons.append("no_recent_price")
            if sealed_id not in imaged_ids:
                reasons.append("no_image")
            if not p.get("full_name_en"):
                reasons.append("no_full_name")
            if reasons:
                gaps.append({"sku": p["sku_id"], "slug": p["slug"], "group": p["group_code"], "reasons": reasons})
        if limit:
            gaps = gaps[:limit]
        doc = {"asOf": utc_now(), "action": "sealed-gaps", "count": len(gaps), "gaps": gaps}
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "gaps.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({**doc, "gaps": gaps[:30]}, ensure_ascii=False, indent=2, default=str))
        return doc
    finally:
        conn.close()


# --- accept binding -------------------------------------------------------------


def _freeze(cur, sealed_id: int, kind: str, source_code: str, external_id: str, actor: str, note: str | None) -> None:
    cur.execute(
        """
        INSERT INTO operator_sealed_binding_freeze
          (sealed_id, freeze_kind, source_code, external_entity_id, content_sha256,
           acceptance_status, actor, evidence_sha256, note, accepted_at)
        VALUES (%s,%s,%s,%s,%s,'accepted',%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          acceptance_status='accepted', actor=VALUES(actor), note=VALUES(note),
          external_entity_id=VALUES(external_entity_id), accepted_at=VALUES(accepted_at)
        """,
        (
            sealed_id, kind, source_code or "", external_id or "",
            sha({"sealedId": sealed_id, "kind": kind, "source": source_code, "ext": external_id}),
            actor,
            sha({"acceptedBy": actor, "kind": kind, "sealedId": sealed_id}),
            note,
            utc_naive().strftime("%Y-%m-%d %H:%M:%S.%f"),
        ),
    )


def cmd_sealed_accept_binding(
    *,
    sku: str | None,
    kind: str,
    source_code: str,
    actor: str,
    note: str | None,
    all_resolved: bool,
    group: str | None,
    external_id: str | None = None,
) -> dict:
    if kind not in SEALED_FREEZE_KINDS:
        raise SystemExit(f"kind must be one of {SEALED_FREEZE_KINDS}")
    if kind == "source" and not source_code:
        raise SystemExit("source freeze requires --source-code")
    if not sku and not all_resolved:
        raise SystemExit("need --sku or --all-resolved")
    if external_id and (not sku or kind == "identity"):
        raise SystemExit("--ext names one SKU's source item or image sha: needs --sku and --kind source|image")
    load_env()
    conn = db()
    accepted = []
    refused = []
    try:
        cur = conn.cursor()
        if sku:
            cur.execute("SELECT id, sku_id FROM catalog_sealed_product WHERE sku_id=%s OR slug=%s", (sku, sku))
            row = cur.fetchone()
            if not row:
                raise SystemExit(f"unknown sku/slug: {sku}")
            targets = [(int(row["id"]), str(row["sku_id"]))]
        elif kind == "source":
            params: list[Any] = [source_code]
            group_sql = ""
            if group:
                group_sql = " AND p.group_code=%s"
                params.append(group)
            cur.execute(
                f"""
                SELECT p.id, p.sku_id FROM catalog_sealed_source_identity i
                JOIN catalog_sealed_product p ON p.id=i.sealed_id
                WHERE i.source_code=%s AND i.resolved=1 AND i.match_status='candidate'{group_sql}
                """,
                params,
            )
            targets = [(int(r["id"]), str(r["sku_id"])) for r in cur.fetchall()]
        elif kind == "image":
            params = []
            group_sql = ""
            if group:
                group_sql = " AND p.group_code=%s"
                params.append(group)
            cur.execute(
                f"""
                SELECT DISTINCT p.id, p.sku_id FROM market_sealed_image_asset a
                JOIN catalog_sealed_product p ON p.id=a.sealed_id
                WHERE a.image_kind='box_front'{group_sql}
                  AND NOT EXISTS(SELECT 1 FROM operator_sealed_binding_freeze f
                                 WHERE f.sealed_id=p.id AND f.freeze_kind='image' AND f.acceptance_status='accepted')
                """,
                params,
            )
            targets = [(int(r["id"]), str(r["sku_id"])) for r in cur.fetchall()]
        else:  # identity bulk
            params = []
            group_sql = ""
            if group:
                group_sql = " WHERE group_code=%s AND status <> 'no-box'"
                params.append(group)
            else:
                group_sql = " WHERE status <> 'no-box'"
            cur.execute(f"SELECT id, sku_id FROM catalog_sealed_product{group_sql}", params)
            targets = [(int(r["id"]), str(r["sku_id"])) for r in cur.fetchall()]
        if not sku:
            targets = _bulk_acceptable(cur, targets, kind, refused)
        wanted = external_id
        for sealed_id, sku_id in targets:
            external_id = ""
            if kind == "image":
                cur.execute(
                    "SELECT content_sha256 FROM market_sealed_image_asset WHERE sealed_id=%s AND image_kind='box_front'"
                    + (" AND content_sha256=%s" if wanted else "") + " ORDER BY captured_at DESC LIMIT 1",
                    (sealed_id, wanted) if wanted else (sealed_id,),
                )
                asset = cur.fetchone()
                if not asset:
                    if wanted:
                        raise SystemExit(f"{sku_id}: no box_front asset {wanted}")
                    continue
                external_id = str(asset["content_sha256"])
            elif kind == "source":
                cur.execute(
                    "SELECT external_entity_id FROM catalog_sealed_source_identity WHERE sealed_id=%s AND source_code=%s AND match_status<>'rejected'"
                    + (" AND external_entity_id=%s" if wanted else "") + " ORDER BY resolved DESC LIMIT 1",
                    (sealed_id, source_code, wanted) if wanted else (sealed_id, source_code),
                )
                bind = cur.fetchone()
                if not bind:
                    if wanted:
                        raise SystemExit(f"{sku_id}: no live {source_code} bind on {wanted}")
                    continue
                external_id = str(bind["external_entity_id"])
                ids = same_item_ids(source_code, external_id)
                cur.execute(
                    f"""
                    SELECT p.sku_id FROM catalog_sealed_source_identity i JOIN catalog_sealed_product p ON p.id=i.sealed_id
                    WHERE i.source_code=%s AND i.external_entity_id IN ({",".join(["%s"] * len(ids))})
                      AND i.sealed_id<>%s AND i.match_status<>'rejected'
                    """,
                    (source_code, *ids, sealed_id),
                )
                held_by = sorted({str(r["sku_id"]) for r in cur.fetchall()})
                if held_by:
                    # One box, one SKU. 2026-09-23: six SNK boxes sat accepted on two SKUs each under the
                    # trading-cards:/apparels: spellings (EB-05 EN priced off the EB-03 EN box). Reject the wrong bind first.
                    refused.append({"sku": sku_id, "ext": external_id, "heldBy": held_by})
                    continue
                cur.execute(
                    "UPDATE catalog_sealed_source_identity SET match_status='exact' WHERE sealed_id=%s AND source_code=%s AND external_entity_id=%s",
                    (sealed_id, source_code, external_id),
                )
            _freeze(cur, sealed_id, kind, source_code, external_id, actor, note)
            accepted.append({"sku": sku_id, "kind": kind, "source": source_code, "ext": external_id})
        conn.commit()
    finally:
        conn.close()
    doc = {"asOf": utc_now(), "action": "sealed-accept-binding", "actor": actor, "accepted": len(accepted), "items": accepted[:50],
           "refused": refused}
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    if refused:
        raise SystemExit(f"refused {len(refused)}: " + "; ".join(
            f"{r['sku']} {r['ext']} held by {', '.join(r['heldBy'])}; reject the wrong bind first" if r.get("heldBy")
            else f"{r['sku']} {r['reason']}; accept it on its own --sku" for r in refused))
    return doc


def _bulk_acceptable(cur, targets: list[tuple[int, str]], kind: str, refused: list[dict]) -> list[tuple[int, str]]:
    """--all-resolved accepts only what a bulk pass may: never a ptcg-jp SKU, an unreleased one, or (for source
    and image) one whose identity no one accepted. The sealed accept gate takes those one SKU at a time; they go to
    refused, so the call exits non-zero after committing the rest."""
    if not targets:
        return targets
    cur.execute(
        f"""
        SELECT p.id, p.group_code, p.status,
          EXISTS(SELECT 1 FROM operator_sealed_binding_freeze f WHERE f.sealed_id=p.id AND f.freeze_kind='identity'
                 AND f.acceptance_status='accepted') AS identity_frozen
        FROM catalog_sealed_product p WHERE p.id IN ({",".join(["%s"] * len(targets))})
        """,
        [t[0] for t in targets],
    )
    facts = {int(r["id"]): r for r in cur.fetchall()}
    keep = []
    for sealed_id, sku_id in targets:
        row = facts.get(sealed_id) or {}
        reason = ("ptcg-jp" if row.get("group_code") == "ptcg-jp" else
                  "unreleased" if row.get("status") == "unreleased" else
                  "identity not accepted" if kind != "identity" and not int(row.get("identity_frozen") or 0) else "")
        if reason:
            refused.append({"sku": sku_id, "ext": "", "reason": f"bulk refused: {reason}"})
        else:
            keep.append((sealed_id, sku_id))
    return keep


# --- release ----------------------------------------------------------------------


def current_month() -> str:
    today = datetime.now(timezone.utc).date()
    return f"{today.year:04d}-{today.month:02d}"


def release_due(row: dict, month: str) -> bool:
    """One rule for scan's releaseDue list and the release flip: still unreleased, catalog month has come."""
    release = str(row.get("release_month") or "")
    return row.get("status") == "unreleased" and bool(release) and release <= month


def cmd_sealed_release(*, sku: str, actor: str, note: str | None) -> dict:
    """unreleased -> active for one SKU whose release month has come; Yahoo sold search, price triage and
    gaps read active rows only. A future month is refused: the box is not out yet, or its catalog month is
    wrong and gets corrected first. The change goes to catalog-changes.jsonl before the commit, so no flip
    lands in the DB without a line naming who made it."""
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, sku_id, status, release_month FROM catalog_sealed_product WHERE sku_id=%s OR slug=%s", (sku, sku))
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"unknown sku/slug: {sku}")
        month = current_month()
        if not release_due(row, month):
            raise SystemExit(f"{row['sku_id']}: status={row['status']} release_month={row['release_month']} (now {month}); "
                             "only an unreleased SKU whose release month has come can be released")
        cur.execute("UPDATE catalog_sealed_product SET status='active' WHERE id=%s AND status='unreleased'", (int(row["id"]),))
        if cur.rowcount != 1:
            raise SystemExit(f"{row['sku_id']}: UPDATE matched {cur.rowcount} rows, expected 1")
        doc = {"asOf": utc_now(), "action": "sealed-release", "sku": row["sku_id"], "sealedId": int(row["id"]),
               "from": "unreleased", "to": "active", "releaseMonth": row["release_month"], "actor": actor, "note": note}
        _log_catalog_change(doc)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def _log_catalog_change(doc: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "catalog-changes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False) + "\n")


# --- catalog add / correct ----------------------------------------------------------

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
PRODUCT_FIELDS = ("name_en", "name_jp", "release_month", "packs_per_box", "official_url", "status")
# set-product --status: a SKU with no booster box leaves /box and every collector (MEW 151 EN and Pokemon GO EN were
# sold as ETB / bundles only). Back to active only once its month has come, so this is never a way around release.
STATUS_MOVES = {"no-box": ("active", "unreleased"), "active": ("no-box",)}


def sku_slug(sku_id: str) -> str:
    """The seed's slug rule (sealed_catalog_ingest.slugify): SM3+ -> sm3plus, every other run of non-alnum -> '-'."""
    return re.sub(r"[^a-z0-9]+", "-", sku_id.lower().replace("+", "plus")).strip("-")


def _check_facts(fields: dict) -> None:
    month = fields.get("release_month")
    if month is not None and not MONTH_RE.match(str(month)):
        raise SystemExit(f"release month must be YYYY-MM, got {month!r}")
    if fields.get("packs_per_box") is not None and int(fields["packs_per_box"]) <= 0:
        raise SystemExit("packs per box must be above 0")
    url = fields.get("official_url")
    if url and not str(url).startswith("https://"):
        raise SystemExit(f"official url must be https: {url!r}")
    if fields.get("status") is not None and fields["status"] not in STATUS_MOVES:
        raise SystemExit(f"status must be one of {sorted(STATUS_MOVES)}, got {fields['status']!r}")


def cmd_sealed_add_product(*, game: str, lang: str, set_code: str, product_kind: str, print_wave: str, name_en: str,
                           name_jp: str | None, release_month: str, packs_per_box: int, official_url: str | None,
                           actor: str, note: str) -> dict:
    """A box the catalog does not know yet. It goes in 'unreleased' whatever its month: release flips it once the
    month has come, so release_due stays the one status rule. The group and product kind must already exist,
    because a typo would mint a SKU nothing else reads. official_url also becomes an 'official' hint, which
    image harvest reads. No Yahoo hint: sealed_collect builds the query from name_jp for every active JP box."""
    game, lang = game.lower(), lang.lower()
    _check_facts({"release_month": release_month, "packs_per_box": packs_per_box, "official_url": official_url})
    sku_id = f"{game}:{lang}:{set_code}:{product_kind}:{print_wave}"
    slug, group = sku_slug(sku_id), f"{game}-{lang}"
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM catalog_sealed_product WHERE group_code=%s AND product_kind=%s", (group, product_kind))
        if not int(cur.fetchone()["n"]):
            raise SystemExit(f"no {product_kind} in group {group} yet; check --game/--lang/--kind")
        cur.execute("SELECT sku_id FROM catalog_sealed_product WHERE sku_id=%s OR slug=%s", (sku_id, slug))
        if cur.fetchone():
            raise SystemExit(f"{sku_id} is already in the catalog; correct it with set-product")
        cur.execute(
            """
            INSERT INTO catalog_sealed_product
              (sku_id, slug, game, lang, group_code, set_code, name_en, name_jp, release_month,
               packs_per_box, product_kind, print_wave, official_url, status, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'unreleased',%s)
            """,
            (sku_id, slug, game, lang, group, set_code, name_en, name_jp, release_month, int(packs_per_box),
             product_kind, print_wave, official_url, note[:1000]),
        )
        sealed_id = int(cur.lastrowid)
        if official_url:
            cur.execute(
                "INSERT INTO catalog_sealed_source_hint (sealed_id, source_code, hint_kind, url, url_sha256, note) "
                "VALUES (%s,'official','official',%s,%s,%s)",
                (sealed_id, official_url, hashlib.sha256(official_url.encode("utf-8")).hexdigest(), f"add-product by {actor}"),
            )
        doc = {"asOf": utc_now(), "action": "sealed-add-product", "sku": sku_id, "slug": slug, "sealedId": sealed_id,
               "status": "unreleased", "nameEn": name_en, "nameJp": name_jp, "releaseMonth": release_month,
               "packsPerBox": int(packs_per_box), "officialUrl": official_url, "actor": actor, "note": note}
        _log_catalog_change(doc)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def cmd_sealed_set_product(*, sku: str, fields: dict, actor: str, note: str) -> dict:
    """Correct catalog facts on one SKU; --note names the official source. A JP name change also moves the SKU's
    Yahoo search hint when that hint is still the query built from the old name: sealed_collect uses a hint as
    is, and Yahoo sold QC wants the own set name in the title. OP-17 JP read '世界最強の戦士達' (official:
    世界最強の戦士), and 0 of its Yahoo sales got through."""
    fields = {k: v for k, v in fields.items() if v is not None}
    if set(fields) - set(PRODUCT_FIELDS):
        raise SystemExit(f"not a catalog fact: {sorted(set(fields) - set(PRODUCT_FIELDS))}")
    _check_facts(fields)
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, sku_id, lang, group_code, print_wave, name_en, name_jp, release_month, packs_per_box, official_url, status "
            "FROM catalog_sealed_product WHERE sku_id=%s OR slug=%s",
            (sku, sku),
        )
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"unknown sku/slug: {sku}")
        changes = {k: {"from": row[k], "to": v} for k, v in fields.items() if str(row[k] if row[k] is not None else "") != str(v)}
        if not changes:
            raise SystemExit(f"{row['sku_id']}: nothing differs from the catalog")
        if "status" in changes:
            to = changes["status"]["to"]
            if row["status"] not in STATUS_MOVES[to]:
                raise SystemExit(f"{row['sku_id']}: status {row['status']} -> {to} is not a set-product move")
            month = fields.get("release_month", row["release_month"])
            if to == "active" and not release_due({"status": "unreleased", "release_month": month}, current_month()):
                raise SystemExit(f"{row['sku_id']}: release month {month} has not come; release flips it when due")
        sealed_id = int(row["id"])
        cur.execute(f"UPDATE catalog_sealed_product SET {', '.join(f'{k}=%s' for k in changes)} WHERE id=%s",
                    (*[c["to"] for c in changes.values()], sealed_id))
        if cur.rowcount != 1:
            raise SystemExit(f"{row['sku_id']}: UPDATE matched {cur.rowcount} rows, expected 1")
        doc = {"asOf": utc_now(), "action": "sealed-set-product", "sku": row["sku_id"], "sealedId": sealed_id,
               "changes": changes, "actor": actor, "note": note}
        if "name_jp" in changes and str(row["lang"]).lower() == "jp":
            wave, group = str(row["print_wave"] or "std"), str(row["group_code"] or "")
            old = yahoo_closedsearch_url(yahoo_jp_query(changes["name_jp"]["from"], row["name_en"], wave, group))
            new = yahoo_closedsearch_url(yahoo_jp_query(changes["name_jp"]["to"], fields.get("name_en", row["name_en"]), wave, group))
            cur.execute(
                "UPDATE catalog_sealed_source_hint SET url=%s, url_sha256=%s "
                "WHERE sealed_id=%s AND source_code='yahoo' AND hint_kind='search' AND url=%s",
                (new, hashlib.sha256(new.encode("utf-8")).hexdigest(), sealed_id, old),
            )
            doc["yahooHint"] = {"from": old, "to": new, "moved": cur.rowcount}
        _log_catalog_change(doc)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


# --- corrections: a wrong row, bind or image comes down by status, never by delete ------------------------------
# Each has its reverse (quarantine --restore, accept-binding --ext, move-binding back, accept-binding --kind image)
# and writes its catalog-changes.jsonl line before the commit.

OBSERVATION_TABLES = {"sale": "market_sealed_sale_observation", "price": "market_sealed_price_observation"}


def _product(cur, sku: str) -> dict:
    cur.execute("SELECT id, sku_id, group_code, status FROM catalog_sealed_product WHERE sku_id=%s OR slug=%s", (sku, sku))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"unknown sku/slug: {sku}")
    return row


def _item_rows(cur, source_code: str, external_id: str, sealed_id: int | None = None) -> list[dict]:
    """Identity rows naming one source item under any spelling (SNK namespaces, PC url-quoting)."""
    key = item_key(source_code, external_id)
    sql = "SELECT sealed_id, external_entity_id, match_status FROM catalog_sealed_source_identity WHERE source_code=%s"
    params: list[Any] = [source_code]
    if sealed_id is not None:
        sql += " AND sealed_id=%s"
        params.append(sealed_id)
    cur.execute(sql, params)
    return [r for r in cur.fetchall() if item_key(source_code, r["external_entity_id"]) == key]


def _reject_source_freeze(cur, sealed_id: int, source_code: str, external_id: str, actor: str, note: str) -> bool:
    """The SKU's accepted source freeze turns rejected when it names this item: compose stops reading the item
    (load_frozen_items) and collect stops pulling it."""
    cur.execute(
        "SELECT external_entity_id FROM operator_sealed_binding_freeze "
        "WHERE sealed_id=%s AND freeze_kind='source' AND source_code=%s AND acceptance_status='accepted'",
        (sealed_id, source_code),
    )
    row = cur.fetchone()
    if not row or item_key(source_code, row["external_entity_id"]) != item_key(source_code, external_id):
        return False
    cur.execute(
        "UPDATE operator_sealed_binding_freeze SET acceptance_status='rejected', actor=%s, note=%s "
        "WHERE sealed_id=%s AND freeze_kind='source' AND source_code=%s AND acceptance_status='accepted'",
        (actor, note[:1000], sealed_id, source_code),
    )
    if cur.rowcount != 1:
        raise SystemExit(f"sealed {sealed_id}: freeze UPDATE matched {cur.rowcount} rows, expected 1")
    return True


def cmd_sealed_quarantine(*, table: str, ids: list[int], restore: bool, actor: str, note: str) -> dict:
    """Named sale or price rows leave compose (metric_status 'quarantined'), or come back ('ok'; compose's trim
    re-judges outliers). A sale is written INSERT IGNORE and a price row keeps its quarantine when its item writes
    it again (sealed_runtime.PRICE_UPSERT_HEAD), so the decision sticks. Every id must be in the state it leaves:
    counted (ok / outlier_trimmed) to quarantine, quarantined to restore, so a QC reject never turns ok."""
    name = OBSERVATION_TABLES[table]
    ids = sorted(set(int(i) for i in ids))
    if not ids:
        raise SystemExit("need --ids")
    leaving = ("quarantined",) if restore else ("ok", "outlier_trimmed")
    marks = ",".join(["%s"] * len(ids))
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT id, sealed_id, source_code, metric_status FROM {name} WHERE id IN ({marks})", ids)
        rows = {int(r["id"]): r for r in cur.fetchall()}
        wrong = {i: (rows[i]["metric_status"] if i in rows else "missing") for i in ids
                 if i not in rows or rows[i]["metric_status"] not in leaving}
        if wrong:
            raise SystemExit(f"{table} rows not in {leaving}: {wrong}")
        cur.execute(
            f"UPDATE {name} SET metric_status=%s WHERE id IN ({marks}) AND metric_status IN ({','.join(['%s'] * len(leaving))})",
            ("ok" if restore else "quarantined", *ids, *leaving),
        )
        if cur.rowcount != len(ids):
            raise SystemExit(f"UPDATE matched {cur.rowcount} rows, expected {len(ids)}")
        doc = {"asOf": utc_now(), "action": "sealed-quarantine-restore" if restore else "sealed-quarantine", "table": table,
               "rows": [{"id": i, "sealedId": int(rows[i]["sealed_id"]), "source": rows[i]["source_code"],
                         "from": rows[i]["metric_status"]} for i in ids],
               "actor": actor, "note": note}
        _log_catalog_change(doc)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


SET_NAME_REJECTS = ("rejected_own_set_name_missing", "rejected_foreign_set_in_title")


# the sales whose box count and title QC come from the title alone (an eBay import can carry its own quantity)
REJUDGE_SOURCES = {"yahoo": "yahoo_closedsearch_v1", "ebay": "pc_page_v1"}


def cmd_sealed_rejudge_sales(*, source: str, skus: list[str], actor: str, note: str, dry_run: bool = False) -> dict:
    """Sales are INSERT IGNORE by lot, so a title-QC fix never reaches rows already written (2026-09-23: S2's own
    'ソード＆シールド … 反逆クラッシュ' titles stayed rejected; 2026-09-24: 'OP-03 BOX' was 3 boxes, 'BOX 10パック' 10,
    'Booster Box 24 Packs' 24, '2BOXセット' 1). This runs today's title QC on a source's rows that are counted, and for
    yahoo those rejected for the set name (with the peers run_yahoo uses). A row today's QC rejects leaves for
    rejected_<reason>, usd NULL; only a set-name reject today's QC accepts comes back, ok. A row that stays or comes back
    counted takes today's box count: its unit is total_native_price / quantity, in USD at the rate the row was priced at,
    or at the latest rate for a row coming back (compose's trim re-judges outliers). A quarantine or any other reject is
    never read."""
    from sealed_collect import _group_name_map, set_names
    from sealed_runtime import fx_units_per_usd, qc_box_title, title_set_contamination, to_usd

    parser = REJUDGE_SOURCES[source]
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        if not skus:
            cur.execute("SELECT DISTINCT p.sku_id FROM market_sealed_sale_observation s JOIN catalog_sealed_product p "
                        "ON p.id=s.sealed_id WHERE s.source_code=%s AND s.parser=%s AND p.status<>'no-box'", (source, parser))
            skus = [r["sku_id"] for r in cur.fetchall()]
        products = [_product(cur, s) for s in skus]
        boxless = [p["sku_id"] for p in products if p["status"] == "no-box"]
        if boxless and source == "yahoo":
            raise SystemExit(f"no-box SKUs have no set-name peers to judge by: {boxless}")
        jpy_per_usd = fx_units_per_usd(cur, "JPY")
        if not jpy_per_usd:
            raise SystemExit("no JPY rate: a row coming back could not be priced")
        groups = _group_name_map(cur)
        states = ("ok", "outlier_trimmed", *(SET_NAME_REJECTS if source == "yahoo" else ()))
        moves = []
        for product in products:
            sealed_id = int(product["id"])
            own, foreign = set_names(groups, str(product["group_code"]), sealed_id)
            cur.execute(
                f"SELECT id, title, native_price, native_currency, quantity, total_native_price, unit_price_usd, metric_status "
                f"FROM market_sealed_sale_observation WHERE sealed_id=%s AND source_code=%s AND parser=%s "
                f"AND metric_status IN ({','.join(['%s'] * len(states))})",
                (sealed_id, source, parser, *states),
            )
            for row in cur.fetchall():
                qc = qc_box_title(row["title"] or "")
                if qc["accepted"] and source == "yahoo":
                    qc = {**qc, **title_set_contamination(row["title"] or "", own, foreign)}
                was = row["metric_status"]
                to = ("ok" if was in SET_NAME_REJECTS else was) if qc["accepted"] else f"rejected_{qc['reason']}"
                qty, usd = int(row["quantity"] or 1), None
                native = None if row["native_price"] is None else float(row["native_price"])  # DECIMAL
                if to in ("ok", "outlier_trimmed"):
                    total, qty = row["total_native_price"], qc["quantity"]
                    currency = str(row["native_currency"] or "JPY")
                    if source == "yahoo":  # yahoo's native_price is the unit, eBay's the lot (run_pc)
                        native = round(float(total) / qty, 2)
                    if was in SET_NAME_REJECTS or row["unit_price_usd"] is None:
                        usd = to_usd(float(total) / qty, currency, jpy_per_usd)
                    else:  # the rate the row was priced at: usd * old boxes = the lot in USD
                        usd = round(float(row["unit_price_usd"]) * int(row["quantity"] or 1) / qty, 2)
                    if to == was and qty == int(row["quantity"] or 1):
                        continue
                elif to == was:
                    continue
                moves.append({"id": int(row["id"]), "sealedId": sealed_id, "sku": product["sku_id"], "from": was, "to": to,
                              "quantity": [int(row["quantity"] or 1), qty], "usd": usd, "native": native,
                              "title": (row["title"] or "")[:120]})
        for m in moves if not dry_run else ():
            cur.execute("UPDATE market_sealed_sale_observation SET metric_status=%s, unit_price_usd=%s, quantity=%s, native_price=%s "
                        "WHERE id=%s AND metric_status=%s AND quantity=%s",
                        (m["to"], m["usd"], m["quantity"][1], m["native"], m["id"], m["from"], m["quantity"][0]))
            if cur.rowcount != 1:
                raise SystemExit(f"sale {m['id']}: UPDATE matched {cur.rowcount} rows, expected 1")
        doc = {"asOf": utc_now(), "action": "sealed-rejudge-sales", "source": source, "dryRun": dry_run,
               "jpyPerUsd": jpy_per_usd, "skus": len(products), "rows": moves, "actor": actor, "note": note}
        if not dry_run:
            _log_catalog_change(doc)
            conn.commit()
    finally:
        conn.close()
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def cmd_sealed_reject_binding(*, sku: str, source_code: str, external_id: str, actor: str, note: str) -> dict:
    """A SKU's bind to the wrong item comes down: its identity rows under every spelling turn rejected, which
    discover never reopens (insert_candidate_bind), and its source freeze turns rejected when it names that item."""
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        product = _product(cur, sku)
        sealed_id = int(product["id"])
        rows = [r for r in _item_rows(cur, source_code, external_id, sealed_id) if r["match_status"] != "rejected"]
        if not rows:
            raise SystemExit(f"{product['sku_id']}: no live {source_code} bind on {external_id}")
        for r in rows:
            cur.execute(
                "UPDATE catalog_sealed_source_identity SET match_status='rejected', note=%s "
                "WHERE source_code=%s AND external_entity_id=%s AND sealed_id=%s",
                (f"rejected by {actor}: {note}"[:500], source_code, r["external_entity_id"], sealed_id),
            )
            if cur.rowcount != 1:
                raise SystemExit(f"{product['sku_id']}: UPDATE matched {cur.rowcount} rows, expected 1")
        doc = {"asOf": utc_now(), "action": "sealed-reject-binding", "sku": product["sku_id"], "sealedId": sealed_id,
               "source": source_code, "ext": [r["external_entity_id"] for r in rows],
               "from": [r["match_status"] for r in rows],
               "freezeRejected": _reject_source_freeze(cur, sealed_id, source_code, external_id, actor, note),
               "actor": actor, "note": note}
        _log_catalog_change(doc)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def cmd_sealed_move_binding(*, source_code: str, external_id: str, from_sku: str, to_sku: str, actor: str, note: str) -> dict:
    """An item bound to the wrong SKU moves to the right one as a candidate (PRB-01 JP's box sat on PRB-02 JP);
    the old SKU's freeze on it turns rejected. Accepting it on the new SKU is its own call: accept-binding --ext.
    Only the old SKU's live rows move and no other SKU may hold the item live. Another SKU's rejected spelling stays
    where it is: it is that SKU's record that the item is not its box (PRB-01 EN rejected trading-cards:216885, the
    JP box that then moved from PRB-02 JP to PRB-01 JP). A SKU that rejected the item cannot receive it."""
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        old, new = _product(cur, from_sku), _product(cur, to_sku)
        old_id, new_id = int(old["id"]), int(new["id"])
        if old_id == new_id:
            raise SystemExit("--from-sku and --to-sku name the same SKU")
        found = _item_rows(cur, source_code, external_id)
        rows = [r for r in found if int(r["sealed_id"]) == old_id and r["match_status"] != "rejected"]
        blocked = [r for r in found if (int(r["sealed_id"]) != old_id and r["match_status"] != "rejected")
                   or (int(r["sealed_id"]) == new_id and r["match_status"] == "rejected")]
        if not rows or blocked:
            raise SystemExit(f"{source_code} {external_id} is not live on {old['sku_id']} alone, or {new['sku_id']} rejected it: "
                             f"{[(int(r['sealed_id']), r['external_entity_id'], r['match_status']) for r in found]}")
        for r in rows:
            cur.execute(
                "UPDATE catalog_sealed_source_identity SET sealed_id=%s, match_status='candidate', note=%s "
                "WHERE source_code=%s AND external_entity_id=%s AND sealed_id=%s",
                (new_id, f"moved from {old['sku_id']} by {actor}: {note}"[:500], source_code, r["external_entity_id"], old_id),
            )
            if cur.rowcount != 1:
                raise SystemExit(f"UPDATE matched {cur.rowcount} rows, expected 1")
        doc = {"asOf": utc_now(), "action": "sealed-move-binding", "source": source_code,
               "ext": [r["external_entity_id"] for r in rows], "fromSku": old["sku_id"], "toSku": new["sku_id"],
               "from": [r["match_status"] for r in rows],
               "freezeRejected": _reject_source_freeze(cur, old_id, source_code, external_id, actor, note),
               "actor": actor, "note": note}
        _log_catalog_change(doc)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def cmd_sealed_revoke_image(*, sku: str, actor: str, note: str) -> dict:
    """A wrong box image comes down: the SKU's image freeze turns rejected, and export shows no image for it
    until a right asset is accepted (add-image, then accept-binding --kind image --ext <sha>)."""
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        product = _product(cur, sku)
        cur.execute(
            "SELECT external_entity_id FROM operator_sealed_binding_freeze "
            "WHERE sealed_id=%s AND freeze_kind='image' AND acceptance_status='accepted'",
            (int(product["id"]),),
        )
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"{product['sku_id']}: no accepted image freeze")
        cur.execute(
            "UPDATE operator_sealed_binding_freeze SET acceptance_status='rejected', actor=%s, note=%s "
            "WHERE sealed_id=%s AND freeze_kind='image' AND acceptance_status='accepted'",
            (actor, note[:1000], int(product["id"])),
        )
        if cur.rowcount != 1:
            raise SystemExit(f"{product['sku_id']}: UPDATE matched {cur.rowcount} rows, expected 1")
        doc = {"asOf": utc_now(), "action": "sealed-revoke-image", "sku": product["sku_id"], "sealedId": int(product["id"]),
               "sha": row["external_entity_id"], "actor": actor, "note": note}
        _log_catalog_change(doc)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def cmd_sealed_add_binding(*, sku: str, source_code: str, external_id: str, url: str, actor: str, note: str) -> dict:
    """The right item for a SKU that discover never proposed goes in as a candidate, under insert_candidate_bind's
    rules (no item another SKU holds, no reopened reject). Accepting it is accept-binding --ext."""
    if source_code not in ("snkrdunk", "pricecharting"):
        raise SystemExit("source must be snkrdunk or pricecharting")
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        product = _product(cur, sku)
        status = insert_candidate_bind(cur, source=source_code, external_id=external_id, sealed_id=int(product["id"]),
                                       url=url, note=f"add-binding by {actor}: {note}", origin="operator")
        if status not in ("inserted", "updated"):
            raise SystemExit(f"{product['sku_id']}: {source_code} {external_id} not added: {status}")
        doc = {"asOf": utc_now(), "action": "sealed-add-binding", "sku": product["sku_id"], "sealedId": int(product["id"]),
               "source": source_code, "ext": external_id, "url": url, "status": status, "actor": actor, "note": note}
        _log_catalog_change(doc)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def cmd_sealed_add_image(*, sku: str, url: str, actor: str, note: str) -> dict:
    """A right box image from a named url becomes an asset of the SKU (the harvest's webp variants). It shows
    once accepted: accept-binding --kind image --ext <sha>. PriceCharting is read through the 9333 session only."""
    import requests

    from sealed_image_harvest import ASSETS_DIR, UA, to_webp_variants

    host = (urlparse(url).hostname or "").lower()
    if not url.startswith("https://") or host.endswith("pricecharting.com"):
        raise SystemExit(f"image url must be https and not PriceCharting: {url}")
    response = requests.get(url, timeout=30, headers={"User-Agent": UA, "Referer": f"https://{host}/"})
    if response.status_code != 200:
        raise SystemExit(f"HTTP {response.status_code} {url}")
    variants = to_webp_variants(response.content)
    if not variants:
        raise SystemExit(f"not an image of at least 150px: {url}")
    full, w200, w600, width, height = variants
    digest = hashlib.sha256(full).hexdigest()
    source = "snkrdunk" if "snkrdunk" in host else "official"
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        product = _product(cur, sku)
        # Refused only while another SKU shows the image. A revoked asset is free to go to its right SKU: PRB-01 EN
        # had shown the JP PRB-01 box, which is PRB-01 JP's image.
        cur.execute("SELECT sealed_id FROM operator_sealed_binding_freeze WHERE freeze_kind='image' "
                    "AND acceptance_status='accepted' AND external_entity_id=%s AND sealed_id<>%s",
                    (digest, int(product["id"])))
        other = cur.fetchone()
        if other:
            raise SystemExit(f"image {digest} is sealed {other['sealed_id']}'s accepted image")
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        for suffix, blob in (("", full), ("_200", w200), ("_600", w600)):
            (ASSETS_DIR / f"{digest}{suffix}.webp").write_bytes(blob)
        cur.execute(
            """
            INSERT INTO market_sealed_image_asset
              (sealed_id, image_kind, content_sha256, source_code, source_url, mime_type, width_px, height_px, captured_at)
            VALUES (%s,'box_front',%s,%s,%s,'image/webp',%s,%s,%s)
            ON DUPLICATE KEY UPDATE source_url=VALUES(source_url), captured_at=VALUES(captured_at)
            """,
            (int(product["id"]), digest, source, url[:700], width, height, utc_naive().strftime("%Y-%m-%d %H:%M:%S.%f")),
        )
        doc = {"asOf": utc_now(), "action": "sealed-add-image", "sku": product["sku_id"], "sealedId": int(product["id"]),
               "sha": digest, "width": width, "height": height, "url": url, "actor": actor, "note": note}
        _log_catalog_change(doc)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


# --- export sealed subset --------------------------------------------------------


def _stories() -> dict[str, dict[str, str]]:
    if EDITORIAL_STORIES.is_file():
        try:
            return json.loads(EDITORIAL_STORIES.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _windows_from_line(line: list[dict], today: date) -> tuple[dict, list[dict], dict | None]:
    """(windows, historyDaily, latestPoint) from the composed aggregate line."""
    pts = [r for r in line if r["composed_price_usd"] is not None]
    history = [
        {
            "date": r["observed_date"].isoformat(),
            "priceUsd": float(r["composed_price_usd"]) if r["composed_price_usd"] is not None else None,
            "soldCount": int(r["sold_count"] or 0),
            "soldValueUsd": float(r["sold_value_usd"]) if r["sold_value_usd"] is not None else None,
        }
        for r in line
    ][-HISTORY_MAX_POINTS:]
    if not pts:
        return {}, history, None
    latest = pts[-1]
    latest_price = float(latest["composed_price_usd"])
    windows: dict[str, Any] = {}
    for label, days in (("1d", 1), ("7d", 7), ("30d", 30)):
        target = latest["observed_date"] - timedelta(days=days)
        prev = None
        for r in reversed(pts):
            if r["observed_date"] <= target:
                prev = float(r["composed_price_usd"])
                break
        sold = sum(int(r["sold_count"] or 0) for r in line if r["observed_date"] > target)
        win: dict[str, Any] = {"soldCount": sold}
        if prev and prev > 0:
            win["changeUsd"] = round(latest_price - prev, 2)
            win["changePct"] = round((latest_price - prev) / prev * 100.0, 2)
        windows[label] = win
    return windows, history, latest


def cmd_export_sealed_subset(output: Path | None = None, *, include_candidates: bool = False) -> dict:
    """Product discipline: only source-frozen SKUs ship by default.
    --include-candidates is the engineering/operator surface."""
    load_env()
    conn = db()
    today = datetime.now(timezone.utc).date()
    try:
        cur = conn.cursor()
        ensure_sealed_fullname_columns(cur)
        conn.commit()
        products = _products(cur)
        if not include_candidates:
            products = [p for p in products if int(p["source_frozen"] or 0)]
        aggregates = _aggregates(cur)
        sales_by = load_sales(cur)
        market_all = load_market(cur)
        asks = load_asks(cur)
        stories = _stories()
        cur.execute(
            """
            SELECT f.sealed_id, f.external_entity_id AS sha, a.width_px, a.height_px
            FROM operator_sealed_binding_freeze f
            JOIN market_sealed_image_asset a
              ON a.sealed_id=f.sealed_id AND a.content_sha256=f.external_entity_id AND a.image_kind='box_front'
            WHERE f.freeze_kind='image' AND f.acceptance_status='accepted'
            """
        )
        frozen_images = {
            int(r["sealed_id"]): {"sha256": str(r["sha"]), "width": int(r["width_px"]), "height": int(r["height_px"])}
            for r in cur.fetchall()
        }
        if include_candidates:
            # Engineering surface: latest harvested asset stands in until DADDY freezes.
            cur.execute(
                """
                SELECT a.sealed_id, a.content_sha256 AS sha, a.width_px, a.height_px
                FROM market_sealed_image_asset a
                JOIN (
                  SELECT sealed_id, MAX(captured_at) AS captured_at
                  FROM market_sealed_image_asset WHERE image_kind='box_front' GROUP BY sealed_id
                ) latest ON latest.sealed_id=a.sealed_id AND latest.captured_at=a.captured_at
                WHERE a.image_kind='box_front'
                """
            )
            for r in cur.fetchall():
                frozen_images.setdefault(
                    int(r["sealed_id"]),
                    {"sha256": str(r["sha"]), "width": int(r["width_px"]), "height": int(r["height_px"])},
                )
        jpy = None
        cur.execute(
            "SELECT rate, base_currency FROM market_fx_rate_observation WHERE (base_currency='USD' AND quote_currency='JPY') OR (base_currency='JPY' AND quote_currency='USD') ORDER BY effective_date DESC LIMIT 1"
        )
        fx_row = cur.fetchone()
        if fx_row:
            rate = float(fx_row["rate"])
            jpy = rate if fx_row["base_currency"] == "USD" else (1.0 / rate if rate else None)

        entries = []
        priced = 0
        imaged = 0
        for p in products:
            sealed_id = int(p["id"])
            group = str(p["group_code"])
            line = aggregates.get(sealed_id) or []
            windows, history, latest = _windows_from_line(line, today)
            sales = sales_by.get(sealed_id, [])
            kept = [s for s in sales if s["metric_status"] == "ok" and s["sold_at"].date() >= today - timedelta(days=30)]
            market_by_source = {s: market_all.get((sealed_id, s), []) for s in ("pricecharting", "snkrdunk")}
            current = compose_current(
                group_code=group, kept_sales=kept, market_by_source=market_by_source,
                ask=asks.get(sealed_id), today=today, all_sales=sales,
            )
            price = None
            if current:
                priced += 1
                price = {
                    "usd": current["usd"],
                    "kind": current["kind"],
                    "source": current["source"],
                    "asOf": (latest["observed_date"].isoformat() if latest else today.isoformat()),
                }
                if jpy and group.endswith("-jp"):
                    price["native"] = {"amount": round(current["usd"] * jpy), "currency": "JPY"}
            ask = asks.get(sealed_id)
            ask_floor = None
            if ask and (today - ask[0]).days <= 7:
                ask_floor = {"usd": ask[1], "source": "snkrdunk", "asOf": ask[0].isoformat()}
                if ask[2] is not None:
                    ask_floor["native"] = {"amount": ask[2], "currency": "JPY"}
            entry = {
                "id": p["slug"],
                "rank": 0,
                "game": p["game"],
                "lang": p["lang"],
                "group": group,
                "setCode": p["set_code"],
                "names": {"en": p["name_en"], **({"jp": p["name_jp"]} if p["name_jp"] else {})},
                "fullNames": {
                    "en": p.get("full_name_en") or p["name_en"],
                    **({"ja": p["full_name_ja"]} if p.get("full_name_ja") else {}),
                },
                "release": p["release_month"],
                "packsPerBox": int(p["packs_per_box"] or 0),
                "productKind": p["product_kind"],
                "printWave": p["print_wave"],
                "status": p["status"],
                "price": price,
                "windows": windows,
                "historyDaily": history,
            }
            if ask_floor:
                entry["askFloor"] = ask_floor
            image = frozen_images.get(sealed_id)
            if image:
                imaged += 1
                entry["image"] = {
                    "src": f"/market-assets/{image['sha256']}.webp",
                    "sha256": image["sha256"],
                    "kind": "box_front",
                    "width": image["width"],
                    "height": image["height"],
                }
            story = stories.get(p["slug"])
            if story and isinstance(story, dict):
                entry["story"] = story
            entries.append(entry)

        entries.sort(key=lambda e: (-(e["price"]["usd"] if e["price"] else -1), e["id"]))
        for idx, entry in enumerate(entries, start=1):
            entry["rank"] = idx
        identity = assert_image_identity(entries)
        doc = {
            "asOf": utc_now(),
            "coverage": {"total": len(entries), "priced": priced, "imaged": imaged},
            "imageIdentity": {"collisions": identity["collisions"], "baseline": identity["baseline"]},
            "products": entries,
        }
    finally:
        conn.close()
    out = output or (OUT_DIR / "sealed-subset-snapshot.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(out)
    print(json.dumps({"action": "export-sealed-subset", "coverage": doc["coverage"], "output": str(out)}, ensure_ascii=False, indent=2))
    return doc


# --- weekly scan -------------------------------------------------------------------


def cmd_sealed_scan() -> dict:
    """Weekly watcher: release flips, upcoming sets, unbound actives.

    New-set discovery beyond the seeded catalog stays a human/agent step
    (official product pages); this scan surfaces everything actionable that is
    already known to the catalog.
    """
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        today = datetime.now(timezone.utc).date()
        month = current_month()
        horizon = today + timedelta(days=60)
        horizon_month = f"{horizon.year:04d}-{horizon.month:02d}"
        products = _products(cur)
        binds = _bind_counts(cur)
        due = []
        upcoming = []
        unbound_active = []
        for p in products:
            sealed_id = int(p["id"])
            release = str(p["release_month"] or "")
            if release_due(p, month):
                due.append({"sku": p["sku_id"], "release": release, "action": "sealed_daily.py release, then bind + accept + stock"})
            elif p["status"] == "unreleased" and release and release <= horizon_month:
                upcoming.append({"sku": p["sku_id"], "release": release})
            if p["status"] == "active" and not binds.get(sealed_id, {}).get("binds"):
                unbound_active.append({"sku": p["sku_id"], "group": p["group_code"]})
        discover_steps = []
        for script in ("pipelines/sealed_snk_discover.py", "pipelines/sealed_pc_discover.py"):
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", script],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
            )
            discover_steps.append({"script": script, "exit": proc.returncode, "tail": (proc.stdout or "")[-400:]})
        doc = {
            "asOf": utc_now(),
            "action": "sealed-scan",
            "releaseDue": due,
            "upcoming60d": upcoming,
            "unboundActive": {"count": len(unbound_active), "sample": unbound_active[:30]},
            "discover": discover_steps,
            "next": [
                "release due -> sealed_daily.py release --sku <slug>, then bind-resolve, accept, stock",
                "new official box not in catalog -> sealed_daily.py add-product (goes in unreleased); wrong catalog fact -> set-product",
                "review snk-discover-receipt / pc-discover-receipt, then accept one SKU at a time: "
                "sealed_daily.py accept-binding --sku <slug> --kind source --source-code <source>",
            ],
        }
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "attention.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(doc, ensure_ascii=False, indent=2, default=str))
        return doc
    finally:
        conn.close()


# --- daily -----------------------------------------------------------------------


def cmd_sealed_daily(*, refresh: bool) -> int:
    py = sys.executable
    steps: list[dict[str, Any]] = []

    def run(cmd: list[str], timeout: int) -> dict:
        proc = subprocess.run([py, "-X", "utf8", *cmd], cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        return {"cmd": " ".join(cmd), "exit": proc.returncode, "tail": (proc.stdout or "")[-500:], "err": (proc.stderr or "")[-300:]}

    if refresh:
        # Operator daily refreshes candidate binds too. Product export below
        # stays accept-freeze only. PC needs CDP 9333 and is daily-mandatory;
        # SNK/Yahoo/compose still run after a PC miss, but the day is red.
        for adapter in ("sealed_pc", "sealed_snk", "sealed_yahoo"):
            steps.append(run(
                ["pipelines/sealed_collect.py", "incr", "--adapter", adapter, "--allow-candidates"],
                timeout=3600 * 2,
            ))
    steps.append(run(["pipelines/sealed_price_compose.py", "--write"], timeout=1800))
    status = cmd_sealed_status()
    gaps = cmd_sealed_gaps()
    export_product = cmd_export_sealed_subset()
    export_live = cmd_export_sealed_subset(
        output=OUT_DIR / "sealed-subset-snapshot.live.json",
        include_candidates=True,
    )
    summary = {
        "asOf": utc_now(),
        "action": "sealed-daily",
        "refresh": refresh,
        "steps": steps,
        "statusTotals": status.get("totals"),
        "gapCount": gaps.get("count"),
        "coverage": export_product.get("coverage"),
        "operatorCoverage": export_live.get("coverage"),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "daily_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "steps"}, ensure_ascii=False, indent=2, default=str))
    failed = [step for step in steps if step["exit"] != 0]
    return 2 if failed else 0
