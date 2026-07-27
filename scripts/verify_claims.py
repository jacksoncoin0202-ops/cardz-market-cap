#!/usr/bin/env python3
"""Re-measure every verification stamp embedded in the project docs.

The failure this exists to stop: an agent reads the authoritative document,
gets the path right, trusts a number that was measured days ago, and works on
the wrong problem.  Documents rot silently because nothing in them records
*when* a number was measured or *how* to measure it again.

A verification stamp fixes both.  It is an HTML comment that sits on the same
line as the claim it backs, so it is invisible when the markdown renders but
travels with the sentence when the sentence moves:

    | `market_fx_rate_observation` | 0 rows, never written |
    <!--@verified 2026-07-26 id=db.fx.rows expect=0
        sql=SELECT COUNT(*) FROM market_fx_rate_observation-->

Grammar (fields may appear in any order, `sql=` / `cmd=` must be last):

    <!--@verified <YYYY-MM-DD> id=<slug> expect<op><value> [ttl=<days>]
        sql=<single read-only statement>-->

    <op>     one of  =  >=  <=  >  <  !=
    ttl      days before the stamp is considered stale (default 3)
    cmd=     shell command instead of sql; only runs with --allow-cmd

The assertion compares <value> against the first column of the first row.

This script is READ-ONLY.  It never edits a document and never writes to the
database.  It reports; a human or agent decides what to re-stamp.

Exit codes: 0 = every stamp fresh and holding, 1 = drift or staleness found,
2 = could not verify (database unreachable, config missing).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "runtime" / "config" / "backend.env"

# Anything older than this must be re-measured before it may be cited as fact.
# Three days is not arbitrary: every documented drift in the 2026-07-26 audit
# (FX rows, quarantine counters, eBay coverage, review queue) was introduced by
# a daily pipeline run within 72 hours of the document being written.
DEFAULT_TTL_DAYS = 3

# Docs scanned when no explicit path is given.
DEFAULT_DOCS = ("CLAUDE.md", "PROJECT_STATE.md", "AGENTS.md", "README.md")
# docs/evidence/<date>-<slug>/FINDING.md sits one level deeper than docs/evidence/
# itself, and those findings are precisely the documents whose premises rot.
DEFAULT_DOC_GLOBS = ("docs/*.md", "docs/evidence/*.md", "docs/evidence/*/*.md")

STAMP_RE = re.compile(r"<!--\s*@verified\s+(?P<body>.*?)-->", re.DOTALL)
DATE_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<rest>.*)$", re.DOTALL)
FIELD_RE = re.compile(r"(?P<key>[a-z_]+)\s*(?P<op>>=|<=|!=|=|>|<)\s*(?P<value>\S+)")

READ_ONLY_PREFIXES = ("select", "show", "desc", "describe", "explain", "with")
# Belt and braces on top of the prefix check: these must not appear anywhere.
FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|replace|rename|grant|"
    r"revoke|commit|rollback|lock|call|handler|load_file|outfile|dumpfile)\b",
    re.IGNORECASE,
)
# TABLE_ROWS is a storage-engine estimate, not a count.  Measured 2026-07-26 on
# this database it reported catalog_variant at 1590 rows when COUNT(*) returned
# 1705 - a 7% understatement, and it has been seen far worse.  Estimates are
# never acceptable as the evidence behind a documented claim.
ESTIMATE_RE = re.compile(r"\btable_rows\b", re.IGNORECASE)

OK = "OK"
DRIFT = "DRIFT"
STALE = "STALE"
ERROR = "ERROR"
SKIPPED = "SKIPPED"


@dataclass
class Claim:
    doc: str
    line: int
    claim_id: str
    measured: date
    op: str
    expected: str
    ttl: int
    sql: str = ""
    cmd: str = ""
    context: str = ""
    parse_error: str = ""

    # filled in by verification
    status: str = ""
    actual: str = ""
    detail: str = ""
    age_days: int = field(default=0)


def read_env_file(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def db_config() -> dict:
    file_values = read_env_file(CONFIG_PATH)

    def pick(key: str, default: str = "") -> str:
        return os.environ.get(key) or file_values.get(key) or default

    return {
        "host": pick("CARDZ_DB_HOST", "127.0.0.1"),
        "port": int(pick("CARDZ_DB_PORT", "3308")),
        "database": pick("CARDZ_DB_NAME", "cardz_market_cap"),
        "user": pick("CARDZ_DB_USER", "cardz"),
        "password": pick("CARDZ_DB_PASSWORD"),
    }


def scrub(text: str, secret: str) -> str:
    """Never let a credential reach stdout, even inside a driver traceback."""
    if secret and secret in text:
        text = text.replace(secret, "***")
    return text


def strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"#[^\n]*", " ", sql)
    return sql


def check_read_only(sql: str) -> str:
    """Return an error message, or '' when the statement is safe to run."""
    bare = strip_sql_comments(sql).strip().rstrip(";").strip()
    if not bare:
        return "empty statement"
    if ";" in bare:
        return "multiple statements are not allowed"
    if not bare.lower().lstrip("( \n\t").startswith(READ_ONLY_PREFIXES):
        return f"not read-only (must start with one of {', '.join(READ_ONLY_PREFIXES)})"
    forbidden = FORBIDDEN_RE.search(bare)
    if forbidden:
        return f"forbidden keyword '{forbidden.group(1)}'"
    if ESTIMATE_RE.search(bare):
        return "information_schema.TABLE_ROWS is an estimate; use exact COUNT(*)"
    return ""


def parse_stamps(path: Path, repo_root: Path) -> list[Claim]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(repo_root).as_posix()
    claims: list[Claim] = []

    for match in STAMP_RE.finditer(text):
        line_no = text.count("\n", 0, match.start()) + 1
        body = match.group("body").strip()
        context = _context_for(text, match.start())

        def broken(reason: str) -> Claim:
            return Claim(
                doc=rel, line=line_no, claim_id="?", measured=date.min, op="=",
                expected="", ttl=DEFAULT_TTL_DAYS, context=context, parse_error=reason,
            )

        head = DATE_RE.match(body)
        if not head:
            claims.append(broken("stamp must start with a YYYY-MM-DD measurement date"))
            continue
        try:
            measured = datetime.strptime(head.group("date"), "%Y-%m-%d").date()
        except ValueError:
            claims.append(broken(f"bad date '{head.group('date')}'"))
            continue

        rest = head.group("rest")
        query, kind = "", ""
        for marker in ("sql=", "cmd="):
            idx = rest.find(marker)
            if idx != -1:
                query = rest[idx + len(marker):].strip()
                kind = marker[:3]
                rest = rest[:idx]
                break

        fields: dict[str, tuple[str, str]] = {}
        for fmatch in FIELD_RE.finditer(rest):
            fields[fmatch.group("key")] = (fmatch.group("op"), fmatch.group("value"))

        if "id" not in fields:
            claims.append(broken("stamp is missing id=<slug>"))
            continue
        if "expect" not in fields:
            claims.append(broken("stamp is missing expect<op><value>"))
            continue
        if not query:
            claims.append(broken("stamp is missing sql=<statement> or cmd=<command>"))
            continue

        op, expected = fields["expect"]
        ttl = DEFAULT_TTL_DAYS
        if "ttl" in fields:
            try:
                ttl = int(fields["ttl"][1])
            except ValueError:
                claims.append(broken(f"bad ttl '{fields['ttl'][1]}'"))
                continue

        claims.append(
            Claim(
                doc=rel,
                line=line_no,
                claim_id=fields["id"][1],
                measured=measured,
                op=op,
                expected=expected,
                ttl=ttl,
                sql=" ".join(query.split()) if kind == "sql" else "",
                cmd=query if kind == "cmd" else "",
                context=context,
            )
        )
    return claims


def _context_for(text: str, offset: int) -> str:
    """The prose the stamp backs: what a reader would actually take as fact.

    Normally that is the text preceding the stamp on the same line.  When the
    stamp sits on its own line, walk back to the nearest non-empty line that is
    not itself part of a stamp.
    """
    start = text.rfind("\n", 0, offset) + 1
    line = text[start:offset].strip()
    if line:
        return re.sub(r"\s+", " ", line)[:110]

    for candidate in reversed(text[:start].splitlines()):
        candidate = candidate.strip()
        if not candidate or candidate.startswith("<!--") or candidate.endswith("-->"):
            continue
        return re.sub(r"\s+", " ", candidate)[:110]
    return ""


def coerce(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def compare(actual, op: str, expected_raw: str) -> bool:
    expected = coerce(expected_raw)
    if actual is None:
        return op == "=" and expected_raw.lower() in ("null", "none")
    # Only compare like with like; a numeric expectation against a text result
    # is a stamp bug, not a passing claim.
    if isinstance(actual, (int, float)) != isinstance(expected, (int, float)):
        actual, expected = str(actual), str(expected)
    if op == "=":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == ">":
        return actual > expected
    if op == "<":
        return actual < expected
    if op == ">=":
        return actual >= expected
    if op == "<=":
        return actual <= expected
    return False


class Runner:
    def __init__(self, allow_cmd: bool) -> None:
        self.allow_cmd = allow_cmd
        self._conn = None
        self._password = ""
        self.db_error = ""

    def connect(self) -> bool:
        if self._conn is not None:
            return True
        if self.db_error:
            return False
        config = db_config()
        self._password = config.get("password", "")
        try:
            import pymysql
            from pymysql.cursors import Cursor

            self._conn = pymysql.connect(charset="utf8mb4", cursorclass=Cursor, **config)
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            self.db_error = scrub(f"{type(exc).__name__}: {exc}", self._password)
            return False
        return True

    def run_sql(self, sql: str):
        guard = check_read_only(sql)
        if guard:
            raise ValueError(guard)
        if not self.connect():
            raise RuntimeError(self.db_error or "database unavailable")
        cur = self._conn.cursor()
        try:
            cur.execute(sql)
            row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(scrub(f"{type(exc).__name__}: {exc}", self._password)) from None
        finally:
            cur.close()
        if row is None:
            return None
        return row[0]

    def run_cmd(self, cmd: str):
        proc = subprocess.run(
            cmd, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=180
        )
        if proc.returncode != 0:
            raise RuntimeError(f"exit {proc.returncode}: {proc.stderr.strip()[:200]}")
        return proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def verify(claims: list[Claim], runner: Runner, today: date) -> list[Claim]:
    for claim in claims:
        if claim.parse_error:
            claim.status = ERROR
            claim.detail = claim.parse_error
            continue

        claim.age_days = (today - claim.measured).days

        try:
            if claim.cmd:
                if not runner.allow_cmd:
                    claim.status = SKIPPED
                    claim.detail = "cmd= stamp; re-run with --allow-cmd"
                    continue
                actual = runner.run_cmd(claim.cmd)
            else:
                actual = runner.run_sql(claim.sql)
        except Exception as exc:  # noqa: BLE001
            claim.status = ERROR
            claim.detail = str(exc)
            claim.actual = "?"
            continue

        claim.actual = "NULL" if actual is None else str(actual)
        holds = compare(coerce(actual), claim.op, claim.expected)

        if not holds:
            claim.status = DRIFT
            claim.detail = f"doc says {claim.op}{claim.expected}, measured {claim.actual}"
        elif claim.age_days > claim.ttl:
            claim.status = STALE
            claim.detail = (
                f"still true, but measured {claim.age_days}d ago (ttl {claim.ttl}d) "
                f"- re-stamp the date"
            )
        else:
            claim.status = OK
            claim.detail = f"{claim.actual} (measured {claim.age_days}d ago)"
    return claims


def render_report(claims: list[Claim], today: date, runner: Runner) -> str:
    order = {DRIFT: 0, ERROR: 1, STALE: 2, SKIPPED: 3, OK: 4}
    claims = sorted(claims, key=lambda c: (order.get(c.status, 9), c.doc, c.line))
    counts = {status: sum(1 for c in claims if c.status == status) for status in
              (DRIFT, ERROR, STALE, SKIPPED, OK)}

    out: list[str] = []
    out.append(f"# Claim verification - {today.isoformat()}")
    out.append("")
    out.append(
        f"{len(claims)} stamps checked | "
        f"{counts[DRIFT]} DRIFT | {counts[ERROR]} ERROR | {counts[STALE]} STALE | "
        f"{counts[SKIPPED]} SKIPPED | {counts[OK]} OK"
    )
    if runner.db_error:
        out.append("")
        out.append(f"! database unreachable: {runner.db_error}")
    out.append("")

    if not claims:
        out.append("No @verified stamps found. Docs are entirely unverifiable.")
        return "\n".join(out)

    for status in (DRIFT, ERROR, STALE, SKIPPED, OK):
        group = [c for c in claims if c.status == status]
        if not group:
            continue
        out.append(f"## {status} ({len(group)})")
        out.append("")
        for claim in group:
            out.append(f"- **{claim.claim_id}** - {claim.doc}:{claim.line}")
            if claim.context:
                out.append(f"  - doc says: {claim.context}")
            out.append(f"  - {claim.detail}")
            if claim.sql:
                out.append(f"  - `{claim.sql}`")
            elif claim.cmd:
                out.append(f"  - `$ {claim.cmd}`")
        out.append("")
    return "\n".join(out)


def collect_docs(explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(p) if Path(p).is_absolute() else ROOT / p for p in explicit]
    paths: list[Path] = [ROOT / name for name in DEFAULT_DOCS]
    for pattern in DEFAULT_DOC_GLOBS:
        paths.extend(sorted(ROOT.glob(pattern)))
    seen, unique = set(), []
    for path in paths:
        if path.exists() and path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("docs", nargs="*", help="markdown files to scan (default: project docs)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--out", help="also write the markdown report to this path")
    parser.add_argument("--allow-cmd", action="store_true",
                        help="execute cmd= stamps (shell); off by default")
    parser.add_argument("--list", action="store_true",
                        help="list stamps without running anything")
    args = parser.parse_args()

    docs = collect_docs(args.docs)
    claims: list[Claim] = []
    for path in docs:
        claims.extend(parse_stamps(path, ROOT))

    if args.list:
        for claim in claims:
            marker = f" [{claim.parse_error}]" if claim.parse_error else ""
            print(f"{claim.doc}:{claim.line}  {claim.claim_id}  "
                  f"expect{claim.op}{claim.expected}  measured {claim.measured}{marker}")
        print(f"\n{len(claims)} stamps across {len(docs)} docs")
        return 0

    today = date.today()
    runner = Runner(allow_cmd=args.allow_cmd)
    try:
        verify(claims, runner, today)
    finally:
        runner.close()

    if args.json:
        payload = {
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "docsScanned": [p.relative_to(ROOT).as_posix() for p in docs],
            "defaultTtlDays": DEFAULT_TTL_DAYS,
            "dbError": runner.db_error,
            "claims": [
                {
                    "id": c.claim_id, "doc": c.doc, "line": c.line, "status": c.status,
                    "measured": c.measured.isoformat() if c.measured != date.min else None,
                    "ageDays": c.age_days, "ttlDays": c.ttl,
                    "expect": f"{c.op}{c.expected}", "actual": c.actual,
                    "detail": c.detail, "sql": c.sql, "cmd": c.cmd,
                }
                for c in claims
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        report = render_report(claims, today, runner)
        print(report)
        if args.out:
            out_path = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(report + "\n", encoding="utf-8")
            print(f"\nreport written to {out_path.relative_to(ROOT).as_posix()}")

    if runner.db_error and any(c.status == ERROR for c in claims):
        return 2
    if any(c.status in (DRIFT, ERROR, STALE) for c in claims):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
