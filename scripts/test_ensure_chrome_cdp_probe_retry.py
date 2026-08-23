"""ensure_chrome_cdp.ps1 must survive one transient /json/version miss.

A04 2026-08-23 08:10:16Z: Chrome 9333 was alive, one 2 s probe failed, the PC
lane reported CDP_9333_UNAVAILABLE and sat out a 300 s retry. This plants a
listener whose FIRST /json/version request stalls past the 2 s probe timeout
and whose later requests answer as Windows headed Chrome, then runs the script:

  -IdentityOnly                   -> CDP_IDENTITY_OK, exit 0   (retry fires)
  -IdentityOnly -ProbeAttempts 1  -> reason=no-listener, exit 1 (seeded bug)

Windows only (powershell.exe + curl.exe); elsewhere prints SKIP and exits 0.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "ensure_chrome_cdp.ps1"
VERSION = {
    "Browser": "Chrome/146.0.7680.75",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
}


def _serve(stall_first: bool) -> tuple[ThreadingHTTPServer, list[str]]:
    seen: list[str] = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # quiet
            return

        def _json(self, payload) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            with lock:
                seen.append(self.path)
                first_version = self.path == "/json/version" and seen.count("/json/version") == 1
            if self.path == "/json/version":
                if stall_first and first_version:
                    time.sleep(3.5)  # past the 2 s probe timeout; the client is gone
                try:
                    self._json(VERSION)
                except OSError:
                    pass
                return
            if self.path == "/json/list":
                port = self.server.server_address[1]
                self._json([{"id": "1", "type": "page", "url": "about:blank",
                             "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/1"}])
                return
            self.send_response(404)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def _run(port: int, extra: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(SCRIPT), "-Port", str(port), "-IdentityOnly", *extra],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main() -> int:
    if sys.platform != "win32":
        print("SKIP test_ensure_chrome_cdp_probe_retry: Windows-only (powershell.exe)")
        return 0
    failed = 0

    server, seen = _serve(stall_first=True)
    rc, out = _run(server.server_address[1], [])
    server.shutdown()
    ok = rc == 0 and "CDP_IDENTITY_OK" in out and seen.count("/json/version") >= 2
    print(("POSITIVE_OK" if ok else "POSITIVE_FAIL"), "retry-survives-one-stalled-probe",
          f"rc={rc} versionProbes={seen.count('/json/version')} out={out!r}")
    failed += 0 if ok else 1

    server, seen = _serve(stall_first=True)
    rc, out = _run(server.server_address[1], ["-ProbeAttempts", "1"])
    server.shutdown()
    ok = rc == 1 and "reason=no-listener" in out and seen.count("/json/version") == 1
    print(("NEGATIVE_OK" if ok else "NEGATIVE_FAIL"), "one-shot-probe-still-fails(seeded bug)",
          f"rc={rc} versionProbes={seen.count('/json/version')} out={out!r}")
    failed += 0 if ok else 1

    server, seen = _serve(stall_first=False)
    rc, out = _run(server.server_address[1], [])
    server.shutdown()
    ok = rc == 0 and "CDP_IDENTITY_OK" in out and seen.count("/json/version") == 1
    print(("POSITIVE_OK" if ok else "POSITIVE_FAIL"), "healthy-listener-takes-one-probe",
          f"rc={rc} versionProbes={seen.count('/json/version')}")
    failed += 0 if ok else 1

    print("EXIT", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
