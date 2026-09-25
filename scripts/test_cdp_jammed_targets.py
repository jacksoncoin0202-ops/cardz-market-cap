#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Version-only 200 is not a usable CDP session.

Plant a local HTTP server that answers /json/version as Windows headed Chrome
and hangs on /json/list. require_headed_windows must pass; require_session_ready
must raise jammed-targets. That is the 2026-08-22 9333 hang.

Run: python -X utf8 scripts/test_cdp_jammed_targets.py
"""
from __future__ import annotations

import sys
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from cdp_identity import (  # noqa: E402
    fetch_targets,
    fetch_version,
    reject_reason,
    require_headed_windows,
    require_session_ready,
)

WINDOWS_HEADED = {
    "Browser": "Chrome/151.0.7922.170",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    ),
}
VERSION_BODY = (
    b'{"Browser":"Chrome/151.0.7922.170","User-Agent":'
    b'"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    b'(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"}'
)


STOP = threading.Event()


class _ReadyServer(ThreadingHTTPServer):
    """Expose when serve_forever has entered its accept loop."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.ready = threading.Event()

    def service_actions(self) -> None:
        self.ready.set()
        super().service_actions()


class _JammedHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/json/version"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(VERSION_BODY)))
            self.end_headers()
            self.wfile.write(VERSION_BODY)
            return
        if self.path.startswith("/json/list"):
            STOP.wait(timeout=8)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        del format, args


def _local_interface_ip() -> str:
    """Address the planted server without WSL mirrored-loopback routing."""

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))
        return str(probe.getsockname()[0])


def _serve() -> tuple[ThreadingHTTPServer, threading.Thread, int, str]:
    host = _local_interface_ip()
    server = _ReadyServer((host, 0), _JammedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if not server.ready.wait(timeout=2.0):
        raise RuntimeError("planted CDP server did not enter its accept loop")
    return server, thread, int(server.server_address[1]), host


def main() -> int:
    failed = 0
    server, _thread, port, test_host = _serve()
    try:
        # WSL mirrored networking reserves explicit 127.0.0.1 for Windows-host
        # services (the real CDP contract), while localhost can alternate
        # between the Windows and WSL sides.  The fixture therefore uses its
        # WSL interface address; production's default host stays unchanged.
        version = fetch_version(port, timeout=1.0, host=test_host)
        if reject_reason(version) is not None:
            print(f"FAIL planted version should be accepted got={reject_reason(version)}")
            failed += 1
        else:
            print("PASS planted version accepted as Windows headed")
        require_headed_windows(port, host=test_host)
        print("PASS require_headed_windows accepts version-only 200")
        started = time.monotonic()
        targets = fetch_targets(port, timeout=1.0, host=test_host)
        elapsed = time.monotonic() - started
        if targets is not None:
            print(f"FAIL hung list returned {targets!r}")
            failed += 1
        elif elapsed > 5:
            print(f"FAIL hung list took {elapsed:.1f}s (must timeout ~1s)")
            failed += 1
        else:
            print(f"PASS hung list timed out in {elapsed:.2f}s")
        try:
            require_session_ready(port, host=test_host)
            print("FAIL require_session_ready accepted jammed session")
            failed += 1
        except RuntimeError as exc:
            if "jammed-targets" not in str(exc):
                print(f"FAIL expected jammed-targets got {exc}")
                failed += 1
            else:
                print("PASS planted jammed-targets fired")
        ps1 = (ROOT / "scripts" / "ensure_chrome_cdp.ps1").read_text(encoding="utf-8")
        if "jammed-targets" not in ps1 or "Get-CdpTargetList" not in ps1:
            print("FAIL ensure_chrome_cdp.ps1 missing list probe")
            failed += 1
        else:
            print("PASS ensure_chrome_cdp.ps1 recycles jammed-targets")
        # Prove the old version-only gate would have missed this plant.
        planted_missed_by_version_only = reject_reason(WINDOWS_HEADED) is None
        if not planted_missed_by_version_only:
            print("FAIL version-only fixtures drifted")
            failed += 1
        else:
            print("PASS version-only 200 is the exact false-OK this plant covers")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {type(exc).__name__}:{exc}")
        failed += 1
    finally:
        STOP.set()
        server.shutdown()
        server.server_close()
    print("FAILED" if failed else "PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
