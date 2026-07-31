"""SNKRDUNK bulk API provider.

直接打 SNKRDUNK 內部 JSON API（唔經 HTML scraping、唔使瀏覽器）。
全部 endpoint 經 CloudFront，無簽名、無 cookie 校驗、免登入。

同舊 LiveSnkrdunkProvider 嘅分別：
- 舊：GET /search?keyword=... 然後 regex 夾 HTML，拎一個最低價
- 新：GET /v1/apparels/{id} + /v3/products/{productCatalogId}/trading-history
      → 3 年逐日 K 線 + 最近 20 單成交 + 16 個 condition 價 + 完整 master

x-version 每次開 job 由 /apparels 頁面 HTML scrape（build tag，例如
prod-20260722-02）。佢唔送都 200，但送埋同前端一致，穩陣啲。
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests

try:
    from .failure_ledger import record_failure, record_resolution
    from .source_crosswalk import canonical_language, complete_collector_number
except ImportError:
    from failure_ledger import record_failure, record_resolution
    from source_crosswalk import canonical_language, complete_collector_number

BASE = "https://snkrdunk.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
X_VERSION_RE = re.compile(r'\\?"version\\?":\\?"(prod-\d{8}-\d{2})\\?"')


class SnkrdunkApi:
    """SNKRDUNK 內部 JSON API client（純 requests）。"""

    def __init__(self, delay: float = 1.5, timeout: int = 30, retries: int = 4):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self._last_call = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA

    # ---- internal --------------------------------------------------------
    def _request(self, url: str, **params) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            wait = self.delay - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                self._last_call = time.time()
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        retry_wait = max(float(retry_after), self.delay) if retry_after else self.delay * (2 ** attempt)
                    except ValueError:
                        retry_wait = self.delay * (2 ** attempt)
                    if attempt + 1 < self.retries:
                        time.sleep(retry_wait)
                        continue
                response.raise_for_status()
                return response
            except requests.RequestException as error:
                last_error = error
                if attempt + 1 < self.retries:
                    time.sleep(self.delay * (2 ** attempt))
                    continue
                raise
        raise RuntimeError(f"SNK request failed without a response: {url}") from last_error

    def _get(self, path: str, **params) -> dict:
        response = self._request(f"{BASE}{path}", **params)
        document = response.json()
        if not isinstance(document, dict):
            raise ValueError(f"SNK endpoint returned non-object JSON: {path}")
        return document

    # ---- x-version auto-discovery ---------------------------------------
    def fetch_x_version(self) -> str | None:
        """由 /apparels 頁面 HTML scrape 最新 build tag。搵唔到就唔送 header。"""
        r = self._request(f"{BASE}/apparels/116069")
        m = X_VERSION_RE.search(r.text)
        version = m.group(1) if m else None
        if version:
            self.session.headers["x-version"] = version
        return version

    # ---- endpoints -------------------------------------------------------
    def get_master(self, item_id: int) -> dict:
        """GET /v1/apparels/{itemId} — product master（含 productCatalogId）。"""
        return self._get(f"/v1/apparels/{item_id}")

    def get_size_chips(self, item_id: int) -> list[dict]:
        """GET /v2/products/{itemId}/size-chips — 16 condition 即時最低價。"""
        data = self._get(f"/v2/products/{item_id}/size-chips", type="apparel")
        return data.get("chips", [])

    def get_trading_history(
        self,
        product_catalog_id: int,
        range_: str = "all",
        condition_code: str | None = None,
        variant_id: int | None = None,
    ) -> dict:
        """GET /v3/products/{productCatalogId}/trading-history

        range: 1w | 1m | 3m | all（注意：而家 server 唔理 range，全部回傳全量）
        condition_code: trading_card_single_*（16 個 condition 之一）
        variant_id: 枚數 variant id（1993521=1枚 ... 1993530=10枚，逐卡唔同）
        """
        params: dict = {"range": range_}
        if condition_code:
            params["condition_code"] = condition_code
        if variant_id:
            params["variant_id"] = variant_id
        return self._get(f"/v3/products/{product_catalog_id}/trading-history", **params)

    def get_same_category(self, item_id: int, page: int = 1, per_page: int = 13) -> list[int]:
        """GET /v1/apparels/{id}/group-items/same-category — BFS 擴展用。

        返回同組其他卡嘅 apparel id list（唔包括自己）。
        """
        data = self._get(
            f"/v1/apparels/{item_id}/group-items/same-category",
            page=page,
            perPage=per_page,
        )
        return [a["id"] for a in data.get("apparels", []) if a.get("id") != item_id]

    # ---- high-level ------------------------------------------------------
    # chips.filterConditionId -> trading-history condition_code
    CONDITION_CODE_MAP = {
        "like_new": "trading_card_single_nearly_unused",
        "minor_scratches": "trading_card_single_little_scratches",
        "moderate_scratches": "trading_card_single_medium_scratches",
        "significant_damage": "trading_card_single_large_damages",
        "psa_10": "trading_card_single_psa10",
        "psa_9": "trading_card_single_psa9",
        "psa_8_below": "trading_card_single_psa8_under",
        "bgs_10_black": "trading_card_single_bgs10bl",
        "bgs_10_gold": "trading_card_single_bgs10gl",
        "bgs_9_5": "trading_card_single_bgs95",
        "bgs_9_below": "trading_card_single_bgs9_less_than_or_equal",
        "ars_10_plus": "trading_card_single_ars10plus",
        "ars_10": "trading_card_single_ars10",
        "ars_9": "trading_card_single_ars9",
        "ars_8_below": "trading_card_single_ars8_less_than_or_equal",
        "other_grading_company": "trading_card_single_other_grading_company",
    }

    def pull_card(self, item_id: int, condition_code: str | None = None) -> dict:
        """一張卡嘅完整資料：master + chips + 3 年 K 線 + 最近成交。"""
        master = self.get_master(item_id)
        pcid = master.get("productCatalogId")
        chips = self.get_size_chips(item_id)
        history = self.get_trading_history(pcid, condition_code=condition_code) if pcid else {}
        return {
            "item_id": item_id,
            "product_catalog_id": pcid,
            "product_number": master.get("productNumber"),
            "name": master.get("name"),
            "localized_name": master.get("localizedName"),
            "image_url": (master.get("primaryMedia") or {}).get("imageUrl"),
            "released_at": master.get("releasedAt"),
            "used_min_price": master.get("usedMinPrice"),
            "used_listing_count": master.get("usedListingCount"),
            "chips": [
                {
                    "condition_id": c.get("conditionId"),
                    "filter_condition_id": c.get("filterConditionId"),
                    "chart_condition_code": self.CONDITION_CODE_MAP.get(c.get("filterConditionId")),
                    "used_min_price": c.get("usedMinPrice"),
                    "has_listing": c.get("hasListing"),
                    "listing_count": c.get("listingCount"),
                }
                for c in chips
            ],
            "chart_points": (history.get("chart", {}).get("lines", [{}])[0].get("points", [])),
            "recent_trades": history.get("trades", []),
        }


def bfs_discover(api: SnkrdunkApi, seeds: list[int], max_ids: int = 2000) -> list[int]:
    """由 seed apparel ids 出發，沿 same-category 做 BFS 擴展全圖鑑。

    每張卡嘅 same-category 頁面固定 13 張卡，有 page 參數。逐頁行到空為止。
    """
    seen = set(seeds)
    queue = list(seeds)
    result = list(seeds)
    while queue and len(result) < max_ids:
        current = queue.pop(0)
        page = 1
        while len(result) < max_ids:
            try:
                ids = api.get_same_category(current, page=page)
            except requests.RequestException:
                break
            if not ids:
                break
            for iid in ids:
                if iid not in seen:
                    seen.add(iid)
                    result.append(iid)
                    queue.append(iid)
            if len(ids) < 13:
                break
            page += 1
    return result


def bfs_discover_strict(api: SnkrdunkApi, seeds: Iterable[int], max_ids: int = 2000) -> list[int]:
    """Discover a bounded same-category graph without swallowing upstream gaps.

    The existing :func:`bfs_discover` remains lenient for legacy callers.  A
    candidate-price refill cannot silently proceed after a category-page error,
    therefore this variant leaves its resumable partial state unpromoted.
    """

    if max_ids < 1:
        raise ValueError("max_ids must be positive")
    ordered_seeds = list(dict.fromkeys(int(seed) for seed in seeds))
    if not ordered_seeds:
        raise ValueError("SNK strict BFS requires at least one seed")
    seen = set(ordered_seeds)
    queue: deque[int] = deque(ordered_seeds)
    discovered = list(ordered_seeds)
    while queue and len(discovered) < max_ids:
        current = queue.popleft()
        page = 1
        while len(discovered) < max_ids:
            ids = api.get_same_category(current, page=page)
            if not isinstance(ids, list) or not all(isinstance(item_id, int) for item_id in ids):
                raise RuntimeError(f"SNK same-category returned invalid identifiers for {current}")
            if not ids:
                break
            for item_id in ids:
                if item_id not in seen:
                    seen.add(item_id)
                    discovered.append(item_id)
                    queue.append(item_id)
                    if len(discovered) >= max_ids:
                        break
            if len(ids) < 13:
                break
            page += 1
    return discovered


def _identity_value(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _canonical_market(value: object) -> str:
    token = _identity_value(value)
    if token in {"pokemon", "pokmon", "ptcg"}:
        return "pokemon"
    if token in {"onepiece", "optcg"}:
        return "one-piece"
    return str(value or "").casefold()


def _identity_from_candidate(candidate: Mapping[str, Any]) -> dict[str, str]:
    canonical = candidate.get("canonicalIdentity")
    source = canonical if isinstance(canonical, Mapping) else candidate
    return {
        "tcg": _canonical_market(source.get("tcg") or source.get("market") or candidate.get("tcg") or candidate.get("market")),
        "language": canonical_language(source.get("language") or candidate.get("language")),
        "setName": str(source.get("setName") or candidate.get("setName") or "").strip(),
        "collectorNumber": str(source.get("collectorNumber") or source.get("collectorNumberRaw") or candidate.get("collectorNumber") or candidate.get("collectorNumberRaw") or "").strip(),
        "edition": str(source.get("edition") or candidate.get("edition") or "").strip(),
        "parallel": str(source.get("parallel") or candidate.get("parallel") or "").strip(),
        "finish": str(source.get("finish") or candidate.get("finish") or "").strip(),
    }


def _canonical_source(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = candidate.get("canonicalSource")
    return nested if isinstance(nested, Mapping) else candidate


def _exact_identity_confirmed(candidate: Mapping[str, Any]) -> bool:
    source = _canonical_source(candidate)
    return (
        str(candidate.get("identityStatus") or "") == "exact_confirmed"
        and bool(source.get("sourceCode") or candidate.get("canonicalSourceCode"))
        and bool(source.get("externalId") or candidate.get("canonicalExternalId"))
    )


def _identity_from_master(master: Mapping[str, Any]) -> dict[str, str] | None:
    nested = master.get("identity")
    source = nested if isinstance(nested, Mapping) else master
    identity = {
        "tcg": _canonical_market(source.get("tcg") or source.get("market") or source.get("game")),
        "language": canonical_language(source.get("language")),
        "setName": str(source.get("setName") or source.get("set") or "").strip(),
        "collectorNumber": str(source.get("collectorNumber") or source.get("cardNumber") or source.get("productNumber") or "").strip(),
        "edition": str(source.get("edition") or "").strip(),
        "parallel": str(source.get("parallel") or "").strip(),
        "finish": str(source.get("finish") or "").strip(),
    }
    if (
        not all(
            identity[field]
            for field in ("tcg", "language", "setName", "collectorNumber")
        )
        or not complete_collector_number(identity["collectorNumber"])
    ):
        return None
    return identity


def _identity_signature(identity: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        _identity_value(identity[key])
        for key in (
            "tcg",
            "language",
            "setName",
            "collectorNumber",
            "edition",
            "parallel",
            "finish",
        )
    )


def _candidate_population(candidate: Mapping[str, Any]) -> int | None:
    value = candidate.get("populationPsa10", candidate.get("psa10Population"))
    return value if isinstance(value, int) and value >= 0 else None


def _result_row(
    candidate: Mapping[str, Any],
    identity: Mapping[str, str],
    *,
    status: str,
    reason: str,
    snk_item_id: int | None = None,
    master_identity: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {"candidateIdentity": dict(identity)}
    if master_identity is not None:
        evidence["masterIdentity"] = dict(master_identity)
    return {
        "canonicalSourceCode": _canonical_source(candidate).get("sourceCode") or candidate.get("canonicalSourceCode"),
        "canonicalExternalId": _canonical_source(candidate).get("externalId") or candidate.get("canonicalExternalId"),
        "pokedexId": candidate.get("pokedexId"),
        "populationPsa10": _candidate_population(candidate),
        "trackingStatus": "eligible" if (_candidate_population(candidate) or 0) >= 1000 else "pre_entry_radar",
        "identityStatus": candidate.get("identityStatus"),
        "canonicalIdentity": dict(candidate.get("canonicalIdentity")) if isinstance(candidate.get("canonicalIdentity"), Mapping) else dict(identity),
        "canonicalSource": dict(_canonical_source(candidate)),
        "status": status,
        "reason": reason,
        "snkItemId": snk_item_id,
        "identityEvidence": evidence,
    }


def build_price_refill_worklist(
    candidates: Iterable[Mapping[str, Any]],
    masters: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve formal and pre-entry candidates by exact printing identity.

    A discovered SNK master is never selected by name or first-search order.
    Any ambiguity, missing master identity fields, or a mismatch enters review;
    an exact candidate with no discovered master is explicitly unavailable.
    """

    source = [dict(candidate) for candidate in candidates if isinstance(candidate, Mapping)]
    formal = [candidate for candidate in source if (_candidate_population(candidate) or 0) >= 1000]
    pre_entry = [candidate for candidate in source if 971 <= (_candidate_population(candidate) or 0) < 1000]
    eligible = [*formal, *pre_entry]
    candidate_signature_counts: dict[tuple[str, ...], int] = {}
    for candidate in eligible:
        identity = _identity_from_candidate(candidate)
        if _exact_identity_confirmed(candidate) and all(
            identity[field]
            for field in ("tcg", "language", "setName", "collectorNumber")
        ) and complete_collector_number(identity["collectorNumber"]):
            signature = _identity_signature(identity)
            candidate_signature_counts[signature] = candidate_signature_counts.get(signature, 0) + 1
    master_identities = {item_id: _identity_from_master(master) for item_id, master in masters.items()}
    exact_by_signature: dict[tuple[str, ...], list[int]] = {}
    for item_id, identity in master_identities.items():
        if identity is not None:
            exact_by_signature.setdefault(_identity_signature(identity), []).append(item_id)

    rows: list[dict[str, Any]] = []
    for candidate in eligible:
        identity = _identity_from_candidate(candidate)
        if not _exact_identity_confirmed(candidate):
            rows.append(_result_row(candidate, identity, status="review", reason="candidate_identity_not_confirmed"))
            continue
        if (
            not all(
                identity[field]
                for field in ("tcg", "language", "setName", "collectorNumber")
            )
            or not complete_collector_number(identity["collectorNumber"])
        ):
            rows.append(_result_row(candidate, identity, status="review", reason="candidate_identity_incomplete"))
            continue
        signature = _identity_signature(identity)
        if candidate_signature_counts.get(signature, 0) != 1:
            rows.append(_result_row(candidate, identity, status="review", reason="multiple_exact_candidates"))
            continue
        target = candidate.get("snkItemId")
        if isinstance(target, int):
            master_identity = master_identities.get(target)
            if target not in masters:
                rows.append(_result_row(candidate, identity, status="review", reason="snk_target_not_discovered", snk_item_id=target))
            elif master_identity is None:
                rows.append(_result_row(candidate, identity, status="review", reason="snk_identity_evidence_missing", snk_item_id=target))
            elif _identity_signature(master_identity) != _identity_signature(identity):
                rows.append(_result_row(candidate, identity, status="review", reason="snk_identity_mismatch", snk_item_id=target, master_identity=master_identity))
            elif len(exact_by_signature.get(signature, [])) != 1:
                rows.append(_result_row(candidate, identity, status="review", reason="multiple_exact_candidates", snk_item_id=target, master_identity=master_identity))
            else:
                rows.append(_result_row(candidate, identity, status="resolved", reason="exact_crosswalk", snk_item_id=target, master_identity=master_identity))
            continue

        matches = exact_by_signature.get(signature, [])
        if len(matches) == 1:
            item_id = matches[0]
            rows.append(_result_row(candidate, identity, status="resolved", reason="exact_crosswalk", snk_item_id=item_id, master_identity=master_identities[item_id]))
        elif len(matches) > 1:
            rows.append(_result_row(candidate, identity, status="review", reason="multiple_exact_candidates"))
        else:
            rows.append(_result_row(candidate, identity, status="unavailable", reason="no_exact_snk_match"))
    rows.sort(key=lambda row: (str(row.get("canonicalSourceCode") or ""), str(row.get("canonicalExternalId") or "")))
    return {
        "schemaVersion": "2.0.0",
        "counts": {
            "candidates": len(source),
            "belowPopulation": len(source) - len(eligible),
            "outsidePopulation": len(source) - len(eligible),
            "eligible": len(eligible),
            "formal": len(formal),
            "preEntryRadar": len(pre_entry),
            "resolved": sum(row["status"] == "resolved" for row in rows),
            "unavailable": sum(row["status"] == "unavailable" for row in rows),
            "review": sum(row["status"] == "review" for row in rows),
        },
        "resolvedItemIds": [row["snkItemId"] for row in rows if row["status"] == "resolved"],
        "cards": rows,
    }


def _stable_request_hash(candidates: Iterable[Mapping[str, Any]], seeds: Iterable[int], max_ids: int) -> str:
    payload = {
        "candidates": [
            {
                "canonicalSourceCode": candidate.get("canonicalSourceCode"),
                "canonicalExternalId": candidate.get("canonicalExternalId"),
                "populationPsa10": _candidate_population(candidate),
                "identity": _identity_from_candidate(candidate),
                "identityStatus": candidate.get("identityStatus"),
                "snkItemId": candidate.get("snkItemId"),
            }
            for candidate in candidates
            if isinstance(candidate, Mapping)
        ],
        "seeds": list(dict.fromkeys(int(seed) for seed in seeds)),
        "maxIds": max_ids,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def run_price_refill(
    api: SnkrdunkApi,
    candidates: Iterable[Mapping[str, Any]],
    seeds: Iterable[int],
    out_path: Path,
    *,
    max_ids: int = 2000,
) -> dict[str, Any]:
    """Build an atomic exact-price worklist, retaining resumable partial state."""

    candidate_rows = [dict(candidate) for candidate in candidates if isinstance(candidate, Mapping)]
    seed_ids = list(dict.fromkeys(int(seed) for seed in seeds))
    request_hash = _stable_request_hash(candidate_rows, seed_ids, max_ids)
    if out_path.is_file():
        completed = json.loads(out_path.read_text(encoding="utf-8"))
        if completed.get("requestSha256") != request_hash:
            raise RuntimeError(f"existing SNK refill output belongs to another request: {out_path}")
        return {"out": str(out_path), "replayed": True, "counts": completed.get("counts", {})}

    partial_path = out_path.with_suffix(out_path.suffix + ".partial")
    if partial_path.is_file():
        state = json.loads(partial_path.read_text(encoding="utf-8"))
        if state.get("requestSha256") != request_hash:
            raise RuntimeError(f"SNK refill partial state belongs to another request: {partial_path}")
    else:
        state = {"schemaVersion": "1.0.0", "requestSha256": request_hash, "discoveredIds": None, "masters": {}}
        _atomic_json(partial_path, state)

    try:
        if not isinstance(state.get("discoveredIds"), list):
            state["discoveredIds"] = bfs_discover_strict(api, seed_ids, max_ids=max_ids)
            _atomic_json(partial_path, state)
        discovered_ids = state["discoveredIds"]
        if not all(isinstance(item_id, int) for item_id in discovered_ids):
            raise RuntimeError("SNK refill partial state contains invalid discovered identifiers")
        masters = state.setdefault("masters", {})
        if not isinstance(masters, dict):
            raise RuntimeError("SNK refill partial state contains invalid masters")
        for item_id in discovered_ids:
            key = str(item_id)
            if key in masters:
                continue
            masters[key] = api.get_master(item_id)
            _atomic_json(partial_path, state)
    except Exception as error:
        raise RuntimeError(f"SNK price refill is partial; output was not promoted: {error}") from error

    master_rows = {int(item_id): value for item_id, value in state["masters"].items() if isinstance(value, Mapping)}
    worklist = build_price_refill_worklist(candidate_rows, master_rows)
    result = {"schemaVersion": "1.0.0", "requestSha256": request_hash, "maxIds": max_ids, **worklist}
    _atomic_json(out_path, result)
    partial_path.unlink(missing_ok=True)
    return {"out": str(out_path), "replayed": False, "counts": worklist["counts"]}


def pull_all(
    item_ids: list[int],
    out_path: Path,
    delay: float = 1.5,
    condition_code: str | None = None,
) -> dict:
    """批量拎全表。逐張卡寫一行 JSONL，斷點續傳：已喺檔案嘅 id 會 skip。"""
    api = SnkrdunkApi(delay=delay)
    version = api.fetch_x_version()

    done: set[int] = set()
    if out_path.exists():
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    if not row.get("error"):
                        done.add(row["item_id"])
                except (json.JSONDecodeError, KeyError):
                    continue

    ok, failed, skipped = 0, 0, 0
    with out_path.open("a", encoding="utf-8") as f:
        for iid in item_ids:
            if iid in done:
                skipped += 1
                continue
            try:
                card = api.pull_card(iid, condition_code=condition_code)
                if card.get("error"):
                    raise RuntimeError(str(card["error"]))
                record_resolution(
                    source="snkrdunk",
                    stage="bulk_pull",
                    script=__file__,
                    item_key=iid,
                    resolution="accepted_payload",
                    context={"condition": condition_code},
                    evidence_paths=[out_path],
                )
                ok += 1
            except Exception as e:
                card = {"item_id": iid, "error": str(e)}
                failed += 1
                record_failure(
                    source="snkrdunk",
                    stage="bulk_pull",
                    script=__file__,
                    item_key=iid,
                    reason_code="item_pull_failed",
                    message=str(e),
                    retryable=True,
                    url=f"{BASE}/en/apparels/{iid}",
                    context={"condition": condition_code},
                    evidence_paths=[out_path],
                    next_action="retry",
                    error_type=type(e).__name__,
                )
            f.write(json.dumps(card, ensure_ascii=False) + "\n")
            f.flush()
            print(
                f"[{ok + failed + skipped}/{len(item_ids)}] "
                f"{iid} {card.get('product_number') or card.get('error')}"
            )

    return {
        "x_version": version,
        "total": len(item_ids),
        "fetched": ok,
        "failed": failed,
        "skipped_existing": skipped,
        "out": str(out_path),
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="SNKRDUNK bulk API pull")
    ap.add_argument("ids", nargs="*", type=int, help="apparel ids to pull")
    ap.add_argument("--discover-from", type=int, nargs="*", default=[],
                    help="seed ids for BFS discovery (e.g. 116069)")
    ap.add_argument("--max-ids", type=int, default=2000)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--condition", default=None,
                    help="e.g. trading_card_single_psa10 (default: aggregate)")
    ap.add_argument("--out", default="snkrdunk_dump.jsonl")
    ap.add_argument("--discover-only", action="store_true",
                    help="write discovered ids and do not fetch market payloads")
    ap.add_argument("--price-refill-candidates", type=Path,
                    help="private candidate manifest; formal POP >=1000 plus pre-entry POP 971-999 are considered")
    ap.add_argument("--price-refill-seeds", type=int, nargs="*", default=[],
                    help="seed apparel ids for strict exact-price refill BFS")
    ap.add_argument("--price-refill-out", type=Path,
                    help="atomic exact-price refill worklist output")
    args = ap.parse_args()

    if args.price_refill_candidates is not None or args.price_refill_out is not None:
        if args.price_refill_candidates is None or args.price_refill_out is None:
            ap.error("--price-refill-candidates and --price-refill-out must be supplied together")
        refill_seeds = list(dict.fromkeys([*args.price_refill_seeds, *args.discover_from]))
        if not refill_seeds:
            ap.error("--price-refill-seeds requires at least one SNK apparel id")
        if not args.price_refill_candidates.is_file():
            ap.error(f"price refill candidates do not exist: {args.price_refill_candidates}")
        try:
            source = json.loads(args.price_refill_candidates.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as error:
            ap.error(f"price refill candidates are invalid JSON: {error}")
        candidates = (source.get("candidates", source.get("cards", [])) if isinstance(source, dict) else source)
        if not isinstance(candidates, list):
            ap.error("price refill candidates must be a JSON array or object with cards")
        api = SnkrdunkApi(delay=args.delay)
        report = run_price_refill(
            api,
            candidates,
            refill_seeds,
            args.price_refill_out,
            max_ids=args.max_ids,
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        raise SystemExit(0)

    ids = list(args.ids)
    if args.discover_from:
        api = SnkrdunkApi(delay=args.delay)
        ids = bfs_discover(api, args.discover_from, max_ids=args.max_ids)
        print(f"BFS discovered {len(ids)} ids")

    if args.discover_only:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(str(value) for value in ids) + "\n", encoding="ascii")
        print(json.dumps({"discovered": len(ids), "out": str(output)}, indent=2))
        raise SystemExit(0)

    report = pull_all(ids, Path(args.out), delay=args.delay, condition_code=args.condition)
    print(json.dumps(report, indent=2, ensure_ascii=False))
