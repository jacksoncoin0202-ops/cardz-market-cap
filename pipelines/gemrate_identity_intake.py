#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GemRate pop>=1000 新卡 intake（PLAN §C.3）。

一句：**今日 GemRate 講過咗 1000、而我哋張 member 仲當佢未夠數嘅卡**，證據齊就自動
開／認身份，證據唔齊就交返人手，兩邊都寫低點解。

## 預設 dry-run

`--apply` 係唯一寫入路徑。冇 `--apply` 就只讀 DB、只讀 census、寫一份 receipt，
零 INSERT / UPDATE。dry-run 出嘅 verdict 表同 `--apply` 行嘅係同一段 `classify()`，
唔係另一套「預覽版」邏輯。

## Census 用新鮮 GemRate census，唔用凍咗嘅 member column

`catalog_rebuild_member.latest_psa10_population` 係上一次 rebuild 嗰刻嘅數（今日最後
寫入 2026-08-20），佢永遠追唔到「今個星期先過線」嗰批。所以入圍判斷讀
`data/private/gemrate_brute/psa10_1000_plus.jsonl`，member 只負責答「呢張卡而家喺
catalog 係咩狀態」。

`--max-age-days`（default 7，抄 `rebuild_036` 自己個 brute 重用規則）過期就 `censusStale:
true` 同**零收卡**：census 舊嘅時候永遠唔准講「冇新卡」。

`data/private` 喺一個新開嘅 worktree 係空嘅（harvester 寫落邊個 checkout 就邊個有），
所以 census 路徑係「搵」出嚟嘅，搵到邊個就喺 receipt 寫返邊個 —— **只讀，永遠唔寫**。

## 唔准讀 `pendingReasons`

`rebuild_036.py:1296` 對 `non_qualified` 強制 `identity_pending=0`，令已裁決嘅卡個
`pendingReasons` 係空。要讀 `detail_json.aliasOf` / `.demotedReason`（2781/2784）。
讀錯 field = 重開 owner 已經落咗嘅決定。

## 六個 verdict（＋一個明寫嘅 fallthrough）

`member_missing` / `already_qualified` / `alias` / `ruled` / `ambiguous` / `auto`，
順序唔可以掉轉。其餘每一種「證據唔齊」都係 `needs_human`，帶住原因碼；
`ambiguous` 同 `needs_human` 一齊入 receipt 個 `needsHuman[]`。

## 三層＋一層去重（順序唔可以掉轉）

  0. `_gemrate_printing_sha(fields)` 已經有主人 → `existing_printing_sha`，綁返佢。
     **呢層唔喺 PLAN 原文，係加嘅。** 冇佢，下一次 rebuild 嘅 `resolve_home()` 會用
     同一條 sha 喺 `printing_sha_owner`（`rebuild_036.py:2104`）撲空，然後為同一張卡
     再 mint 多一個 variant —— 即係「一個產品出現兩次」。所以 intake 開新 variant 嘅
     時候一定連 `catalog_printing_identity` 一齊開，個 sha 就係鎖。
  1. 算出嘅 `opaque_id` 撞到現有 variant → `existing_opaque`
  2. `(tcg, language, set_name, collector)` printing key 撞到**一張** → `existing_printing`；
     撞到**多過一張** → `ambiguous`、零寫入
  3. 都撞唔到 → `new_variant`

第 0–2 層任何一次「撞到」都仲要過 `_fingerprint_variant_conflicts` /
`_name_agrees` / `_print_signature_agrees`：set + 號碼 + parallel **釘唔死**一張卡
（one-piece 個 118 號有幾張唔同 alt-art），名唔啱就係另一張卡，出
`print_identity_unrepresentable_vs_variant_N` 交人手，唔准靜靜合併。

## Ratchet 上限（唯一一條新 fail-closed 規矩）

`cap[tcg] = baseline.noCandidateAtPop[tcg] - _discovery_gap_census[tcg] - RESERVE`。
收卡會令 `_discovery_gap_census`（`rebuild_036.py:8478`）變大，而
`_assert_discovery_gap_not_worse`（8488）一過 baseline 就 **ABORT daily-accept**。
收最多 `min(cap[tcg], --max-seed)` 張，pop 高先，餘數報 `deferredByRatchet`。
**永遠唔准改 `pipelines/discovery_coverage_baseline.json` 嚟令佢過。**

Exit codes: 0 = 正常；2 = `--apply` 前置條件唔成立（零寫入）；1 = 環境／輸入壞。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import discovery_ledger
import gemrate_source
import rebuild_036 as rebuild
from g10_public_snapshot import normalize_collector, opaque_id
from g10_variant_seed import load_existing as load_seed_indexes

SOURCE_CODE = "gemrate"
# The same floor the acceptance ratchet uses. Two numbers here would let intake
# admit a card the ratchet is not measuring, or refuse one it is.
POP_FLOOR = rebuild.DISCOVERY_GAP_POP
RESERVE = 25
DEFAULT_MAX_SEED = 25
DEFAULT_MAX_AGE_DAYS = 7
SCHEMA_VERSION = "055"
DECISION_TABLE = "market_identity_intake_decision"
PARSER_VERSION = "gemrate-identity-intake-1"
# 'qualified_identity' is rebuild_036.py:1290's own word, and it is the value
# that makes the row visible to _discovery_gap_rows (cohort <> 'non_qualified')
# so a lane will actually chase it. identity_pending=0 keeps stage_validate's
# cohortEquation (pending_n == 0 and unbound_n == 0) satisfiable next rebuild.
MEMBER_COHORT = "qualified_identity"
MEMBER_IDENTITY_PENDING = 0

CENSUS_RELATIVE = Path("data") / "private" / "gemrate_brute" / "psa10_1000_plus.jsonl"
# Read-only search order. The brute harvester writes into whichever checkout ran
# it, and a fresh worktree's data/private is empty; recording which file was
# actually read is what keeps the freshness claim honest.
CENSUS_SEARCH_ROOTS: tuple[Path, ...] = (
    ROOT,
    rebuild.OLD_CHECKOUT_ROOT.parent / "cardz-market-cap-fe-db-20260805",
    rebuild.OLD_CHECKOUT_ROOT,
)
RECEIPT_DIR = ROOT / "data" / "runtime" / "daily-chain-v2"

VERDICT_MEMBER_MISSING = "member_missing"
VERDICT_ALREADY_QUALIFIED = "already_qualified"
VERDICT_ALIAS = "alias"
VERDICT_RULED = "ruled"
VERDICT_AMBIGUOUS = "ambiguous"
VERDICT_AUTO = "auto"
VERDICT_NEEDS_HUMAN = "needs_human"

VERDICT_ORDER = (
    VERDICT_MEMBER_MISSING,
    VERDICT_ALREADY_QUALIFIED,
    VERDICT_ALIAS,
    VERDICT_RULED,
    VERDICT_AMBIGUOUS,
    VERDICT_AUTO,
    VERDICT_NEEDS_HUMAN,
)
NEEDS_HUMAN_VERDICTS = frozenset({VERDICT_AMBIGUOUS, VERDICT_NEEDS_HUMAN})

DECISION_UPSERT = (
    "INSERT INTO market_identity_intake_decision (gemrate_id, generation_id,"
    " decision, reason_code, variant_id, evidence_sha256, decided_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s)"
    " ON DUPLICATE KEY UPDATE generation_id=VALUES(generation_id),"
    " decision=VALUES(decision), reason_code=VALUES(reason_code),"
    " variant_id=VALUES(variant_id), evidence_sha256=VALUES(evidence_sha256),"
    " decided_at=VALUES(decided_at)"
)


# --------------------------------------------------------------------------
# census
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Census:
    path: Path | None
    mtime: str | None
    age_days: float | None
    stale: bool
    missing: bool
    rows: dict[str, int]
    below_floor: int
    duplicates: int

    def as_report(self) -> dict[str, Any]:
        return {
            "censusPath": str(self.path) if self.path else None,
            "censusMtime": self.mtime,
            "censusAgeDays": round(self.age_days, 3) if self.age_days is not None else None,
            "censusStale": self.stale,
            "censusMissing": self.missing,
            "censusRowsAtOrAbovePop": len(self.rows),
            "censusRowsBelowPop": self.below_floor,
            "censusDuplicateIds": self.duplicates,
            "populationFloor": POP_FLOOR,
        }


def _as_int(value: Any) -> int:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_census_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    for root in CENSUS_SEARCH_ROOTS:
        candidate = root / CENSUS_RELATIVE
        if candidate.is_file():
            return candidate
    return None


def census(
    path: Path | None = None,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> Census:
    """Fresh GemRate PSA10 census, keyed by `psa_id` (the gemrate_id space).

    `gemrate_checklist_id` is a DIFFERENT id space and joins 0/956 against
    catalog_rebuild_member.gemrate_id -- using it reads as "no new cards".
    """

    moment = now or _utc_now()
    resolved = resolve_census_path(path)
    if resolved is None:
        # Fail closed: a census we cannot read is not a census that says zero.
        return Census(path, None, None, True, True, {}, 0, 0)
    stat = resolved.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    age_days = (moment - mtime).total_seconds() / 86400.0
    rows: dict[str, int] = {}
    below_floor = 0
    duplicates = 0
    with resolved.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            gid = str(payload.get("psa_id") or "").strip()
            if not gid:
                continue
            population = _as_int(payload.get("psa_10"))
            if population < POP_FLOOR:
                below_floor += 1
                continue
            if gid in rows:
                duplicates += 1
                if population <= rows[gid]:
                    continue
            rows[gid] = population
    return Census(
        path=resolved,
        mtime=mtime.strftime("%Y-%m-%dT%H:%M:%SZ"),
        age_days=age_days,
        stale=age_days > float(max_age_days),
        missing=False,
        rows=rows,
        below_floor=below_floor,
        duplicates=duplicates,
    )


def refresh_census(*, timeout: int = 5400) -> dict[str, Any]:
    """Shell the existing brute harvester. Off by default; never auto-invoked."""

    completed = subprocess.run(
        [sys.executable, "-X", "utf8",
         str(ROOT / "pipelines" / "gemrate_brute_harvest.py"), "--all-sets"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
    )
    return {
        "returncode": completed.returncode,
        "stderrTail": (completed.stderr or "")[-2000:],
    }


# --------------------------------------------------------------------------
# catalog indexes / members
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogIndexes:
    by_opaque: dict[str, int]
    by_printing: dict[tuple, list[int]]
    by_printing_sha: dict[str, int]
    variants: dict[int, dict[str, Any]]
    gemrate_bound: set[str]


def load_catalog_indexes(cursor: Any) -> CatalogIndexes:
    # Layers 1 and 2 are g10_variant_seed's, by call and not by copy: two
    # implementations of "is this card already in the catalog" is how the same
    # card ends up in the catalog twice.
    by_opaque, by_printing, _snkrdunk_mapped = load_seed_indexes(cursor)
    cursor.execute(
        "SELECT v.id, v.opaque_id, v.tcg_code, v.card_language, v.canonical_name,"
        " v.set_name, v.set_code, v.printing_code, v.collector_number,"
        " p.parallel_code, p.canonical_printing_sha256"
        " FROM catalog_variant v"
        " LEFT JOIN catalog_printing_identity p ON p.variant_id = v.id"
    )
    variants: dict[int, dict[str, Any]] = {}
    by_printing_sha: dict[str, int] = {}
    for row in cursor.fetchall():
        variant_id = int(row["id"])
        variants[variant_id] = dict(row)
        sha = str(row.get("canonical_printing_sha256") or "")
        if sha:
            by_printing_sha[sha] = variant_id
    cursor.execute(
        "SELECT external_entity_id FROM catalog_source_identity WHERE source_code = %s",
        (SOURCE_CODE,),
    )
    bound = {str(row["external_entity_id"]) for row in cursor.fetchall()}
    return CatalogIndexes(by_opaque, by_printing, by_printing_sha, variants, bound)


def latest_generation(cursor: Any) -> str:
    cursor.execute(
        "SELECT generation_id FROM catalog_rebuild_member"
        " ORDER BY computed_at DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if not row:
        raise SystemExit("intake ABORT: catalog_rebuild_member is empty")
    return str(row["generation_id"])


def load_members(cursor: Any, generation: str, gids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Chunked base-table read. Never the projection view (PLAN §A.0 #6)."""

    ordered = sorted(set(gids))
    members: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ordered), 200):
        chunk = ordered[start:start + 200]
        placeholders = ",".join(["%s"] * len(chunk))
        cursor.execute(
            "SELECT gemrate_id, variant_id, latest_psa10_population, cohort,"
            " identity_pending, detail_json FROM catalog_rebuild_member"
            f" WHERE generation_id = %s AND gemrate_id IN ({placeholders})",
            [generation, *chunk],
        )
        for row in cursor.fetchall():
            members[str(row["gemrate_id"])] = dict(row)
    return members


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    gemrate_id: str
    verdict: str
    reason_code: str
    population: int
    tcg_code: str = ""
    variant_id: int | None = None
    resolution: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def as_report(self) -> dict[str, Any]:
        return {
            "gemrateId": self.gemrate_id,
            "verdict": self.verdict,
            "reasonCode": self.reason_code,
            "population": self.population,
            "tcgCode": self.tcg_code or None,
            "variantId": self.variant_id,
            "resolution": self.resolution or None,
            "cardName": self.detail.get("cardName"),
            "setName": (self.detail.get("fields") or {}).get("set_name"),
            "collectorNumber": self.detail.get("collectorDisplay"),
            "canonicalUrl": self.detail.get("canonicalUrl"),
            "blockers": self.detail.get("blockers"),
        }


def _adoption_blockers(fingerprint: Mapping[str, Any], variant: Mapping[str, Any]) -> list[str]:
    """Everything that says "this existing variant is a DIFFERENT card"."""

    blockers = list(rebuild._fingerprint_variant_conflicts(fingerprint, variant))
    if not rebuild._name_agrees(
        str(fingerprint.get("name") or ""), str(variant.get("canonical_name") or "")
    ):
        blockers.append(
            f"name:{fingerprint.get('name')!r}!={variant.get('canonical_name')!r}"
        )
    if not rebuild._print_signature_agrees(str(fingerprint.get("parallel") or ""), variant):
        blockers.append(
            f"parallel:{fingerprint.get('parallel')!r}!={variant.get('parallel_code')!r}"
        )
    return blockers


def classify(
    gemrate_id: str,
    *,
    population: int,
    member: Mapping[str, Any] | None,
    indexes: CatalogIndexes,
) -> Verdict:
    """One census row -> one verdict. Order of the checks is the contract."""

    if member is None:
        return Verdict(gemrate_id, VERDICT_MEMBER_MISSING, "not_in_generation", population)

    cohort = str(member.get("cohort") or "")
    if cohort != "non_qualified":
        variant_id = member.get("variant_id")
        return Verdict(
            gemrate_id, VERDICT_ALREADY_QUALIFIED, f"cohort:{cohort}", population,
            variant_id=int(variant_id) if variant_id is not None else None,
        )

    detail = json.loads(member.get("detail_json") or "{}")
    fingerprint = detail.get("fingerprint")
    settled = str((fingerprint or {}).get("settledId") or "") if isinstance(fingerprint, dict) else ""
    alias_of = detail.get("aliasOf")
    # settledId != gid IS an alias even with no aliasOf row: rebuild's rule 8
    # only records alias_of when the settled entity is itself a member.
    if alias_of or (settled and settled != gemrate_id):
        return Verdict(
            gemrate_id, VERDICT_ALIAS, "alias_of_settled_entity", population,
            detail={"aliasOf": alias_of or settled, "settledId": settled or None},
        )

    demoted = detail.get("demotedReason")
    if demoted:
        # Verbatim. This is a ruling somebody (or rebuild) already made; a
        # paraphrase here is how a decision gets quietly reopened.
        return Verdict(gemrate_id, VERDICT_RULED, str(demoted), population)

    if not isinstance(fingerprint, dict):
        return Verdict(gemrate_id, VERDICT_NEEDS_HUMAN, "fingerprint_missing", population)
    psa_rows = fingerprint.get("psaRowCount")
    if _as_int(psa_rows) != 1:
        return Verdict(gemrate_id, VERDICT_NEEDS_HUMAN, f"psa_rows:{psa_rows}", population)
    if not settled:
        return Verdict(gemrate_id, VERDICT_NEEDS_HUMAN, "settled_id_missing", population)
    if not str(fingerprint.get("cardNumber") or "").strip():
        return Verdict(gemrate_id, VERDICT_NEEDS_HUMAN, "card_number_missing", population)
    if member.get("variant_id") is not None:
        return Verdict(
            gemrate_id, VERDICT_NEEDS_HUMAN, "member_already_bound", population,
            variant_id=int(member["variant_id"]),
        )
    if gemrate_id in indexes.gemrate_bound:
        return Verdict(gemrate_id, VERDICT_NEEDS_HUMAN, "gemrate_binding_exists", population)

    fields, reason = rebuild._derive_print_fields(fingerprint)
    if fields is None:
        return Verdict(gemrate_id, VERDICT_NEEDS_HUMAN, f"fields:{reason}", population)
    tcg_code = str(fields["tcg_code"])
    canonical_name = str(fingerprint.get("description") or "").strip()
    if not canonical_name:
        return Verdict(
            gemrate_id, VERDICT_NEEDS_HUMAN, "description_missing", population,
            tcg_code=tcg_code,
        )
    collector = normalize_collector(fingerprint.get("cardNumber"))
    opaque = opaque_id(
        tcg_code, str(fields["card_language"]), str(fields["set_name"]),
        collector, canonical_name,
    )
    printing_sha = rebuild._gemrate_printing_sha(fields)
    base: dict[str, Any] = {
        "fields": dict(fields),
        "opaqueId": opaque,
        "printingSha256": printing_sha,
        "collectorDisplay": collector.display,
        "canonicalName": canonical_name,
        "cardName": str(fingerprint.get("name") or ""),
        "parallel": str(fingerprint.get("parallel") or ""),
        "canonicalUrl": str(fingerprint.get("canonicalUrl") or ""),
    }

    # Layer 0 -- printing sha. See the module docstring: without this the next
    # rebuild mints a second variant for the same printing.
    owner = indexes.by_printing_sha.get(printing_sha)
    if owner is not None:
        blockers = _adoption_blockers(fingerprint, indexes.variants[owner])
        if blockers:
            return Verdict(
                gemrate_id, VERDICT_AMBIGUOUS,
                f"print_identity_unrepresentable_vs_variant_{owner}", population,
                tcg_code=tcg_code, variant_id=owner,
                detail={**base, "blockers": blockers},
            )
        return Verdict(
            gemrate_id, VERDICT_AUTO, "existing_printing_sha", population,
            tcg_code=tcg_code, variant_id=owner, resolution="existing_printing_sha",
            detail=base,
        )

    # Layer 1 -- content-addressed opaque id.
    if opaque in indexes.by_opaque:
        existing = indexes.by_opaque[opaque]
        blockers = _adoption_blockers(fingerprint, indexes.variants[existing])
        if blockers:
            return Verdict(
                gemrate_id, VERDICT_AMBIGUOUS,
                f"opaque_collision_vs_variant_{existing}", population,
                tcg_code=tcg_code, variant_id=existing,
                detail={**base, "blockers": blockers},
            )
        return Verdict(
            gemrate_id, VERDICT_AUTO, "existing_opaque", population,
            tcg_code=tcg_code, variant_id=existing, resolution="existing_opaque",
            detail=base,
        )

    # Layer 2 -- printing key.
    key = (
        tcg_code.casefold(),
        str(fields["card_language"]).casefold(),
        str(fields["set_name"]).strip().casefold(),
        collector.display.strip().casefold(),
    )
    candidates = list(indexes.by_printing.get(key, []))
    if len(candidates) > 1:
        return Verdict(
            gemrate_id, VERDICT_AMBIGUOUS,
            f"printing_key_hits_{len(candidates)}_variants", population,
            tcg_code=tcg_code, detail={**base, "blockers": [f"candidates:{candidates}"]},
        )
    if len(candidates) == 1:
        existing = candidates[0]
        blockers = _adoption_blockers(fingerprint, indexes.variants[existing])
        if blockers:
            return Verdict(
                gemrate_id, VERDICT_AMBIGUOUS,
                f"printing_key_conflict_vs_variant_{existing}", population,
                tcg_code=tcg_code, variant_id=existing,
                detail={**base, "blockers": blockers},
            )
        return Verdict(
            gemrate_id, VERDICT_AUTO, "existing_printing", population,
            tcg_code=tcg_code, variant_id=existing, resolution="existing_printing",
            detail=base,
        )

    # Layer 3 -- genuinely new card.
    return Verdict(
        gemrate_id, VERDICT_AUTO, "new_variant", population,
        tcg_code=tcg_code, variant_id=None, resolution="new_variant", detail=base,
    )


def queue(verdicts: Sequence[Verdict]) -> list[dict[str, Any]]:
    """The rows a human has to look at, pop first. Never a promise of retry."""

    rows = [v for v in verdicts if v.verdict in NEEDS_HUMAN_VERDICTS]
    rows.sort(key=lambda v: (-v.population, v.gemrate_id))
    return [v.as_report() for v in rows]


# --------------------------------------------------------------------------
# ratchet
# --------------------------------------------------------------------------


def headroom(cursor: Any, *, reserve: int = RESERVE) -> dict[str, Any]:
    """How many cards may be interned before daily-accept's ratchet ABORTs.

    Interning a card gives it a variant and a non_qualified-free cohort, so it
    joins _discovery_gap_rows immediately -- a gemrate binding is NOT one of the
    three sources that census counts. The cap is therefore the whole cost.
    """

    if not rebuild.DISCOVERY_BASELINE.is_file():
        raise SystemExit(
            f"intake ABORT: discovery baseline missing at {rebuild.DISCOVERY_BASELINE}."
            " An absent baseline is not a passing check."
        )
    baseline = json.loads(rebuild.DISCOVERY_BASELINE.read_text(encoding="utf-8"))
    if int(baseline.get("population", 0)) != POP_FLOOR:
        raise SystemExit(
            "intake ABORT: discovery baseline was written for population"
            f" {baseline.get('population')}, intake admits at {POP_FLOOR}"
        )
    allowed = {str(k): int(v) for k, v in (baseline.get("noCandidateAtPop") or {}).items()}
    gap = rebuild._discovery_gap_census(cursor)
    cap = {
        tcg: max(0, allowed.get(tcg, 0) - gap.get(tcg, 0) - reserve)
        for tcg in sorted(set(allowed) | set(gap))
    }
    return {
        "baselineNoCandidateAtPop": allowed,
        "gapCensus": gap,
        "reserve": reserve,
        "cap": cap,
    }


def select_interned(
    autos: Sequence[Verdict],
    *,
    cap: Mapping[str, int],
    max_seed: int,
    census_stale: bool,
) -> tuple[list[Verdict], list[dict[str, Any]]]:
    ordered = sorted(autos, key=lambda v: (-v.population, v.gemrate_id))
    interned: list[Verdict] = []
    deferred: list[dict[str, Any]] = []
    used: dict[str, int] = {}
    for verdict in ordered:
        if census_stale:
            deferred.append({**verdict.as_report(), "deferReason": "census_stale"})
            continue
        if len(interned) >= max_seed:
            deferred.append({**verdict.as_report(), "deferReason": f"max_seed:{max_seed}"})
            continue
        tcg = verdict.tcg_code
        allowance = int(cap.get(tcg, 0))
        if used.get(tcg, 0) + 1 > allowance:
            deferred.append({**verdict.as_report(), "deferReason": f"ratchet_cap:{tcg}:{allowance}"})
            continue
        used[tcg] = used.get(tcg, 0) + 1
        interned.append(verdict)
    return interned, deferred


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


def _table_exists(cursor: Any, table: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) AS n FROM information_schema.TABLES"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return int((cursor.fetchone() or {}).get("n") or 0) > 0


def apply_preconditions(cursor: Any, census_result: Census) -> list[str]:
    """Everything that has to be true before --apply may write a single row."""

    problems: list[str] = []
    if census_result.missing:
        problems.append("censusMissing")
    elif census_result.stale:
        problems.append(f"censusStale:{census_result.age_days:.2f}d")
    cursor.execute(
        "SELECT 1 AS ok FROM cardz_schema_version WHERE version_code = %s",
        (SCHEMA_VERSION,),
    )
    if not cursor.fetchone():
        problems.append(f"schemaVersionMissing:{SCHEMA_VERSION}")
    if not _table_exists(cursor, DECISION_TABLE):
        problems.append(f"tableMissing:{DECISION_TABLE}")
    return problems


def apply(
    conn: Any,
    *,
    generation: str,
    interned: Sequence[Verdict],
    verdicts: Sequence[Verdict],
    now: datetime | None = None,
    cards_dir: Path | None = None,
) -> dict[str, Any]:
    """One transaction, inside the operator lease. Any refusal rolls back all."""

    moment = (now or _utc_now()).replace(tzinfo=None)
    capture_root = cards_dir or gemrate_source.CARDS_DIR
    counts = {
        "variantsCreated": 0, "variantsReused": 0, "bindings": 0,
        "captureReceipts": 0, "membersUpdated": 0, "decisions": 0,
    }
    written: list[dict[str, Any]] = []
    ledger: dict[str, Any] = {}
    try:
        with conn.cursor() as cursor:
            for verdict in interned:
                gid = verdict.gemrate_id
                fields = dict(verdict.detail["fields"])
                # Re-prove the capture at write time. The member row was written
                # by a rebuild days ago; if the capture moved since, the evidence
                # this binding claims no longer exists.
                fingerprint, reason = rebuild._capture_fingerprint(capture_root, gid)
                if fingerprint is None:
                    raise RuntimeError(f"intake ABORT {gid}: capture unusable ({reason})")
                fresh_fields, fresh_reason = rebuild._derive_print_fields(fingerprint)
                if fresh_fields is None:
                    raise RuntimeError(f"intake ABORT {gid}: fields underivable ({fresh_reason})")
                if rebuild._gemrate_printing_sha(fresh_fields) != verdict.detail["printingSha256"]:
                    raise RuntimeError(
                        f"intake ABORT {gid}: capture drifted since classification"
                    )
                capture_path = capture_root / gid / "card_details.json"
                capture_sha = rebuild.sha256_bytes(capture_path.read_bytes())
                evidence, evidence_sha = rebuild._gemrate_bind_evidence(fingerprint, generation)

                variant_id = verdict.variant_id
                if variant_id is None:
                    cursor.execute(
                        "INSERT INTO catalog_variant (opaque_id, tcg_code, card_language,"
                        " canonical_name, set_name, set_code, printing_code, rarity_code,"
                        " collector_number, identity_status)"
                        " VALUES (%s, %s, %s, %s, %s, '', '', '', %s, 'confirmed')",
                        (
                            verdict.detail["opaqueId"],
                            fields["tcg_code"],
                            fields["card_language"],
                            str(verdict.detail["canonicalName"])[:255],
                            str(fields["set_name"])[:255],
                            str(fields["collector_number"])[:96],
                        ),
                    )
                    variant_id = int(cursor.lastrowid)
                    counts["variantsCreated"] += 1
                    # The printing sha is the lock that stops the next rebuild
                    # minting this same card a second time.
                    cursor.execute(
                        "INSERT INTO catalog_printing_identity (variant_id, tcg_code,"
                        " card_language, set_name, set_code, printing_code, rarity_code,"
                        " collector_number, edition_code, parallel_code, finish_code,"
                        " canonical_printing_sha256, identity_status, evidence_sha256,"
                        " provenance_json, observed_at)"
                        " VALUES (%s, %s, %s, %s, '', '', '', %s, '', %s, '', %s,"
                        " 'confirmed', %s, %s, %s)",
                        (
                            variant_id, fields["tcg_code"], fields["card_language"],
                            str(fields["set_name"])[:255],
                            str(fields["collector_number"])[:96],
                            str(fields["parallel_code"])[:96],
                            verdict.detail["printingSha256"], evidence_sha,
                            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                            moment,
                        ),
                    )
                else:
                    counts["variantsReused"] += 1

                cursor.execute(
                    "INSERT INTO catalog_source_identity (source_code,"
                    " external_entity_id, variant_id, match_status, evidence_sha256,"
                    " source_product_number, bound_set_code, bound_printing_code,"
                    " bind_evidence_json, bound_tcg_code, bound_card_language,"
                    " bound_collector_number, bound_edition_code, bound_parallel_code,"
                    " bound_finish_code)"
                    " VALUES ('gemrate', %s, %s, 'exact', %s, %s, '', '', %s, %s, %s,"
                    " %s, '', %s, '')"
                    " ON DUPLICATE KEY UPDATE variant_id=VALUES(variant_id),"
                    " match_status=VALUES(match_status),"
                    " evidence_sha256=VALUES(evidence_sha256),"
                    " bind_evidence_json=VALUES(bind_evidence_json)",
                    (
                        gid, variant_id, evidence_sha,
                        str(fingerprint.get("cardNumber") or "")[:64],
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                        str(fields["tcg_code"])[:32],
                        str(fields["card_language"])[:8],
                        str(fields["collector_number"])[:96],
                        str(fields["parallel_code"])[:64],
                    ),
                )
                counts["bindings"] += 1

                cursor.execute(
                    "INSERT INTO catalog_provider_capture_receipt (source_code,"
                    " external_entity_id, capture_sha256, capture_path,"
                    " captured_at, generation_id, parser_version)"
                    " VALUES ('gemrate', %s, %s, %s, %s, %s, %s)"
                    " ON DUPLICATE KEY UPDATE capture_path=VALUES(capture_path),"
                    " captured_at=VALUES(captured_at),"
                    " generation_id=VALUES(generation_id),"
                    " parser_version=VALUES(parser_version)",
                    (
                        gid, capture_sha,
                        f"data/private/gemrate/cards/{gid}/card_details.json"[:500],
                        moment, generation, PARSER_VERSION,
                    ),
                )
                counts["captureReceipts"] += 1

                cursor.execute(
                    "UPDATE catalog_rebuild_member SET variant_id=%s,"
                    " latest_psa10_population=GREATEST(latest_psa10_population, %s),"
                    " cohort=%s, identity_pending=%s, computed_at=%s"
                    " WHERE generation_id=%s AND gemrate_id=%s AND variant_id IS NULL",
                    (
                        variant_id, int(verdict.population), MEMBER_COHORT,
                        MEMBER_IDENTITY_PENDING, moment, generation, gid,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"intake ABORT {gid}: member update touched {cursor.rowcount}"
                        " rows, expected exactly 1 (NULL -> non-NULL)"
                    )
                counts["membersUpdated"] += 1
                written.append({
                    **verdict.as_report(),
                    "variantId": variant_id,
                    "evidenceSha256": evidence_sha,
                })

            # Same transaction: rebuild_ledger's own assert is ledger == catalog,
            # and a new variant with no ledger row makes the NEXT discover lane
            # raise instead of run.
            ledger = discovery_ledger.rebuild_ledger(cursor)

            for verdict in verdicts:
                cursor.execute(
                    DECISION_UPSERT,
                    (
                        verdict.gemrate_id, generation, verdict.verdict,
                        verdict.reason_code[:191], verdict.variant_id,
                        str(verdict.detail.get("printingSha256") or "")[:64],
                        moment,
                    ),
                )
                counts["decisions"] += 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"counts": counts, "ledger": ledger, "interned": written}


# --------------------------------------------------------------------------
# receipt
# --------------------------------------------------------------------------


def write_receipt(report: Mapping[str, Any], *, business_date: str, out: Path | None = None) -> Path:
    path = out or (RECEIPT_DIR / f"identity-intake-{business_date}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def run(
    conn: Any,
    *,
    census_result: Census,
    generation: str | None = None,
    max_seed: int = DEFAULT_MAX_SEED,
    reserve: int = RESERVE,
    do_apply: bool = False,
    now: datetime | None = None,
) -> tuple[dict[str, Any], int]:
    moment = now or _utc_now()
    with conn.cursor() as cursor:
        gen = generation or latest_generation(cursor)
        indexes = load_catalog_indexes(cursor)
        members = load_members(cursor, gen, census_result.rows)
        head = headroom(cursor, reserve=reserve)
        refusals = apply_preconditions(cursor, census_result) if do_apply else []

    verdicts = [
        classify(gid, population=pop, member=members.get(gid), indexes=indexes)
        for gid, pop in sorted(census_result.rows.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    buckets = {name: 0 for name in VERDICT_ORDER}
    for verdict in verdicts:
        buckets[verdict.verdict] = buckets.get(verdict.verdict, 0) + 1

    autos = [v for v in verdicts if v.verdict == VERDICT_AUTO]
    interned, deferred = select_interned(
        autos, cap=head["cap"], max_seed=max_seed, census_stale=census_result.stale,
    )

    report: dict[str, Any] = {
        "tool": "gemrate_identity_intake",
        "mode": "apply" if do_apply else "dry-run",
        "ranAt": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generation": gen,
        **census_result.as_report(),
        "membersJoined": len(members),
        "buckets": buckets,
        "headroom": head,
        "maxSeed": max_seed,
        "interned": [v.as_report() for v in interned],
        "deferredByRatchet": deferred,
        "needsHuman": queue(verdicts),
        "applyRefused": refusals,
        "written": None,
    }

    if not do_apply:
        report["ok"] = True
        return report, 0
    if refusals:
        report["ok"] = False
        # Refusal is the whole point of the flag existing before 055 is applied.
        return report, 2

    import operator_control

    with operator_control.operator_e2e_lease("v2-identity-intake"):
        report["written"] = apply(
            conn, generation=gen, interned=interned, verdicts=verdicts,
            now=moment,
        )
    report["ok"] = True
    return report, 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--census", type=Path, default=None,
                        help="psa10_1000_plus.jsonl（唔畀就按 CENSUS_SEARCH_ROOTS 搵）")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--max-seed", type=int, default=DEFAULT_MAX_SEED)
    parser.add_argument("--reserve", type=int, default=RESERVE)
    parser.add_argument("--generation", default=None)
    parser.add_argument("--business-date", default=None)
    parser.add_argument("--refresh", action="store_true",
                        help="行 gemrate_brute_harvest.py --all-sets（預設關）")
    parser.add_argument("--apply", action="store_true",
                        help="唯一寫入路徑；前置條件唔齊就拒絕，零寫入")
    parser.add_argument("--report-out", type=Path, default=None)
    parser.add_argument("--credentials-env", type=Path,
                        default=rebuild.DAILY_CREDENTIALS_ENV
                        if rebuild.DAILY_CREDENTIALS_ENV.exists()
                        else rebuild.DEFAULT_CREDENTIALS_ENV)
    args = parser.parse_args(argv)

    now = _utc_now()
    business_date = args.business_date or now.strftime("%Y-%m-%d")
    refresh_result = refresh_census() if args.refresh else None
    if refresh_result is not None and refresh_result["returncode"] != 0:
        print(json.dumps({"ok": False, "censusRefresh": refresh_result}, ensure_ascii=False))
        return 1

    census_result = census(args.census, max_age_days=args.max_age_days, now=now)
    conn = rebuild.connect(args.credentials_env)
    try:
        with conn.cursor() as cursor:
            # Same read-path cap the rest of the 036 read path uses; a runaway
            # SELECT here is what ate MySQL 3308 for 6272 seconds on 08-22.
            cursor.execute("SET SESSION max_execution_time=60000")
        report, code = run(
            conn,
            census_result=census_result,
            generation=args.generation,
            max_seed=args.max_seed,
            reserve=args.reserve,
            do_apply=bool(args.apply),
            now=now,
        )
    finally:
        conn.close()

    if refresh_result is not None:
        report["censusRefresh"] = refresh_result
    report["receiptPath"] = str(
        args.report_out or (RECEIPT_DIR / f"identity-intake-{business_date}.json")
    )
    write_receipt(report, business_date=business_date, out=args.report_out)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
