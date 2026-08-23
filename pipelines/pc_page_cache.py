#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One read + one parse per local PriceCharting page, shared by every pass.

A04 2026-08-23, profiled read-only on WSL over the live tree (1238 map pages,
718 MB on /mnt/c; 3638 pages under the history root): the PC lane read and
parsed the same local pages four times per run --
``partition_local_pc_stock_pages`` -> ``validate_pc_psa10`` (20 s),
``c11_pc_sold_ingest.collect_sales`` (inside the 39.5 s ingest),
``pc_psa10_price_derivation.derive`` (20 s, ``validate_pc_psa10`` again) and
``pc_psa10_price_materialize.collect_local_history`` (24.7 s, every page under
the root).  ``parse_product_html`` costs 9.2 ms/page; a 9p read ~5 ms; a stat
~2.4 ms.

A page is keyed by its absolute path and validated by (size, mtime_ns, parser
fingerprint).  The row stores the sha256 of the bytes that were parsed, the
canonical URL and the parsed document (zlib JSON) in one sqlite file on the
WSL side (ext4), so a hit costs one stat plus one local sqlite read.  Evidence
is unchanged: ``sha256`` is the hash of exactly the bytes whose parse is
returned; any byte change moves size or mtime and misses; any parser edit
changes the fingerprint and misses everything once.

``CARDZ_PC_PAGE_CACHE`` overrides the file (tests) or disables the cache with
``off`` (every call reads and parses, still through this module).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
import stat as stat_mod
import sys
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pc_ungraded_reference_ingest import canonical_url_from_html  # noqa: E402
from pricecharting_page_parse import parse_product_html  # noqa: E402

_PARSER_FILES = (
    Path(__file__).resolve().parent / "pricecharting_page_parse.py",
)


def _parser_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in _PARSER_FILES:
        digest.update(path.read_bytes())
    digest.update(inspect.getsource(canonical_url_from_html).encode("utf-8"))
    return digest.hexdigest()[:16]


PARSER_FINGERPRINT = _parser_fingerprint()
SCHEMA = """
CREATE TABLE IF NOT EXISTS page (
  path TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  parser TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  canonical_url TEXT,
  parsed BLOB NOT NULL
)
"""


@dataclass
class CachedPage:
    path: Path
    size: int
    mtime_ns: int
    sha256: str
    canonical_url: str | None
    parsed: dict[str, Any]
    hit: bool


def cache_path() -> Path | None:
    """None disables the cache (``CARDZ_PC_PAGE_CACHE=off``)."""
    raw = os.environ.get("CARDZ_PC_PAGE_CACHE", "").strip()
    if raw.casefold() in {"off", "0", "none", "disable", "disabled"}:
        return None
    if raw:
        return Path(raw)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "cardz-marketcap"
    else:
        base = Path.home() / ".local" / "state" / "cardz-marketcap"
    return base / "pc_page_parse_cache.sqlite3"


_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_CONN_PATH: Path | None = None
STATS = {"hit": 0, "miss": 0, "bypass": 0}


def reset() -> None:
    """Close the connection (tests switch the path between cases)."""
    global _CONN, _CONN_PATH
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
        _CONN = None
        _CONN_PATH = None
        STATS.update(hit=0, miss=0, bypass=0)


def _connection() -> sqlite3.Connection | None:
    global _CONN, _CONN_PATH
    path = cache_path()
    if path is None:
        return None
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    if _CONN is not None:
        _CONN.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(SCHEMA)
    conn.commit()
    _CONN, _CONN_PATH = conn, path
    return conn


def _parse(html_bytes: bytes) -> tuple[str | None, dict[str, Any]]:
    html = html_bytes.decode("utf-8", errors="replace")
    return canonical_url_from_html(html), parse_product_html(html, source_url=None)


def _with_source(parsed: dict[str, Any], source_url: str | None) -> dict[str, Any]:
    out = dict(parsed)
    out["source_url"] = source_url
    return out


def load_page(path: Path, *, source_url: str | None = None) -> CachedPage | None:
    """Return the page's sha256 / canonical URL / parse, or None when ``path``
    is not a regular file.  Never raises for a missing or unreadable file."""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None
    if not stat_mod.S_ISREG(st.st_mode):
        return None
    # A10 2026-08-23 [KNOWN, measured in WSL on the live tree]: Path.resolve()
    # walks every component with an lstat and costs 11.2 ms per call on
    # /mnt/c; the PC lane asks for ~4,548 pages per run, so the key alone was
    # ~50 s of a 100 s lane.  os.path.abspath is pure string work (0.00 ms) and
    # yields the identical key for every live page (A/B over the 1,098 map
    # pages: 0 misses).  Content identity is still (size, mtime_ns, parser).
    key = os.path.abspath(path)
    size, mtime_ns = int(st.st_size), int(st.st_mtime_ns)

    with _LOCK:
        conn = _connection()
        if conn is not None:
            row = conn.execute(
                "SELECT size, mtime_ns, parser, sha256, canonical_url, parsed FROM page WHERE path=?",
                (key,),
            ).fetchone()
            if row is not None and (int(row[0]), int(row[1]), str(row[2])) == (size, mtime_ns, PARSER_FINGERPRINT):
                parsed = json.loads(zlib.decompress(row[5]).decode("utf-8"))
                STATS["hit"] += 1
                return CachedPage(path, size, mtime_ns, str(row[3]), row[4], _with_source(parsed, source_url), True)

    try:
        html_bytes = path.read_bytes()
    except OSError:
        return None
    sha256 = hashlib.sha256(html_bytes).hexdigest()
    canonical_url, parsed = _parse(html_bytes)

    with _LOCK:
        conn = _connection()
        if conn is None:
            STATS["bypass"] += 1
        else:
            try:
                blob = zlib.compress(json.dumps(parsed, separators=(",", ":")).encode("utf-8"), 6)
            except (TypeError, ValueError):
                blob = None  # not JSON-representable: serve it, do not cache it
            if blob is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO page (path,size,mtime_ns,parser,sha256,canonical_url,parsed)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (key, size, mtime_ns, PARSER_FINGERPRINT, sha256, canonical_url, blob),
                )
                conn.commit()
            STATS["miss"] += 1
    return CachedPage(path, size, mtime_ns, sha256, canonical_url, _with_source(parsed, source_url), False)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="PriceCharting page parse cache status")
    parser.add_argument("--stats", action="store_true")
    parser.parse_args()
    path = cache_path()
    print(json.dumps({"cache": None if path is None else str(path), "parser": PARSER_FINGERPRINT,
                      "rows": 0 if path is None or not path.is_file() else
                      _connection().execute("SELECT COUNT(*) FROM page").fetchone()[0]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
