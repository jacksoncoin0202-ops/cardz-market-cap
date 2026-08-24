#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Morning identity brief: a pure reader and renderer, shared by three callers.

The V2 chain stage (`identity-brief`), `operator_control identity-status` and
`scripts/notify_hermes.py identity-brief` all have to say the SAME thing about
the same morning.  When each of them assembled its own numbers the three
disagreed within a day, and the disagreement is what made the report unusable:
a number nobody can reproduce is worse than no number.  So the assembly lives
here once, and the three entry points differ only in where they send the text.

Everything this module knows is READ.  It opens no writes to MySQL, moves no
binding, and settles no ruling; the only file it may write is its own full-list
artifact, and only when a caller asks for one.

Eight rules are load bearing, each with a test in
`scripts/test_identity_brief_message.py`:

1.  The four headline numbers PARTITION the population at PSA10 pop >= 1000.
    A card that belongs to none of them is a card the report forgot, and the
    forgetting is silent; the partition is asserted, not hoped for.
2.  The 5-new / 5-needs-you caps are STRUCTURAL -- applied to the rows before
    a single character is rendered.  Telegram truncates at 4096 and today's
    backlog is 156 cards; a rendered string cut at 4096 loses whichever half
    of a card's command survived, and a half command is a trap.
3.  DECIDED (red-listed, operator-ruled, aliased, demoted) and CHAIN-WILL-RETRY
    rows are COUNTED and never listed as work for the owner.  Re-asking a
    settled question is how a ruling gets quietly overturned.
4.  Every listed hold carries the DATE of the artifact its reason came from, so
    "the browser lane did not run today" is readable as stale rather than read
    as fresh evidence.
5.  Line 2 states whether the identity phase was closed by IDENTITY_CUTOFF
    (`daily_chain_v2.py`), because otherwise yesterday's counts read as this
    morning's lane output.
6.  Zero prints as zero.  A silent morning and a broken stage look identical
    when empty blocks are dropped.
7.  Dedupe is keyed on sha256(variant_id|hold_reason) under its own state key
    `identity_brief_seen`, with a 14-day refloat.  The reason is IN the key, so
    a changed reason refloats by construction rather than by remembering to.
8.  EVERY reverify hold is either listed or counted.  An unanswered hold whose
    reason asks a question (ADJUDICATION_HOLD_REASONS) is read BEFORE the
    product_ready / exact_n short-circuits and becomes 等你綁; every other hold
    is counted by reason on one summary line carrying its artifact and date.
    On 2026-08-23 all 226 holds were swallowed -- needsYou=0 was the branch
    order, not an observation -- so `collect` now ABORTS on a swallowed one.

`seen_manual_review` (scripts/notify_hermes.py) is NOT reused: that key belongs
to the legacy 037 digest and sharing it would make either report silence the
other.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]

# The population this report is about.  Same floor as rebuild_036's discovery
# ratchet (`DISCOVERY_GAP_POP`) on purpose: a report drawn over a different
# population than the gate cannot explain the gate.
POPULATION_FLOOR = 1000

# Journal event type.  It is delivered through `deliver_events()` ->
# notify_hermes.send_message(), which keeps the HTML intact.  It must never be
# added to daily_chain_v2.ALWAYS_ALERT_EVENTS: that path renders through
# `_alert_text` + html.escape and would print every <a href> as literal markup.
BRIEF_EVENT_TYPE = "identity.brief"

# Own dedupe key.  See module docstring.
SEEN_STATE_KEY = "identity_brief_seen"
REFLOAT_DAYS = 14
# Both entry points (the V2 `brief` stage and `notify_hermes identity-brief`)
# render the same message, so they share one dedupe record inside the notify
# state document -- under this key only, never under the legacy 037 digest key.
NOTIFY_STATE_PATH = ROOT / "data" / "runtime" / "notify" / "hermes_notify_state.json"

# Structural caps (rule 2).
MAX_NEW_CARDS = 5
MAX_NEEDS_YOU = 5
# notify_hermes.send_message() truncates above 3900 characters.  Staying under
# that is the whole point of the structural cap, so the budget is checked here
# where a row can still be dropped as a ROW.
MESSAGE_BUDGET = 3900

# Chain lanes only reach English (PriceCharting, browser) and Japanese
# (SNKRDUNK, http).  `_lane_for_language` routes every non-`en` language to the
# http lane, so a zhCN card is queued against a provider that does not carry
# it: the chain will never judge it and the brief may not promise a retry.
CHAIN_JUDGEABLE_LANGUAGES = frozenset({"en", "ja"})

# daily_discovery_activation.DAILY_PROVIDER_LIMIT.  Copied rather than imported
# so the renderer stays free of rebuild_036's import cost; the test asserts the
# two agree, which is what stops the copy from drifting.
LANE_DAILY_BUDGET = 40

# operator_control subcommands the brief hands out as copy-paste lines.
#
# The NO half shipped as `operator-rule`; the plan's C.5 sketch called it
# `rule`.  They are one command -- write or supersede the operator ruling on a
# binding row -- and the name that goes in the message is the name argparse
# will accept, verified at render time by `operator_commands_available`.  A
# subcommand that is not declared yet is MARKED, never pasted as if it worked.
BIND_COMMAND = "bind-url"
RULE_COMMAND = "operator-rule"

DEFAULT_ARTIFACT_DIR = ROOT / "data" / "runtime" / "rebuild-036"
DEFAULT_INTAKE_DIR = ROOT / "data" / "runtime" / "daily-chain-v2"
DEFAULT_CENSUS_PATH = ROOT / "data" / "private" / "gemrate_brute" / "psa10_1000_plus.jsonl"
DEFAULT_BASELINE_PATH = ROOT / "pipelines" / "discovery_coverage_baseline.json"
DEFAULT_OPERATOR_CONTROL = ROOT / "pipelines" / "operator_control.py"

# Headline buckets (rule 1): these four partition the population.
HEADLINE_LIVE = "live"
HEADLINE_IDENTITY_NOT_LIVE = "identity_not_live"
HEADLINE_NEEDS_YOU = "needs_you"
HEADLINE_NOT_YOURS = "not_yours"
HEADLINES = (HEADLINE_LIVE, HEADLINE_IDENTITY_NOT_LIVE, HEADLINE_NEEDS_YOU, HEADLINE_NOT_YOURS)

# Sub-buckets of HEADLINE_NOT_YOURS, reported as the three numbers in block 5.
DETAIL_CHAIN_WILL_RETRY = "chain_will_retry"
DETAIL_DECIDED = "decided"
DETAIL_UNJUDGEABLE = "unjudgeable"

# Sub-bucket of HEADLINE_NEEDS_YOU: a reverify hold nobody has answered.
DETAIL_NEEDS_ADJUDICATION = "needs_adjudication"

# Hold reasons that are a QUESTION FOR THE OWNER.  rebuild_036's `hold()`
# (:9323) only appends to the run's artifact -- it writes no ledger blocker and
# moves no next_due -- so the row still reads active_exact / chain-owned and
# NOTHING reschedules it.  Until the ledger side is fixed the brief may not
# count these as "chain 自己再試"; they are the owner's work, and they reach him
# only because the hold is read BEFORE the product_ready / exact_n
# short-circuits: every held variant already carries exact_n>=1, so a hold
# checked after them can never fire, which is how 27 print-signature /
# product-mismatch questions read as green on 2026-08-23 and needsYou=0 was the
# branch order rather than an observation.
ADJUDICATION_HOLD_REASONS = frozenset({
    "print_signature_mismatch", "product_mismatch", "parallel_soft_mismatch",
})

# Hold reasons that are the chain REFUSING a wrong candidate (SNK ja-vs-en
# mirrors, dead pages, a map pointing elsewhere).  A correct refusal is not a
# question, so these keep whatever bucket the row already had -- but they are
# still SAID, as one summary line carrying the artifact and its date, because
# 211 silently swallowed holds is what made the last report unusable.  The set
# is the KNOWN half only: `collect` counts every non-adjudication reason, and
# marks the ones neither set names, so a new reason cannot vanish either.
LANE_OBJECTION_HOLD_REASONS = frozenset({
    "hard_conflict", "map_product_mismatch", "page_missing",
})

# rebuild_036.OPERATOR_RULING_REASON_PREFIX.  The prefix is the ONLY thing that
# makes a reason a ruling; "zero-20260814: ..." is a note somebody left, and a
# report that treats it as a ruling silently stops asking about a live card.
OPERATOR_RULING_REASON_PREFIX = "operator-"

GEMRATE_CARD_URL = "https://www.gemrate.com/card/{gemrate_id}"
SNKRDUNK_ITEM_URL = "https://snkrdunk.com/en/trading-cards/{external_id}"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    """MySQL hands back naive UTC datetimes; artifacts hand back ISO strings."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _day(value: Any) -> str:
    moment = _as_utc(value)
    return moment.strftime("%Y-%m-%d") if moment else "?"


def _obj(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def hold_key(variant_id: Any, hold_reason: Any) -> str:
    """Dedupe identity (rule 7): the reason is part of the key on purpose."""

    return hashlib.sha256(f"{variant_id}|{hold_reason}".encode("utf-8")).hexdigest()


def ruling_text(value: Any) -> str:
    """The operator ruling carried by a row, or "".

    The SQL hands this over as either the bare reason or
    "<source_code>: <reason>" (VARIANT_OPERATOR_RULING_SQL's shape).  Both are
    accepted, and in both the reason itself must carry the operator- prefix --
    the check is repeated here rather than trusted from the query so that a
    caller passing rows from anywhere else cannot skip it.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(OPERATOR_RULING_REASON_PREFIX):
        return text
    _, separator, reason = text.partition(": ")
    return text if separator and reason.startswith(OPERATOR_RULING_REASON_PREFIX) else ""


def operator_commands_available(source_text: str) -> dict[str, bool]:
    """Which copy-paste subcommands operator_control.py actually declares.

    The brief prints commands the owner is meant to paste at 08:00.  Printing
    one that does not exist yet costs a confused morning and teaches the reader
    to distrust the whole message, so the renderer marks an absent subcommand
    instead of pretending.  Source text, not an import: importing
    operator_control drags in rebuild_036 and a credentials file.
    """

    return {
        name: bool(re.search(r'add_parser\(\s*["\']' + re.escape(name) + r'["\']', source_text))
        for name in (BIND_COMMAND, RULE_COMMAND)
    }


def _read_operator_commands(path: Path | None) -> dict[str, bool]:
    try:
        text = (path or DEFAULT_OPERATOR_CONTROL).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {BIND_COMMAND: False, RULE_COMMAND: False}
    return operator_commands_available(text)


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
def pending_adjudication_hold(
    row: Mapping[str, Any],
    hold: Mapping[str, Any] | None,
    *,
    red_listed: Iterable[int] = (),
) -> dict[str, Any] | None:
    """The reverify hold that makes this row a question for the owner, or None.

    A hold is a question only while nobody has answered it, and the answer is
    an operator ruling, a red-list entry, an alias or a demotion (rule 3:
    re-asking a settled question is how a ruling gets quietly overturned).
    Nothing else can serve as the answer -- `hold()` writes no ledger row -- so
    those four ARE the adjudication signal, and they are checked here rather
    than by branch order so that `classify` may read the hold before its
    product_ready / exact_n short-circuits without reopening a decided card.

    Deliberately independent of `classify`: `collect` asks the same question
    again to ASSERT that no such row was swallowed by an earlier branch.
    """

    if not hold:
        return None
    if str(hold.get("reason") or "") not in ADJUDICATION_HOLD_REASONS:
        return None
    detail = _obj(row.get("detail_json") if "detail_json" in row else row.get("detail"))
    if detail.get("aliasOf") or detail.get("demotedReason"):
        return None
    if ruling_text(row.get("ruling")):
        return None
    variant_id = row.get("variant_id")
    if variant_id is not None and int(variant_id) in set(int(v) for v in red_listed):
        return None
    return dict(hold)


def classify(
    row: Mapping[str, Any],
    *,
    now: datetime,
    red_listed: Iterable[int] = (),
    holds: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Place one pop>=1000 member in exactly one headline bucket.

    Order is the argument.  `aliasOf` / `demotedReason` come before the binding
    check because a merged or demoted card is settled whatever its rows say,
    and `detail_json.pendingReasons` is never consulted: rebuild_036 forces it
    to empty for `non_qualified`, so reading it re-opens decisions the owner
    already made (plan risk 4).

    An unanswered hold is read BEFORE `product_ready` and before `exact_n > 0`
    for the reason in ADJUDICATION_HOLD_REASONS, and defers to a ruling / a
    red-list entry / an alias / a demotion through
    `pending_adjudication_hold`, so a decided card stays decided even when a
    lane holds it.
    """

    red = set(int(v) for v in red_listed)
    detail = _obj(row.get("detail_json") if "detail_json" in row else row.get("detail"))
    cohort = str(row.get("cohort") or "")
    exact_n = int(row.get("exact_n") or 0)
    variant_id = row.get("variant_id")
    ruling = ruling_text(row.get("ruling"))
    language = str(row.get("card_language") or "").strip()

    held = pending_adjudication_hold(
        row,
        None if (variant_id is None or not holds) else holds.get(int(variant_id)),
        red_listed=red,
    )
    if held is not None:
        return {"headline": HEADLINE_NEEDS_YOU, "detail": DETAIL_NEEDS_ADJUDICATION,
                "reasonCode": str(held.get("reason") or "hold"),
                "reasonText": f'{held.get("lane") or "?"} lane'
                              f' {held.get("artifactDay") or "?"} hold 咗，冇人裁決過：'
                              f'{held.get("detail") or "冇 detail"}',
                "ruled": False}

    if cohort == "product_ready":
        return {"headline": HEADLINE_LIVE, "detail": "live", "reasonCode": "product_ready",
                "reasonText": "已經出街", "ruled": bool(ruling)}

    alias_of = detail.get("aliasOf")
    if alias_of:
        return {"headline": HEADLINE_NOT_YOURS, "detail": DETAIL_DECIDED, "reasonCode": "alias_of",
                "reasonText": f"已合併去 {alias_of}", "ruled": bool(ruling)}

    demoted = detail.get("demotedReason")
    if demoted:
        return {"headline": HEADLINE_NOT_YOURS, "detail": DETAIL_DECIDED, "reasonCode": "demoted",
                "reasonText": str(demoted), "ruled": bool(ruling)}

    if exact_n > 0:
        return {"headline": HEADLINE_IDENTITY_NOT_LIVE, "detail": "identity_not_live",
                "reasonCode": f"cohort:{cohort or '?'}",
                "reasonText": "有 exact 身份，cohort 未升到 product_ready",
                "ruled": bool(ruling)}

    if variant_id is None:
        return {"headline": HEADLINE_NEEDS_YOU, "detail": "needs_intake",
                "reasonCode": "no_catalog_variant",
                "reasonText": "冇 catalog variant，要行 identity-intake", "ruled": False}

    if int(variant_id) in red:
        return {"headline": HEADLINE_NOT_YOURS, "detail": DETAIL_DECIDED, "reasonCode": "red_listed",
                "reasonText": "034 audit sheet 紅卡，未 release", "ruled": bool(ruling)}

    if ruling:
        return {"headline": HEADLINE_NOT_YOURS, "detail": DETAIL_DECIDED, "reasonCode": "operator_ruled",
                "reasonText": ruling, "ruled": True}

    if language and language not in CHAIN_JUDGEABLE_LANGUAGES:
        return {"headline": HEADLINE_NOT_YOURS, "detail": DETAIL_UNJUDGEABLE,
                "reasonCode": f"language:{language}",
                "reasonText": f"{language}：冇 provider lane 覆蓋，chain 永遠判唔到，只有你綁得到",
                "ruled": False}

    quarantine = _as_utc(row.get("quarantine_until"))
    if quarantine and quarantine > now:
        return {"headline": HEADLINE_NOT_YOURS, "detail": DETAIL_CHAIN_WILL_RETRY,
                "reasonCode": "quarantined",
                "reasonText": f"quarantine 到 {_day(quarantine)}", "ruled": False}

    next_due = _as_utc(row.get("next_due_at"))
    if next_due and next_due > now:
        return {"headline": HEADLINE_NOT_YOURS, "detail": DETAIL_CHAIN_WILL_RETRY,
                "reasonCode": "next_due",
                "reasonText": f"chain 排咗 {_day(next_due)} 再試", "ruled": False}

    attempts = row.get("attempt_count")
    if attempts is None or int(attempts) == 0:
        # Never attempted is the chain's queue, not the owner's backlog.  On
        # 2026-08-22 there were 78 such rows and calling them "waiting for you"
        # would have been a lie in the first line of the report.
        return {"headline": HEADLINE_NOT_YOURS, "detail": DETAIL_CHAIN_WILL_RETRY,
                "reasonCode": "never_attempted",
                "reasonText": f"未試過，排緊 {LANE_DAILY_BUDGET}/lane/日 嘅隊", "ruled": False}

    blocker = str(row.get("blocker_code") or row.get("discovery_status") or "unknown")
    return {"headline": HEADLINE_NEEDS_YOU, "detail": "blocked", "reasonCode": blocker,
            "reasonText": blocker, "ruled": False}


# --------------------------------------------------------------------------- #
# artifact readers
# --------------------------------------------------------------------------- #
_STAMP_RE = re.compile(r"(\d{8})T(\d{6})Z")


def _artifact_day(path: Path) -> str:
    found = _STAMP_RE.search(path.name)
    if found:
        raw = found.group(1)
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")
    except OSError:
        return "?"


def _repo_relative(path: Path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise."""

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_reverify_holds(artifact_dir: Path | None = None) -> dict[int, dict[str, Any]]:
    """Newest `held[]` per lane out of the pc/snk reverify artifacts.

    Rule 4 lives here: the hold reason is only meaningful next to the date of
    the run that produced it.  A lane that did not run today still has a story,
    and it has to read as an old story.
    """

    directory = artifact_dir or DEFAULT_ARTIFACT_DIR
    holds: dict[int, dict[str, Any]] = {}
    for lane, source_code, pattern in (
        ("browser", "pricecharting", "pc-identity-reverify-*.json"),
        ("http", "snkrdunk", "snk-identity-reverify-*.json"),
    ):
        try:
            candidates = sorted(directory.glob(pattern))
        except OSError:
            candidates = []
        if not candidates:
            continue
        newest = candidates[-1]
        try:
            document = json.loads(newest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        day = _artifact_day(newest)
        for entry in document.get("held") or []:
            if not isinstance(entry, Mapping) or entry.get("variant_id") is None:
                continue
            variant_id = int(entry["variant_id"])
            record = {
                "lane": lane,
                "sourceCode": source_code,
                "reason": str(entry.get("reason") or "unknown"),
                "detail": str(entry.get("detail") or ""),
                "externalId": str(entry.get("pid") or entry.get("external_id") or "") or None,
                "artifactDay": day,
                "artifact": newest.name,
                "artifactPath": _repo_relative(newest),
            }
            previous = holds.get(variant_id)
            if previous is None or record["artifactDay"] >= previous["artifactDay"]:
                holds[variant_id] = record
    return holds


def load_intake(business_date: str, intake_dir: Path | None = None) -> dict[str, Any]:
    """The morning's intake receipt, or an explicit statement that none exists.

    "No new cards" and "intake did not run" are different mornings and the
    report may not blur them (plan risk 5).
    """

    directory = intake_dir or DEFAULT_INTAKE_DIR
    path = directory / f"identity-intake-{business_date}.json"
    if not path.is_file():
        return {"present": False, "path": str(path)}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {"present": False, "path": str(path), "error": f"{type(error).__name__}: {error}"}
    document = dict(document) if isinstance(document, Mapping) else {}
    document["present"] = True
    document["path"] = str(path)
    return document


def census_state(intake: Mapping[str, Any], census_path: Path | None = None) -> dict[str, Any]:
    """Census freshness, from the receipt when there is one, else from disk."""

    if intake.get("present"):
        return {
            "known": True,
            "path": str(intake.get("censusPath") or (census_path or DEFAULT_CENSUS_PATH)),
            "day": _day(intake.get("censusMtime")),
            "stale": bool(intake.get("censusStale")),
            "source": "intake-receipt",
        }
    path = census_path or DEFAULT_CENSUS_PATH
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return {"known": False, "path": str(path), "day": "?", "stale": True, "source": "missing"}
    return {"known": True, "path": str(path), "day": mtime.strftime("%Y-%m-%d"),
            "stale": (utc_now() - mtime) > timedelta(days=7), "source": "file-mtime"}


def identity_phase_state(
    tasks: Sequence[Mapping[str, Any]] | None,
    *,
    run_id: str | None = None,
    business_date: str | None = None,
    journal_path: Any = None,
) -> dict[str, Any]:
    """Rule 5: did the identity phase run, or was it closed at 10:15?"""

    if tasks is None:
        return {"known": False, "runId": run_id, "businessDate": business_date,
                "journalPath": str(journal_path) if journal_path else None,
                "closedByCutoff": False, "cutoffTasks": 0, "total": 0, "byStatus": {}}
    by_status: dict[str, int] = {}
    cutoff = 0
    for task in tasks:
        status = str(task.get("status") or "?")
        by_status[status] = by_status.get(status, 0) + 1
        if str(task.get("last_error_code") or "") == "IDENTITY_CUTOFF":
            cutoff += 1
    return {"known": True, "runId": run_id, "businessDate": business_date,
            "journalPath": str(journal_path) if journal_path else None,
            "closedByCutoff": cutoff > 0, "cutoffTasks": cutoff,
            "total": len(tasks), "byStatus": by_status}


# --------------------------------------------------------------------------- #
# collection
# --------------------------------------------------------------------------- #
MEMBER_SQL = """
SELECT rm.gemrate_id, rm.variant_id, rm.latest_psa10_population AS pop, rm.cohort,
       rm.identity_pending, rm.detail_json, rm.computed_at,
       v.tcg_code, v.card_language, v.canonical_name, v.set_name, v.set_code,
       v.collector_number,
       l.discovery_status, l.blocker_code, l.attempt_count, l.next_due_at,
       l.quarantine_until, l.last_outcome, l.last_reviewed_at,
       COALESCE(si.exact_n, 0) AS exact_n, COALESCE(si.nonexact_n, 0) AS nonexact_n,
       si.pc_review_id, si.snk_review_id, si.ruling
  FROM catalog_rebuild_member rm
  LEFT JOIN catalog_variant v ON v.id = rm.variant_id
  LEFT JOIN market_identity_discovery_ledger l ON l.variant_id = rm.variant_id
  LEFT JOIN (
        SELECT variant_id,
               SUM(match_status = 'exact') AS exact_n,
               SUM(match_status <> 'exact') AS nonexact_n,
               MAX(CASE WHEN match_status = 'manual_review'
                         AND source_code = 'pricecharting'
                        THEN external_entity_id END) AS pc_review_id,
               MAX(CASE WHEN match_status = 'manual_review'
                         AND source_code IN ('snkrdunk', 'snk', 'snk_psa10')
                        THEN external_entity_id END) AS snk_review_id,
               MAX(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json, '$.reason'))
                             LIKE 'operator-%%'
                        THEN CONCAT(source_code, ': ',
                             JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json, '$.reason')))
                        END) AS ruling
          FROM catalog_source_identity
         GROUP BY variant_id
  ) si ON si.variant_id = rm.variant_id
 WHERE rm.generation_id = %s
   AND rm.latest_psa10_population >= %s
"""

LATEST_GENERATION_SQL = (
    "SELECT generation_id FROM catalog_rebuild_member ORDER BY computed_at DESC LIMIT 1"
)

RECENT_RULINGS_SQL = """
SELECT source_code, variant_id, external_entity_id, updated_at,
       JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json, '$.reason')) AS reason
  FROM catalog_source_identity
 WHERE JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json, '$.reason')) LIKE 'operator-%%'
   AND updated_at >= %s
"""


def collect(
    cursor: Any,
    *,
    now: datetime | None = None,
    generation: str | None = None,
    population_floor: int = POPULATION_FLOOR,
    red_listed: Iterable[int] = (),
    holds: Mapping[int, Mapping[str, Any]] | None = None,
    intake: Mapping[str, Any] | None = None,
    census: Mapping[str, Any] | None = None,
    baseline: Mapping[str, Any] | None = None,
    identity_phase: Mapping[str, Any] | None = None,
    commands: Mapping[str, bool] | None = None,
    business_date: str | None = None,
    full_list_path: Any = None,
) -> dict[str, Any]:
    """Read everything the brief needs and bucket it.  No writes, no network."""

    moment = now or utc_now()
    if generation is None:
        cursor.execute(LATEST_GENERATION_SQL)
        row = cursor.fetchone()
        generation = str(row["generation_id"]) if row else ""
    cursor.execute(MEMBER_SQL, (generation, int(population_floor)))
    members = [dict(row) for row in cursor.fetchall()]

    holds = dict(holds or {})
    red = set(int(v) for v in red_listed)
    headline_counts = {name: 0 for name in HEADLINES}
    detail_counts = {DETAIL_CHAIN_WILL_RETRY: 0, DETAIL_DECIDED: 0, DETAIL_UNJUDGEABLE: 0}
    gap_census: dict[str, int] = {}
    needs_you: list[dict[str, Any]] = []
    identity_not_live_ruled = 0
    never_attempted = 0
    # Every hold on a member lands in exactly one of these two, so a hold can
    # no longer be dropped by being neither listed nor counted.
    needs_adjudication = 0
    objection_counts: dict[str, int] = {}
    held_in_population = 0
    swallowed: list[Any] = []

    unbucketed: list[Any] = []
    for member in members:
        variant_id = member.get("variant_id")
        hold_record = holds.get(int(variant_id)) if variant_id is not None else None
        verdict = classify(member, now=moment, red_listed=red, holds=holds)
        if verdict["headline"] in headline_counts:
            headline_counts[verdict["headline"]] += 1
        else:
            unbucketed.append(
                {"gemrateId": member.get("gemrate_id"), "variantId": member.get("variant_id"),
                 "headline": verdict["headline"]}
            )
        if verdict["headline"] == HEADLINE_NOT_YOURS:
            detail_counts[verdict["detail"]] += 1
        if verdict["headline"] == HEADLINE_IDENTITY_NOT_LIVE and verdict["ruled"]:
            identity_not_live_ruled += 1
        if verdict["reasonCode"] == "never_attempted":
            never_attempted += 1

        if hold_record:
            held_in_population += 1
            if verdict["detail"] == DETAIL_NEEDS_ADJUDICATION:
                needs_adjudication += 1
            else:
                reason = str(hold_record.get("reason") or "unknown")
                objection_counts[reason] = objection_counts.get(reason, 0) + 1
                # Asked again instead of read off the verdict: this answer does
                # not depend on branch order, so a classifier that short-cuts
                # before the hold branch surfaces as `swallowed` below rather
                # than as a quiet needsYou=0.
                if pending_adjudication_hold(member, hold_record, red_listed=red) is not None:
                    swallowed.append({
                        "gemrateId": member.get("gemrate_id"),
                        "variantId": member.get("variant_id"),
                        "reason": reason,
                        "headline": verdict["headline"],
                        "detail": verdict["detail"],
                    })

        # rebuild_036._discovery_gap_rows: qualified cohort, at the floor, no
        # exact binding, INNER JOIN catalog_variant.  Same shape, same rows.
        if (
            member.get("variant_id") is not None
            and str(member.get("cohort") or "") != "non_qualified"
            and int(member.get("exact_n") or 0) == 0
        ):
            tcg = str(member.get("tcg_code") or "?")
            gap_census[tcg] = gap_census.get(tcg, 0) + 1

        if verdict["headline"] != HEADLINE_NEEDS_YOU:
            continue
        hold = dict(hold_record or {})
        source_code = hold.get("sourceCode") or (
            "pricecharting" if str(member.get("card_language") or "") == "en" else "snkrdunk"
        )
        external_id = hold.get("externalId") or member.get("pc_review_id") or member.get("snk_review_id")
        reason = hold.get("reason") or verdict["reasonCode"]
        evidence_day = hold.get("artifactDay") or _day(member.get("last_reviewed_at"))
        evidence_from = hold.get("artifact") or (
            "market_identity_discovery_ledger.last_reviewed_at"
            if member.get("last_reviewed_at")
            else "冇任何 lane 記錄"
        )
        needs_you.append({
            "variantId": None if variant_id is None else int(variant_id),
            "gemrateId": str(member.get("gemrate_id") or ""),
            "pop": int(member.get("pop") or 0),
            "tcg": str(member.get("tcg_code") or "?"),
            "language": str(member.get("card_language") or "?"),
            "setCode": str(member.get("set_code") or ""),
            "collectorNumber": str(member.get("collector_number") or ""),
            "name": str(member.get("canonical_name") or ""),
            "holdReason": reason,
            "holdDetail": hold.get("detail") or verdict["reasonText"],
            "evidenceDay": evidence_day,
            "evidenceFrom": evidence_from,
            "sourceCode": source_code,
            "externalId": None if external_id is None else str(external_id),
            "link": _guess_link(source_code, external_id, member.get("gemrate_id")),
            "key": hold_key(variant_id, reason),
        })

    needs_you.sort(key=lambda row: (-row["pop"], row["variantId"] or 0))

    since = (moment - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(RECENT_RULINGS_SQL, (since,))
    yesterday = (moment - timedelta(days=1)).strftime("%Y-%m-%d")
    overrides = [
        {"sourceCode": str(row.get("source_code") or ""),
         "variantId": row.get("variant_id"),
         "externalId": str(row.get("external_entity_id") or ""),
         "reason": str(row.get("reason") or ""),
         "day": _day(row.get("updated_at"))}
        for row in cursor.fetchall()
    ]
    overrides_yesterday = [row for row in overrides if row["day"] == yesterday]

    # len(members), NOT sum(headline_counts): a bucket that quietly dropped a
    # row must show up as headline sum != population, which is exactly what the
    # partition test asserts (rule 1).  Summing the buckets would make the
    # check tautological and the drop invisible.
    population = len(members)
    if sum(headline_counts.values()) != population:
        raise AssertionError(
            f"identity brief ABORT: {population - sum(headline_counts.values())} 張"
            f" pop>={int(population_floor)} 嘅卡冇入任何一個 headline bucket："
            f"{json.dumps(unbucketed[:5], ensure_ascii=False, default=str)}"
        )
    if swallowed:
        raise AssertionError(
            f"identity brief ABORT: {len(swallowed)} 個未有人裁決嘅 lane hold 冇入「等你綁」"
            f"（classify 嘅分支次序食咗佢哋，即係 2026-08-23 audit 嗰個病）："
            f"{json.dumps(swallowed[:5], ensure_ascii=False, default=str)}"
        )
    member_variant_ids = {
        int(row["variant_id"]) for row in members if row.get("variant_id") is not None
    }
    artifacts: dict[str, dict[str, Any]] = {}
    for record in holds.values():
        name = str(record.get("artifact") or "?")
        artifacts.setdefault(name, {
            "lane": record.get("lane"),
            "artifact": name,
            "artifactDay": record.get("artifactDay") or "?",
            "path": record.get("artifactPath") or "",
        })
    lane_objections = {
        "total": sum(objection_counts.values()),
        "byReason": objection_counts,
        # A reason neither set names is still printed, marked, and counted.
        "unknownReasons": sorted(
            reason for reason in objection_counts
            if reason not in LANE_OBJECTION_HOLD_REASONS
            and reason not in ADJUDICATION_HOLD_REASONS
        ),
        "needsAdjudication": needs_adjudication,
        "heldInPopulation": held_in_population,
        "offPopulation": sum(
            1 for variant_id in holds if int(variant_id) not in member_variant_ids
        ),
        "artifacts": sorted(artifacts.values(), key=lambda row: str(row["artifact"])),
    }
    baseline_map = {
        str(k): int(v)
        for k, v in ((baseline or {}).get("noCandidateAtPop") or {}).items()
    }
    return {
        "schema": "cardz-identity-brief-v1",
        "generation": generation,
        "generatedAt": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "businessDate": business_date or moment.strftime("%Y-%m-%d"),
        "populationFloor": int(population_floor),
        "population": population,
        "headline": headline_counts,
        "detail": detail_counts,
        "identityNotLiveRuled": identity_not_live_ruled,
        "neverAttempted": never_attempted,
        "laneDailyBudget": LANE_DAILY_BUDGET,
        "needsYou": needs_you,
        "identityPhase": dict(identity_phase or identity_phase_state(None)),
        "intake": dict(intake or {"present": False, "path": None}),
        "census": dict(census or {"known": False, "path": None, "day": "?", "stale": True}),
        "laneObjections": lane_objections,
        "gapCensus": gap_census,
        "gapBaseline": baseline_map,
        "overridesYesterday": overrides_yesterday,
        "commands": dict(commands or {BIND_COMMAND: False, RULE_COMMAND: False}),
        "fullListPath": None if full_list_path is None else str(full_list_path),
    }


def _guess_link(source_code: str, external_id: Any, gemrate_id: Any) -> str | None:
    """Only links this repo already builds elsewhere; never a guessed slug.

    PriceCharting product pages are addressed by slug, not by product id, so a
    pid alone yields no URL here -- the honest answer is the GemRate page the
    identity actually came from.
    """

    if source_code in {"snkrdunk", "snk", "snk_psa10"} and external_id:
        return SNKRDUNK_ITEM_URL.format(external_id=external_id)
    if gemrate_id:
        return GEMRATE_CARD_URL.format(gemrate_id=gemrate_id)
    return None


# --------------------------------------------------------------------------- #
# dedupe
# --------------------------------------------------------------------------- #
def split_by_seen(
    rows: Sequence[Mapping[str, Any]],
    seen: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Fresh rows, repeat rows, and the state to persist.

    A row is fresh when its key is unknown -- and the key carries the reason,
    so a reason that changed IS a new key -- or when the last showing is older
    than REFLOAT_DAYS.
    """

    moment = now or utc_now()
    state = dict(seen or {})
    fresh: list[dict[str, Any]] = []
    repeat: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("key") or hold_key(row.get("variantId"), row.get("holdReason")))
        record = state.get(key)
        last_shown = _as_utc((record or {}).get("lastShown"))
        if record is None or last_shown is None or (moment - last_shown) >= timedelta(days=REFLOAT_DAYS):
            fresh.append(dict(row))
        else:
            repeat.append(dict(row))
    return fresh, repeat, state


def load_seen(path: Path | None = None) -> dict[str, Any]:
    """Read this report's own dedupe record out of the notify state."""

    try:
        state = json.loads((path or NOTIFY_STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    record = state.get(SEEN_STATE_KEY)
    return dict(record) if isinstance(record, Mapping) else {}


def save_seen(seen: Mapping[str, Any], path: Path | None = None) -> Path:
    """Merge the record back, leaving every other notify key untouched."""

    target = path or NOTIFY_STATE_PATH
    try:
        state = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    state[SEEN_STATE_KEY] = dict(seen)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(target)
    return target


def remember(
    rows: Sequence[Mapping[str, Any]],
    seen: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Stamp the rows this message actually listed, and forget stale keys."""

    moment = now or utc_now()
    state = dict(seen or {})
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    for row in rows:
        key = str(row.get("key") or hold_key(row.get("variantId"), row.get("holdReason")))
        record = dict(state.get(key) or {})
        record.setdefault("firstSeen", stamp)
        record["lastShown"] = stamp
        record["variantId"] = row.get("variantId")
        record["holdReason"] = row.get("holdReason")
        state[key] = record
    horizon = moment - timedelta(days=REFLOAT_DAYS * 4)
    for key in list(state):
        last = _as_utc((state.get(key) or {}).get("lastShown"))
        if last is not None and last < horizon:
            state.pop(key, None)
    return state


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _bind_command(row: Mapping[str, Any], commands: Mapping[str, bool]) -> str:
    text = (
        f'python -X utf8 pipelines/operator_control.py {BIND_COMMAND} '
        f'--variant-id {row.get("variantId")} --url "<paste>" --actor daddy --write'
    )
    if not commands.get(BIND_COMMAND):
        text += f"   # {BIND_COMMAND} 未落地"
    return text


def _rule_command(row: Mapping[str, Any], commands: Mapping[str, bool]) -> str:
    text = (
        f'python -X utf8 pipelines/operator_control.py {RULE_COMMAND} '
        f'--source-code {row.get("sourceCode")} --variant-id {row.get("variantId")} '
        f'--external-id {row.get("externalId") or "<id>"} --actor daddy --action reject '
        f'--reason "<你嘅理由>" --write'
    )
    if not commands.get(RULE_COMMAND):
        text += f"   # {RULE_COMMAND} 未落地"
    return text


def _card_label(row: Mapping[str, Any]) -> str:
    bits = [f'v{row.get("variantId")}' if row.get("variantId") is not None else "（無 variant）"]
    bits.append(f'pop={row.get("pop")}')
    bits.append(f'{row.get("tcg")}/{row.get("language")}')
    number = " ".join(x for x in (row.get("setCode"), row.get("collectorNumber")) if x)
    if number:
        bits.append(number)
    if row.get("name"):
        bits.append(str(row["name"])[:40])
    return " ".join(bits)


def _needs_you_lines(
    rows: Sequence[Mapping[str, Any]],
    commands: Mapping[str, bool],
    *,
    with_commands: int,
) -> list[str]:
    lines: list[str] = []
    for index, row in enumerate(rows):
        label = _esc(_card_label(row))
        link = row.get("link")
        head = f"  • <a href=\"{_esc(link)}\">{label}</a>" if link else f"  • {label}"
        lines.append(head)
        lines.append(
            f'    hold=<code>{_esc(row.get("holdReason"))}</code>'
            f' · 出處 {_esc(row.get("evidenceFrom"))} {_esc(row.get("evidenceDay"))}'
        )
        if index < with_commands:
            lines.append(f'    ✅ <code>{_esc(_bind_command(row, commands))}</code>')
            lines.append(f'    ❌ <code>{_esc(_rule_command(row, commands))}</code>')
    return lines


def _render_at(
    data: Mapping[str, Any],
    listed: Sequence[Mapping[str, Any]],
    *,
    with_commands: int,
    repeat_n: int,
) -> str:
    headline = data.get("headline") or {}
    detail = data.get("detail") or {}
    phase = data.get("identityPhase") or {}
    intake = data.get("intake") or {}
    census = data.get("census") or {}
    needs_you = data.get("needsYou") or []

    live = int(headline.get(HEADLINE_LIVE, 0))
    not_live = int(headline.get(HEADLINE_IDENTITY_NOT_LIVE, 0))
    yours = int(headline.get(HEADLINE_NEEDS_YOU, 0))
    theirs = int(headline.get(HEADLINE_NOT_YOURS, 0))

    lines: list[str] = []
    # block 1 -- one line: generation plus the four numbers that partition it.
    lines.append(
        f'🪪 <b>CARDZ 身份日報</b> {_esc(data.get("businessDate"))}'
        f' gen=<code>{_esc(data.get("generation"))}</code>'
        f' ｜ 🟢 出街 {live}'
        f' ｜ ⛔ 有身份未出街 {not_live}'
        f' ｜ 🙋 等你綁 {yours}'
        f' ｜ 🤖 唔使你郁 {theirs}'
        f' ＝ pop≥{int(data.get("populationFloor") or POPULATION_FLOOR)} 母體 {int(data.get("population") or 0)}'
    )
    # block 2 -- rule 5: was the identity phase closed at the 10:15 cutoff?
    if not phase.get("known"):
        lines.append(
            "🧭 身份階段：<b>未知</b>（讀唔到 V2 journal"
            f' <code>{_esc(phase.get("journalPath") or "-")}</code>）'
            "——下面係 DB 現況，唔代表今朝條 lane 行過"
        )
    elif phase.get("closedByCutoff"):
        lines.append(
            f'🧭 身份階段：<b>畀 IDENTITY_CUTOFF 閂咗</b>（{int(phase.get("cutoffTasks") or 0)}'
            f'/{int(phase.get("total") or 0)} 個 stage 被 degrade，run'
            f' <code>{_esc(phase.get("runId") or "-")}</code>）——下面啲數係閂之前嘅'
        )
    else:
        status_bits = "、".join(
            f"{name}×{count}" for name, count in sorted((phase.get("byStatus") or {}).items())
        )
        lines.append(
            f'🧭 身份階段：未被 IDENTITY_CUTOFF 閂（run <code>{_esc(phase.get("runId") or "-")}</code>，'
            f'{int(phase.get("total") or 0)} 個 stage：{_esc(status_bits or "冇 stage")}）'
        )

    # block 3 -- new cards.
    if not intake.get("present"):
        lines.append(
            f'🆕 <b>新卡</b>：今日未行過 intake（冇 <code>{_esc(intake.get("path") or "-")}</code>）'
            "——<b>唔代表冇新卡</b>"
        )
    else:
        interned = list(intake.get("interned") or [])
        buckets = intake.get("buckets") or {}
        bucket_bits = "、".join(f"{_esc(k)}×{int(v)}" for k, v in sorted(buckets.items())) or "冇分桶"
        stale_mark = "⚠️ census 過期" if census.get("stale") else "census ✅"
        lines.append(
            f'🆕 <b>新卡</b> {len(interned)}（{bucket_bits}；{stale_mark}'
            f' {_esc(census.get("day") or "?")}）'
        )
        for row in interned[:MAX_NEW_CARDS]:
            gid = str((row or {}).get("gemrateId") or (row or {}).get("gemrate_id") or "")
            name = str((row or {}).get("name") or gid[:12])
            pop = (row or {}).get("pop")
            verdict = (row or {}).get("verdict") or (row or {}).get("decision") or "?"
            link = GEMRATE_CARD_URL.format(gemrate_id=gid) if gid else None
            label = _esc(f"{name} pop={pop} → {verdict}")
            lines.append(f'  • <a href="{_esc(link)}">{label}</a>' if link else f"  • {label}")
        if len(interned) > MAX_NEW_CARDS:
            lines.append(f"  …仲有 {len(interned) - MAX_NEW_CARDS} 張，全名單見下面個檔")
        for row in list(intake.get("deferredByRatchet") or [])[:1]:
            lines.append(f'  ⏸ ratchet 押後 {len(intake.get("deferredByRatchet") or [])} 張')
            break

    # block 4 -- what actually waits for the owner (rule 2 + rule 3 + rule 4).
    if yours == 0:
        lines.append("🙋 <b>等你綁</b> 0 —— 今日冇嘢等你做")
    else:
        shown = len(listed)
        lines.append(
            f'🙋 <b>等你綁</b> {yours}（列 {shown} 張；'
            f'之前講過而 reason 冇變 {repeat_n} 張唔重覆嘈，{REFLOAT_DAYS} 日後重浮）'
        )
        lines.extend(_needs_you_lines(listed, data.get("commands") or {}, with_commands=with_commands))
        if len(needs_you) > shown:
            lines.append(f"  …其餘 {len(needs_you) - shown} 張見全名單檔，唔喺度截字")
        if with_commands < shown:
            lines.append(f"  （命令只列頭 {with_commands} 條，其餘去全名單檔攞）")

    # block 5 -- the three numbers that make up 唔使你郁 (rule 3 + rule 6).
    lines.append(
        f'⏳ chain 自己再試 {int(detail.get(DETAIL_CHAIN_WILL_RETRY, 0))}'
        f' ｜ 🔒 已裁決 {int(detail.get(DETAIL_DECIDED, 0))}'
        f' ｜ 🈲 chain 判唔到 {int(detail.get(DETAIL_UNJUDGEABLE, 0))}（zhCN／zhTW 只有你綁得到）'
    )
    never = int(data.get("neverAttempted") or 0)
    budget = int(data.get("laneDailyBudget") or LANE_DAILY_BUDGET)
    lines.append(
        f"   其中未試過 {never} 條；lane 預算 {budget}/日，"
        f"{max(0, never - budget)} 條今日輪唔到（聽日先到，唔使你郁）"
    )

    # block 5b -- the lane's refusals.  They are not the owner's work, but a
    # hold nobody prints is a hold nobody knows about (rule 6 again).
    objections = data.get("laneObjections") or {}
    by_reason = objections.get("byReason") or {}
    reason_bits = "、".join(
        f"{_esc(reason)}×{int(count)}" for reason, count in sorted(by_reason.items())
    ) or "0"
    artifact_bits = "、".join(
        f'<code>{_esc(row.get("path") or row.get("artifact"))}</code>'
        f' {_esc(row.get("artifactDay") or "?")}'
        for row in (objections.get("artifacts") or [])
    ) or "今日冇 lane artifact"
    unknown = objections.get("unknownReasons") or []
    lines.append(
        f'🧾 lane 反對（唔算等你綁）：{reason_bits}'
        f' ｜ 未裁決升咗做等你綁 {int(objections.get("needsAdjudication") or 0)}'
        f' ｜ 唔喺母體 {int(objections.get("offPopulation") or 0)}'
        f' ｜ 出處 {artifact_bits}'
        + (f' ｜ ⚠️ 未分類 reason：{_esc("、".join(map(str, unknown)))}' if unknown else "")
    )

    # block 6 -- the standing holes, stated even when zero (rule 6).
    lines.append(
        f'⛔ <b>有身份但未出街</b> {not_live}'
        f'（其中 {int(data.get("identityNotLiveRuled") or 0)} 條有 operator ruling 講明唔出街）'
        "：cohort 停喺 qualified_identity／qualified_market_pending，冇 stage 會自動升 product_ready"
    )
    gap = data.get("gapCensus") or {}
    base = data.get("gapBaseline") or {}
    gap_bits = "、".join(
        f"{_esc(tcg)} {int(gap.get(tcg, 0))}/{int(base.get(tcg, 0))}"
        for tcg in sorted(set(gap) | set(base))
    ) or "冇 baseline"
    lines.append(f"📉 gap ratchet（census/baseline）：{gap_bits}")
    lines.append(f'🖐 尋日 operator override：{len(data.get("overridesYesterday") or [])}')
    lines.append(f'📄 全名單：<code>{_esc(data.get("fullListPath") or "（今次冇寫檔）")}</code>')
    return "\n".join(lines)


def render(
    data: Mapping[str, Any],
    *,
    seen: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    budget: int = MESSAGE_BUDGET,
) -> tuple[str, dict[str, Any]]:
    """Render the brief and return the dedupe state to persist.

    The caps are applied to ROWS (rule 2).  When even the capped rows overflow
    the Telegram budget the renderer drops whole rows and whole command pairs
    and says so; it never cuts a rendered string, because the half that
    survives a cut is a command the owner might paste.
    """

    moment = now or utc_now()
    rows = list(data.get("needsYou") or [])
    fresh, repeat, _ = split_by_seen(rows, seen, now=moment)
    listed = fresh[:MAX_NEEDS_YOU]

    message = ""
    for row_cap, command_cap in (
        (MAX_NEEDS_YOU, MAX_NEEDS_YOU), (MAX_NEEDS_YOU, 2), (MAX_NEEDS_YOU, 0), (2, 0), (0, 0)
    ):
        trimmed = listed[:row_cap]
        message = _render_at(
            data, trimmed, with_commands=min(command_cap, len(trimmed)), repeat_n=len(repeat)
        )
        if len(message) <= budget:
            listed = trimmed
            break
    return message, remember(listed, seen, now=moment)


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def _load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _journal_identity_phase() -> dict[str, Any]:
    """Best effort: an unreadable journal is reported as unknown, not as calm."""

    try:
        sys.path.insert(0, str(ROOT / "pipelines"))
        import daily_chain_v2_journal as journal_module

        path = journal_module.default_state_path()
        if not Path(path).exists():
            return identity_phase_state(None, journal_path=path)
        journal = journal_module.Journal(Path(path))
        with journal.connect() as conn:
            row = conn.execute(
                "SELECT run_id,business_date FROM chain_run ORDER BY business_date DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return identity_phase_state(None, journal_path=path)
            tasks = [
                dict(item)
                for item in conn.execute(
                    "SELECT * FROM chain_task WHERE run_id=? AND phase='identity'",
                    (row["run_id"],),
                ).fetchall()
            ]
        return identity_phase_state(
            tasks, run_id=row["run_id"], business_date=row["business_date"], journal_path=path
        )
    except Exception:  # a report that cannot read the journal still has to ship
        return identity_phase_state(None)


def build(
    *,
    conn: Any = None,
    now: datetime | None = None,
    business_date: str | None = None,
    artifact_dir: Path | None = None,
    intake_dir: Path | None = None,
    census_path: Path | None = None,
    baseline_path: Path | None = None,
    seen: Mapping[str, Any] | None = None,
    full_list_path: Path | None = None,
) -> dict[str, Any]:
    """Assemble + render.  Read-only against MySQL; optional artifact write."""

    moment = now or utc_now()
    day = business_date or moment.strftime("%Y-%m-%d")
    owned = conn is None
    if owned:
        sys.path.insert(0, str(ROOT / "pipelines"))
        import rebuild_036 as rebuild

        conn = rebuild.connect(rebuild.DAILY_CREDENTIALS_ENV)
    try:
        cursor = conn.cursor()
        # Read path only: the same session cap the 2026-08-22 runaway taught us
        # to set, so a slow join cannot hold MySQL for six thousand seconds.
        cursor.execute("SET SESSION max_execution_time=%s", (int(os.environ.get("CARDZ_MAX_EXEC_MS", "60000")),))
        try:
            sys.path.insert(0, str(ROOT / "pipelines"))
            import rebuild_036 as rebuild_for_red

            red = rebuild_for_red.red_listed_variants()
        except Exception:
            red = []
        intake = load_intake(day, intake_dir)
        data = collect(
            cursor,
            now=moment,
            red_listed=red,
            holds=load_reverify_holds(artifact_dir),
            intake=intake,
            census=census_state(intake, census_path),
            baseline=_load_json(baseline_path or DEFAULT_BASELINE_PATH),
            identity_phase=_journal_identity_phase(),
            commands=_read_operator_commands(DEFAULT_OPERATOR_CONTROL),
            business_date=day,
            full_list_path=full_list_path,
        )
    finally:
        if owned:
            conn.close()
    message, state = render(data, seen=seen, now=moment)
    return {"message": message, "data": data, "seen": state}


def write_full_list(data: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


def _markdown(result: Mapping[str, Any]) -> str:
    data = result.get("data") or {}
    return "\n".join([
        f'# CARDZ 身份日報 {data.get("businessDate")}',
        "",
        f'- generation：`{data.get("generation")}`',
        f'- generatedAt：`{data.get("generatedAt")}`（UTC，read-only render）',
        f'- pop≥{data.get("populationFloor")} 母體：{data.get("population")}',
        "",
        "## 渲染後訊息（Telegram HTML 原文）",
        "",
        "```html",
        str(result.get("message") or ""),
        "```",
        "",
        "## 數據",
        "",
        "```json",
        json.dumps(data, ensure_ascii=False, indent=1, default=str),
        "```",
        "",
    ])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CARDZ 身份日報（純讀）")
    parser.add_argument("--json", action="store_true", help="出 JSON（data + message）")
    parser.add_argument("--out", help="寫一份 markdown 落呢個路徑")
    parser.add_argument("--full-list", help="寫全名單 JSON 落呢個路徑")
    parser.add_argument("--business-date", help="覆寫 business date（default 今日 UTC）")
    args = parser.parse_args(list(argv) if argv is not None else None)

    full_list = Path(args.full_list) if args.full_list else None
    result = build(business_date=args.business_date, full_list_path=full_list)
    if full_list is not None:
        write_full_list(result["data"], full_list)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_markdown(result), encoding="utf-8")
    if args.json:
        print(json.dumps({"message": result["message"], "data": result["data"]},
                         ensure_ascii=False, indent=1, default=str))
    else:
        print(result["message"])
    if args.out:
        print(f"\n[out] {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
