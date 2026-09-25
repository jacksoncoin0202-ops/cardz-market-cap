#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""promo_post gates. No 9222, no send."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import promo_post as PP  # noqa: E402

COPY = "TCG Top 100 · 7D\n\n🟢 #4 Up One  +9.10%\n\nhttps://cardzmarketcap.com\n"


class FakeNode:
    """Playwright-locator shaped stub. Everything chains, nothing touches a browser."""

    def __init__(self, n: int = 1) -> None:
        self._n = n

    @property
    def first(self):
        return self

    def count(self) -> int:
        return self._n

    def __getattr__(self, _name):
        def call(*_a, **_k):
            return self

        return call


class FakePage:
    def __init__(self) -> None:
        self.keyboard = FakeNode()

    def goto(self, *_a, **_k) -> None:
        return None

    def get_by_text(self, *_a, **_k):
        return FakeNode()

    def get_by_role(self, *_a, **_k):
        return FakeNode()

    def locator(self, *_a, **_k):
        return FakeNode()


def make_pack(pack: Path) -> None:
    """Minimal but real pack: brief + copy + a heatmap that passes reusable_heatmap."""
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "brief.json").write_text(
        json.dumps(
            {
                "generation": "db3308_test",
                "generatedAt": "2026-08-20T08:31:21.350Z",
                "lagHours": 1.4774,
                "period": "7d",
                "post": False,
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    (pack / "x.com-en.txt").write_text(COPY, encoding="utf-8", newline="\n")
    (pack / "heatmap-all-7d-post-en.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 9000)


def compose_args(pack: Path, **over) -> argparse.Namespace:
    base = {
        "channel": "x.com-en",
        "pack": str(pack),
        "confirm": False,
        "fill_only": False,
        "open_once": False,
        "cdp": PP.CDP,
        "business_date": "2026-08-20",
    }
    base.update(over)
    return argparse.Namespace(**base)


def boom(*_a, **_k):
    raise AssertionError("dry-run must not touch a browser")

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
    except PP.PostError as error:
        return str(error)
    FAILED.append(f"FAIL {label} did not fire")
    return ""


def expect_assertion(label: str, fn) -> str:
    global CHECKS
    CHECKS += 1
    try:
        fn()
    except AssertionError as error:
        return str(error)
    FAILED.append(f"FAIL {label} did not fire")
    return ""


def main() -> int:
    x_steps = PP.plan_steps("x.com-en")
    check("x-testid", any("tweetTextarea_0" in s for s in x_steps), True)
    check("x-confirm", any("--confirm" in s for s in x_steps), True)
    check("ig-pack-square", PP.P.CHANNEL_FORMAT["instagram-en"], "square")
    expect_fail(
        "ig-publish-owned-by-hermes",
        lambda: PP.cmd_compose(
            type("A", (), {"channel": "instagram-en", "confirm": False, "fill_only": False})()
        ),
    )
    th = PP.plan_steps("threads-zh")
    check("threads-community", any("CARDZGAME" in s for s in th), True)
    wa = PP.plan_steps("whatsapp-ptcg")
    check("wa-fixed-name", any("whatsapp:#PTCG" in s for s in wa), True)
    check("wa-no-list", any("send --list" in s for s in wa), False)
    check("no-fuzzy-fn", hasattr(PP, "match_hermes_group"), False)
    check("no-needles", hasattr(PP, "CHANNEL_GROUP_NEEDLES"), False)
    check("no-list-fn", hasattr(PP, "hermes_list"), False)
    check(
        "table-ptcg",
        PP.hermes_target_for_channel("whatsapp-ptcg", destinations={}),
        "whatsapp:#PTCG",
    )
    check("wa-ptcg-board", PP.P.CHANNEL_BOARD["whatsapp-ptcg"], "pokemon")
    check(
        "table-tcg-4",
        PP.hermes_target_for_channel("whatsapp-tcg-4", destinations={}),
        "whatsapp:#Yaichi x Cardz.Game TCG 社區｜4號群",
    )
    wa4 = PP.plan_steps("whatsapp-tcg-4")
    check("wa4-fixed-name", any("4號群" in s for s in wa4), True)
    check("wa4-tcg-board", PP.P.CHANNEL_BOARD["whatsapp-tcg-4"], "all")
    check(
        "wa-hermes-keys",
        set(PP.CHANNEL_HERMES_NAME),
        {k for k in PP.P.CHANNEL_SCRIPT if k.startswith("whatsapp-")},
    )
    check(
        "table-op",
        PP.hermes_target_for_channel("whatsapp-op", destinations={}),
        "whatsapp:#海賊王",
    )
    check(
        "placeholder-ignored",
        PP.hermes_target_for_channel(
            "whatsapp-ptcg",
            destinations={"whatsapp-ptcg": "whatsapp:REPLACE_PTCG_CHAT_ID"},
        ),
        "whatsapp:#PTCG",
    )
    check(
        "exact-override",
        PP.hermes_target_for_channel(
            "whatsapp-ptcg",
            destinations={"whatsapp-ptcg": "whatsapp:#PTCG"},
        ),
        "whatsapp:#PTCG",
    )
    expect_fail(
        "unknown-channel",
        lambda: PP.hermes_target_for_channel("x.com-en", destinations={}),
    )

    with tempfile.TemporaryDirectory(prefix="promo-authority-") as tmp:
        jobs = Path(tmp) / "jobs.json"
        jobs.write_text(
            json.dumps({"jobs": [{"name": PP.HERMES_PROMO_JOB, "enabled": True, "state": "scheduled"}]}),
            encoding="utf-8",
        )
        authority_err = expect_fail(
            "manual-publisher-blocked-while-hermes-active",
            lambda: PP.assert_manual_post_authority(jobs),
        )
        check("manual-publisher-block-names-owner", PP.HERMES_PROMO_JOB in authority_err, True)
        jobs.write_text(
            json.dumps({"jobs": [{"name": PP.HERMES_PROMO_JOB, "enabled": False, "state": "paused"}]}),
            encoding="utf-8",
        )
        check("manual-publisher-allowed-only-when-hermes-paused", PP.assert_manual_post_authority(jobs), None)

    expect_fail("pack-missing", lambda: PP.load_pack(Path(tempfile.gettempdir()) / "no-such-promo-pack"))
    expect_fail(
        "wa-no-dest",
        lambda: PP.send_whatsapp("whatsapp-ptcg", "hi", Path("x.jpg"), confirm=True, destinations={}),
    )

    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp)
        (pack / "brief.json").write_text(json.dumps({"generation": "g1", "period": "7d", "post": False}), encoding="utf-8")
        err = expect_fail("no-copy", lambda: PP.channel_copy(pack, "x.com-en"))
        check("no-copy-mentions", "missing copy" in err, True)

    # --- dry-run is side-effect free (task 1) ---
    check("cdp-default-unchanged", PP.CDP, "http://127.0.0.1:9222")
    real_connect = PP._connect
    real_list = PP.P.list_cdp_pages
    with tempfile.TemporaryDirectory(prefix="promo-post-") as tmp:
        base = Path(tmp)
        pack = base / "pack"
        make_pack(pack)
        runtime = base / "runtime"
        env_rt = os.environ.get("PROMO_RUNTIME_DIR")
        os.environ["PROMO_RUNTIME_DIR"] = str(runtime)
        PP._connect = boom
        PP.P.list_cdp_pages = boom
        try:
            rc = PP.cmd_compose(compose_args(pack))
            check("dry-run-rc", rc, 0)
            receipts = sorted((runtime / "receipts").glob("2026-08-20_x.com-en_*.json"))
            check("dry-run-receipt-written", len(receipts), 1)
            body = json.loads(receipts[0].read_text(encoding="utf-8"))
            check("dry-run-receipt-dry", body["dry_run"], True)
            check("dry-run-receipt-fill", body["fill_only"], False)
            check("dry-run-receipt-posted", body["posted"], False)
            check("dry-run-receipt-outcome", body["outcome"], "dry_run")
            check("dry-run-receipt-lag", body["lag_hours"], 1.4774)
            check("dry-run-receipt-gen-at", body["live_generated_at"], "2026-08-20T08:31:21.350Z")
            check("dry-run-receipt-no-cr", b"\r" in receipts[0].read_bytes(), False)

            # positive: --fill-only IS the opt-in that reaches the browser path
            reached = expect_assertion(
                "fill-only-reaches-connect", lambda: PP.cmd_compose(compose_args(pack, fill_only=True))
            )
            check("fill-only-connect-msg", "must not touch a browser" in reached, True)

            expect_fail(
                "fill-only-vs-confirm",
                lambda: PP.cmd_compose(compose_args(pack, fill_only=True, confirm=True)),
            )
            check("dry-run-receipt-count-unchanged", len(sorted((runtime / "receipts").glob("*.json"))), 1)
        finally:
            PP._connect = real_connect
            PP.P.list_cdp_pages = real_list
            if env_rt is None:
                os.environ.pop("PROMO_RUNTIME_DIR", None)
            else:
                os.environ["PROMO_RUNTIME_DIR"] = env_rt

    # --- Threads audience stays the hard constant (AGENTS.md 16), not overridable ---
    check("audience-constant", PP.THREADS_COMMUNITY, "CARDZGAME")
    check("audience-no-override-helper", hasattr(PP, "threads_audience_for_channel"), False)

    class FakePw:
        def stop(self) -> None:
            return None

    seen: dict = {}

    def record_threads(_page, _text, _image, **kwargs):
        seen.update(kwargs)
        return {"filled": True, "posted": False, "outcome": "filled"}

    real_dest_file = PP.DEST_FILE
    real_page_for_host = PP._page_for_host
    real_compose_threads = PP.compose_threads
    real_connect_2 = PP._connect
    with tempfile.TemporaryDirectory(prefix="promo-dest-") as tmp:
        dest_file = Path(tmp) / "destinations.json"
        dest_file.write_text(
            json.dumps({"threads-en": "threads:HIJACK", "whatsapp-ptcg": "whatsapp:#PTCG"}),
            encoding="utf-8",
        )
        PP.DEST_FILE = dest_file
        PP._connect = lambda cdp_url=PP.CDP: (FakePw(), object())
        PP._page_for_host = lambda _browser, _host, *, allow_open: (FakePage(), {})
        PP.compose_threads = record_threads
        try:
            # positive control: this runtime file IS read (whatsapp target comes from it)
            check("dest-file-is-live", PP.hermes_target_for_channel("whatsapp-ptcg"), "whatsapp:#PTCG")
            PP._drive_browser(
                "threads-en", COPY, Path("x.jpg"), confirm=False, cdp_url=PP.CDP, open_once=False
            )
            check("audience-not-hijackable", seen.get("audience"), "CARDZGAME")
        finally:
            PP.DEST_FILE = real_dest_file
            PP._connect = real_connect_2
            PP._page_for_host = real_page_for_host
            PP.compose_threads = real_compose_threads

    # --- Threads audience read-back (task 4) ---
    match = PP.compose_threads(
        FakePage(), COPY, Path("x.jpg"), confirm=True, page_reader=lambda _page: "CARDZGAME"
    )
    check("audience-match-outcome", match["outcome"], "posted")
    check("audience-match-posted", match["posted"], True)
    check("audience-match-exit", PP.exit_code_for_outcome(match["outcome"]), 0)
    mismatch_err = expect_fail(
        "audience-wrong-stops-before-post",
        lambda: PP.compose_threads(
            FakePage(), COPY, Path("x.jpg"), confirm=True, page_reader=lambda _page: "Anyone"
        ),
    )
    check("audience-wrong-names-cardzgame", "CARDZGAME" in mismatch_err, True)
    check("audience-wrong-says-not-selected", "未選定" in mismatch_err, True)

    def unreadable(_page):
        raise RuntimeError("selector gone")

    blind_err = expect_fail(
        "audience-unreadable-stops-before-post",
        lambda: PP.compose_threads(FakePage(), COPY, Path("x.jpg"), confirm=True, page_reader=unreadable),
    )
    check("audience-unreadable-names-cardzgame", "CARDZGAME" in blind_err, True)
    filled = PP.compose_threads(
        FakePage(), COPY, Path("x.jpg"), confirm=False, page_reader=lambda _page: "CARDZGAME"
    )
    check("fill-only-threads-outcome", filled["outcome"], "filled")
    check("fill-only-threads-not-posted", filled["posted"], False)
    check("audience-normalizes-case", PP.audience_outcome("Posted to cardzgame", "CARDZGAME"), "posted")
    pre = PP.require_threads_audience(
        FakePage(), "CARDZGAME", reader=lambda _page: "CARDZGAME"
    )
    check("require-composer-chip-ok", pre, "CARDZGAME")
    empty_pre = expect_fail(
        "require-composer-chip-empty",
        lambda: PP.require_threads_audience(FakePage(), "CARDZGAME", reader=lambda _page: ""),
    )
    check("require-composer-chip-empty-stops", "未選定" in empty_pre, True)
    check("exit-map-error", PP.exit_code_for_outcome("error"), 1)
    check("exit-map-unknown", PP.exit_code_for_outcome("who-knows"), 1)

    class TimeoutNode(FakeNode):
        def click(self, *_a, **_k):
            raise type("TimeoutError", (Exception,), {})("mask intercepts pointer events")

    class TimeoutPage(FakePage):
        def locator(self, selector="", **_k):
            if "tweetTextarea" in str(selector):
                return TimeoutNode()
            return FakeNode()

    leak = expect_fail(
        "x-mask-timeout-is-posterror",
        lambda: PP.compose_x(TimeoutPage(), COPY, Path("x.jpg"), confirm=False),
    )
    check("x-mask-timeout-mentions-intercept", "intercepted" in leak, True)
    check("timeout-name-helper", PP._is_timeout(type("TimeoutError", (Exception,), {})()), True)

    class NoSearchPage(FakePage):
        def get_by_role(self, role="", name=""):
            if role == "searchbox":
                return FakeNode(0)
            return FakeNode(1)

    missing = expect_fail(
        "threads-no-searchbox-stops",
        lambda: PP.compose_threads(NoSearchPage(), COPY, Path("x.jpg"), confirm=False),
    )
    check("threads-no-searchbox-names-cardzgame", "CARDZGAME" in missing, True)

    if FAILED:
        print("\n".join(FAILED))
        print(f"{len(FAILED)} failed / {CHECKS} checks")
        return 1
    print(f"PASS {CHECKS} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
