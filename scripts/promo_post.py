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
HERMES_PROMO_JOB = "cardz-mc-6acct-12jst-daily"
HERMES_CRON_JOBS = Path(
    os.environ.get("HERMES_CRON_JOBS", "/home/jackson0202/.hermes/cron/jobs.json")
)
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
THREADS_HOME = "https://www.threads.com/"
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


def assert_manual_post_authority(jobs_path: Path | None = None) -> None:
    """One publisher only: manual repo posting is locked while Hermes owns the day."""
    path = Path(jobs_path) if jobs_path is not None else HERMES_CRON_JOBS
    if not path.is_file():
        raise PostError(f"Hermes promo authority state unreadable: {path} — 唔准手動出帖")
    try:
        jobs = json.loads(path.read_text(encoding="utf-8")).get("jobs") or []
    except Exception as error:
        raise PostError(f"Hermes promo authority state unreadable: {path} — 唔准手動出帖") from error
    job = next((row for row in jobs if str(row.get("name")) == HERMES_PROMO_JOB), None)
    if not isinstance(job, dict):
        raise PostError(f"Hermes promo authority {HERMES_PROMO_JOB} missing — 唔准手動出帖")
    if bool(job.get("enabled")) and str(job.get("state") or "").lower() != "paused":
        raise PostError(
            f"Hermes promo authority {HERMES_PROMO_JOB} 正在啟用；先 pause 正式鏈，唔准雙 publisher"
        )


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
    fmt = P.CHANNEL_FORMAT[channel]
    jpg = pack / f"heatmap-{board}-{period}-{fmt}-{lang}.jpg"
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


def _node_text(node) -> str:
    """Playwright inner_text/input_value; ignore non-str fakes in unit tests."""
    for attr in ("input_value", "inner_text", "text_content"):
        fn = getattr(node, attr, None)
        if not callable(fn):
            continue
        try:
            val = fn(timeout=3000)
        except TypeError:
            try:
                val = fn()
            except Exception:
                continue
        except Exception:
            continue
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def read_composer_audience(page) -> str:
    """Selected community = searchbox value after the suggestion listbox closed."""
    listbox = page.get_by_role("listbox")
    if listbox.count():
        visible = getattr(listbox.first, "is_visible", None)
        if callable(visible):
            try:
                if visible():
                    return ""
            except TypeError:
                pass
    search = page.get_by_role("searchbox")
    if search.count():
        typed = _node_text(search.first)
        if typed:
            return typed
    return ""


def read_threads_audience(page) -> str:
    """Read-back: composer chip, then a visible CARDZGAME label. Never a feed username."""
    chip = read_composer_audience(page)
    if chip:
        return chip
    hit = page.get_by_text(THREADS_COMMUNITY, exact=False)
    if hit.count():
        vis = getattr(hit.first, "is_visible", None)
        if callable(vis):
            try:
                if vis():
                    return THREADS_COMMUNITY
            except TypeError:
                pass
        else:
            text = _node_text(hit.first)
            if audience_outcome(text, THREADS_COMMUNITY) == "posted":
                return text
    return ""


def require_threads_audience(page, expected: str, *, reader=None) -> str:
    """Stop before Post if CARDZGAME is not the selected composer audience."""
    try:
        label = str(reader(page) or "") if reader else read_composer_audience(page)
    except Exception as error:
        raise PostError(
            f"Threads 社羣 {expected} 未選定 — 停，唔好發去個人主 feed"
        ) from error
    if audience_outcome(label, expected) != "posted":
        raise PostError(
            f"Threads 社羣 {expected} 未選定 got={label!r} — 停，唔好發去個人主 feed"
        )
    return label


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
        want = str(decision.get("url") or "")
        ranked: list[tuple[int, Any]] = []
        for ctx in browser.contexts:
            for page in ctx.pages:
                if want and str(page.url) == want:
                    return page, decision
                hostname = str(urlparse(page.url).hostname or "").casefold()
                if P.hostname_matches_cdp_host(hostname, host):
                    ranked.append((P._threads_tab_rank(str(page.url)), page))
        if ranked:
            ranked.sort(key=lambda item: item[0])
            return ranked[0][1], decision
        raise PostError(f"9222 listed {host} but Playwright 見唔到嗰個 tab")
    if not allow_open:
        raise PostError(f"no {host} tab; dry-run will not open a new one unless --open-once")
    if not browser.contexts:
        raise PostError("9222 冇 browser context")
    page = browser.contexts[0].new_page()
    return page, decision


def _is_timeout(error: BaseException) -> bool:
    return error.__class__.__name__ == "TimeoutError"


def _dismiss_x_layers(page) -> None:
    """Close sheets/masks that intercept the compose textarea (data-testid=mask)."""
    try:
        page.keyboard.press("Escape")
    except Exception:
        return
    mask = page.locator('[data-testid="mask"]')
    if mask.count():
        try:
            page.keyboard.press("Escape")
        except Exception:
            return


def _click_first(locator, *, timeout: int = 8000, force: bool = False) -> None:
    try:
        locator.click(timeout=timeout, force=force)
    except TypeError:
        locator.click()


def _fill_draftjs(page, box, text: str) -> None:
    """Draft.js ignores insert_text; type after selecting the existing draft."""
    _click_first(box, timeout=8000)
    try:
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
    except Exception:
        pass
    typer = getattr(page.keyboard, "type", None)
    body = text.strip()
    if callable(typer):
        try:
            typer(body, delay=15)
            return
        except TypeError:
            typer(body)
            return
    page.keyboard.insert_text(body)


def compose_x(page, text: str, image: Path, *, confirm: bool) -> dict[str, Any]:
    page.goto(X_COMPOSE, wait_until="domcontentloaded", timeout=30000)
    _dismiss_x_layers(page)
    box = page.locator('[data-testid="tweetTextarea_0"]').first
    box.wait_for(timeout=15000)
    try:
        _fill_draftjs(page, box, text)
    except Exception as error:
        if not _is_timeout(error):
            raise
        _dismiss_x_layers(page)
        try:
            _click_first(box, timeout=8000, force=True)
            _fill_draftjs(page, box, text)
        except Exception as retry_error:
            raise PostError(
                f"x.com composer click intercepted (mask/layer): {retry_error}"
            ) from retry_error
    file_input = page.locator('input[type="file"][data-testid="fileInput"]').first
    if file_input.count():
        file_input.set_input_files(str(image))
    btn = page.locator('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]').first
    if not confirm:
        return {"filled": True, "posted": False, "reason": "fill-only", "outcome": "filled"}
    waiter = getattr(page, "wait_for_function", None)
    if callable(waiter):
        snippet = text.strip().splitlines()[0][:12]
        try:
            waiter(
                """(snippet) => {
                    const box = document.querySelector('[data-testid="tweetTextarea_0"]');
                    const b = document.querySelector('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]');
                    const landed = !!(box && (box.innerText || '').indexOf(snippet) >= 0);
                    const enabled = !!(b && !b.disabled && b.getAttribute('aria-disabled') !== 'true');
                    return landed && enabled;
                }""",
                snippet,
                timeout=20000,
            )
        except TypeError:
            waiter(
                """() => {
                    const b = document.querySelector('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]');
                    return !!(b && !b.disabled && b.getAttribute('aria-disabled') !== 'true');
                }""",
                timeout=20000,
            )
        except Exception as error:
            if not _is_timeout(error):
                raise
            raise PostError("x.com Post button stayed disabled (text/media did not land)") from error
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
    # CARDZGAME is a searchbox inside the "新串文" dialog, not home-feed text.
    search = page.get_by_role("searchbox")
    for label in ("新串文", "What's new?", "有什麼新鮮事", "有什么新鲜事", "建立"):
        opener = page.get_by_role("button", name=label)
        if opener.count() == 0:
            continue
        try:
            opener.first.click(timeout=8000)
        except Exception as error:
            if not _is_timeout(error):
                raise
            continue
        waiter = getattr(search.first, "wait_for", None)
        if callable(waiter):
            try:
                waiter(timeout=12000)
            except Exception as error:
                if not _is_timeout(error):
                    raise
                continue
        break
    search = page.get_by_role("searchbox")
    if search.count() == 0:
        raise PostError(f"Threads 社羣 {audience} 未出現 — 停，唔好發去個人主 feed")
    try:
        search.first.click(timeout=8000)
        filler = getattr(search.first, "fill", None)
        if callable(filler):
            filler(audience)
        else:
            page.keyboard.insert_text(audience)
    except Exception as error:
        raise PostError(f"Threads 社羣 {audience} 搜尋失敗 — 停，唔好發去個人主 feed") from error
    listbox = page.get_by_role("listbox")
    waiter = getattr(listbox.first, "wait_for", None)
    if callable(waiter):
        try:
            waiter(timeout=10000)
        except Exception as error:
            if not _is_timeout(error):
                raise
    try:
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
    except Exception:
        pass
    hide = getattr(listbox.first, "wait_for", None)
    if callable(hide):
        try:
            hide(state="hidden", timeout=8000)
        except TypeError:
            pass
        except Exception as error:
            if not _is_timeout(error):
                raise
            option = listbox.get_by_text(audience, exact=False)
            if option.count() == 0:
                option = listbox.get_by_text(audience.lower(), exact=False)
            if option.count() == 0:
                raise PostError(f"Threads 社羣 {audience} 未出現 — 停，唔好發去個人主 feed")
            try:
                option.first.click(timeout=8000)
            except Exception:
                option.first.click(force=True, timeout=8000)
            if callable(hide):
                try:
                    hide(state="hidden", timeout=8000)
                except Exception:
                    pass
    require_threads_audience(page, audience, reader=page_reader)
    composer = page.locator('[role="dialog"] [role="textbox"]').first
    if composer.count() == 0:
        composer = page.locator('[role="textbox"]').first
    composer.wait_for(timeout=15000)
    try:
        _click_first(composer, timeout=8000, force=True)
        _fill_draftjs(page, composer, text)
    except Exception as error:
        if not _is_timeout(error):
            raise
        raise PostError("Threads composer click intercepted — 停，唔好發去個人主 feed") from error
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
    sleeper = getattr(page, "wait_for_timeout", None)
    if callable(sleeper):
        try:
            sleeper(1500)
        except TypeError:
            pass
    reader = page_reader or read_threads_audience
    try:
        label = str(reader(page) or "")
        error = None
    except Exception as read_error:  # unreadable == unverified == fail closed
        label = ""
        error = str(read_error)
    if audience_outcome(label, audience) != "posted":
        visible = page.get_by_text(audience, exact=False)
        vis = getattr(visible.first, "is_visible", None) if visible.count() else None
        shown = False
        if visible.count() and callable(vis):
            try:
                shown = bool(vis())
            except TypeError:
                shown = False
        if shown:
            label = audience
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
    if channel.startswith("instagram-"):
        raise PostError(
            "Instagram publish is owned by Hermes cardz_marketcap_meta_post.py on isolated CDP 9222"
        )
    confirm = bool(args.confirm)
    fill_only = bool(getattr(args, "fill_only", False))
    if confirm and fill_only:
        raise PostError("--fill-only and --confirm are mutually exclusive")
    if confirm:
        assert_manual_post_authority()
    if confirm and channel.startswith(("x.com-", "threads-", "instagram-")):
        raise PostError(
            "public social publish is owned by the Hermes four-lane chain on isolated CDP 9222"
        )
    pack = Path(args.pack)
    brief = load_pack(pack)
    P.assert_heatmap_not_repeat(pack)
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
    except Exception as error:
        if not _is_timeout(error):
            raise
        error_text = f"playwright timeout: {error}"
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
