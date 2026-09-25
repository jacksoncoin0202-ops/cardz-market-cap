"""PC tabs must not accumulate after navigation failure or cancellation.

Run on the Windows collector interpreter: python -X utf8 scripts/test_pc_tab_cleanup.py
No real browser, credentials, DB, or network is used.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import cdp_identity
import pc_cdp_sold_refresh_win as pool_module
import pricecharting_cf_session as cf

URL = "https://www.pricecharting.com/"


class Context:
    def __init__(self, *, asynchronous=False, failure=None):
        self.pages = []
        self.asynchronous = asynchronous
        self.failure = failure
        self.peak = 0

    def make_page(self, url="about:blank"):
        kind = AsyncPage if self.asynchronous else Page
        page = kind(self, url)
        self.pages.append(page)
        self.peak = max(self.peak, len(self.pages))
        return page

    def new_page(self):
        page = self.make_page()
        if not self.asynchronous:
            return page

        async def result():
            return page

        return result()


class Page:
    def __init__(self, context, url):
        self.context = context
        self.url = url

    def set_default_timeout(self, timeout):
        pass

    def goto(self, url, **kwargs):
        if self.context.failure == "goto":
            raise TimeoutError("planted navigation timeout")
        self.url = url
        return SimpleNamespace(status=200)

    def content(self):
        if self.context.failure == "content":
            raise RuntimeError("planted detached page")
        return "VGPC" + "x" * 60000

    def title(self):
        return "PriceCharting"

    def close(self):
        self.context.pages.remove(self)


class AsyncPage(Page):
    async def goto(self, url, **kwargs):
        if self.context.failure == "cancel":
            raise asyncio.CancelledError()
        return super().goto(url, **kwargs)

    async def close(self):
        super().close()


def sync_engine(context):
    @contextlib.contextmanager
    def manager():
        yield SimpleNamespace(chromium=SimpleNamespace(
            connect_over_cdp=lambda *a, **k: SimpleNamespace(contexts=[context])))
    return manager


def async_engine(context):
    async def connect(*args, **kwargs):
        return SimpleNamespace(contexts=[context])

    @contextlib.asynccontextmanager
    async def manager():
        yield SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    return manager


class TabCleanupTests(unittest.TestCase):
    def test_identity_fetch_repeated_failures_release_only_own_tab(self):
        for failure in ("goto", "content", "save"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                context = Context(failure=failure)
                anchor = context.make_page(URL)
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(cf, "sync_playwright", sync_engine(context)))
                    stack.enter_context(patch.object(cdp_identity, "fetch_version", return_value={}))
                    stack.enter_context(patch.object(cdp_identity, "reject_reason", return_value=None))
                    stack.enter_context(patch.object(cdp_identity, "fetch_targets", return_value=[]))
                    stack.enter_context(patch.object(cf, "_wait_clear", return_value=True))
                    stack.enter_context(patch.object(cf, "_save_state", side_effect=RuntimeError("planted save failure")))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    for _ in range(6):
                        with self.assertRaises((TimeoutError, RuntimeError)):
                            cf._cmd_fetch_once(URL, Path(directory) / "page.html")
                self.assertEqual(context.pages, [anchor])
                self.assertEqual(context.peak, 2)

    def test_pool_setup_retries_do_not_accumulate_blank_tabs(self):
        context = Context(asynchronous=True, failure="goto")
        anchor = context.make_page(URL)
        with patch.object(pool_module, "async_playwright", async_engine(context)), \
             patch.object(cdp_identity, "require_session_ready"), \
             patch.object(pool_module, "ensure_cdp"):
            for _ in range(4):
                result = asyncio.run(pool_module.run_fetch_pool([], cdp_port=9333, tabs=2))
                self.assertIn("single_cdp_connect", result["sessionError"])
        self.assertEqual(context.pages, [anchor])
        self.assertEqual(context.peak, 2)

    def test_pool_setup_cancellation_closes_created_tab(self):
        context = Context(asynchronous=True, failure="cancel")
        anchor = context.make_page(URL)
        with patch.object(pool_module, "async_playwright", async_engine(context)), \
             patch.object(cdp_identity, "require_session_ready"), \
             patch.object(pool_module, "ensure_cdp"):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(pool_module.run_fetch_pool([], cdp_port=9333, tabs=2))
        self.assertEqual(context.pages, [anchor])

    def test_pool_worker_failure_and_cancellation_close_second_tab(self):
        for error in (RuntimeError("planted worker failure"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                context = Context(asynchronous=True)
                anchor = context.make_page(URL)

                async def worker(*args, **kwargs):
                    raise error

                with patch.object(pool_module, "async_playwright", async_engine(context)), \
                     patch.object(cdp_identity, "require_session_ready"), \
                     patch.object(pool_module, "run_fetch_pool_with_pages", worker):
                    with self.assertRaises(type(error)):
                        asyncio.run(pool_module.run_fetch_pool([], cdp_port=9333, tabs=2))
                self.assertEqual(context.pages, [anchor])
                self.assertEqual(context.peak, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
