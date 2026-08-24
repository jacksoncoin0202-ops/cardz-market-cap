#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Promo-pack gates must fire. Zero call site = no gate.

Run: python -X utf8 scripts/test_promo_pack.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promo_after_publish as PAP  # noqa: E402
import promo_chain as P  # noqa: E402

# Fixed clock so the freshness gate is deterministic: 1.5h after generatedAt.
NOW_FIXED = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def expect_fail(label: str, fn) -> str:
    global CHECKS
    CHECKS += 1
    try:
        fn()
    except P.PromoError as error:
        return str(error)
    FAILED.append(f"FAIL {label} did not fire")
    return ""


def expect_stale(label: str, fn) -> str:
    global CHECKS
    CHECKS += 1
    try:
        fn()
    except P.PromoStaleLive as error:
        return str(error)
    except P.PromoError as error:
        FAILED.append(f"FAIL {label} raised the wrong error: {error}")
        return ""
    FAILED.append(f"FAIL {label} did not fire")
    return ""


def live_fixture(cards, generated_at: str, generation: str = "db3308_fix") -> dict:
    payload = {"generation": {"id": generation}, "cards": cards}
    return {
        "health": {"generation": generation, "generatedAt": generated_at},
        "scoped": {"pokemon": payload, "all": payload, "one-piece": payload},
    }


def main() -> int:
    cards = [
        {
            "id": "up1",
            "viewRank": 4,
            "tcg": "Pokémon",
            "name": {"en": "Up One", "zh-TW": "升一", "zh-CN": "升一"},
            "windows": {
                "1d": {"changePct": {"value": 9.1, "status": "ready"}},
                "7d": {"changePct": {"value": 9.1, "status": "ready"}},
            },
        },
        {
            "id": "up2",
            "viewRank": 8,
            "tcg": "Pokémon",
            "name": {"en": "Up Two", "zh-TW": "升二"},
            "windows": {
                "1d": {"changePct": {"value": 3.2, "status": "ready"}},
                "7d": {"changePct": {"value": 3.2, "status": "ready"}},
            },
        },
        {
            "id": "down1",
            "viewRank": 11,
            "tcg": "Pokémon",
            "name": {"en": "Down One", "zh-TW": "跌一"},
            "windows": {
                "1d": {"changePct": {"value": -11.0, "status": "ready"}},
                "7d": {"changePct": {"value": -11.0, "status": "ready"}},
            },
        },
        {
            "id": "grey",
            "viewRank": 1,
            "name": {"en": "Grey"},
            "windows": {"1d": {"changePct": {"value": None, "status": "accumulating"}}},
        },
        {
            "id": "zero",
            "viewRank": 2,
            "name": {"en": "Zero"},
            "windows": {"1d": {"changePct": {"value": 0, "status": "ready"}}},
        },
        {
            "id": "outside",
            "viewRank": 101,
            "name": {"en": "Watchlist"},
            "windows": {"1d": {"changePct": {"value": 50, "status": "ready"}}},
        },
    ]
    movers = P.movers_from_cards(cards)
    check("up0", movers["up"][0]["id"], "up1")
    check("up1", movers["up"][1]["id"], "up2")
    check("down0", movers["down"][0]["id"], "down1")
    check("period", movers["period"], "7d")
    check("top100-only", [row["id"] for row in movers["up"]], ["up1", "up2"])
    copy_hant = P.render_copy(movers, board="all", script="zh-Hant")
    check("lantern-up", "🟢 #4 升一" in copy_hant, True)
    check("lantern-down", "🔴 #11 跌一" in copy_hant, True)
    check("link", P.PUBLIC_LINK in copy_hant, True)
    check("no-speech", ("因為" in copy_hant) or ("我們" in copy_hant), False)
    copy_hans = P.render_copy(movers, board="all", script="zh-Hans")
    P.assert_text("x.com-zh", copy_hans)
    P.assert_text("threads-zh", copy_hant)

    expect_fail("period-180d", lambda: P.movers_from_cards(cards, period="180d"))
    expect_fail("period-6m", lambda: P.movers_from_cards(cards, period="365d"))

    expect_fail("hant-simplified", lambda: P.assert_text("fork-zh", "这张卡今天涨了"))
    expect_fail("whatsapp-leak", lambda: P.assert_text("whatsapp-ptcg", r"C:\Users\jackson0202\Downloads\x.png"))
    expect_fail("en-localhost", lambda: P.assert_text("x.com-en", "http://127.0.0.1:3800"))
    expect_fail("x-en-over-280", lambda: P.assert_text("x.com-en", "a" * 281))
    P.assert_text("x.com-zh", "这张卡今天涨了")  # Hans lane is allowed
    P.assert_text("fork-zh", "這張卡今日升咗")
    check("x-url-23", P.x_weighted_length("https://cardzmarketcap.com"), 23)
    check("x-cjk-2", P.x_weighted_length("龍"), 2)
    check("x-emoji-2", P.x_weighted_length("🟢"), 2)

    long_en = {
        "id": "long",
        "viewRank": 4,
        "officialName": "2015 Pokemon Japanese XY Promo Pretend Magikarp Pikachu Holo 150/XY-P",
        "setName": {"en": "Pokemon Japanese XY Promo"},
        "collectorNumber": "150/XY-P",
        "name": {"en": "2015 Pokemon Japanese XY Promo Pretend Magikarp Pikachu Holo 150/XY-P"},
        "windows": {"7d": {"changePct": {"value": 9.1, "status": "ready"}}},
    }
    peeled = P.short_name(long_en, "en")
    check("en-peel-year", "2015" in peeled, False)
    check("en-peel-subject", "Pretend Magikarp Pikachu Holo" in peeled, True)
    check("en-peel-number", "150/XY-P" in peeled, True)
    luffy = {
        "id": "luffy",
        "viewRank": 98,
        "tcg": "One Piece",
        "name": {"zh-TW": "蒙其・D・魯夫", "en": "Monkey D. Luffy"},
        "collectorNumber": "OP05-060",
        "windows": {"7d": {"changePct": {"value": -10.0, "status": "ready"}}},
    }
    check("op-number", "OP05-060" in P.short_name(luffy, "zh-Hant"), True)
    op_copy = P.render_copy(
        P.movers_from_cards([luffy], locale="zh-Hant"),
        board="one-piece",
        script="zh-Hant",
        channel="whatsapp-op",
    )
    check("op-copy-number", "OP05-060" in op_copy, True)

    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp)
        good = pack / "ok.jpg"
        good.write_bytes(b"\xff\xd8\xff" + b"\x00" * 9000)
        check("resume-jpeg", P.reusable_heatmap(good), True)
        tiny = pack / "tiny.jpg"
        tiny.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        check("resume-too-small", P.reusable_heatmap(tiny), False)
        leak = pack / "leak.jpg"
        leak.write_bytes(b"\xff\xd8\xff" + b"C:\\Users\\jackson0202\\" + b"\x00" * 9000)
        check("resume-leak", P.reusable_heatmap(leak), False)
        P.write_receipt(pack, {"generation": "g1", "errors": [{"stage": "heatmap", "key": "all-en", "error": "HTTP 404"}]})
        rec = P.read_receipt(pack)
        check("receipt-errors", rec["errors"][0]["error"], "HTTP 404")

    health = {"generation": "db3308_aaa", "generatedAt": "2026-08-20T08:31:21.350Z"}
    payload = {"generation": {"id": "db3308_aaa"}, "cards": cards}
    brief = P.brief_from_payload(
        health, {"pokemon": payload, "all": payload, "one-piece": payload}, now=NOW_FIXED
    )
    check("brief-gen", brief["generation"], "db3308_aaa")
    check(
        "heatmap",
        brief["boards"]["pokemon"]["heatmapUrl"],
        "https://app.cardzmarketcap.com/api/og/heatmap?period=7d&show=40&scope=pokemon&format=post&theme=dark&updown=green-up&lang=en",
    )
    check(
        "channel-ptcg",
        brief["channelHeatmaps"]["whatsapp-ptcg"],
        "https://app.cardzmarketcap.com/api/og/heatmap?period=7d&show=40&scope=pokemon&format=post&theme=dark&updown=green-up&lang=zh-TW",
    )
    check(
        "channel-tcg-4",
        brief["channelHeatmaps"]["whatsapp-tcg-4"],
        "https://app.cardzmarketcap.com/api/og/heatmap?period=7d&show=40&scope=all&format=post&theme=dark&updown=green-up&lang=zh-TW",
    )
    check(
        "channel-x-zh",
        brief["channelHeatmaps"]["x.com-zh"],
        "https://app.cardzmarketcap.com/api/og/heatmap?period=7d&show=40&scope=all&format=post&theme=dark&updown=green-up&lang=zh-CN",
    )
    expect_fail("og-180d", lambda: P.heatmap_og_url("all", "180d"))
    check("post-flag", brief["post"], False)
    expect_fail(
        "gen-mismatch",
        lambda: P.brief_from_payload(
            health, {"pokemon": {"generation": "other", "cards": cards}}, now=NOW_FIXED
        ),
    )

    # --- freshness gate (task 2) ---
    expected_lag = round(
        (NOW_FIXED - P.parse_iso_utc(health["generatedAt"])).total_seconds() / 3600.0, 4
    )
    check("brief-lag-hours", brief["lagHours"], expected_lag)
    check("brief-max-lag", brief["maxLagHours"], 26.0)
    fresh_edge = P.brief_from_payload(
        health,
        {"all": payload},
        now=P.parse_iso_utc(health["generatedAt"]) + timedelta(hours=25, minutes=59),
    )
    check("fresh-under-26h", round(fresh_edge["lagHours"], 2), 25.98)
    stale_msg = expect_stale(
        "stale-over-26h",
        lambda: P.brief_from_payload(
            health,
            {"all": payload},
            now=P.parse_iso_utc(health["generatedAt"]) + timedelta(hours=26, minutes=1),
        ),
    )
    check("stale-msg", "PROMO_MAX_LIVE_LAG_HOURS" in stale_msg, True)
    allowed = P.brief_from_payload(
        health,
        {"all": payload},
        now=P.parse_iso_utc(health["generatedAt"]) + timedelta(hours=200),
        allow_stale=True,
    )
    check("allow-stale-builds", round(allowed["lagHours"], 1), 200.0)
    check("allow-stale-flag", allowed["allowStale"], True)
    expect_stale(
        "stale-unparseable",
        lambda: P.brief_from_payload({"generation": "g", "generatedAt": ""}, {"all": payload}),
    )
    env_before = os.environ.get("PROMO_MAX_LIVE_LAG_HOURS")
    os.environ["PROMO_MAX_LIVE_LAG_HOURS"] = "1"
    try:
        check("env-limit", P.max_live_lag_hours(), 1.0)
        expect_stale(
            "stale-env-1h",
            lambda: P.brief_from_payload(health, {"all": payload}, now=NOW_FIXED),
        )
    finally:
        if env_before is None:
            os.environ.pop("PROMO_MAX_LIVE_LAG_HOURS", None)
        else:
            os.environ["PROMO_MAX_LIVE_LAG_HOURS"] = env_before

    # --- receipts (task 3) + newline="\n" (task 5) ---
    with tempfile.TemporaryDirectory(prefix="promo-receipt-") as tmp:
        runtime = Path(tmp)
        receipt = P.write_action_receipt(
            business_date="2026-08-20",
            destination="x.com-en",
            dry_run=True,
            fill_only=False,
            posted=False,
            text="hello\n",
            live_generated_at=health["generatedAt"],
            lag_hours=brief["lagHours"],
            outcome="built",
            runtime_dir=runtime,
        )
        check("receipt-under-receipts", receipt.parent.name, "receipts")
        check("receipt-name-prefix", receipt.name.startswith("2026-08-20_x.com-en_"), True)
        raw = receipt.read_bytes()
        check("receipt-no-cr", b"\r" in raw, False)
        body = json.loads(raw.decode("utf-8"))
        check(
            "receipt-fields",
            sorted(body),
            [
                "business_date",
                "destination",
                "dry_run",
                "error",
                "fill_only",
                "lag_hours",
                "live_generated_at",
                "outcome",
                "posted",
                "text_sha256",
            ],
        )
        check(
            "receipt-sha",
            body["text_sha256"],
            "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03",
        )
        check("receipt-outcome", body["outcome"], "built")
        check("receipt-lag", body["lag_hours"], expected_lag)
        check("receipt-error-null", body["error"], None)

    # --- scheduled consumer promo_after_publish (task 6) ---
    fresh_iso = (
        (datetime.now(timezone.utc) - timedelta(hours=1))
        .isoformat()
        .replace("+00:00", "Z")
    )
    stale_iso = (
        (datetime.now(timezone.utc) - timedelta(hours=100))
        .isoformat()
        .replace("+00:00", "Z")
    )
    dest_file_default, warn_default = PAP.resolve_destinations_file(None)
    if PAP.DEST_FILE.is_file():
        check("dest-real-used", dest_file_default, PAP.DEST_FILE)
    else:
        check("dest-example-fallback", dest_file_default, PAP.DEST_EXAMPLE)
        check("dest-fallback-warns", any("falling back" in w for w in warn_default), True)

    with tempfile.TemporaryDirectory(prefix="promo-after-") as tmp:
        base = Path(tmp)
        (base / "live-fresh.json").write_text(
            json.dumps(live_fixture(cards, fresh_iso)), encoding="utf-8"
        )
        (base / "live-stale.json").write_text(
            json.dumps(live_fixture(cards, stale_iso)), encoding="utf-8"
        )
        (base / "dest-bad.json").write_text(json.dumps({"nope-zz": "x"}), encoding="utf-8")
        runtime = base / "runtime"
        env_rt = os.environ.get("PROMO_RUNTIME_DIR")
        os.environ["PROMO_RUNTIME_DIR"] = str(runtime)
        try:
            rc_ok = PAP.main(
                [
                    "--live-json", str(base / "live-fresh.json"),
                    "--destinations", str(PAP.DEST_EXAMPLE),
                    "--business-date", "2026-08-20",
                ]
            )
            check("after-publish-ok-rc", rc_ok, 0)
            pack = runtime / "db3308_fix"
            check("after-publish-brief", (pack / "brief.json").is_file(), True)
            check("after-publish-copy", (pack / "whatsapp-ptcg.txt").is_file(), True)
            check("after-publish-copy-no-cr", b"\r" in (pack / "whatsapp-ptcg.txt").read_bytes(), False)
            check("after-publish-brief-no-cr", b"\r" in (pack / "brief.json").read_bytes(), False)
            receipts = sorted((runtime / "receipts").glob("2026-08-20_*.json"))
            check("after-publish-receipts", len(receipts), 3)
            first = json.loads(receipts[0].read_text(encoding="utf-8"))
            check("after-publish-not-posted", first["posted"], False)
            check("after-publish-dry", first["dry_run"], True)
            check("after-publish-outcome", first["outcome"], "built")

            rc_stale = PAP.main(
                [
                    "--live-json", str(base / "live-stale.json"),
                    "--destinations", str(PAP.DEST_EXAMPLE),
                    "--business-date", "2026-08-20",
                ]
            )
            check("after-publish-stale-rc", rc_stale, 3)

            rc_err = PAP.main(
                [
                    "--live-json", str(base / "live-fresh.json"),
                    "--destinations", str(base / "dest-bad.json"),
                    "--business-date", "2026-08-20",
                ]
            )
            check("after-publish-error-rc", rc_err, 2)
            failures = sorted((runtime / "failures").glob("2026-08-20_*.json"))
            check("after-publish-failure-artifacts", len(failures), 2)
            failure_outcomes = {
                json.loads(path.read_text(encoding="utf-8"))["outcome"]
                for path in failures
            }
            check("after-publish-failure-outcomes", failure_outcomes, {"stale", "error"})

            mixed = base / "mixed-pack"
            mixed.mkdir()
            (mixed / "brief.json").write_text(
                json.dumps({"generation": "old-generation"}), encoding="utf-8"
            )
            rc_mixed = PAP.main(
                [
                    "--live-json", str(base / "live-fresh.json"),
                    "--destinations", str(PAP.DEST_EXAMPLE),
                    "--business-date", "2026-08-20",
                    "--out-dir", str(mixed),
                ]
            )
            check("after-publish-mixed-generation-refused", rc_mixed, 2)
            check(
                "after-publish-mixed-generation-preserved",
                json.loads((mixed / "brief.json").read_text(encoding="utf-8"))["generation"],
                "old-generation",
            )
        finally:
            if env_rt is None:
                os.environ.pop("PROMO_RUNTIME_DIR", None)
            else:
                os.environ["PROMO_RUNTIME_DIR"] = env_rt

    # --- CLI exit codes for the freshness gate (task 2), zero network ---
    real_fetch = P.fetch_json
    real_heatmap = P.download_heatmap
    real_argv = sys.argv
    with tempfile.TemporaryDirectory(prefix="promo-cli-") as tmp:
        cli_pack = Path(tmp)
        stale_health = {"generation": "db3308_cli", "generatedAt": stale_iso}
        stale_payload = {"generation": {"id": "db3308_cli"}, "cards": cards}
        P.fetch_json = lambda url: stale_health if "/api/health" in url else stale_payload
        P.download_heatmap = lambda *_a, **_k: cli_pack / "heatmap-stub.jpg"
        env_rt = os.environ.get("PROMO_RUNTIME_DIR")
        os.environ["PROMO_RUNTIME_DIR"] = str(cli_pack / "runtime")
        try:
            sys.argv = ["promo_chain.py", "brief", "--out", str(cli_pack / "brief.json")]
            check("cli-stale-exit-3", P.main(), 3)
            check("cli-stale-wrote-nothing", (cli_pack / "brief.json").exists(), False)
            sys.argv = [
                "promo_chain.py",
                "brief",
                "--out",
                str(cli_pack / "brief.json"),
                "--allow-stale",
            ]
            check("cli-allow-stale-exit-0", P.main(), 0)
            check("cli-allow-stale-wrote-brief", (cli_pack / "brief.json").is_file(), True)
            check("cli-brief-no-cr", b"\r" in (cli_pack / "brief.json").read_bytes(), False)
            check("cli-copy-no-cr", b"\r" in (cli_pack / "x.com-en.txt").read_bytes(), False)
            cli_receipts = sorted((cli_pack / "runtime" / "receipts").glob("*.json"))
            check("cli-build-receipts", len(cli_receipts), len(P.CHANNEL_SCRIPT))
        finally:
            P.fetch_json = real_fetch
            P.download_heatmap = real_heatmap
            sys.argv = real_argv
            if env_rt is None:
                os.environ.pop("PROMO_RUNTIME_DIR", None)
            else:
                os.environ["PROMO_RUNTIME_DIR"] = env_rt

    banned = sorted(
        name
        for name in list(sys.modules)
        if name == "promo_post"
        or any(part in name.lower() for part in ("websocket", "playwright", "selenium", "pychrome"))
    )
    check("after-publish-no-browser-modules", banned, [])

    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp)
        (pack / "brief.json").write_text(json.dumps(brief), encoding="utf-8")
        (pack / "fork-zh.txt").write_text("這張卡今日升咗", encoding="utf-8")
        result = P.assert_pack(pack, expect_generation="db3308_aaa")
        check("pack-ok", result["ok"], True)
        (pack / "fork-zh.txt").write_text("这张卡涨了", encoding="utf-8")
        expect_fail("pack-hans", lambda: P.assert_pack(pack, expect_generation="db3308_aaa"))
        (pack / "fork-zh.txt").write_text("這張卡", encoding="utf-8")
        expect_fail("pack-wrong-gen", lambda: P.assert_pack(pack, expect_generation="live_now"))

    pages = [
        {"id": "x1", "type": "page", "url": "https://x.com/home"},
        {"id": "x2", "type": "page", "url": "https://x.com/compose/post"},
        {"id": "live", "type": "page", "url": "https://app.cardzmarketcap.com/?period=180d"},
    ]
    reuse = P.pick_cdp_tab(pages, "x.com")
    check("tab-reuse", reuse["action"], "reuse")
    check("tab-id", reuse["id"], "x1")
    check("tab-no-new", reuse["openNew"], False)
    check("tab-extra", reuse["extraSameHost"], ["x2"])
    missing = P.pick_cdp_tab(pages, "threads.net")
    check("tab-open-once", missing["action"], "open_once")
    check("tab-open-new", missing["openNew"], True)

    if FAILED:
        print("\n".join(FAILED))
        print(f"{len(FAILED)} failed / {CHECKS} checks")
        return 1
    print(f"PASS {CHECKS} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
