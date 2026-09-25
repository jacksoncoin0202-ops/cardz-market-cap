#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure tests for pipelines/daily_anomaly_census.py (no DB, no network).

Every rule is pinned twice: the shipped module must pass its check, and a
twin of the module with that one rule planted back to the bug must FAIL the
same check (AGENTS.md rule 9).  A check whose plant does not fire proves
nothing, so the run stops on it.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_anomaly_census as C  # noqa: E402
from daily_chain_v2_contract import sha256  # noqa: E402

SOURCE_PATH = ROOT / "pipelines" / "daily_anomaly_census.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
D = "2026-09-25"
D1 = "2026-09-24"
D2 = "2026-09-23"
REAL_BUDGET = C.MESSAGE_BUDGET


def sha(ch: str) -> str:
    return ch * 64


def twin(fixed: str, buggy: str) -> types.ModuleType:
    """The census module with exactly one anchor replaced by its bug."""

    assert SOURCE.count(fixed) == 1, f"plant anchor must be unique: {fixed!r}"
    module = types.ModuleType("daily_anomaly_census_twin")
    module.__file__ = str(SOURCE_PATH)
    exec(compile(SOURCE.replace(fixed, buggy), str(SOURCE_PATH), "exec"), module.__dict__)
    return module


PLANTS: list[tuple[str, Callable[[Any], None], str, str]] = []


def rule(name: str, check: Callable[[Any], None], plants: list[tuple[str, str]]) -> None:
    check(C)
    for fixed, buggy in plants:
        PLANTS.append((name, check, fixed, buggy))
    print(f"POSITIVE_OK {name}")


def fps(result: dict) -> list[str]:
    return sorted(item["fp"] for item in result["items"])


def card(card_id: str, rank: int, **extra: Any) -> dict[str, Any]:
    return {"id": card_id, "rank": rank, "names": {"en": f"Card {card_id}"}, **extra}


# ---------------------------------------------------------------- ① price

PC_PRICE = {
    "reason": C.pcq.REASON_PRICE, "variantId": 1, "saleObservationId": 11,
    "observedDate": "2026-09-20", "unitPriceUsd": 900.0, "direction": "above_band",
    "priorMedianUsd": 100.0, "followingMedianUsd": 110.0,
}
PC_TITLE = {
    "reason": C.pcq.REASON_TITLE, "variantId": 2, "saleObservationId": 12,
    "observedDate": "2026-09-21", "unitPriceUsd": 50.0,
}
SNK_SALES = {3: [
    {"saleObservationId": 21, "variantId": 3, "soldAt": "2026-09-19 10:00:00", "unitPriceUsd": 5.0},
    {"saleObservationId": 22, "variantId": 3, "soldAt": "2026-09-19 11:00:00", "unitPriceUsd": 50.0},
]}
SNK_VERDICTS = {21: {"direction": "below_band", "priorMedianUsd": 50.0, "followingMedianUsd": 55.0}}


def check_price(mod: Any) -> None:
    result = mod.detect_price([PC_PRICE, PC_TITLE], SNK_VERDICTS, SNK_SALES, {1: card("c1", 7)})
    assert fps(result) == ["price:pricecharting:11", "price:snkrdunk:21"], fps(result)
    by_fp = {item["fp"]: item for item in result["items"]}
    assert by_fp["price:pricecharting:11"]["severity"] == "quarantined"
    assert by_fp["price:pricecharting:11"]["rank"] == 7
    # SNK is judged by the same discriminator but only reported, never isolated.
    assert by_fp["price:snkrdunk:21"]["severity"] == "report"
    assert by_fp["price:snkrdunk:21"]["soldAt"] == "2026-09-19"


rule("price: PC = receipt price entries only, SNK = verdicts, report-only", check_price, [
    ('if entry.get("reason") != pcq.REASON_PRICE:\n            continue\n        variant_id',
     'if entry.get("reason") is None:\n            continue\n        variant_id'),
    ('f"price:snkrdunk:{int(sale_id)}", "report",', 'f"price:snkrdunk:{int(sale_id)}", "quarantined",'),
])


# --------------------------------------------------------- ② image review

AUTO = "collect_control:snk_en_image"


def check_image_review(mod: Any) -> None:
    acceptances = [
        {"variant_id": 1, "content_sha256": sha("A"), "accepted_by": AUTO},       # flagged (case-folded)
        {"variant_id": 1, "content_sha256": sha("a"), "accepted_by": AUTO},       # duplicate freeze
        {"variant_id": 2, "content_sha256": sha("b"), "accepted_by": mod.HUMAN_IMAGE_ACCEPTED_BY},
        {"variant_id": 3, "content_sha256": sha("c"), "accepted_by": AUTO},       # Pokemon
        {"variant_id": 4, "content_sha256": sha("d"), "accepted_by": AUTO},       # not the published sha
        {"variant_id": 5, "content_sha256": sha("f"), "accepted_by": AUTO},       # human-approved
    ]
    published = {(1, sha("a")), (2, sha("b")), (3, sha("c")), (4, sha("e")), (5, sha("f"))}
    approvals = {(5, sha("f"))}
    tcg = {1: "one-piece", 2: "one-piece", 3: "pokemon", 4: "one-piece", 5: "one-piece"}
    result = mod.detect_image_review(acceptances, published, approvals, tcg, {1: card("c1", 3)})
    assert fps(result) == [f"image-review:1:{sha('a')}"], fps(result)
    item = result["items"][0]
    assert item["severity"] == "review" and item["lane"] == AUTO and item["rank"] == 3


rule("imageReview: published, non-human, One Piece, unapproved only", check_image_review, [
    ('if str(row["accepted_by"]) == HUMAN_IMAGE_ACCEPTED_BY or tcg_of.get(variant_id) != ONE_PIECE:',
     'if tcg_of.get(variant_id) != ONE_PIECE:'),
    ('if str(row["accepted_by"]) == HUMAN_IMAGE_ACCEPTED_BY or tcg_of.get(variant_id) != ONE_PIECE:',
     'if str(row["accepted_by"]) == HUMAN_IMAGE_ACCEPTED_BY:'),
    ('if (variant_id, sha) not in published_pairs or (variant_id, sha) in approvals:',
     'if (variant_id, sha) in approvals:'),
    ('if (variant_id, sha) not in published_pairs or (variant_id, sha) in approvals:',
     'if (variant_id, sha) not in published_pairs:'),
])


# ------------------------------------------------------- ③ image registry


def check_image_registry(mod: Any) -> None:
    published = [
        (card("c1", 1), 1, sha("1")),              # rejected for this very card
        (card("c2", 2), 2, sha("2")),              # sha rejected only for variant 9
        (card("c3", 3), 3, mod.PLACEHOLDER_SHA),   # placeholder never counts
        (card("c4", 4), 4, ""),                    # no image
        (card("c5", 5), 5, sha("5")),              # clean
    ]
    registry = {(1, sha("1")), (9, sha("2")), (3, mod.PLACEHOLDER_SHA)}
    result = mod.detect_image_registry(published, registry)
    assert fps(result) == [f"image-registry:1:{sha('1')}", f"image-xvariant:2:{sha('2')}"], fps(result)
    by_fp = {item["fp"]: item for item in result["items"]}
    assert by_fp[f"image-registry:1:{sha('1')}"]["severity"] == "critical"
    cross = by_fp[f"image-xvariant:2:{sha('2')}"]
    assert cross["severity"] == "warn" and cross["rejectedFor"] == [9]


rule("imageRegistry: critical keyed on (variant, sha); sha-only is a warn", check_image_registry, [
    # sha-only key: a rejection for another card turns critical here.
    ("if (variant_id, sha) in registry:", "if sha in rejected_for:"),
    ("if not sha or sha == PLACEHOLDER_SHA:", "if not sha:"),
])


# ---------------------------------------------------------------- ④ top100


def windows(**status: str) -> dict[str, Any]:
    return {w: {"changePct": {"status": s}} for w, s in status.items()}


def check_top100(mod: Any) -> None:
    c1 = card("c1", 1, pricePsa10={"value": 100.0}, windows={
        **windows(**{"1d": "ready", "7d": "unavailable"}),
        "30d": {"changePct": {"status": "ready"}, "trackedSales": {"count": {"value": 3}}},
    })
    c2 = card("c2", 2, pricePsa10={"value": None}, windows={
        **windows(**{"7d": "unavailable"}),
        "30d": {"changePct": {"status": "ready"}, "trackedSales": {"count": {"value": 0}}},
    })
    c3 = card("c3", 3, pricePsa10={"value": 900.0}, windows={
        "30d": {"changePct": {"status": "ready"}, "trackedSales": {"count": {"value": 5}}},
    }, historyDaily=[
        {"at": "2026-09-20T00:00:00Z", "priceUsd": 900.0, "trackedSalesCount": 1},
        {"at": "2026-09-21T00:00:00Z", "priceUsd": 500.0, "trackedSalesCount": 2},
    ])
    c4 = card("c4", 4, pricePsa10={"value": 300.0}, windows={"30d": {}}, historyDaily=[
        {"at": "2026-09-22T00:00:00Z", "priceUsd": 300.0, "trackedSalesCount": 1},
        {"at": "2026-09-23T00:00:00Z", "priceUsd": 100.0, "trackedSalesCount": 1},
    ])
    flagged = {
        (3, "2026-09-20"): [{"source": "pricecharting", "saleObservationId": 31, "priceUsd": 900.0}],
        (3, "2026-09-21"): [{"source": "snkrdunk", "saleObservationId": 32, "priceUsd": 500.0}],
        (4, "2026-09-22"): [{"source": "snkrdunk", "saleObservationId": 41, "priceUsd": 300.5}],
        (4, "2026-09-23"): [{"source": "snkrdunk", "saleObservationId": 42, "priceUsd": 400.0}],
    }
    result = mod.detect_top100([c1, c2, c3, c4], {"c1": 1, "c2": 2, "c3": 3, "c4": 4}, flagged)
    assert fps(result) == sorted([
        "top100-withheld:c1:7d",
        "top100-no30d:c2",
        "top100-flagged-day:c3:2026-09-20",
        "top100-flagged-day:c4:2026-09-22",
        "top100-no30d:c4",
    ]), fps(result)
    by_fp = {item["fp"]: item for item in result["items"]}
    # A PC sale in the receipt showing as the day's only sale = FE subtraction failed.
    assert by_fp["top100-flagged-day:c3:2026-09-20"]["severity"] == "error"
    assert by_fp["top100-flagged-day:c4:2026-09-22"]["severity"] == "warn"
    assert by_fp["top100-no30d:c2"]["staleReady"] is True
    assert by_fp["top100-no30d:c4"]["staleReady"] is False


rule("top100: withheld / single-sale flagged day / no 30d sales", check_top100, [
    ('severity = "error" if sale["source"] == "pricecharting" else "warn"', 'severity = "warn"'),
    ('if (card.get("pricePsa10") or {}).get("value") is not None:', "if True:"),
    ('if point.get("trackedSalesCount") != 1 or point.get("priceUsd") is None:',
     'if point.get("priceUsd") is None:'),
    ('if not _same_price(point["priceUsd"], sale["priceUsd"]):', "if False:"),
    # A missing 30d count is "no sales", not "unknown, skip".
    ("        if not count:\n", "        if count == 0:\n"),
])


# ------------------------------------------------ ⑤ quarantine + flagged map


def check_quarantine(mod: Any) -> None:
    result = mod.detect_quarantine([PC_PRICE, PC_TITLE])
    assert fps(result) == [
        f"quarantine:11:{C.pcq.REASON_PRICE}", f"quarantine:12:{C.pcq.REASON_TITLE}",
    ], fps(result)
    assert {item["severity"] for item in result["items"]} == {"quarantined"}


rule("quarantine: one item per receipt entry, reason in the key", check_quarantine, [
    ("f\"quarantine:{int(entry['saleObservationId'])}:{entry['reason']}\"",
     "f\"quarantine:{int(entry['saleObservationId'])}\""),
])


def check_flagged_map(mod: Any) -> None:
    got = mod.flagged_sales_by_day([PC_PRICE, PC_TITLE], SNK_VERDICTS, SNK_SALES)
    assert sorted(got) == [(1, "2026-09-20"), (3, "2026-09-19")], sorted(got)
    assert got[(1, "2026-09-20")] == [{"source": "pricecharting", "saleObservationId": 11, "priceUsd": 900.0}]
    assert [sale["saleObservationId"] for sale in got[(3, "2026-09-19")]] == [21]


rule("flagged-by-day: PC price entries + SNK verdicts only", check_flagged_map, [
    ('if entry.get("reason") == pcq.REASON_PRICE and entry.get("unitPriceUsd") is not None:',
     'if entry.get("unitPriceUsd") is not None:'),
    ('if int(sale["saleObservationId"]) in snk_verdicts:', "if True:"),
])


# ---------------------------------------------------------------- assemble


def results_with(quarantine: list[str], top: list[str] | None = None) -> dict[str, dict]:
    out = {name: {"status": "ok", "items": []} for name in C.DETECTORS}
    out["quarantine"]["items"] = [{"fp": fp, "severity": "quarantined"} for fp in quarantine]
    out["top100"]["items"] = [
        {"fp": fp, "severity": "warn", "kind": "no30d", "staleReady": False, "rank": 5,
         "name": "Luffy", "variantId": 5}
        for fp in (top or [])
    ]
    return out


def assemble_with(mod: Any, day: str, results: dict, previous: dict | None) -> dict:
    return mod.assemble(business_date=day, generation="gen-1", snapshot_sha256=sha("9"),
                        inputs={}, results=results, previous=previous)


Q0, Q1, Q2 = "quarantine:1:title_collector_contradiction", "quarantine:2:x", "quarantine:3:x"


def check_baseline(mod: Any) -> None:
    doc = assemble_with(mod, D, results_with([Q1, Q2]), None)
    assert doc["baseline"] is True and doc["newCount"] == 0 and doc["dry"] is False
    assert all(doc["detectors"][name]["new"] == [] for name in C.DETECTORS)
    assert doc["openCount"] == 2 and doc["consecutiveDryDays"] == 0
    assert "首日基線" in doc["message"]


rule("assemble: first census is a baseline, nothing is new", check_baseline, [
    ("new = [] if baseline else [fp for fp in open_fps if fp not in seen]",
     "new = [fp for fp in open_fps if fp not in seen]"),
])


def check_set_difference(mod: Any) -> None:
    previous = assemble_with(mod, D1, results_with([Q0, Q1]), None)
    doc = assemble_with(mod, D, results_with([Q1, Q2]), previous)
    assert doc["detectors"]["quarantine"]["new"] == [Q2], doc["detectors"]["quarantine"]["new"]
    assert doc["newCount"] == 1 and doc["openCount"] == 2 and doc["dry"] is False
    assert doc["consecutiveDryDays"] == 0


rule("assemble: new = today's open minus the previous census's open", check_set_difference, [
    ("new = [] if baseline else [fp for fp in open_fps if fp not in seen]",
     "new = [] if baseline else list(open_fps)"),
])


def check_streak(mod: Any) -> None:
    yesterday = {**assemble_with(mod, D1, results_with([Q1]), None), "consecutiveDryDays": 3}
    doc = assemble_with(mod, D, results_with([Q1]), yesterday)
    assert doc["dry"] is True and doc["consecutiveDryDays"] == 4, doc["consecutiveDryDays"]
    # A gap day breaks the streak: the previous census is from D-2.
    older = {**assemble_with(mod, D2, results_with([Q1]), None), "consecutiveDryDays": 3}
    gap = assemble_with(mod, D, results_with([Q1]), older)
    assert gap["dry"] is True and gap["consecutiveDryDays"] == 0, gap["consecutiveDryDays"]
    # A day with news is not dry.
    wet = assemble_with(mod, D, results_with([Q1, Q2]), yesterday)
    assert wet["dry"] is False and wet["consecutiveDryDays"] == 0


rule("assemble: dry streak only continues from the census of D-1", check_streak, [
    ('if previous.get("businessDate") == yesterday:', "if True:"),
])


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(doc, ensure_ascii=False).encode("utf-8"))


def check_previous_and_rerun(mod: Any) -> None:
    with tempfile.TemporaryDirectory(prefix="anomaly-prev-") as folder:
        audit = Path(folder)
        for day, stamp, doc_day in (
            (D2, "20260923T030000Z", D2),
            (D1, "20260924T030000Z", D1),
            (D1, "20260924T050000Z", D1),
        ):
            write_json(audit / f"anomaly_census_{day}_{stamp}.json",
                       {**assemble_with(mod, doc_day, results_with([Q1]), None), "stamp": stamp})
        write_json(audit / "anomaly_census_current.json",
                   {**assemble_with(mod, D1, results_with([Q1]), None), "stamp": "current"})
        path, prev = mod.previous_receipt(D, audit)
        assert path.name == "anomaly_census_2026-09-24_20260924T050000Z.json", path.name
        # Same-day rerun: today's first archive and current must not become
        # the baseline, or the rerun reports "0 new" and a different message.
        first = assemble_with(mod, D, results_with([Q1, Q2]), prev)
        write_json(audit / f"anomaly_census_{D}_20260925T030000Z.json", first)
        write_json(audit / "anomaly_census_current.json", first)
        path2, prev2 = mod.previous_receipt(D, audit)
        second = assemble_with(mod, D, results_with([Q1, Q2]), prev2)
        assert path2 == path, path2.name
        assert second["message"] == first["message"]
        assert f"{D}:{sha256(second['message'])[:16]}" == f"{D}:{sha256(first['message'])[:16]}"
        assert second["newCount"] == 1
        assert mod.previous_receipt(D2, audit) is None


rule("previous_receipt ignores today's archive and current; rerun is identical", check_previous_and_rerun, [
    ("if day < business_date and (best is None or path.name > best[1].name):",
     "if day <= business_date and (best is None or path.name > best[1].name):"),
])


# ------------------------------------------------------------------ render


def loud_doc(mod: Any) -> dict:
    """Every section at its listing cap with long, HTML-hostile names."""

    name = "<Monkey & D. Luffy> " * 6
    items: dict[str, list[dict]] = {n: [] for n in C.DETECTORS}
    for i in range(40):
        items["price"].append({"fp": f"price:pricecharting:{i}", "severity": "quarantined", "rank": i + 1,
                               "name": name, "priceUsd": 123456.78, "priorMedianUsd": 1234.5,
                               "followingMedianUsd": 1250.0, "source": "pricecharting",
                               "soldAt": "2026-09-24"})
        items["imageRegistry"].append({"fp": f"image-registry:{i}:{sha('a')}", "severity": "critical",
                                       "rank": i + 1, "name": name, "sha256": sha("a"), "variantId": i})
        items["top100"].append({"fp": f"top100-flagged-day:c{i}:2026-09-24", "severity": "error",
                                "kind": "flagged-day", "day": "2026-09-24", "priceUsd": 99999.0,
                                "source": "pricecharting", "rank": i + 1, "name": name})
        items["imageReview"].append({"fp": f"image-review:{i}:{sha('b')}", "severity": "review"})
        items["quarantine"].append({"fp": f"quarantine:{i}:reason_number_{i % 9}", "severity": "quarantined"})
    results = {n: {"status": "ok", "items": items[n]} for n in C.DETECTORS}
    previous = assemble_with(mod, D1, {n: {"status": "ok", "items": []} for n in C.DETECTORS}, None)
    return assemble_with(mod, D, results, previous)


def check_render_budget(mod: Any) -> None:
    doc = loud_doc(mod)
    assert doc["newCount"] == 200
    assert len(doc["message"]) <= REAL_BUDGET, len(doc["message"])
    assert "<Monkey" not in doc["message"] and "&lt;Monkey" in doc["message"]
    saved = mod.MESSAGE_BUDGET
    try:
        for budget in (300, 450, 700, 1100):
            mod.MESSAGE_BUDGET = budget
            text = mod.render(doc)
            assert len(text) <= budget, (budget, len(text))
            assert "…仲有" in text and text.endswith(f"receipt <code>{C.CURRENT.name}</code>"), text[-80:]
    finally:
        mod.MESSAGE_BUDGET = saved


rule("render: always within MESSAGE_BUDGET, truncation names the receipt", check_render_budget, [
    ('if len("\\n".join(kept + [line, more])) > MESSAGE_BUDGET:',
     'if len("\\n".join(kept + [line])) > MESSAGE_BUDGET:'),
])


# ------------------------------------------------------------ input errors


def raises(fn: Callable[[], Any], code: str) -> str:
    try:
        fn()
    except C.AnomalyInputError as error:
        assert str(error).startswith(code), str(error)
        return str(error)
    raise AssertionError(f"expected {code}")


def check_snapshot_generation(mod: Any) -> None:
    with tempfile.TemporaryDirectory(prefix="anomaly-snap-") as folder:
        path = Path(folder) / "seed-snapshot.json"
        raw = json.dumps({"generation": {"id": "db3308_a"}, "top100": []}).encode("utf-8")
        path.write_bytes(raw)
        try:
            mod.load_snapshot(path, "db3308_b")
        except Exception as error:  # the twin raises its own class object
            assert type(error).__name__ == "AnomalyInputError", error
            assert str(error).startswith("ANOMALY_SNAPSHOT_MISMATCH"), str(error)
        else:
            raise AssertionError("generation mismatch was not refused")
        snapshot, digest = mod.load_snapshot(path, "db3308_a")
        assert snapshot["generation"]["id"] == "db3308_a" and digest == hashlib.sha256(raw).hexdigest()
        # CLI: an input error exits 2 before any DB connection is opened.
        assert mod.main(["--business-date", D, "--snapshot", str(path), "--expected-generation", "db3308_b"]) == 2


rule("load_snapshot refuses a generation it was not told to judge", check_snapshot_generation, [
    ("if expected_generation and generation != expected_generation:", "if False:"),
])


def check_receipt(mod: Any) -> None:
    def expect(stamp: Any, code: str | None, entries: Any = ()) -> None:
        with tempfile.TemporaryDirectory(prefix="anomaly-receipt-") as folder:
            path = Path(folder) / "pc_sale_title_quarantine_current.json"
            if stamp is not None:
                write_json(path, {"generatedAt": stamp, "entries": list(entries) if entries != "bad" else {}})
            try:
                mod.load_quarantine_receipt(D, path)
            except Exception as error:
                assert type(error).__name__ == "AnomalyInputError", error
                assert code is not None and str(error).startswith(code), (stamp, str(error))
                return
            assert code is None, (stamp, f"expected {code}")

    expect(None, "ANOMALY_RECEIPT_MISSING")
    expect("20260924T030000Z", "ANOMALY_RECEIPT_STALE")      # 12:00 JST on D-1
    expect("20260924T145959Z", "ANOMALY_RECEIPT_STALE")      # 23:59:59 JST on D-1
    expect("20260924T153000Z", None)                         # 00:30 JST on D: fresh
    expect("20260925T030000Z", None)                         # 12:00 JST on D
    expect("garbage", "ANOMALY_RECEIPT_STALE")               # unparseable fails closed
    expect("", "ANOMALY_RECEIPT_STALE")
    expect("20260925T030000Z", "ANOMALY_RECEIPT_STALE", entries="bad")


rule("quarantine receipt: missing/stale refused, freshness judged on the JST day", check_receipt, [
    # The pre-fix comparison on raw UTC digits.
    ("day = receipt_business_day(stamp)", 'day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if stamp else None'),
    ("day is None or day < business_date", "day is None"),
])


class FakeCursor:
    def __init__(self, map_rows: list[dict]) -> None:
        self.map_rows = map_rows
        self.sql: list[str] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append(sql)

    def fetchall(self) -> list[dict]:
        assert self.sql and self.sql[-1] == C.MAP_SQL, "only the map query may run before the unmapped check"
        return self.map_rows


def check_unmapped(mod: Any) -> None:
    snapshot = {"top100": [card("cmc_a", 1)], "watchlist": [card("cmc_b", 2)]}
    cursor = FakeCursor([{"variant_id": 1, "public_id": "cmc_a", "tcg_code": "pokemon"}])
    try:
        mod.collect(cursor, snapshot, {"entries": []})
    except Exception as error:
        assert type(error).__name__ == "AnomalyInputError", repr(error)
        assert str(error).startswith("ANOMALY_CARD_UNMAPPED") and "cmc_b" in str(error), str(error)
    else:
        raise AssertionError("an unmapped card was not refused")
    assert all(sql == C.MAP_SQL for sql in cursor.sql), "no further query after an unmapped card"


rule("collect refuses a published card it cannot map to a variant", check_unmapped, [
    ("    if unmapped:\n", "    if False:\n"),
])


def check_select_only(mod: Any) -> None:
    for sql in (mod.MAP_SQL, mod.REGISTRY_SQL, mod.APPROVAL_SQL, mod.ACCEPTANCE_SQL):
        head = sql.strip().split(None, 1)[0].upper()
        assert head == "SELECT", sql
    assert "v.tcg_code" in mod.MAP_SQL, "tcg from catalog_variant, not an optional join"


rule("every census query is a SELECT; tcg comes from catalog_variant", check_select_only, [
    ("COALESCE(a.public_id, v.opaque_id) AS public_id, v.tcg_code",
     "COALESCE(a.public_id, v.opaque_id) AS public_id, p.tcg_code"),
])


class StuckConnection:
    """Fake pymysql connection whose first census SELECT hits a server-side timeout."""

    def __init__(self, errno: int) -> None:
        self.errno = errno
        self.sql: list[str] = []
        self.rolled_back = self.closed = False

    def cursor(self) -> "StuckConnection":
        return self

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append(sql)
        if sql.strip().upper().startswith("SELECT"):
            raise C.pymysql.err.OperationalError(self.errno, "Query execution was interrupted")

    def fetchall(self) -> list[dict]:
        raise AssertionError("no rows after a timed-out SELECT")

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def run_on(mod: Any, conn: StuckConnection, folder: Path) -> Any:
    import rebuild_036

    saved = rebuild_036.connect, mod.load_snapshot, mod.load_quarantine_receipt
    rebuild_036.connect = lambda _env: conn
    mod.load_snapshot = lambda _path, _gen: ({"generation": {"id": "db3308_a"}, "top100": []}, sha("0"))
    mod.load_quarantine_receipt = lambda _day: {"generatedAt": "20260925T030000Z", "entries": []}
    try:
        return mod.run(business_date=D, snapshot_path=folder / "seed-snapshot.json",
                       expected_generation="db3308_a", out=folder / "out.json")
    finally:
        rebuild_036.connect, mod.load_snapshot, mod.load_quarantine_receipt = saved


def check_db_timeout(mod: Any) -> None:
    for errno in (3024, 1205):  # MAX_EXECUTION_TIME, lock_wait_timeout
        with tempfile.TemporaryDirectory(prefix="anomaly-db-") as folder:
            conn = StuckConnection(errno)
            try:
                run_on(mod, conn, Path(folder))
            except Exception as error:  # the twin raises its own class object
                assert type(error).__name__ == "AnomalyInputError", (errno, repr(error))
                assert str(error).startswith("ANOMALY_DB_TIMEOUT") and str(errno) in str(error), str(error)
            else:
                raise AssertionError(f"MySQL {errno} did not end the census")
            start = conn.sql.index("START TRANSACTION READ ONLY")
            for bound in ("SET SESSION MAX_EXECUTION_TIME=60000", "SET SESSION lock_wait_timeout=30"):
                assert bound in conn.sql[:start], f"{bound} must precede the read transaction: {conn.sql}"
            assert conn.rolled_back and conn.closed, "a timed-out census still releases its connection"
            assert not (Path(folder) / "out.json").exists(), "a timed-out census writes nothing"
    # Any other DB error is not a timeout and keeps its own class.
    with tempfile.TemporaryDirectory(prefix="anomaly-db-") as folder:
        try:
            run_on(mod, StuckConnection(1146), Path(folder))
        except Exception as error:
            assert type(error).__name__ == "OperationalError", repr(error)
        else:
            raise AssertionError("MySQL 1146 vanished")


rule("census DB wait is bounded; a stuck query ends as ANOMALY_DB_TIMEOUT", check_db_timeout, [
    ('    "SET SESSION MAX_EXECUTION_TIME=60000",\n', ""),
    ('    "SET SESSION lock_wait_timeout=30",\n', ""),
    ("DB_TIMEOUT_ERRNOS = (1205, 3024)", "DB_TIMEOUT_ERRNOS = (3024,)"),
    ("if error.args and error.args[0] in DB_TIMEOUT_ERRNOS:", "if False:"),
    ("if error.args and error.args[0] in DB_TIMEOUT_ERRNOS:", "if error.args:"),
])


# ------------------------------------------------------------------ plants

assert C.ANOMALY_CENSUS_EVENT == "anomaly.census"
assert C.MESSAGE_BUDGET == REAL_BUDGET

fired = 0
for name, check, fixed, buggy in PLANTS:
    mutant = twin(fixed, buggy)
    try:
        check(mutant)
    except AssertionError:
        # Only the check's own verdict counts: a NameError from a broken twin
        # would otherwise pass as "fired" while proving nothing.
        fired += 1
        print(f"PLANTED_BUG_FIRED {name} :: {buggy.strip()[:60]!r}")
        continue
    raise AssertionError(f"planted bug did not fire, so this check proves nothing: {name} :: {buggy!r}")

print(f"ALL_OK daily_anomaly_census: {len(PLANTS)} plants, {fired} fired")
