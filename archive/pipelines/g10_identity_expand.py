#!/usr/bin/env python3
"""Expand `catalog_source_identity` coverage for grade10-scraper card directories.

G10 目錄 → variant 嘅對應率係其他幾條線嘅閘。呢個腳本用可查證嘅證據把對應率推高，
對唔到嘅一律入 `market_identity_review_queue`，唔准靜靜丟。

證據階梯（強 → 弱，全部喺 641 個真實目錄上校準過）:

  T0 direct        `catalog_source_identity(snkrdunk|ebay, <目錄名>)` 已經存在。
  T1 gemrate       `populations.json` 嘅 `source` URL 帶 40-hex `gemrate_id`，
                   同 `catalog_source_identity(gemrate, ...)` 係同一個 id space。
                   641 個目錄全部有 gemrate_id；377 個喺我哋 DB 有 anchor。
                   同 T0 重疊嘅 317 個之中 300 個一致（94.6%），17 個衝突（見下）。
  T2 name+col      normalize 後 (cardName, cardId) 喺 catalog_variant 唯一命中。
                   喺 455 個已知正確嘅目錄上量度：375 TP / 0 FP → precision 1.0000。
  T3 tiebreak      T2 撞多個候選，再用 setName 或 language 收窄到唯一。
                   已知樣本 10/10 正確，樣本細，所以 match_status 只記 'derived'。

明確唔做嘅嘢:
  * 唔准淨靠卡名 join。641 個目錄得 371 個 unique 卡名，而且實測有 92 個目錄
    「卡名喺 catalog 有、但 collector number 唔同」——例如 G10 `Pikachu V #1`
    (Start Deck 100) vs 我哋嘅 `Pikachu V 104/100`，係兩張唔同嘅卡。
    所以卡名淨係做 T2/T3 嘅其中一項，永遠唔可以單獨成立。
  * 唔改 `catalog_variant` 任何 canonical 欄位。
  * 對唔到就留低唔寫，入 review queue。

Exit codes: 0 = 正常, 2 = G10 目錄唔見。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args

DEFAULT_G10_ROOT = ROOT.parent / "grade10-scraper" / "data" / "cards"

RUN_SOURCE_CODE = "g10_identity"
ALIAS_PROVIDER_CODE = "gemrate"

# G10 目錄 provider → `catalog_source_identity.source_code`
PROVIDER_IDENTITY_SOURCE = {"altxyz": "ebay", "snkrdunk": "snkrdunk"}
# 同上，但用喺 `catalog_provider_identity_alias.alias_type`。
# 刻意避開 db_runtime.GEMRATE_ALIAS_TYPES ({entity,universal,grader_member,spec})，
# 咁 GemRate receipt import 同呢度嘅 row 永遠唔會撞 semantic key。
PROVIDER_ALIAS_TYPE = {"altxyz": "g10_altxyz_asset", "snkrdunk": "g10_snkrdunk_asset"}

G10_TO_DB_LANGUAGE = {"jp": "ja", "ja": "ja", "en": "en", "zh-hans": "zhCN", "zh-hant": "zhTW"}

GEMRATE_ID_PATTERN = re.compile(r"gemrate_id=([0-9a-fA-F]{40})")

# review queue reason codes
REASON_NO_ASSET_INFO = "g10_no_asset_info"
REASON_NAME_NOT_IN_CATALOG = "g10_name_not_in_catalog"
REASON_COLLECTOR_MISMATCH = "g10_collector_number_mismatch"
REASON_AMBIGUOUS = "g10_ambiguous_candidates"
REASON_VARIANT_CONFLICT = "g10_variant_duplicate_conflict"


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #

CJK_KEEP = "　-鿿＀-￯"


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.sub(rf"[^0-9a-z{CJK_KEEP}]+", " ", text).split())


def normalize_collector(value: Any) -> str:
    """Order/format-insensitive collector key.

    `SM-P 288` / `288/SM-P` → `288|p|sm`; `56/76` / `056/076` → `56|76`.
    喺 455 個已知正確配對上，raw 相等得 265，normalize 之後 437。
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    tokens = [tok for tok in re.split(r"[^0-9a-z]+", text) if tok]
    tokens = [(tok.lstrip("0") or "0") if tok.isdigit() else tok for tok in tokens]
    return "|".join(sorted(tokens))


def normalize_set(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    if ":" in text:
        text = text.split(":", 1)[0]
    return " ".join(re.sub(rf"[^0-9a-z{CJK_KEEP}]+", " ", text).split())


def normalize_language(value: Any) -> str:
    text = str(value or "").casefold()
    return G10_TO_DB_LANGUAGE.get(text, text)


# --------------------------------------------------------------------------- #
# G10 scan
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class G10Asset:
    provider: str          # altxyz | snkrdunk
    directory: str         # 目錄名 = external_entity_id
    gemrate_id: str | None
    card_name: str | None
    set_name: str | None
    collector_number: str | None
    language: str | None
    source_pointer: str
    observed_at: datetime
    set_code: str | None = None
    printing_code: str | None = None
    rarity_code: str | None = None
    gemrate_source_pointer: str | None = None
    gemrate_observed_at: datetime | None = None

    @property
    def identity_source(self) -> str:
        return PROVIDER_IDENTITY_SOURCE[self.provider]

    @property
    def has_asset_info(self) -> bool:
        return self.card_name is not None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def scan_g10_root(root: Path) -> list[G10Asset]:
    assets: list[G10Asset] = []
    for provider in sorted(PROVIDER_IDENTITY_SOURCE):
        provider_dir = root / provider
        if not provider_dir.is_dir():
            continue
        for card_dir in sorted(provider_dir.iterdir()):
            if not card_dir.is_dir():
                continue
            populations = card_dir / "populations.json"
            gemrate_id = None
            observed_epoch = None
            gemrate_observed_at = None
            if populations.is_file():
                match = GEMRATE_ID_PATTERN.search(populations.read_text(encoding="utf-8", errors="replace"))
                if match:
                    gemrate_id = match.group(1).casefold()
                observed_epoch = populations.stat().st_mtime
                gemrate_observed_at = datetime.fromtimestamp(
                    observed_epoch, tz=timezone.utc
                ).replace(tzinfo=None)
            asset_info = card_dir / "asset_info.json"
            info = _read_json(asset_info)
            info = info if isinstance(info, Mapping) else {}
            if asset_info.is_file():
                observed_epoch = asset_info.stat().st_mtime
            observed_at = datetime.fromtimestamp(observed_epoch or 0, tz=timezone.utc).replace(tzinfo=None)
            assets.append(
                G10Asset(
                    provider=provider,
                    directory=card_dir.name,
                    gemrate_id=gemrate_id,
                    card_name=info.get("cardName"),
                    set_name=info.get("setName"),
                    collector_number=info.get("cardId"),
                    language=info.get("language"),
                    set_code=info.get("setCode"),
                    printing_code=info.get("printingCode"),
                    rarity_code=info.get("rarityCode"),
                    gemrate_source_pointer=(
                        f"grade10-scraper/data/cards/{provider}/{card_dir.name}/populations.json"
                        if populations.is_file()
                        else None
                    ),
                    gemrate_observed_at=gemrate_observed_at,
                    source_pointer=(
                        f"grade10-scraper/data/cards/{provider}/{card_dir.name}/asset_info.json"
                        if asset_info.is_file()
                        else f"grade10-scraper/data/cards/{provider}/{card_dir.name}/populations.json"
                    ),
                    observed_at=observed_at,
                )
            )
    return assets


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class VariantRow:
    id: int
    canonical_name: str
    set_name: str
    collector_number: str
    card_language: str


@dataclass
class Resolution:
    asset: G10Asset
    variant_id: int | None = None
    method: str = "unresolved"
    match_status: str = ""
    reason_code: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    write_identity: bool = False
    write_alias: bool = False

    @property
    def resolved(self) -> bool:
        return self.variant_id is not None

    @property
    def evidence_sha256(self) -> str:
        return sha256(canonical_json(self.evidence))


def build_variant_indexes(variants: Sequence[VariantRow]) -> dict[str, Any]:
    by_name_collector: dict[tuple[str, str], set[int]] = defaultdict(set)
    by_name: dict[str, set[int]] = defaultdict(set)
    for row in variants:
        by_name_collector[(normalize_name(row.canonical_name), normalize_collector(row.collector_number))].add(row.id)
        by_name[normalize_name(row.canonical_name)].add(row.id)
    return {"name_collector": by_name_collector, "name": by_name, "rows": {r.id: r for r in variants}}


def resolve_asset(
    asset: G10Asset,
    identities: Mapping[tuple[str, str], int],
    indexes: Mapping[str, Any],
) -> Resolution:
    direct = identities.get((asset.identity_source, asset.directory))
    gem = identities.get(("gemrate", asset.gemrate_id)) if asset.gemrate_id else None

    # --- T0/T1 -------------------------------------------------------------- #
    if direct is not None and gem is not None and direct != gem:
        # 我哋 catalog 有兩條 variant 指住同一張實體卡（set_name 寫法唔同）。
        # 目錄本身早就有 direct identity（唔係我開嘅），所以照計已對到，
        # 但唔准攞 gemrate 嗰個頂替 direct，亦唔寫 alias——交返 review 由人決定合併邊條。
        rows = indexes["rows"]
        return Resolution(
            asset=asset,
            variant_id=direct,
            method="direct+gemrate_conflict",
            match_status="exact",
            reason_code=REASON_VARIANT_CONFLICT,
            evidence={
                "directVariantId": direct,
                "gemrateVariantId": gem,
                "gemrateId": asset.gemrate_id,
                "directVariant": _variant_evidence(rows.get(direct)),
                "gemrateVariant": _variant_evidence(rows.get(gem)),
            },
        )
    if direct is not None:
        return Resolution(
            asset=asset,
            variant_id=direct,
            method="direct" if gem is None else "direct+gemrate",
            match_status="exact",
            evidence={"directVariantId": direct, "gemrateId": asset.gemrate_id},
            write_identity=False,
            write_alias=gem is not None,
        )
    if gem is not None:
        return Resolution(
            asset=asset,
            variant_id=gem,
            method="gemrate",
            match_status="exact",
            evidence={"gemrateId": asset.gemrate_id, "gemrateVariantId": gem},
            write_identity=True,
            write_alias=True,
        )

    # --- T2/T3 -------------------------------------------------------------- #
    if not asset.has_asset_info:
        return Resolution(asset=asset, method="unresolved", reason_code=REASON_NO_ASSET_INFO,
                          evidence={"directory": asset.directory, "provider": asset.provider})

    name_key = normalize_name(asset.card_name)
    collector_key = normalize_collector(asset.collector_number)
    candidates = set(indexes["name_collector"].get((name_key, collector_key), ()))
    base_evidence = {
        "cardName": asset.card_name,
        "setName": asset.set_name,
        "cardId": asset.collector_number,
        "language": asset.language,
        "normalizedName": name_key,
        "normalizedCollector": collector_key,
    }

    if len(candidates) == 1:
        return Resolution(
            asset=asset, variant_id=next(iter(candidates)), method="name_collector",
            match_status="derived",
            evidence={**base_evidence, "candidateVariantIds": sorted(candidates)},
            write_identity=True,
        )

    if len(candidates) > 1:
        rows = indexes["rows"]
        set_key = normalize_set(asset.set_name)
        narrowed = [vid for vid in candidates if normalize_set(rows[vid].set_name) == set_key]
        method = "name_collector_set"
        if len(narrowed) != 1:
            language = normalize_language(asset.language)
            narrowed = [vid for vid in candidates if rows[vid].card_language == language]
            method = "name_collector_language"
        if len(narrowed) == 1:
            return Resolution(
                asset=asset, variant_id=narrowed[0], method=method, match_status="derived",
                evidence={**base_evidence, "candidateVariantIds": sorted(candidates), "tiebreak": method},
                write_identity=True,
            )
        return Resolution(asset=asset, method="unresolved", reason_code=REASON_AMBIGUOUS,
                          evidence={**base_evidence, "candidateVariantIds": sorted(candidates)})

    if name_key in indexes["name"]:
        return Resolution(asset=asset, method="unresolved", reason_code=REASON_COLLECTOR_MISMATCH,
                          evidence={**base_evidence, "sameNameVariantIds": sorted(indexes["name"][name_key])})
    return Resolution(asset=asset, method="unresolved", reason_code=REASON_NAME_NOT_IN_CATALOG,
                      evidence=base_evidence)


def _variant_evidence(row: VariantRow | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id, "name": row.canonical_name, "setName": row.set_name,
        "collectorNumber": row.collector_number, "language": row.card_language,
    }


def resolve_all(
    assets: Iterable[G10Asset],
    identities: Mapping[tuple[str, str], int],
    indexes: Mapping[str, Any],
) -> list[Resolution]:
    return [resolve_asset(asset, identities, indexes) for asset in assets]


def summarize(resolutions: Sequence[Resolution]) -> dict[str, Any]:
    methods = Counter(r.method for r in resolutions)
    reasons = Counter(r.reason_code for r in resolutions if r.reason_code)
    resolved = [r for r in resolutions if r.resolved]
    unresolved = [r for r in resolutions if not r.resolved]
    rejected = sum(1 for r in unresolved if r.reason_code == REASON_NO_ASSET_INFO)
    quarantined = len(unresolved) - rejected
    return {
        "observed": len(resolutions),
        "accepted": len(resolved),
        "quarantined": quarantined,
        "rejected": rejected,
        "methods": dict(methods),
        "reasons": dict(reasons),
        "newIdentities": sum(1 for r in resolutions if r.write_identity),
        "aliasRows": sum(1 for r in resolutions if r.write_alias),
        "byProviderAccepted": dict(Counter(r.asset.provider for r in resolved)),
        "byProviderUnresolved": dict(Counter(r.asset.provider for r in resolutions if not r.resolved)),
    }


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #

def load_identities(cursor: Any) -> dict[tuple[str, str], int]:
    cursor.execute("SELECT source_code, external_entity_id, variant_id FROM catalog_source_identity")
    return {(row["source_code"], row["external_entity_id"]): int(row["variant_id"]) for row in cursor.fetchall()}


def load_variants(cursor: Any) -> list[VariantRow]:
    cursor.execute(
        "SELECT id, canonical_name, set_name, collector_number, card_language FROM catalog_variant"
    )
    return [
        VariantRow(
            id=int(row["id"]), canonical_name=row["canonical_name"], set_name=row["set_name"],
            collector_number=row["collector_number"], card_language=row["card_language"],
        )
        for row in cursor.fetchall()
    ]


def compute_run_key(assets: Sequence[G10Asset]) -> str:
    """Identify the *input* batch, never the outcome.

    第一次 --write 之後，同一批目錄嘅判定會由 `gemrate` 變成 `direct+gemrate`
    （因為 identity 已經寫咗）。如果 run_key 跟判定行，第二次跑就會開多一條
    `market_ingest_run`。所以 key 只可以睇 G10 掃返嚟嘅內容。
    """
    payload = [
        {
            "provider": a.provider, "directory": a.directory, "gemrateId": a.gemrate_id,
            "cardName": a.card_name, "setName": a.set_name,
            "cardId": a.collector_number, "language": a.language,
            "setCode": a.set_code, "printingCode": a.printing_code,
            "rarityCode": a.rarity_code,
        }
        for a in assets
    ]
    return sha256(canonical_json({"pipeline": "g10_identity_expand", "assets": payload}))


def _open_run(cursor: Any, run_key: str, summary: Mapping[str, Any], started_at: datetime) -> int:
    cursor.execute("SELECT id FROM market_ingest_run WHERE run_key = %s", (run_key,))
    existing = cursor.fetchone()
    if existing:
        run_id = int(existing["id"])
        cursor.execute(
            "UPDATE market_ingest_run SET status='running', started_at=%s, completed_at=NULL, error_summary=NULL "
            "WHERE id=%s",
            (started_at, run_id),
        )
        return run_id
    cursor.execute(
        """
        INSERT INTO market_ingest_run
            (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
             status, observed_count, accepted_count, quarantined_count, rejected_count, started_at)
        VALUES (%s, %s, 'backfill', %s, %s, %s, 'running', %s, %s, %s, %s, %s)
        """,
        (
            run_key, RUN_SOURCE_CODE, started_at, run_key, run_key,
            summary["observed"], summary["accepted"], summary["quarantined"], summary["rejected"], started_at,
        ),
    )
    return int(cursor.lastrowid)


def _insert_identity(cursor: Any, resolution: Resolution) -> bool:
    asset = resolution.asset
    key = (asset.identity_source, asset.directory)
    cursor.execute(
        "SELECT variant_id FROM catalog_source_identity WHERE source_code=%s AND external_entity_id=%s FOR UPDATE",
        key,
    )
    existing = cursor.fetchone()
    if existing is not None:
        if int(existing["variant_id"]) != resolution.variant_id:
            raise ValueError(f"source identity ownership changed: {key[0]}:{key[1]}")
        return False
    provider_claims = {
        "provider": asset.provider,
        "externalEntityId": asset.directory,
        "cardName": asset.card_name,
        "setName": asset.set_name,
        "productNumber": asset.collector_number,
        "language": asset.language,
        "setCode": asset.set_code,
        "printingCode": asset.printing_code,
        "rarityCode": asset.rarity_code,
        "sourcePointer": asset.source_pointer,
        "observedAt": asset.observed_at.isoformat(),
    }
    bind_evidence = {
        "schemaVersion": 1,
        "matchMethod": resolution.method,
        "providerClaims": provider_claims,
        "resolutionEvidence": resolution.evidence,
    }
    source_product_number = str(asset.collector_number or "")
    bound_set_code = str(asset.set_code or "")
    bound_printing_code = str(asset.printing_code or "")
    if (
        len(source_product_number) > 191
        or len(bound_set_code) > 64
        or len(bound_printing_code) > 64
    ):
        raise ValueError(f"provider claim exceeds schema limit: {key[0]}:{key[1]}")
    cursor.execute(
        """
        INSERT INTO catalog_source_identity
            (source_code, external_entity_id, variant_id, match_status, evidence_sha256,
             source_product_number, bound_set_code, bound_printing_code, bind_evidence_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            *key, resolution.variant_id, resolution.match_status, resolution.evidence_sha256,
            source_product_number, bound_set_code, bound_printing_code,
            canonical_json(bind_evidence).decode("utf-8"),
        ),
    )
    return True


def _insert_alias(cursor: Any, resolution: Resolution) -> bool:
    asset = resolution.asset
    semantic = (ALIAS_PROVIDER_CODE, PROVIDER_ALIAS_TYPE[asset.provider], asset.directory, "")
    association = (*semantic, asset.gemrate_id)
    cursor.execute(
        "SELECT DISTINCT variant_id FROM catalog_provider_identity_alias "
        "WHERE provider_code=%s AND alias_type=%s AND alias_value=%s AND grader_code=%s FOR UPDATE",
        semantic,
    )
    if any(int(row["variant_id"]) != resolution.variant_id for row in cursor.fetchall()):
        raise ValueError(f"provider alias ownership changed: {semantic[1]}:{semantic[2]}")
    cursor.execute(
        "SELECT variant_id FROM catalog_provider_identity_alias "
        "WHERE provider_code=%s AND alias_type=%s AND alias_value=%s AND grader_code=%s "
        "AND requested_external_entity_id=%s FOR UPDATE",
        association,
    )
    if cursor.fetchone() is not None:
        return False
    cursor.execute(
        """
        INSERT INTO catalog_provider_identity_alias
            (provider_code, alias_type, alias_value, grader_code, requested_external_entity_id,
             variant_id, match_status, receipt_payload_sha256, source_pointer, observed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            *association, resolution.variant_id, resolution.match_status,
            resolution.evidence_sha256,
            asset.gemrate_source_pointer or asset.source_pointer,
            asset.gemrate_observed_at or asset.observed_at,
        ),
    )
    return True


def _insert_review(cursor: Any, run_id: int, resolution: Resolution) -> bool:
    asset = resolution.asset
    key = (asset.identity_source, asset.directory, resolution.reason_code, resolution.evidence_sha256)
    cursor.execute(
        "SELECT id FROM market_identity_review_queue "
        "WHERE source_code=%s AND external_entity_id=%s AND reason_code=%s AND evidence_sha256=%s",
        key,
    )
    if cursor.fetchone() is not None:
        return False
    cursor.execute(
        """
        INSERT INTO market_identity_review_queue
            (run_id, source_code, external_entity_id, reason_code, evidence_sha256, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
        """,
        (run_id, *key),
    )
    return True


def apply_resolutions(connection: Any, resolutions: Sequence[Resolution], *, commit: bool) -> dict[str, Any]:
    summary = summarize(resolutions)
    run_key = compute_run_key([r.asset for r in resolutions])
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    written = Counter()
    with connection.cursor() as cursor:
        run_id = _open_run(cursor, run_key, summary, started_at)
        for resolution in resolutions:
            if resolution.write_identity and resolution.resolved:
                written["identity"] += _insert_identity(cursor, resolution)
            if resolution.write_alias and resolution.resolved and resolution.asset.gemrate_id:
                written["alias"] += _insert_alias(cursor, resolution)
            # 對唔到、或者對到但有數據質素衝突，一律入 review queue——唔准靜靜丟。
            if resolution.reason_code:
                written["review"] += _insert_review(cursor, run_id, resolution)
        cursor.execute(
            """
            UPDATE market_ingest_run
            SET status='complete', completed_at=%s, observed_count=%s, accepted_count=%s,
                quarantined_count=%s, rejected_count=%s
            WHERE id=%s
            """,
            (
                datetime.now(timezone.utc).replace(tzinfo=None), summary["observed"], summary["accepted"],
                summary["quarantined"], summary["rejected"], run_id,
            ),
        )
    if commit:
        connection.commit()
    else:
        connection.rollback()
    return {"runId": run_id, "runKey": run_key, "written": dict(written), **summary}


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def _print_report(report: Mapping[str, Any], resolutions: Sequence[Resolution], *, wrote: bool) -> None:
    print("=" * 72)
    print(f"G10 identity expand — {'WRITE' if wrote else 'DRY-RUN'}")
    print("=" * 72)
    print(f"  run_id                     : {report['runId']}")
    print(f"  run_key                    : {report['runKey']}")
    print(f"  observed (G10 目錄)        : {report['observed']}")
    print(f"  accepted (對到 variant)    : {report['accepted']}")
    print(f"  quarantined (入 review)    : {report['quarantined']}")
    print(f"  rejected (冇 asset_info)   : {report['rejected']}")
    print("\n[證據階梯]")
    for method, count in sorted(report["methods"].items(), key=lambda kv: -kv[1]):
        print(f"  {method:<24} {count}")
    print("\n[對唔到嘅原因]")
    for reason, count in sorted(report["reasons"].items(), key=lambda kv: -kv[1]):
        print(f"  {reason:<32} {count}")
    print("\n[實際寫入]")
    for table, count in sorted(report["written"].items()):
        print(f"  {table:<24} +{count}")
    print(f"\n  accepted by provider   : {report['byProviderAccepted']}")
    print(f"  unresolved by provider : {report['byProviderUnresolved']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expand G10 directory → variant identity coverage.")
    parser.add_argument("--g10-root", type=Path, default=DEFAULT_G10_ROOT)
    parser.add_argument("--write", action="store_true", help="commit（預設 dry-run）")
    parser.add_argument("--json-out", type=Path, default=None, help="把逐個目錄嘅判定寫落檔")
    add_connection_args(parser)
    args = parser.parse_args(argv)

    if not args.g10_root.is_dir():
        print(f"G10 目錄唔見: {args.g10_root}", file=sys.stderr)
        return 2

    assets = scan_g10_root(args.g10_root)
    connection = connection_from_args(args)
    try:
        with connection.cursor() as cursor:
            identities = load_identities(cursor)
            variants = load_variants(cursor)
        indexes = build_variant_indexes(variants)
        resolutions = resolve_all(assets, identities, indexes)
        report = apply_resolutions(connection, resolutions, commit=bool(args.write))
    finally:
        connection.close()

    _print_report(report, resolutions, wrote=bool(args.write))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(
                {
                    "report": report,
                    "rows": [
                        {
                            "provider": r.asset.provider, "directory": r.asset.directory,
                            "gemrateId": r.asset.gemrate_id, "method": r.method,
                            "variantId": r.variant_id, "matchStatus": r.match_status,
                            "reasonCode": r.reason_code, "evidenceSha256": r.evidence_sha256,
                            "cardName": r.asset.card_name, "setName": r.asset.set_name,
                            "cardId": r.asset.collector_number, "language": r.asset.language,
                        }
                        for r in resolutions
                    ],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n  逐行判定已寫入 {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
