#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bind a pasted provider URL to a variant, fail-closed, one card at a time.

The repo had no bind-from-URL path at all. `operator_control.accept_binding`
freezes a row that already exists and raises SystemExit when it does not, so
the only way a card ever acquired a provider id was a discovery lane guessing
one. An operator who had already found the page -- by eye, in a browser, in
two seconds -- had nowhere to put it, and the card waited for a lane that had
already failed to find it.

This module is that path, and it is deliberately the LONG way round. It never
writes `exact`. It writes a PROPOSAL at `manual_review` with a capture receipt
and an evidence block, then hands the row to the judge that every other lane
faces (`rebuild_036.cmd_pc_identity_reverify` /
`cmd_snk_identity_reverify`) and reports whatever that judge says, naming the
gate. A human knowing the answer is a reason to look at the page; it is not a
reason to skip the page. The one place a human ruling outranks the judge is
`--operator-ruling`, which is stamped with actor and UTC date, is written into
`bind_evidence_json.reason` where `rebuild_036.operator_ruling()` reads it,
prints in full the refusal it is overriding, and leaves a receipt.

Two invariants are load-bearing and are checked here rather than assumed:

* `catalog_source_identity` PRIMARY KEY is `(source_code, external_entity_id)`,
  so one external id belongs to one variant. A paste that would move an id
  already owned by another variant is REFUSED -- one product cannot appear
  twice, and the fix is to release the other row first, deliberately.
* Every write is guarded on the state that was read. `rowcount == 0` means the
  chain moved the row between the read and the write; that prints "the chain
  just changed this row" and exits 2. It NEVER prints success.

Default is dry-run. `--write` is the only path that touches MySQL.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as R  # noqa: E402

# ---------------------------------------------------------------- exit codes

EXIT_OK = 0
# The contract refused. The card is unchanged and the reason is printed.
EXIT_REFUSED = 1
# A guarded write changed zero rows: the chain edited the row underneath us.
# Reserved for exactly that, so a caller can tell "run it again" apart from
# "this paste is wrong".
EXIT_CHAIN_CHANGED = 2
# The command line itself was wrong (bad --operator-ruling prefix, no --note).
EXIT_USAGE = 3

# ------------------------------------------------------------------- vocabulary

# CDP 9333 only. 9222 is the Codex browser profile and `cdp_identity`
# raises on it by number; naming the port here keeps the refusal readable
# instead of arriving as an attribute error deep in a fetch.
CDP_PORT = 9333

PC_PAGES_DIRNAME = "full900"

SOURCE_PRICECHARTING = "pricecharting"
SOURCE_SNKRDUNK = "snkrdunk"

_PC_URL_RE = re.compile(
    r"^https?://(?:www\.)?pricecharting\.com/game/([^/?#]+)/([^/?#]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
# The `/en` is optional because that is how the site itself serves it, not
# because we are being generous: a paste from a Japanese browser has no /en
# and names the same item.
_SNK_URL_RE = re.compile(
    r"^https?://(?:www\.)?snkrdunk\.com/(?:[a-z]{2}/)?trading-cards/(\d+)"
    r"(?:/[^?#]*)?/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_GEMRATE_URL_RE = re.compile(
    r"^https?://(?:www\.)?gemrate\.com/card/([0-9a-fA-F]{40})(?:/|$|[?#])",
    re.IGNORECASE,
)

# Printed verbatim on an unrecognised paste. A URL table that guesses is a URL
# table that binds the wrong product the first time a provider changes a path,
# so the refusal has to be more useful than the guess would have been.
KNOWN_URL_VOCABULARY = (
    "https://www.pricecharting.com/game/<console-slug>/<product-slug>",
    "https://snkrdunk.com/trading-cards/<numeric-id>"
    "  (also accepted: https://snkrdunk.com/en/trading-cards/<numeric-id>)",
)

STEP_NAMES: tuple[tuple[int, str], ...] = (
    (1, "red-list"),
    (2, "standing-ruling"),
    (3, "capture"),
    (4, "prove"),
    (5, "proposal"),
    (6, "judge"),
    (7, "verdict"),
    (8, "operator-ruling"),
    (9, "freeze"),
    (10, "row-recheck"),
)

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_BLOCKED = "blocked"
STATUS_REFUSED = "refused"
STATUS_CHAIN_CHANGED = "chain_changed"

CHAIN_CHANGED_MESSAGE = (
    "the chain just changed this row between the read and the write "
    "(rowcount == 0) -- nothing was written, run bind-url again"
)


class BindStop(Exception):
    """A step ended the run. Carries the verdict the operator has to read."""

    def __init__(
        self,
        step: int,
        status: str,
        verdict: str,
        detail: str = "",
        *,
        exit_code: int = EXIT_REFUSED,
    ) -> None:
        super().__init__(f"{verdict}: {detail}" if detail else verdict)
        self.step = step
        self.status = status
        self.verdict = verdict
        self.detail = detail
        self.exit_code = exit_code


class ParsedUrl:
    """What the URL itself says. Never more than that."""

    __slots__ = ("source_code", "external_entity_id", "console_slug",
                 "product_slug", "url", "canonical_path")

    def __init__(
        self,
        source_code: str,
        external_entity_id: str,
        *,
        console_slug: str = "",
        product_slug: str = "",
        url: str = "",
    ) -> None:
        self.source_code = source_code
        # Empty for PriceCharting on purpose: the product id is NOT in the URL,
        # and pretending otherwise is how a slug that looks numeric becomes a
        # product id. It is filled from the page body, after the fetch.
        self.external_entity_id = external_entity_id
        self.console_slug = console_slug
        self.product_slug = product_slug
        self.url = url
        self.canonical_path = (
            f"/game/{console_slug.lower()}/{product_slug.lower()}"
            if console_slug else ""
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceCode": self.source_code,
            "externalEntityId": self.external_entity_id,
            "consoleSlug": self.console_slug,
            "productSlug": self.product_slug,
            "url": self.url,
        }


def parse_url(url: str) -> ParsedUrl:
    """URL -> (source, external id). Table driven; refuses what it cannot read."""

    text = (url or "").strip()
    if not text:
        raise BindStop(0, STATUS_REFUSED, "url_missing", "no URL was given")

    match = _PC_URL_RE.match(text)
    if match:
        return ParsedUrl(
            SOURCE_PRICECHARTING, "",
            console_slug=match.group(1), product_slug=match.group(2), url=text,
        )

    match = _SNK_URL_RE.match(text)
    if match:
        return ParsedUrl(SOURCE_SNKRDUNK, match.group(1), url=text)

    match = _GEMRATE_URL_RE.match(text)
    if match:
        # Refused by design, not by omission. GemRate is where `canonical_name`
        # and the whole printing fingerprint come from; re-anchoring a variant
        # onto a different GemRate id rewrites what the catalog believes the
        # card IS, and the place that decision belongs is the intake path that
        # can see the alias graph.
        raise BindStop(
            0, STATUS_REFUSED, "gemrate_is_the_identity_authority",
            "gemrate.com is refused by bind-url v1: re-anchoring the GemRate id "
            "rewrites the catalog's own idea of which card this is. "
            "Run gemrate_identity_intake instead.",
        )

    raise BindStop(
        0, STATUS_REFUSED, "url_not_in_vocabulary",
        "this command never guesses a provider from a URL. Known shapes:\n  "
        + "\n  ".join(KNOWN_URL_VOCABULARY),
    )


# ------------------------------------------------------------------ seams
# Named at module scope so a test can replace them without a network, a
# browser, or MySQL. Nothing else in this file reaches the outside world.


def red_listed_variants() -> set[int]:
    """The 034 audit sheet's standing refusals, minus the 036 release."""

    return set(R.red_listed_variants())


def fetch_pricecharting(
    url: str, pages_dir: Path, variant_id: int, *, port: int = CDP_PORT
) -> tuple[Path, str, str]:
    """Fetch a PC product page through the headed 9333 session.

    The file lands under a temporary name and is renamed to
    `{variant}_{pid}.html` only AFTER the page has told us its own product id,
    because that name is the key `pc_capture_for_product` searches by. Naming
    the file before reading it means naming it after a guess."""

    import cdp_identity
    import pricecharting_cf_session

    cdp_identity.require_session_ready(port)
    pages_dir.mkdir(parents=True, exist_ok=True)
    staging = pages_dir / f"{variant_id}_bind-url-staging.html"
    code = pricecharting_cf_session.cmd_fetch(
        url, staging, headless=False, prefer_cdp=True
    )
    if code != 0 or not staging.is_file():
        raise BindStop(
            3, STATUS_BLOCKED, "pc_fetch_failed",
            f"pricecharting_cf_session.cmd_fetch returned {code} for {url}",
        )
    html = staging.read_text(encoding="utf-8", errors="replace")
    product_id = R._pc_page_product_id(html)
    if not product_id:
        raise BindStop(
            3, STATUS_REFUSED, "pc_page_states_no_product_id",
            f"kept for diagnosis at {staging}",
        )
    final = pages_dir / f"{variant_id}_{product_id}.html"
    staging.replace(final)
    return final, html, product_id


def fetch_snkrdunk(item_id: str, out_path: Path, *, run_id: str) -> dict[str, Any]:
    """Harvest one SNKRDUNK master. Same reader the SNK judge uses."""

    import snk_market_data

    out_path.parent.mkdir(parents=True, exist_ok=True)
    snk_market_data.run(
        [int(item_id)], out_path, delay=0.0,
        condition_code=snk_market_data.PSA10_CONDITION,
        run_id=run_id, workers=1,
    )
    path = out_path if out_path.is_file() else out_path.with_suffix(
        out_path.suffix + ".partial"
    )
    if not path.is_file():
        raise BindStop(
            3, STATUS_BLOCKED, "snk_harvest_produced_no_file", str(out_path)
        )
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if str(payload.get("item_id")) == str(item_id):
            return payload
    raise BindStop(
        3, STATUS_REFUSED, "snk_master_missing",
        f"the harvest carried no row for item {item_id}",
    )


def snk_judge_supports_scoping() -> bool:
    """Does `cmd_snk_identity_reverify` honour `variant_ids` yet?

    Until §A Step 2 lands, that lane selects EVERY held snkrdunk row and
    harvests all of them, so asking it to judge one paste would spend a full
    provider sweep on one card. Checked against the function's own source
    rather than by passing the argument and hoping: the thing that has to be
    true is that the lane READS it, and a Namespace attribute nobody reads is
    exactly the shape of that bug."""

    import inspect

    try:
        source = inspect.getsource(R.cmd_snk_identity_reverify)
    except (OSError, TypeError):
        return False
    return "variant_ids" in source


def trailing_json_object(printed: str) -> dict[str, Any] | None:
    """The LAST top-level JSON object in a judge's stdout, or None.

    Both lanes reach a provider before they print, and the libraries they
    reach through log on stdout: `snk_market_data.run` announces
    "[snk_market_data] x-version acquired ..." before the report and names the
    report file after it. Reading the whole buffer with json.loads() therefore
    died on the first log character -- receipt
    data/runtime/operator/bind-url/20260823T134607Z-v2252-snkrdunk.json stopped
    at step 6 with judge_report_unreadable after the harvest and the proposal
    had already happened, so a real judgement was thrown away for a log line.

    Scanning FORWARD and keeping the last object that decodes is what makes
    this honest: raw_decode consumes a whole object including its nested ones,
    so the walk steps over the report's inner dicts instead of handing one of
    them back, which is exactly what a "last line" or "last brace" scan would
    do to a report printed with indent=1. Nothing here relaxes the gate: a
    buffer with no object in it still returns None."""

    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    index = printed.find("{")
    while index >= 0:
        try:
            # Every start tried is a "{", so anything that decodes here is an
            # object -- an array or a bare scalar is never mistaken for a report.
            value, end = decoder.raw_decode(printed, index)
        except ValueError:
            index = printed.find("{", index + 1)
            continue
        found = value
        index = printed.find("{", end)
    return found


def run_judge(
    source_code: str,
    variant_id: int,
    *,
    write: bool,
    pages_dir: Path | None,
    map_path: Path | None,
    credentials_env: Path | None,
) -> dict[str, Any]:
    """Hand the row to the lane judge and return its own report, verbatim.

    bind-url does not re-derive a verdict. It reads the judge's report, which
    is the judge's stdout, so the gate name the operator sees is the gate name
    the lane wrote -- there is no second vocabulary to drift."""

    args = argparse.Namespace(
        credentials_env=credentials_env,
        write=bool(write),
        variant_ids=[int(variant_id)],
    )
    buffer = io.StringIO()
    if source_code == SOURCE_PRICECHARTING:
        args.pages_dir = pages_dir
        args.map = map_path
        args.fetch_missing = False
        with contextlib.redirect_stdout(buffer):
            R.cmd_pc_identity_reverify(args)
    else:
        if not snk_judge_supports_scoping():
            raise BindStop(
                6, STATUS_BLOCKED, "snk_judge_not_scoped",
                "cmd_snk_identity_reverify does not read variant_ids yet, so "
                "judging this paste would re-harvest every held snkrdunk row. "
                "Land the SNK scoping change (plan §A Step 2) first.",
            )
        with contextlib.redirect_stdout(buffer):
            R.cmd_snk_identity_reverify(args)
    printed = buffer.getvalue()
    # Re-emit it: redirecting the judge's own words away from the operator
    # would make bind-url the only account of what happened.
    sys.stdout.write(printed)
    report = trailing_json_object(printed)
    if report is None:
        try:
            json.loads(printed)
        except ValueError as error:
            raise BindStop(
                6, STATUS_BLOCKED, "judge_report_unreadable", str(error)
            ) from error
        raise BindStop(
            6, STATUS_BLOCKED, "judge_report_unreadable",
            "the judge printed no JSON object",
        )
    return report


# ------------------------------------------------------------------ DB reads


def variant_rows(conn: Any, variant_id: int) -> list[dict[str, Any]]:
    """Every source-identity row of ONE variant, newest first.

    Deliberately a base-table read keyed by a single variant_id.
    `operator_card_product_projection` with a multi-id IN list is the query
    that held MySQL 3308 at 100% for 6272 seconds on 2026-08-22 and again for
    3760 seconds on 2026-08-23; a client timeout does not stop it, only a root
    KILL does. This command never asks that view anything."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT source_code, external_entity_id, variant_id, match_status,"
            " evidence_sha256,"
            " CAST(bind_evidence_json AS CHAR) AS bind_evidence_json, updated_at"
            " FROM catalog_source_identity WHERE variant_id=%s"
            " ORDER BY updated_at DESC",
            (int(variant_id),),
        )
        return list(cursor.fetchall() or [])


def row_owning_external_id(
    conn: Any, source_code: str, external_entity_id: str
) -> dict[str, Any] | None:
    """Who owns this provider id today. The PK says at most one variant can."""

    if not external_entity_id:
        return None
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT source_code, external_entity_id, variant_id, match_status,"
            " evidence_sha256,"
            " CAST(bind_evidence_json AS CHAR) AS bind_evidence_json"
            " FROM catalog_source_identity"
            " WHERE source_code=%s AND external_entity_id=%s",
            (source_code, str(external_entity_id)),
        )
        return cursor.fetchone()


def standing_ruling(
    rows: list[Mapping[str, Any]], source_code: str, external_entity_id: str
) -> tuple[str, str]:
    """The operator ruling this paste would be overturning, and where it lives.

    Own row first, then the newest ruling on any OTHER row of the same variant
    -- the same order `cmd_pc_identity_reverify` uses, because the ruling is
    about the CARD and was written on whichever row existed that day. The
    decision of what counts as a ruling is delegated to
    `rebuild_036.operator_ruling` rather than re-spelled in SQL here, so the
    prefix cannot drift between this command and the lanes that honour it."""

    own = ""
    sibling = ""
    sibling_where = ""
    for row in rows:
        ruling = R.operator_ruling(row.get("bind_evidence_json"))
        if not ruling:
            continue
        same_row = (
            str(row.get("source_code")) == source_code
            and str(row.get("external_entity_id")) == str(external_entity_id)
        )
        if same_row:
            own = ruling
        elif not sibling:
            sibling = ruling
            sibling_where = (
                f"{row.get('source_code')}/{row.get('external_entity_id')}"
            )
    if own:
        return own, f"{source_code}/{external_entity_id}"
    return sibling, sibling_where


# ----------------------------------------------------------------- evidence


def build_evidence(
    *,
    parsed: ParsedUrl,
    identity: Mapping[str, Any] | None,
    capture_path: Path | None,
    capture_sha: str,
    captured_at: str,
    actor: str,
    note: str | None,
    external_entity_id: str,
) -> dict[str, Any]:
    """The same evidence shape the reverify lanes write, plus who pasted it."""

    provider_claims = {
        "tcgCode": str((identity or {}).get("tcg") or ""),
        "cardLanguage": str((identity or {}).get("language") or ""),
        "collectorNumber": str((identity or {}).get("collector") or ""),
        "setCode": "",
        "printingCode": "",
        "parallelCode": str((identity or {}).get("parallel") or ""),
    }
    evidence = {
        "type": R.EVIDENCE_TYPE_PROVIDER_PAGE,
        "sha256": capture_sha,
        "path": (
            capture_path.relative_to(ROOT).as_posix()
            if capture_path is not None and capture_path.is_relative_to(ROOT)
            else (str(capture_path) if capture_path is not None else "")
        ),
        "canonicalUrl": str((identity or {}).get("canonicalUrl") or parsed.url),
        "pageHeading": str((identity or {}).get("heading") or ""),
        "capturedAt": captured_at,
        "generation": "operator_bind_url",
    }
    return {
        "providerClaims": provider_claims,
        "evidence": evidence,
        # NOT one of REJECTION_VERDICT_ACTIONS and no redListed key, so the
        # proposal stays visible to NOT_A_REJECTION_VERDICT_SQL. A paste is
        # not a verdict; the judge below is.
        "action": "operator-bind-url",
        "actor": actor,
        "note": note or "",
        "sourceUrl": parsed.url,
        "externalEntityId": str(external_entity_id),
    }


def ruling_reason(actor: str, text: str, *, now: datetime) -> str:
    """`operator-<actor>-<UTCdate>: <text>` -- the prefix the lanes read."""

    slug = re.sub(r"[^a-z0-9]+", "-", actor.strip().lower()).strip("-") or "operator"
    return (
        f"{R.OPERATOR_RULING_REASON_PREFIX}{slug}-"
        f"{now.strftime('%Y%m%d')}: {text.strip()}"
    )


# ------------------------------------------------------------------- writes


def write_proposal(
    conn: Any,
    *,
    variant_id: int,
    source_code: str,
    external_entity_id: str,
    evidence: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    capture_path: Path | None,
    capture_sha: str,
    captured_at: str,
) -> str:
    """Write the proposal at `manual_review`. Never `exact`, never unguarded.

    Returns "inserted" or "updated". Raises BindStop(EXIT_CHAIN_CHANGED) when
    the guarded statement changed zero rows -- which is the honest reading of
    "the row I looked at is not the row that is there now"."""

    payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    evidence_sha = R.sha256_bytes(R.canonical_json(dict(evidence)))
    product_number = str(evidence.get("providerClaims", {}).get(
        "collectorNumber", ""
    ))[:64]
    with conn.cursor() as cursor:
        if existing is None:
            try:
                cursor.execute(
                    "INSERT INTO catalog_source_identity"
                    " (source_code, external_entity_id, variant_id, match_status,"
                    "  evidence_sha256, source_product_number, bind_evidence_json)"
                    " VALUES (%s,%s,%s,'manual_review',%s,%s,%s)",
                    (
                        source_code, str(external_entity_id), int(variant_id),
                        evidence_sha, product_number, payload,
                    ),
                )
            except Exception as error:  # noqa: BLE001 - narrowed on the message
                text = str(error)
                if "1062" in text or "Duplicate entry" in text:
                    raise BindStop(
                        5, STATUS_CHAIN_CHANGED, "chain_changed_row",
                        f"{CHAIN_CHANGED_MESSAGE} (a row for "
                        f"{source_code}/{external_entity_id} appeared)",
                        exit_code=EXIT_CHAIN_CHANGED,
                    ) from error
                raise
            outcome = "inserted"
        else:
            cursor.execute(
                "UPDATE catalog_source_identity"
                "   SET evidence_sha256=%s, source_product_number=%s,"
                "       bind_evidence_json=%s, updated_at=NOW()"
                " WHERE source_code=%s AND external_entity_id=%s"
                "   AND variant_id=%s"
                # Naming the two statuses this command may touch is what keeps
                # it away from 'exact' and 'conflict' without a second rule to
                # remember, and re-checking them here is what makes rowcount
                # the concurrency answer.
                "   AND match_status IN ('manual_review','rejected')",
                (
                    evidence_sha, product_number, payload,
                    source_code, str(external_entity_id), int(variant_id),
                ),
            )
            if cursor.rowcount == 0:
                raise BindStop(
                    5, STATUS_CHAIN_CHANGED, "chain_changed_row",
                    CHAIN_CHANGED_MESSAGE, exit_code=EXIT_CHAIN_CHANGED,
                )
            outcome = "updated"
        if capture_path is not None:
            cursor.execute(
                "INSERT INTO catalog_provider_capture_receipt (source_code,"
                " external_entity_id, capture_sha256, capture_path, captured_at,"
                " generation_id, parser_version)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE"
                " capture_sha256=VALUES(capture_sha256),"
                " capture_path=VALUES(capture_path),"
                " captured_at=VALUES(captured_at),"
                " generation_id=VALUES(generation_id),"
                " parser_version=VALUES(parser_version)",
                (
                    source_code, str(external_entity_id), capture_sha,
                    str(evidence["evidence"]["path"])[:500],
                    datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=timezone.utc
                    ),
                    "operator_bind_url", "operator_bind_url_v1",
                ),
            )
    return outcome


def write_operator_ruling(
    conn: Any,
    *,
    variant_id: int,
    source_code: str,
    external_entity_id: str,
    reason: str,
    slug: str,
    actor: str,
    overrides: str,
    expected_evidence_sha: str,
    now: datetime,
) -> None:
    """Stamp `operator-…` onto the row, guarded on the state we read.

    Read-merge-write rather than JSON_SET so the rest of the evidence block
    survives verbatim, and `evidence_sha256=%s` in the WHERE is the optimistic
    lock: if the chain rewrote the row since the read, this changes zero rows
    and says so instead of clobbering it."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT CAST(bind_evidence_json AS CHAR) AS bind_evidence_json"
            " FROM catalog_source_identity"
            " WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s"
            "   AND evidence_sha256=%s",
            (source_code, str(external_entity_id), int(variant_id),
             expected_evidence_sha),
        )
        row = cursor.fetchone()
        if not row:
            raise BindStop(
                8, STATUS_CHAIN_CHANGED, "chain_changed_row",
                CHAIN_CHANGED_MESSAGE, exit_code=EXIT_CHAIN_CHANGED,
            )
        try:
            block = json.loads(row.get("bind_evidence_json") or "{}")
        except ValueError:
            block = {}
        if not isinstance(block, dict):
            block = {}
        block["reason"] = reason
        block["operatorRuling"] = {
            "actor": actor,
            "slug": slug,
            "ruledAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "overrides": overrides,
        }
        payload = json.dumps(block, ensure_ascii=False, sort_keys=True)
        new_sha = R.sha256_bytes(R.canonical_json(block))
        cursor.execute(
            "UPDATE catalog_source_identity"
            "   SET bind_evidence_json=%s, evidence_sha256=%s, updated_at=NOW()"
            " WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s"
            "   AND evidence_sha256=%s",
            (payload, new_sha, source_code, str(external_entity_id),
             int(variant_id), expected_evidence_sha),
        )
        if cursor.rowcount == 0:
            raise BindStop(
                8, STATUS_CHAIN_CHANGED, "chain_changed_row",
                CHAIN_CHANGED_MESSAGE, exit_code=EXIT_CHAIN_CHANGED,
            )


def current_match_status(
    conn: Any, source_code: str, external_entity_id: str, variant_id: int
) -> str:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT match_status FROM catalog_source_identity"
            " WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s",
            (source_code, str(external_entity_id), int(variant_id)),
        )
        row = cursor.fetchone()
    return str((row or {}).get("match_status") or "")


# ------------------------------------------------------------------ capture


def pc_capture_matching_url(
    pages_dir: Path, variant_id: int, canonical_path: str
) -> tuple[Path | None, str, str]:
    """A capture already on disk that IS the pasted page.

    Matched on the page's OWN canonical path, not on a product id, because the
    product id is exactly what the paste does not carry. Returns
    (path, html, product_id)."""

    if not canonical_path or not pages_dir.is_dir():
        return None, "", ""
    for path in sorted(pages_dir.glob(f"{variant_id}_*.html")):
        html = path.read_text(encoding="utf-8", errors="replace")
        identity, _ = R._pc_page_identity(html)
        if identity is None:
            continue
        got = urlsplit(str(identity.get("canonicalUrl") or "")).path
        if got.rstrip("/").lower() == canonical_path:
            return path, html, R._pc_page_product_id(html)
    return None, "", ""


def captured_at_of(path: Path) -> str:
    return datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


# -------------------------------------------------------------- the ten steps


def apply_one(
    *,
    variant_id: int,
    url: str,
    actor: str,
    note: str | None = None,
    write: bool = False,
    operator_ruling_slug: str | None = None,
    freeze: bool = False,
    conn: Any = None,
    pages_dir: Path | None = None,
    map_path: Path | None = None,
    credentials_env: Path | None = None,
    allow_fetch: bool = True,
    receipts_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The single entry point. Returns a verdict document; never raises for a
    refusal (the refusal IS the answer) and never reports success on a write
    that changed no rows."""

    now = now or datetime.now(timezone.utc)
    variant_id = int(variant_id)
    steps: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "bindUrl": True,
        "variantId": variant_id,
        "url": url,
        "actor": actor,
        "note": note or "",
        "write": bool(write),
        "allowFetch": bool(allow_fetch),
        "asOf": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "steps": steps,
    }

    def record(step: int, status: str, detail: str = "", **extra: Any) -> None:
        name = dict(STEP_NAMES).get(step, "usage")
        entry: dict[str, Any] = {
            "step": step, "name": name, "status": status, "detail": detail
        }
        entry.update(extra)
        steps.append(entry)

    def finish(verdict: str, exit_code: int, gate: str = "") -> dict[str, Any]:
        report["verdict"] = verdict
        report["gate"] = gate
        report["exitCode"] = exit_code
        for number, name in STEP_NAMES:
            if not any(entry["step"] == number for entry in steps):
                steps.append({
                    "step": number, "name": name,
                    "status": STATUS_SKIPPED, "detail": "",
                })
        steps.sort(key=lambda entry: entry["step"])
        if receipts_dir is not None:
            report["receiptPath"] = write_receipt(report, receipts_dir, now=now)
        return report

    try:
        # --- usage, before anything is read or fetched ------------------
        if operator_ruling_slug is not None:
            if not str(operator_ruling_slug).startswith(
                R.OPERATOR_RULING_REASON_PREFIX
            ):
                raise BindStop(
                    0, STATUS_REFUSED, "operator_ruling_prefix_required",
                    f"--operator-ruling must start with "
                    f"'{R.OPERATOR_RULING_REASON_PREFIX}'; got "
                    f"{operator_ruling_slug!r}",
                    exit_code=EXIT_USAGE,
                )
            if not (note or "").strip():
                raise BindStop(
                    0, STATUS_REFUSED, "operator_ruling_needs_note",
                    "--operator-ruling writes a reason every lane will honour "
                    "forever; --note is the reason and it is required",
                    exit_code=EXIT_USAGE,
                )

        parsed = parse_url(url)
        report.update(parsed.as_dict())

        # --- 1. red list ------------------------------------------------
        red = red_listed_variants()
        if variant_id in red:
            raise BindStop(
                1, STATUS_REFUSED, "red_listed",
                "the 034 audit sheet refuses this card. bind-url cannot "
                "override it -- neither --write nor --operator-ruling -- the "
                "only mechanism is data/editorial/red-sheet-036-release.json",
            )
        record(1, STATUS_OK, f"not red listed (red list holds {len(red)})")

        if conn is None:
            raise BindStop(
                2, STATUS_BLOCKED, "no_database_connection",
                "apply_one needs an open connection to read the row it is "
                "about to propose against",
            )

        rows = variant_rows(conn, variant_id)

        # --- 2. standing operator ruling --------------------------------
        ruling, ruling_where = standing_ruling(
            rows, parsed.source_code, parsed.external_entity_id
        )
        if ruling and operator_ruling_slug is None:
            raise BindStop(
                2, STATUS_REFUSED, "standing_operator_ruling",
                f"{ruling_where} carries: {ruling}\n"
                "pass --operator-ruling operator-<slug> --note '<why>' to "
                "supersede it, deliberately and on the record",
            )
        record(
            2, STATUS_OK,
            f"superseding: {ruling}" if ruling else "no standing operator ruling",
            standingRuling=ruling, standingRulingRow=ruling_where,
        )

        # --- 3. capture (reuse, else fetch, else say it is blocked) -----
        identity: dict[str, Any] | None = None
        capture_path: Path | None = None
        capture_html = ""
        external_entity_id = parsed.external_entity_id
        if parsed.source_code == SOURCE_PRICECHARTING:
            pc_dir = pages_dir or (
                ROOT / "data" / "private" / "pricecharting_session" / "html"
                / PC_PAGES_DIRNAME
            )
            capture_path, capture_html, page_pid = pc_capture_matching_url(
                pc_dir, variant_id, parsed.canonical_path
            )
            if capture_path is not None:
                external_entity_id = page_pid
                record(
                    3, STATUS_OK,
                    f"reused the capture that already IS this page: "
                    f"{capture_path.name}",
                    capturePath=str(capture_path), productId=page_pid,
                )
            elif not allow_fetch:
                raise BindStop(
                    3, STATUS_BLOCKED, "fresh_page_required",
                    f"no capture under {pc_dir} carries canonical "
                    f"{parsed.canonical_path}; proving this paste needs a fresh "
                    f"fetch through the headed CDP {CDP_PORT} session, which "
                    "this run is not allowed to make",
                )
            else:
                capture_path, capture_html, external_entity_id = fetch_pricecharting(
                    parsed.url, pc_dir, variant_id
                )
                record(
                    3, STATUS_OK, f"fetched through CDP {CDP_PORT}",
                    capturePath=str(capture_path),
                    productId=external_entity_id,
                )
        else:
            if not allow_fetch:
                raise BindStop(
                    3, STATUS_BLOCKED, "fresh_master_required",
                    f"proving snkrdunk item {external_entity_id} needs a live "
                    "SNKRDUNK master fetch, which this run is not allowed to "
                    "make",
                )
            harvest = ROOT / "data" / "runtime" / "operator" / "bind-url" / (
                f"snk-{now.strftime('%Y%m%dT%H%M%SZ')}-{external_entity_id}.jsonl"
            )
            payload = fetch_snkrdunk(
                external_entity_id, harvest,
                run_id=f"bind_url_{now.strftime('%Y%m%dT%H%M%SZ')}",
            )
            capture_path = harvest
            capture_html = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            record(
                3, STATUS_OK, f"harvested snkrdunk master {external_entity_id}",
                capturePath=str(capture_path),
            )
        report["externalEntityId"] = external_entity_id

        # --- 4. prove the page is the product ---------------------------
        if parsed.source_code == SOURCE_PRICECHARTING:
            identity, why = R._pc_page_identity(capture_html)
            if identity is None:
                raise BindStop(4, STATUS_REFUSED, "page_parse_failed", why)
            page_product = R._pc_page_product_id(capture_html)
            if not page_product or page_product != str(external_entity_id):
                raise BindStop(
                    4, STATUS_REFUSED, "page_product_mismatch",
                    f"the page says product {page_product or '?'} and this bind "
                    f"is for {external_entity_id}",
                )
            # The judge finds captures by `{variant}_{pid}.html`. A proposal
            # backed by a file the judge cannot reach is a proposal it will
            # hold `page_missing`, so refuse now rather than write a row that
            # is already doomed.
            pc_dir = capture_path.parent if capture_path else None
            visible = R.pc_capture_for_product(
                pc_dir, variant_id, external_entity_id
            ) if pc_dir else None
            if visible is None or R._pc_page_product_id(
                visible.read_text(encoding="utf-8", errors="replace")
            ) != str(external_entity_id):
                raise BindStop(
                    4, STATUS_REFUSED, "capture_not_visible_to_judge",
                    f"pc_capture_for_product({variant_id}, {external_entity_id}) "
                    f"does not reach {capture_path}",
                )
            capture_sha = R.sha256_file(capture_path)
            captured_at = captured_at_of(capture_path)
            record(
                4, STATUS_OK,
                f"page product id == bound id == {external_entity_id}; "
                f"heading {identity['heading']!r}",
            )
        else:
            capture_sha = R.sha256_file(capture_path) if capture_path else ""
            captured_at = captured_at_of(capture_path) if capture_path else (
                now.strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            record(4, STATUS_OK, f"snkrdunk master {external_entity_id} harvested")

        # --- one product, one variant -----------------------------------
        owner = row_owning_external_id(
            conn, parsed.source_code, external_entity_id
        )
        if owner is not None and int(owner["variant_id"]) != variant_id:
            raise BindStop(
                5, STATUS_REFUSED, "external_id_owned_by_another_variant",
                f"{parsed.source_code}/{external_entity_id} already belongs to "
                f"variant {owner['variant_id']} at match_status "
                f"{owner['match_status']}. The primary key is "
                "(source_code, external_entity_id): one product cannot be two "
                "cards. Release that row first, on purpose.",
            )
        existing = owner if owner is not None else None
        existing_status = str((existing or {}).get("match_status") or "")

        if existing_status == "exact":
            record(
                5, STATUS_SKIPPED,
                f"variant {variant_id} is already bound exact to "
                f"{parsed.source_code}/{external_entity_id}; bind-url never "
                "rewrites an exact row",
            )
            record(6, STATUS_SKIPPED, "nothing to judge: the row is already exact")
            record(7, STATUS_OK, "already exact")
            record(8, STATUS_SKIPPED, "")
            if freeze and write:
                _freeze(variant_id, parsed.source_code, actor, note)
                record(9, STATUS_OK, "accept_binding recorded a source freeze")
            else:
                record(9, STATUS_SKIPPED, "")
            record(10, STATUS_OK, "no write attempted")
            return finish("already_exact", EXIT_OK)

        if existing is not None and R.rejection_is_verdict(
            existing.get("bind_evidence_json")
        ) and operator_ruling_slug is None:
            raise BindStop(
                5, STATUS_REFUSED, "reasoned_rejection",
                "this row carries a rejection somebody reasoned about "
                f"(status {existing_status}). Reopening it needs "
                "--operator-ruling operator-<slug> --note '<why>'",
            )

        evidence = build_evidence(
            parsed=parsed, identity=identity, capture_path=capture_path,
            capture_sha=capture_sha, captured_at=captured_at, actor=actor,
            note=note, external_entity_id=external_entity_id,
        )

        # --- 5. proposal only. Never `exact`. ---------------------------
        if not write:
            record(
                5, STATUS_SKIPPED,
                "dry run: this would "
                + ("UPDATE" if existing is not None else "INSERT")
                + f" catalog_source_identity {parsed.source_code}/"
                f"{external_entity_id} at match_status='manual_review'",
                wouldWrite=True,
            )
            record(
                6, STATUS_SKIPPED,
                "dry run: the judge sees the row as it stands today, not the "
                "proposal this run did not write",
            )
        else:
            outcome = write_proposal(
                conn, variant_id=variant_id, source_code=parsed.source_code,
                external_entity_id=external_entity_id, evidence=evidence,
                existing=existing, capture_path=capture_path,
                capture_sha=capture_sha, captured_at=captured_at,
            )
            conn.commit()
            record(
                5, STATUS_OK,
                f"{outcome} the proposal at match_status='manual_review'",
            )

        # --- 6/7. the judge speaks, and it is quoted ---------------------
        judged = run_judge(
            parsed.source_code, variant_id, write=bool(write),
            pages_dir=(capture_path.parent if (
                capture_path is not None
                and parsed.source_code == SOURCE_PRICECHARTING
            ) else pages_dir),
            map_path=map_path, credentials_env=credentials_env,
        )
        report["judge"] = {
            "counts": judged.get("counts"),
            "promotable": judged.get("promotable"),
        }
        held = [
            entry for entry in (judged.get("held") or [])
            if int(entry.get("variant_id") or 0) == variant_id
        ]
        promoted = [
            entry for entry in (judged.get("promotedSample") or [])
            if int(entry.get("variant_id") or 0) == variant_id
        ]
        record(6, STATUS_OK, f"held={len(held)} promotable={len(promoted)}")

        gate = ""
        if held:
            gate = str(held[0].get("reason") or "")
            verdict_text = f"{gate}: {held[0].get('detail') or ''}".strip(": ")
            record(7, STATUS_REFUSED, verdict_text, judgeHeld=held[0])
        elif promoted:
            record(7, STATUS_OK, "the judge proved this binding", judgeHeld=None)
        else:
            record(
                7, STATUS_SKIPPED,
                "the judge did not consider this row (it is not in a status "
                "the reverify lane selects)",
            )

        # --- 8. the one place a human outranks the judge -----------------
        if operator_ruling_slug is not None and write:
            judge_refusal = (
                f"{held[0].get('reason') or ''}: {held[0].get('detail') or ''}"
                .strip(": ")
                if held else ""
            )
            overrides = "; ".join(
                filter(None, [ruling, judge_refusal])
            ) or "no standing refusal"
            print(
                "operator ruling overrides, in full:\n  "
                + (ruling or "(no prior operator ruling)")
                + "\n  judge: "
                + (json.dumps(held[0], ensure_ascii=False) if held else "(no hold)")
            )
            reason = ruling_reason(actor, note or "", now=now)
            expected = current_evidence_sha(
                conn, parsed.source_code, external_entity_id, variant_id
            )
            write_operator_ruling(
                conn, variant_id=variant_id, source_code=parsed.source_code,
                external_entity_id=external_entity_id, reason=reason,
                slug=str(operator_ruling_slug), actor=actor,
                overrides=overrides, expected_evidence_sha=expected, now=now,
            )
            conn.commit()
            record(8, STATUS_OK, reason, overrides=overrides)
        elif operator_ruling_slug is not None:
            record(
                8, STATUS_SKIPPED,
                "dry run: this would stamp "
                + ruling_reason(actor, note or "", now=now),
            )
        else:
            record(8, STATUS_SKIPPED, "")

        # --- 9/10. freeze, and the row re-check --------------------------
        final_status = (
            current_match_status(
                conn, parsed.source_code, external_entity_id, variant_id
            ) if write else existing_status
        )
        report["matchStatus"] = final_status
        if promoted and write and final_status != "exact":
            # The judge said it was promotable and the row is still not exact:
            # its guarded UPDATE changed zero rows. Say so; never report a
            # success we cannot see in the table.
            record(
                10, STATUS_CHAIN_CHANGED,
                f"{CHAIN_CHANGED_MESSAGE} (the judge proved "
                f"{external_entity_id} but the row reads {final_status!r})",
            )
            record(9, STATUS_SKIPPED, "not frozen: the promotion did not land")
            return finish("chain_changed_row", EXIT_CHAIN_CHANGED, gate)
        record(10, STATUS_OK, f"row reads match_status={final_status!r}")

        if freeze and write and final_status == "exact":
            _freeze(variant_id, parsed.source_code, actor, note)
            record(9, STATUS_OK, "accept_binding recorded a source freeze")
        elif freeze:
            record(
                9, STATUS_SKIPPED,
                f"--freeze needs an exact row; this one reads {final_status!r}",
            )
        else:
            record(9, STATUS_SKIPPED, "")

        if held:
            return finish("held_by_judge", EXIT_REFUSED, gate)
        if promoted and write:
            return finish("bound_exact", EXIT_OK)
        if promoted:
            return finish("would_promote", EXIT_OK)
        return finish("no_change", EXIT_OK, gate)

    except BindStop as stop:
        record(stop.step or 0, stop.status, stop.detail or stop.verdict)
        return finish(stop.verdict, stop.exit_code, stop.verdict)


def current_evidence_sha(
    conn: Any, source_code: str, external_entity_id: str, variant_id: int
) -> str:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT evidence_sha256 FROM catalog_source_identity"
            " WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s",
            (source_code, str(external_entity_id), int(variant_id)),
        )
        row = cursor.fetchone()
    return str((row or {}).get("evidence_sha256") or "")


def _freeze(variant_id: int, source_code: str, actor: str, note: str | None) -> None:
    """Only reachable once the row is exact, which is why it works at all:
    accept_binding raises SystemExit on a variant with no row for the source."""

    import operator_control

    operator_control.accept_binding(
        variant_id=int(variant_id), freeze_kind="source",
        source_code=source_code, actor=actor, note=note,
    )


# ------------------------------------------------------------------ receipts


def write_receipt(
    report: Mapping[str, Any], receipts_dir: Path, *, now: datetime
) -> str:
    receipts_dir.mkdir(parents=True, exist_ok=True)
    path = receipts_dir / (
        f"{now.strftime('%Y%m%dT%H%M%SZ')}-v{report.get('variantId')}"
        f"-{report.get('sourceCode') or 'unknown'}.json"
    )
    path.write_bytes(R.canonical_json(dict(report)))
    return str(path)


# ---------------------------------------------------------------------- CLI


def inbox_items(inbox: Path) -> list[tuple[Path, dict[str, Any]]]:
    items: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(inbox.glob("*.json")):
        items.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operator_bind",
        description="Bind a pasted provider URL to a variant, fail-closed.",
    )
    parser.add_argument("--variant-id", type=int)
    parser.add_argument("--url")
    parser.add_argument("--actor", default="daddy")
    parser.add_argument("--note")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--operator-ruling", dest="operator_ruling")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--from-inbox", dest="from_inbox", type=Path)
    parser.add_argument("--pages-dir", dest="pages_dir", type=Path)
    parser.add_argument("--map", dest="map_path", type=Path)
    parser.add_argument("--credentials-env", dest="credentials_env", type=Path)
    parser.add_argument("--receipts-dir", dest="receipts_dir", type=Path)
    parser.add_argument(
        "--no-fetch", dest="allow_fetch", action="store_false",
        help="never open a browser or a socket: any step that would need a "
             "fresh page is reported as blocked instead of fetched",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    receipts_dir = args.receipts_dir or (
        ROOT / "data" / "runtime" / "operator" / "bind-url"
    )
    conn = R.connect(args.credentials_env or R.DAILY_CREDENTIALS_ENV)
    reports: list[dict[str, Any]] = []
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION max_execution_time=60000")
        if args.from_inbox:
            done = args.from_inbox / "done"
            for path, item in inbox_items(args.from_inbox):
                report = apply_one(
                    variant_id=int(item["variantId"]), url=str(item["url"]),
                    actor=str(item.get("actor") or args.actor),
                    note=item.get("note"), write=args.write,
                    operator_ruling_slug=args.operator_ruling,
                    freeze=args.freeze, conn=conn, pages_dir=args.pages_dir,
                    map_path=args.map_path,
                    credentials_env=args.credentials_env,
                    allow_fetch=args.allow_fetch, receipts_dir=receipts_dir,
                )
                reports.append(report)
                if args.write:
                    done.mkdir(parents=True, exist_ok=True)
                    path.replace(done / path.name)
        else:
            if not args.variant_id or not args.url:
                raise SystemExit("bind-url needs --variant-id and --url")
            reports.append(apply_one(
                variant_id=args.variant_id, url=args.url, actor=args.actor,
                note=args.note, write=args.write,
                operator_ruling_slug=args.operator_ruling, freeze=args.freeze,
                conn=conn, pages_dir=args.pages_dir, map_path=args.map_path,
                credentials_env=args.credentials_env,
                allow_fetch=args.allow_fetch, receipts_dir=receipts_dir,
            ))
    finally:
        conn.close()

    for report in reports:
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    # The receipt path prints last, after the verdict, so the last line of a
    # long run is where to look rather than the first thing scrolled past.
    for report in reports:
        if report.get("receiptPath"):
            print(f"receipt: {report['receiptPath']}")
    return max((int(report.get("exitCode") or 0) for report in reports), default=0)


if __name__ == "__main__":
    raise SystemExit(main())
