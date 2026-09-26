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
from datetime import datetime
from decimal import Decimal
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
# Since 2026-09-25 the producer quarantines SNKRDUNK spikes too (sale 13).
SNK_PRICE = {**PC_PRICE, "variantId": 3, "saleObservationId": 13, "observedDate": "2026-09-18", "unitPriceUsd": 400.0}
SOURCES = {11: "pricecharting", 13: "snkrdunk"}


def check_price(mod: Any) -> None:
    result = mod.detect_price([PC_PRICE, PC_TITLE, SNK_PRICE], SOURCES, SNK_VERDICTS, SNK_SALES, {1: card("c1", 7)})
    assert fps(result) == [
        "price-unquarantined:snkrdunk:21", "price:pricecharting:11", "price:snkrdunk:13",
    ], fps(result)
    by_fp = {item["fp"]: item for item in result["items"]}
    assert by_fp["price:pricecharting:11"]["severity"] == "quarantined"
    assert by_fp["price:pricecharting:11"]["rank"] == 7
    # A receipt spike keeps its own lane.
    assert by_fp["price:snkrdunk:13"]["severity"] == "quarantined"
    assert by_fp["price:snkrdunk:13"]["source"] == "snkrdunk"
    # A spike nothing quarantines is only reported, never isolated.
    assert by_fp["price-unquarantined:snkrdunk:21"]["severity"] == "report"
    assert by_fp["price-unquarantined:snkrdunk:21"]["soldAt"] == "2026-09-19"
    # A quarantined sale without a landing row has no lane: said, not guessed.
    lone = mod.detect_price([{**PC_PRICE, "saleObservationId": 14}], SOURCES, {}, {}, {})
    assert fps(lone) == ["price:unknown:14"], fps(lone)


rule("price: receipt spikes under their own lane, unquarantined SNK spikes report-only", check_price, [
    ('if entry.get("reason") != pcq.REASON_PRICE:\n            continue\n        variant_id',
     'if entry.get("reason") is None:\n            continue\n        variant_id'),
    ('f"price-unquarantined:snkrdunk:{int(sale_id)}", "report",',
     'f"price-unquarantined:snkrdunk:{int(sale_id)}", "quarantined",'),
    # The pre-fix fp: render cannot tell "SNK quarantined" from "SNK unquarantined".
    ('f"price-unquarantined:snkrdunk:{int(sale_id)}"', 'f"price:snkrdunk:{int(sale_id)}"'),
    # The pre-fix hard-coded lane, and a lane guessed for a sale without one.
    ("source = source_of.get(sale_id, UNKNOWN_SOURCE)", 'source = "pricecharting"'),
    ("source = source_of.get(sale_id, UNKNOWN_SOURCE)", 'source = source_of.get(sale_id, "pricecharting")'),
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
        (3, "2026-09-20"): [{"source": "snkrdunk", "quarantined": True, "saleObservationId": 31, "priceUsd": 900.0}],
        (3, "2026-09-21"): [{"source": "snkrdunk", "quarantined": False, "saleObservationId": 32, "priceUsd": 500.0}],
        (4, "2026-09-22"): [{"source": "snkrdunk", "quarantined": False, "saleObservationId": 41, "priceUsd": 300.5}],
        (4, "2026-09-23"): [{"source": "snkrdunk", "quarantined": False, "saleObservationId": 42, "priceUsd": 400.0}],
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
    # A receipt sale of any lane showing as the day's only sale = FE subtraction failed.
    assert by_fp["top100-flagged-day:c3:2026-09-20"]["severity"] == "error"
    assert by_fp["top100-flagged-day:c4:2026-09-22"]["severity"] == "warn"
    assert by_fp["top100-no30d:c2"]["staleReady"] is True
    assert by_fp["top100-no30d:c4"]["staleReady"] is False


rule("top100: withheld / single-sale flagged day / no 30d sales", check_top100, [
    ('severity = "error" if sale["quarantined"] else "warn"', 'severity = "warn"'),
    # The pre-fix rule: a quarantined SNK sale on show is an error too.
    ('severity = "error" if sale["quarantined"] else "warn"',
     'severity = "error" if sale["source"] == "pricecharting" else "warn"'),
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
    got = mod.flagged_sales_by_day([PC_PRICE, PC_TITLE, SNK_PRICE], SOURCES, SNK_VERDICTS, SNK_SALES)
    assert sorted(got) == [(1, "2026-09-20"), (3, "2026-09-18"), (3, "2026-09-19")], sorted(got)
    assert got[(1, "2026-09-20")] == [
        {"source": "pricecharting", "quarantined": True, "saleObservationId": 11, "priceUsd": 900.0}]
    assert got[(3, "2026-09-18")] == [
        {"source": "snkrdunk", "quarantined": True, "saleObservationId": 13, "priceUsd": 400.0}]
    assert got[(3, "2026-09-19")] == [
        {"source": "snkrdunk", "quarantined": False, "saleObservationId": 21, "priceUsd": 5.0}]


rule("flagged-by-day: receipt price entries (own lane) + unquarantined SNK verdicts", check_flagged_map, [
    ('if entry.get("reason") == pcq.REASON_PRICE and entry.get("unitPriceUsd") is not None:',
     'if entry.get("unitPriceUsd") is not None:'),
    ('if int(sale["saleObservationId"]) in snk_verdicts:', "if True:"),
    ('"source": source_of.get(sale_id, UNKNOWN_SOURCE), "quarantined": True,',
     '"source": "pricecharting", "quarantined": True,'),
    ('"source": "snkrdunk", "quarantined": False,', '"source": "snkrdunk", "quarantined": True,'),
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
        # Every budget: a hand-picked few stop catching the overflow as soon
        # as a header line changes length.
        for budget in range(300, 1101):
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


BAKED_AT = "2026-09-25T12:59:29.453Z"  # generation.generatedAt of live db3308_6f0d6e09d56c49d1
BOUND_STAMP, LATER_STAMP = "20260925T125916Z", "20260925T210114Z"


def check_bake_binding(mod: Any) -> None:
    # The producer's archive name is the contract this binding reads.
    producer = Path(C.pcq.__file__).read_text(encoding="utf-8")
    assert f'AUDIT_DIR / f"{mod.RECEIPT_ARCHIVE_PREFIX}{{stamp}}.json"' in producer, mod.RECEIPT_ARCHIVE_PREFIX
    with tempfile.TemporaryDirectory(prefix="anomaly-bind-") as folder:
        audit = Path(folder)
        for stamp in ("20260925T043115Z", BOUND_STAMP, LATER_STAMP, "current", "20260925T999999Z"):
            write_json(audit / f"pc_sale_title_quarantine_{stamp}.json", {"generatedAt": stamp, "entries": []})

        def expect(generated_at: Any, want: str) -> None:
            try:
                path = mod.bake_receipt_path({"generation": {"id": "db3308_a", "generatedAt": generated_at}}, audit)
            except Exception as error:  # a twin raises its own class object
                assert type(error).__name__ == "AnomalyInputError", repr(error)
                assert str(error).startswith(want), (generated_at, str(error))
                return
            assert path.name == f"pc_sale_title_quarantine_{want}.json", (generated_at, path.name)

        expect(BAKED_AT, BOUND_STAMP)                            # the 2026-09-25 bake: not the 1412-entry rerun
        expect("2026-09-25T12:59:16Z", BOUND_STAMP)              # at or before
        expect("2026-09-25T12:59:15.999Z", "20260925T043115Z")
        expect("2026-09-26T00:00:00+09:00", BOUND_STAMP)         # an offset is an instant, not digits
        expect("2026-09-25T04:31:14Z", "ANOMALY_RECEIPT_UNBOUND")
        for undated in (None, "", "garbage", "2026-09-25T12:59:29"):  # naive = no instant
            expect(undated, "ANOMALY_SNAPSHOT_UNDATED")


rule("quarantine receipt = the archive the bake read (newest at or before generatedAt)", check_bake_binding, [
    ("if at <= baked and", "if at < baked and"),
    ("if at <= baked and (best is None or at > best[0]):", "if best is None or at > best[0]:"),
    ('raise AnomalyInputError(f"ANOMALY_SNAPSHOT_UNDATED: generation.generatedAt={raw!r}")',
     "baked = datetime.now(timezone.utc)"),
    ('    if best is None:\n        raise AnomalyInputError(\n            f"ANOMALY_RECEIPT_UNBOUND',
     '    if best is None:\n        return audit_dir / "pc_sale_title_quarantine_current.json"\n'
     '        raise AnomalyInputError(\n            f"ANOMALY_RECEIPT_UNBOUND'),
    ('RECEIPT_ARCHIVE_PREFIX = "pc_sale_title_quarantine_"', 'RECEIPT_ARCHIVE_PREFIX = "pc_sale_quarantine_"'),
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
    for sql in (mod.MAP_SQL, mod.REGISTRY_SQL, mod.APPROVAL_SQL, mod.ACCEPTANCE_SQL, mod.SOURCE_SQL):
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

    saved = rebuild_036.connect, mod.load_snapshot, mod.bake_receipt_path, mod.load_quarantine_receipt
    rebuild_036.connect = lambda _env: conn
    mod.load_snapshot = lambda _path, _gen: ({"generation": {"id": "db3308_a"}, "top100": []}, sha("0"))
    mod.bake_receipt_path = lambda _snapshot, _audit: folder / "receipt.json"
    mod.load_quarantine_receipt = lambda _day, _path: {"generatedAt": "20260925T030000Z", "entries": []}
    try:
        return mod.run(business_date=D, snapshot_path=folder / "seed-snapshot.json",
                       expected_generation="db3308_a", out=folder / "out.json")
    finally:
        rebuild_036.connect, mod.load_snapshot, mod.bake_receipt_path, mod.load_quarantine_receipt = saved


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


# ---------------------------------------------- whole census vs the bake


class FakeDb:
    """Read-only fake census DB: answers each SELECT the census issues by its shape."""

    def __init__(self, tables: dict[str, Any]) -> None:
        self.tables = tables
        self.rows: list[dict] = []

    def cursor(self) -> "FakeDb":
        return self

    def execute(self, sql: str, params: Any = None) -> None:
        text = " ".join(sql.split())
        assert text.startswith(("SELECT", "SET SESSION", "START TRANSACTION READ ONLY")), text
        t, wanted = self.tables, set(params or ())
        if not text.startswith("SELECT"):
            self.rows = []
        elif sql == C.MAP_SQL:
            self.rows = t["map"]
        elif sql in (C.REGISTRY_SQL, C.APPROVAL_SQL, C.ACCEPTANCE_SQL):
            self.rows = []
        elif sql == C.pcq.STORED_RELEASE_SQL:
            self.rows = t["stored"]
        elif "FROM market_universe_member" in text:
            self.rows = [{"variant_id": v} for v in t["universe"]]
        elif "FROM operator_strict_source_identity" in text:
            self.rows = [r for r in t["identity"] if r["source_code"] == params[0] and r["variant_id"] in wanted]
        elif "FROM market_sale_observation s WHERE s.source_code=%s" in text:
            self.rows = [r for r in t["sales"] if r["source_code"] == params[0] and r["variant_id"] in wanted]
        elif text.startswith("SELECT id, source_code FROM market_sale_observation WHERE id IN"):
            self.rows = [{"id": i, "source_code": t["sources"][i]} for i in sorted(wanted) if i in t["sources"]]
        else:
            raise AssertionError(f"unexpected census SQL: {text[:80]}")

    def fetchall(self) -> list[dict]:
        return [dict(row) for row in self.rows]

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def spike(sale_id: int, variant_id: int, day: str, price: float) -> dict[str, Any]:
    return {"reason": C.pcq.REASON_PRICE, "variantId": variant_id, "saleObservationId": sale_id,
            "observedDate": day, "unitPriceUsd": price, "direction": "above_band",
            "priorMedianUsd": 100.0, "followingMedianUsd": 100.0}


def snk_row(sale_id: int, day: int, price: str) -> dict[str, Any]:
    return {"id": sale_id, "variant_id": 3, "source_code": "snkrdunk", "external_entity_id": "snkrdunk:807560",
            "sold_at": datetime(2026, 9, day, 12, 0), "unit_price_usd": Decimal(price), "quantity": 1,
            "transaction_fingerprint": f"fp-{sale_id}", "listing_item_id": None, "listing_url": None,
            "listing_title": None}


def check_bound_census(mod: Any) -> None:
    """run() end to end: the published board is judged against the receipt its bake read.

    v3 (SNK) trades at $100 with $1000 spikes on 09-04 (301: bound receipt),
    09-08 (302: quarantined after the bake, in the later receipt and the
    effective view), 09-12 (303: released) and 09-16 (304: never quarantined).
    """

    import rebuild_036

    bound = [spike(11, 1, "2026-09-20", 900.0), PC_TITLE, spike(301, 3, "2026-09-04", 1000.0)]
    later = [*bound, spike(302, 3, "2026-09-08", 1000.0)]
    line = [snk_row(3001 + i, day, "100.00") for i, day in enumerate((1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15, 17, 18, 19))]
    db = FakeDb({
        "map": [{"variant_id": v, "public_id": f"c{v}", "tcg_code": tcg}
                for v, tcg in ((1, "pokemon"), (2, "pokemon"), (3, "one-piece"))],
        "universe": [1, 3],
        "identity": [{"source_code": "snkrdunk", "variant_id": 3, "external_entity_id": "807560"}],
        "sales": line + [snk_row(sale_id, day, "1000.00") for sale_id, day in ((301, 4), (302, 8), (303, 12), (304, 16))],
        "stored": [
            {"id": 12, "variant_id": 2, "reason": C.pcq.REASON_TITLE, "released": 0, "release_evidence_sha256": None},
            {"id": 302, "variant_id": 3, "reason": C.pcq.REASON_PRICE, "released": 0, "release_evidence_sha256": None},
            {"id": 303, "variant_id": 3, "reason": C.pcq.REASON_TITLE, "released": 1, "release_evidence_sha256": sha("e")},
        ],
        "sources": {11: "pricecharting", 12: "pricecharting", 301: "snkrdunk", 302: "snkrdunk"},
    })
    month = {"30d": {"changePct": {"status": "ready"}, "trackedSales": {"count": {"value": 3}}}}
    snapshot = {"generation": {"id": "db3308_bound", "generatedAt": BAKED_AT}, "watchlist": [], "top100": [
        card("c1", 1, windows=month, historyDaily=[
            {"at": "2026-09-20T00:00:00Z", "priceUsd": 900.0, "trackedSalesCount": 1}]),
        card("c3", 2, windows=month, historyDaily=[
            {"at": f"2026-09-{day:02d}T00:00:00Z", "priceUsd": 1000.0, "trackedSalesCount": 1} for day in (4, 8, 12, 16)]),
    ]}
    saved = rebuild_036.connect, mod.previous_receipt, C.pcq.AUDIT_DIR, C.pcq.CURRENT_RECEIPT
    with tempfile.TemporaryDirectory(prefix="anomaly-bound-") as folder:
        audit = Path(folder) / "audit"
        for name, stamp, entries in (
            ("20260925T043115Z", "20260925T043115Z", bound[:1]),
            (BOUND_STAMP, BOUND_STAMP, bound),
            (LATER_STAMP, LATER_STAMP, later),   # the rerun after the bake ...
            ("current", LATER_STAMP, later),     # ... which also rewrote CURRENT
        ):
            write_json(audit / f"pc_sale_title_quarantine_{name}.json", {"generatedAt": stamp, "entries": entries})
        snapshot_path = Path(folder) / "seed-snapshot.json"
        write_json(snapshot_path, snapshot)
        rebuild_036.connect = lambda _env: db
        mod.previous_receipt = lambda _day: None
        C.pcq.AUDIT_DIR, C.pcq.CURRENT_RECEIPT = audit, audit / "pc_sale_title_quarantine_current.json"
        try:
            doc = mod.run(business_date=D, snapshot_path=snapshot_path, expected_generation="db3308_bound",
                          out=Path(folder) / "census.json")
        finally:
            rebuild_036.connect, mod.previous_receipt, C.pcq.AUDIT_DIR, C.pcq.CURRENT_RECEIPT = saved
    det = doc["detectors"]
    # Each quarantined spike under its own lane; 302 (quarantined after the
    # bake) is neither a published error nor an unquarantined spike; 303
    # (released) is a real sale again.  No sale is listed twice.
    assert det["price"]["open"] == [
        "price-unquarantined:snkrdunk:303", "price-unquarantined:snkrdunk:304",
        "price:pricecharting:11", "price:snkrdunk:301",
    ], det["price"]["open"]
    assert det["quarantine"]["open"] == [
        f"quarantine:11:{C.pcq.REASON_PRICE}", f"quarantine:12:{C.pcq.REASON_TITLE}",
        f"quarantine:301:{C.pcq.REASON_PRICE}",
    ], det["quarantine"]["open"]
    flagged = {item["fp"]: (item["severity"], item["source"]) for item in det["top100"]["items"]}
    assert flagged == {
        "top100-flagged-day:c1:2026-09-20": ("error", "pricecharting"),
        "top100-flagged-day:c3:2026-09-04": ("error", "snkrdunk"),
        "top100-flagged-day:c3:2026-09-12": ("warn", "snkrdunk"),
        "top100-flagged-day:c3:2026-09-16": ("warn", "snkrdunk"),
    }, flagged
    assert "PC 已隔離 1｜SNK 已隔離 1｜SNK 未隔離 2｜" in doc["message"], doc["message"]
    receipt = doc["inputs"]["quarantineReceipt"]
    assert Path(receipt["path"]).name == f"pc_sale_title_quarantine_{BOUND_STAMP}.json", receipt
    assert receipt["generatedAt"] == BOUND_STAMP and receipt["boundToGeneratedAt"] == BAKED_AT, receipt


rule("census judges the board against its bake's receipt; lanes and SNK verdicts exact", check_bound_census, [
    # bug 1: every receipt spike hard-coded to PriceCharting
    ("source = source_of.get(sale_id, UNKNOWN_SOURCE)", 'source = "pricecharting"'),
    ('"source": source_of.get(sale_id, UNKNOWN_SOURCE), "quarantined": True,',
     '"source": "pricecharting", "quarantined": True,'),
    ('severity = "error" if sale["quarantined"] else "warn"',
     'severity = "error" if sale["source"] == "pricecharting" else "warn"'),
    # bug 1: SNK verdicts over sales that are already quarantined
    ("quarantined_sale_ids=quarantined,", "quarantined_sale_ids=set(),"),
    ("active_rows, _released = pcq.split_released(pcq.load_stored_rows(cursor))",
     "active_rows, _released = pcq.load_stored_rows(cursor), {}"),
    ('quarantined = {int(entry["saleObservationId"]) for entry in entries} | {int(row["id"]) for row in active_rows}',
     'quarantined = {int(row["id"]) for row in active_rows}'),
    # bug 2: the receipt of a later run instead of the bake's
    ("if at <= baked and (best is None or at > best[0]):", "if best is None or at > best[0]:"),
    ("receipt_path = bake_receipt_path(snapshot, pcq.AUDIT_DIR)", "receipt_path = pcq.CURRENT_RECEIPT"),
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
