#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Promo publish drivers. Default is dry-run: build a receipt, touch nothing.

Dry-run is side-effect free: zero CDP / websocket / network calls. Filling the
composer is an explicit opt-in (`--fill-only`); it never clicks Post. Only
`--confirm` posts, and posting is always a human-triggered command.

X.com: Chrome 9222, one tab, proven data-testid selectors.
Threads (Daddy 講「Flock」= Threads 社羣): 9222, must pick CARDZGAME or stop.
WhatsApp: Hermes WS `send --to whatsapp:#群名` + MEDIA:<jpg>.
群名來自 CHANNEL_HERMES_NAME 固定表（同 CHANNEL_BOARD 對齊），唔 list、唔模糊、唔 AI。
Facebook: not in this chain.

Never call from live.confirmed. Never open a second tab for the same host.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import promo_chain as P  # noqa: E402

CDP = "http://127.0.0.1:9222"
THREADS_COMMUNITY = "CARDZGAME"
DEST_FILE = ROOT / "data" / "runtime" / "promo" / "destinations.json"
HERMES_REAL = "/home/jackson0202/.local/bin/hermes.real"
# 一早分好類，同 CHANNEL_BOARD 對齊。每次 send 用呢個表，唔好 AI／模糊字估群。
CHANNEL_HERMES_NAME = {
    "whatsapp-ptcg": "PTCG",
    "whatsapp-tcg-4": "Yaichi x Cardz.Game TCG 社區｜4號群",
    "whatsapp-op": "海賊王",
}
_WA_CHANNELS = {k for k in P.CHANNEL_SCRIPT if k.startswith("whatsapp-")}
if set(CHANNEL_HERMES_NAME) != _WA_CHANNELS:
    raise RuntimeError(f"CHANNEL_HERMES_NAME missing {_WA_CHANNELS - set(CHANNEL_HERMES_NAME)}")

X_COMPOSE = "https://x.com/compose/post"
THREADS_HOME = "https://www.threads.net/"
_AUDIENCE_WS_RE = re.compile(r"\s+")

# Outcome -> process exit code. A post that landed in the wrong audience is a
# failure even though the click succeeded, so it must not exit 0.
OUTCOME_EXIT = {
    "dry_run": 0,
    "built": 0,
    "filled": 0,
    "posted": 0,
    "audience_mismatch": 4,
    "error": 1,
}


def exit_code_for_outcome(outcome: str) -> int:
    return OUTCOME_EXIT.get(str(outcome), 1)


class PostError(P.PromoError):
    pass


def load_pack(pack: Path) -> dict[str, Any]:
    brief = pack / "brief.json"
    if not brief.is_file():
        raise PostError(f"pack missing brief.json: {pack}")
    return json.loads(brief.read_text(encoding="utf-8"))


def channel_copy(pack: Path, channel: str) -> str:
    path = pack / f"{channel}.txt"
    if not path.is_file():
        raise PostError(f"missing copy {path.name}")
    text = path.read_text(encoding="utf-8")
    P.assert_text(channel, text)
    return text


def channel_image(pack: Path, channel: str) -> Path:
    brief = load_pack(pack)
    period = str(brief.get("period") or P.DAILY_PERIOD)
    board = P.CHANNEL_BOARD[channel]
    lang = P.SCRIPT_TO_OG_LANG[P.CHANNEL_SCRIPT[channel]]
    jpg = pack / f"heatmap-{board}-{period}-post-{lang}.jpg"
    if not jpg.is_file():
        raise PostError(f"missing heatmap {jpg.name} — run brief first")
    if not P.reusable_heatmap(jpg):
        raise PostError(f"heatmap unusable {jpg.name}")
    return jpg


def load_destinations() -> dict[str, str]:
    if not DEST_FILE.is_file():
        return {}
    raw = json.loads(DEST_FILE.read_text(encoding="utf-8"))
    return {str(k): str(v).strip() for k, v in raw.items() if str(v).strip()}


def hermes_target_for_channel(
    channel: str,
    destinations: Mapping[str, str] | None = None,
) -> str:
    """whatsapp-ptcg → whatsapp:#PTCG。固定表，唔 list／唔模糊／唔 AI。"""
    dest_map = load_destinations() if destinations is None else dict(destinations)
    dest = dest_map.get(channel, "").strip()
    if dest and "REPLACE_" not in dest:
        return dest if ":" in dest else f"whatsapp:{dest}"
    name = CHANNEL_HERMES_NAME.get(channel)
    if not name:
        raise PostError(f"{channel}: 唔喺固定 Hermes 群表")
    return f"whatsapp:#{name}"


def _normalize_audience(value: Any) -> str:
    return _AUDIENCE_WS_RE.sub("", str(value or "")).casefold()


def audience_outcome(label: Any, expected: Any) -> str:
    """Read-back label must still name the configured audience, else mismatch."""
    got = _normalize_audience(label)
    want = _normalize_audience(expected)
    if want and want in got:
        return "posted"
    return "audience_mismatch"


def read_threads_audience(page) -> str:
    """Best-effort read-back of the audience/visibility label of the new post."""
    for selector in (
        '[data-testid="audience-selector"]',
        '[role="dialog"] [role="button"][aria-haspopup]',
        '[data-pressable-container] [role="link"]',
    ):
        node = page.locator(selector).first
        if node.count():
            return str(node.inner_text(timeout=5000) or "").strip()
    return ""


def plan_steps(channel: str) -> list[str]:
    host = P.HOST_FOR_CHANNEL[channel]
    steps = [
        f"reuse 9222 tab host={host} (never extra tab)",
        "assert copy + heatmap",
    ]
    if channel.startswith("x.com"):
        steps += [
            f"open {X_COMPOSE} in the same tab",
            "fill [data-testid=tweetTextarea_0]",
            "attach input[data-testid=fileInput]",
            "click [data-testid=tweetButton] only with --confirm",
        ]
    elif channel.startswith("threads"):
        steps += [
            f"open {THREADS_HOME} in the same tab",
            f"pick community/topic {THREADS_COMMUNITY} or STOP",
            "fill composer",
            "attach image",
            "publish only with --confirm",
        ]
    elif channel.startswith("whatsapp"):
        name = CHANNEL_HERMES_NAME[channel]
        steps += [
            f"hermes send --to whatsapp:#{name} MEDIA:<jpg>",
            "fixed table CHANNEL_HERMES_NAME, no AI match",
        ]
    return steps


def cmd_plan(args: argparse.Namespace) -> int:
    pack = Path(args.pack)
    load_pack(pack)
    rows = []
    for channel in P.CHANNEL_SCRIPT:
        if channel in {"fork-zh", "site-zh"}:
            continue
        image = None
        try:
            image = str(channel_image(pack, channel))
            channel_copy(pack, channel)
            copy_ok = True
        except (PostError, P.PromoError) as error:
            copy_ok = False
            image = str(error)
        wa_target = ""
        if channel.startswith("whatsapp"):
            try:
                wa_target = hermes_target_for_channel(channel)
            except PostError as error:
                wa_target = str(error)
        rows.append(
            {
                "channel": channel,
                "host": P.HOST_FOR_CHANNEL[channel],
                "steps": plan_steps(channel),
                "ready": copy_ok,
                "image": image,
                "hermesTarget": wa_target,
                "confirmRequired": True,
            }
        )
    print(json.dumps({"pack": str(pack), "post": False, "channels": rows}, ensure_ascii=False, indent=2))
    return 0


def _connect(cdp_url: str = CDP):
    """Only reached with --fill-only or --confirm. Dry-run never calls this."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise PostError("playwright not installed") from error
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(cdp_url)
    except Exception as error:
        pw.stop()
        raise PostError(f"{cdp_url} connect failed: {error}") from error
    return pw, browser


def _page_for_host(browser, host: str, *, allow_open: bool):
    pages = P.list_cdp_pages()
    decision = P.pick_cdp_tab(pages, host)
    if decision["action"] == "reuse":
        for ctx in browser.contexts:
            for page in ctx.pages:
                hostname = urlparse(page.url).hostname or ""
                if host in hostname:
                    return page, decision
        raise PostError(f"9222 listed {host} but Playwright 見唔到嗰個 tab")
    if not allow_open:
        raise PostError(f"no {host} tab; dry-run will not open a new one unless --open-once")
    if not browser.contexts:
        raise PostError("9222 冇 browser context")
    page = browser.contexts[0].new_page()
    return page, decision


def compose_x(page, text: str, image: Path, *, confirm: bool) -> dict[str, Any]:
    page.goto(X_COMPOSE, wait_until="domcontentloaded", timeout=30000)
    box = page.locator('[data-testid="tweetTextarea_0"]').first
    box.wait_for(timeout=15000)
    box.click()
    page.keyboard.insert_text(text.strip())
    file_input = page.locator('input[type="file"][data-testid="fileInput"]').first
    if file_input.count():
        file_input.set_input_files(str(image))
    btn = page.locator('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]').first
    if not confirm:
        return {"filled": True, "posted": False, "reason": "fill-only", "outcome": "filled"}
    btn.click(timeout=10000)
    return {"filled": True, "posted": True, "outcome": "posted"}


def compose_threads(
    page,
    text: str,
    image: Path,
    *,
    confirm: bool,
    audience: str = THREADS_COMMUNITY,
    page_reader=None,
) -> dict[str, Any]:
    page.goto(THREADS_HOME, wait_until="domcontentloaded", timeout=30000)
    community = page.get_by_text(audience, exact=False)
    if community.count() == 0:
        raise PostError(f"Threads 社羣 {audience} 未出現 — 停，唔好發去個人主 feed")
    community.first.click(timeout=8000)
    composer = page.locator('[role="textbox"]').first
    composer.wait_for(timeout=15000)
    composer.click()
    page.keyboard.insert_text(text.strip())
    file_input = page.locator('input[type="file"]').first
    if file_input.count():
        file_input.set_input_files(str(image))
    if not confirm:
        return {
            "filled": True,
            "posted": False,
            "community": audience,
            "reason": "fill-only",
            "outcome": "filled",
        }
    post_btn = page.get_by_role("button", name="Post").or_(page.get_by_role("button", name="發佈"))
    post_btn.first.click(timeout=10000)
    reader = page_reader or read_threads_audience
    try:
        label = str(reader(page) or "")
        error = None
    except Exception as read_error:  # unreadable == unverified == fail closed
        label = ""
        error = str(read_error)
    outcome = audience_outcome(label, audience)
    return {
        "filled": True,
        "posted": True,
        "community": audience,
        "audienceLabel": label,
        "outcome": outcome,
        "error": error,
    }


def send_whatsapp(
    channel: str,
    text: str,
    image: Path,
    *,
    confirm: bool,
    destinations: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if destinations is not None:
        dest = destinations.get(channel, "")
        if not dest:
            raise PostError(f"{channel}: destinations 冇群")
        if not dest.startswith("whatsapp:"):
            dest = f"whatsapp:{dest}"
    else:
        dest = hermes_target_for_channel(channel)
    body = f"{text.strip()}\nMEDIA:{image}"
    if not confirm:
        return {
            "filled": False,
            "posted": False,
            "to": dest,
            "reason": "dry-run",
            "outcome": "dry_run",
        }
    if os.name == "nt":
        cmd = ["wsl.exe", "-d", "Ubuntu", "--", HERMES_REAL, "send", "-q", "--to", dest]
    else:
        cmd = [HERMES_REAL, "send", "-q", "--to", dest]
    proc = subprocess.run(cmd, input=body.encode("utf-8"), capture_output=True, timeout=60)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).decode("utf-8", "replace")[-300:]
        raise PostError(f"hermes send exit {proc.returncode}: {err}")
    return {"filled": True, "posted": True, "to": dest, "outcome": "posted"}


def _drive_browser(
    channel: str,
    text: str,
    image: Path,
    *,
    confirm: bool,
    cdp_url: str,
    open_once: bool,
    page_reader=None,
) -> dict[str, Any]:
    pw, browser = _connect(cdp_url)
    try:
        page, decision = _page_for_host(browser, P.HOST_FOR_CHANNEL[channel], allow_open=open_once)
        if decision.get("extraSameHost"):
            print("same-host extras exist; reuse the first, do not open another tab", file=sys.stderr)
        if channel.startswith("x.com"):
            result = compose_x(page, text, image, confirm=confirm)
        else:
            result = compose_threads(
                page,
                text,
                image,
                confirm=confirm,
                audience=THREADS_COMMUNITY,
                page_reader=page_reader,
            )
        result["tab"] = decision
        return result
    finally:
        pw.stop()


def cmd_compose(args: argparse.Namespace) -> int:
    channel = args.channel
    if channel not in P.CHANNEL_SCRIPT or channel in {"fork-zh", "site-zh"}:
        raise PostError(f"unsupported channel {channel}")
    confirm = bool(args.confirm)
    fill_only = bool(getattr(args, "fill_only", False))
    if confirm and fill_only:
        raise PostError("--fill-only and --confirm are mutually exclusive")
    pack = Path(args.pack)
    brief = load_pack(pack)
    text = channel_copy(pack, channel)
    image = channel_image(pack, channel)
    business_date = str(getattr(args, "business_date", "") or P.business_date_jst())
    live_generated_at = brief.get("generatedAt")
    lag_hours = brief.get("lagHours")

    error_text: str | None = None
    try:
        if channel.startswith("whatsapp"):
            result = send_whatsapp(channel, text, image, confirm=confirm)
        elif not confirm and not fill_only:
            # Hard rule: dry-run touches no browser, no CDP, no socket.
            result = {"filled": False, "posted": False, "reason": "dry-run", "outcome": "dry_run"}
        else:
            result = _drive_browser(
                channel,
                text,
                image,
                confirm=confirm,
                cdp_url=str(getattr(args, "cdp", CDP) or CDP),
                open_once=bool(args.open_once),
            )
    except (PostError, P.PromoError) as error:
        error_text = str(error)
        result = {"filled": False, "posted": False, "outcome": "error"}
    error_text = error_text or result.get("error")
    outcome = str(result.get("outcome") or ("posted" if result.get("posted") else "dry_run"))
    receipt = P.write_action_receipt(
        business_date=business_date,
        destination=channel,
        dry_run=not confirm,
        fill_only=fill_only,
        posted=bool(result.get("posted")),
        text=text,
        live_generated_at=live_generated_at,
        lag_hours=lag_hours,
        outcome=outcome,
        error=error_text,
    )
    print(json.dumps({"channel": channel, "receipt": str(receipt), **result}, ensure_ascii=False))
    if outcome == "audience_mismatch":
        print(
            f"PROMO_POST_AUDIENCE_MISMATCH {channel} got={result.get('audienceLabel')!r}"
            f" want={result.get('community')!r}",
            file=sys.stderr,
        )
    elif error_text:
        print(f"PROMO_POST_FAIL {error_text}", file=sys.stderr)
    return exit_code_for_outcome(outcome)


def main() -> int:
    parser = argparse.ArgumentParser(description="Promo publish (dry-run unless --confirm)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--pack", required=True)
    comp = sub.add_parser("compose")
    comp.add_argument("--channel", required=True)
    comp.add_argument("--pack", required=True)
    comp.add_argument("--confirm", action="store_true", help="actually click Post / hermes send")
    comp.add_argument(
        "--fill-only",
        action="store_true",
        help="opt in to touching the composer (fills it, never clicks Post)",
    )
    comp.add_argument("--open-once", action="store_true", help="allow a single new tab if host missing")
    comp.add_argument("--cdp", default=CDP, help=f"CDP endpoint for the post path (default {CDP})")
    comp.add_argument("--business-date", help="receipt business date (default: today JST)")
    args = parser.parse_args()
    try:
        if args.cmd == "plan":
            return cmd_plan(args)
        if args.cmd == "compose":
            return cmd_compose(args)
    except (PostError, P.PromoError) as error:
        print(f"PROMO_POST_FAIL {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
