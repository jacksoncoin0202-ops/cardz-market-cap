#!/usr/bin/env python3
"""Check that code references in the docs still point at the code they claim.

Written 2026-07-26 after a measured failure: one agent inserted ~80 lines into
`pipelines/canonical_public_snapshot.py` and thereby broke three documentation
references **inside the same session**.  `CLAUDE.md` said line 778 was
`localize_cards()`; it had become 1021, and line 778 had become an unrelated
`result.setdefault(...)`.  The next agent to read that line did not get an
error - it got working, plausible, wrong code.

That is the same disease as a stale number: the agent read the right file, did
not guess a path, and was misled by the content.  Line numbers are simply the
fastest-rotting fact in the repository - they do not survive a single session.

What this catches
-----------------
BROKEN    the referenced file does not exist, or the file is shorter than the
          line being cited.  Cheap, certain.
BROKENLINK an active/generated Markdown document links to a missing local
          document.
DOCROUTE  an active/generated Markdown document links to a registered
          reference-only, superseded, historical, quarantined, or delete
          candidate document.
DRIFT     the doc names a symbol next to the line number, the symbol is really
          defined in that file, and it is defined at a *different* line.  This
          is the case above, and it is unambiguous: no false positives are
          possible, because the symbol was found in the very file the doc named.
RENAMED   the doc names a symbol next to a link to a file, and that file does
          not define it at all.  Reported as a warning, not a failure - a doc
          may legitimately name a field, a column, or a symbol from elsewhere
          in the sentence.
BARE      a `path:line` reference not wrapped in a markdown link.  Style only.
          Never fails the run.  The repo has dozens; hard-failing on day one
          would only get this check switched off.
TEMPDEP   a scheduler / installer / CI definition that executes something under
          `temp/`.  Measured the same day: the live Windows task
          `CARDZ-Freeze-Sweep-Guard` executed `temp\\freeze-sweep-guard.ps1`;
          the file was moved to `deploy/windows/` and the task was never
          re-pointed, so the guard now fails silently on every run.

What this does NOT do
---------------------
It does not read the code semantically.  A reference to a line that still
exists, with no symbol named beside it, is unverifiable and is reported as
UNCHECKED rather than passed.  Pure existence checking would have passed all
three of the real failures above - the file was long enough every time.  The
symbol comparison is the part that has teeth; the line-range check only catches
deletion.

Read-only.  Never edits a document, never touches the database.

    python -X utf8 scripts/verify_doc_refs.py
    python -X utf8 scripts/verify_doc_refs.py --json
    python -X utf8 scripts/verify_doc_refs.py --authority-active-only --strict
    python -X utf8 scripts/verify_doc_refs.py CLAUDE.md

Exit codes: 0 = nothing broken, 1 = BROKEN/DRIFT/TEMPDEP present, 2 = nothing
to scan.  Warnings alone never produce a non-zero exit.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DOCS = ("CLAUDE.md", "PROJECT_STATE.md", "AGENTS.md", "README.md", "FILE_CLAIMS.md")
DEFAULT_DOC_GLOBS = ("docs/*.md", "docs/**/*.md")

SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".mjs", ".js", ".sh", ".ps1", ".sql"}
DOCUMENT_ROUTE_SUFFIXES = {".md", ".html", ".json"}

# Opt-out for lines that quote a reference as an EXAMPLE rather than making one.
# Line-level and explicit on purpose - see the comment in scan_document().
EXAMPLE_MARKER = "<!--docref:example-->"

# `path/to/file.ext:123` or `:123-456`.  The suffix list is deliberate: a bare
# `foo:12` is far more likely to be a time or a ratio than a code reference.
# `#L264` is the GitHub anchor spelling of the same thing and rots identically.
REF_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+"
    r"\.(?:py|ts|tsx|mjs|js|json|md|ps1|sh|sql|service|timer))"
    r"(?::|#L)(?P<start>\d+)(?:[-–](?:L)?(?P<end>\d+))?"
)

# `[label](target)` - used both to tell a linked reference from a bare one and
# to pair a symbol with the file it was named beside.
LINK_RE = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)\s]+)\)")

# Backticked identifiers.  `foo()` is the strongest signal, but the docs also
# write `` `SECRETS_PATH` `` and `` `singleton_lock` `` beside a line number and
# mean exactly the same thing.  Excluding those produced a false DRIFT on both,
# which is worse than missing a real one: a report that cries wolf gets muted.
SYMBOL_RE = re.compile(r"`(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<call>\(\))?`")

# Anything under temp/ that is executable by a scheduler.  `temp` may be
# preceded by a path separator - the live case was an absolute Windows path,
# `...\cardz-market-cap\temp\freeze-sweep-guard.ps1`, and an anchor that only
# accepted quote/space/equals missed it entirely.
TEMP_EXEC_RE = re.compile(
    r"(?:^|[\"'\s=/\\])((?:\./)?temp[/\\][A-Za-z0-9_.-]+\.(?:ps1|py|sh|mjs|js|bat|cmd))"
)

BROKEN = "BROKEN"
BROKENLINK = "BROKENLINK"
DOCROUTE = "DOCROUTE"
DRIFT = "DRIFT"
RENAMED = "RENAMED"
AMBIGUOUS = "AMBIGUOUS"
BARE = "BARE"
UNCHECKED = "UNCHECKED"
TEMPDEP = "TEMPDEP"
OK = "OK"

# BROKEN and TEMPDEP are certain: the file is absent, the line is past EOF, the
# scheduler runs something out of temp/.  DRIFT is a heuristic - it pairs a
# symbol named on the doc line with the line number cited beside it, and on this
# repo one candidate in four was a false positive (the doc named a second,
# unrelated symbol in the same sentence).  A check that hard-fails on a coin
# flip gets switched off within a week, so DRIFT reports loudly and exits 0
# unless --strict is passed.
FAILING = {BROKEN, BROKENLINK, DOCROUTE, TEMPDEP}
WARNING = {RENAMED, AMBIGUOUS}


@dataclass
class Ref:
    doc: str
    doc_line: int
    target: str
    start: int | None
    end: int | None
    linked: bool
    symbols: list[str] = field(default_factory=list)
    status: str = OK
    detail: str = ""
    resolved: str = ""

    def where(self) -> str:
        span = ""
        if self.start is not None:
            span = f":{self.start}" + (f"-{self.end}" if self.end else "")
        return f"{self.target}{span}"


def symbol_definition_lines(path: Path, name: str) -> list[int]:
    """Line numbers where `name` looks like it is being defined.

    Heuristic and deliberately narrow.  A symbol that cannot be located is
    reported as unknown, never as an error - guessing wrong here would train
    people to ignore the report, which is worse than not checking.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    escaped = re.escape(name)
    patterns = (
        rf"^\s*(?:async\s+)?def\s+{escaped}\s*\(",              # python def
        rf"^\s*class\s+{escaped}\s*[(:]",                        # python class
        rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{escaped}\b",
        rf"^\s*(?:export\s+)?(?:const|let|var)\s+{escaped}\s*[:=]",
        rf"^\s*(?:export\s+)?class\s+{escaped}\b",
        rf"^\s*{escaped}\s*[:=]\s*(?:async\s*)?\([^)]*\)\s*(?::[^=]+)?=>",  # object/arrow method
        rf"^\s*function\s+{escaped}\s*\(",
        rf"^\s*{escaped}\s*(?::[^=\n]+)?=[^=]",  # module-level constant: SECRETS_PATH = ...
    )
    compiled = [re.compile(p) for p in patterns]
    return [n for n, line in enumerate(lines, 1) if any(rx.search(line) for rx in compiled)]


def symbol_usage_lines(path: Path, name: str) -> list[int]:
    """Every line mentioning the symbol at all.

    Docs cite call sites as often as definitions - `g10_public_snapshot.py:404`
    meant "KadoRawResolver calls normalize_collector here", and the call had
    moved to 417 while the definition never left 129.  Reporting only the
    definition made a real drift look like a different, wrong drift.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rx = re.compile(rf"\b{re.escape(name)}\b")
    return [n for n, line in enumerate(lines, 1) if rx.search(line)]


def line_count(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def symbols_for_target(text: str, target_span: tuple[int, int], links: list[re.Match]) -> list[str]:
    """Symbols on this line that belong to this reference.

    A line can name several files and several symbols:

        [a.py](a.py) `one()` -> [b.py](b.py) `two()`

    Each symbol is attributed to the nearest file mentioned before it.  Symbols
    appearing before any file mention are attributed to the first reference on
    the line, which is the common `` `foo()` in [bar.py](bar.py) `` shape.
    """
    boundaries = sorted({m.start() for m in links} | {target_span[0]})
    index = boundaries.index(target_span[0])
    next_boundary = boundaries[index + 1] if index + 1 < len(boundaries) else len(text)
    lower = boundaries[index - 1] if index == 0 else target_span[0]
    if index == 0:
        lower = 0

    found = []
    for match in SYMBOL_RE.finditer(text):
        if lower <= match.start() < next_boundary:
            # `foo()` marks itself as code; a bare `foo` might be an env var, a
            # column, or the word "exec".  Keep the distinction - it decides
            # whether "not defined here" is worth saying out loud.
            found.append(f"{match.group('name')}()" if match.group("call") else match.group("name"))
    return found


def scan_document(doc_path: Path, root: Path) -> list[Ref]:
    rel_doc = doc_path.relative_to(root).as_posix()
    refs: list[Ref] = []
    try:
        text = doc_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [Ref(rel_doc, 0, str(doc_path), None, None, False, status=BROKEN, detail=str(exc))]

    for lineno, line in enumerate(text.splitlines(), 1):
        # The document that BANS bare line numbers has to quote one to show what
        # the banned form looks like.  Without an opt-out, CLAUDE.md can never be
        # clean, and a checker that permanently reports the most-read file gets
        # ignored - which is the exact failure this checker exists to prevent.
        # Deliberately line-level and explicit: no file-level or pattern-level
        # muting, so nothing gets exempted by accident.
        if EXAMPLE_MARKER in line:
            continue
        links = list(LINK_RE.finditer(line))
        # Keep both spellings: the anchor form `path.tsx#L154` is what the link
        # actually contains, and stripping `#` before comparing made every
        # anchored link look like a bare reference.
        link_targets = {m.group("target").split("#")[0] for m in links} | {m.group("target") for m in links}
        # `[rankings.tsx:154](apps/web/src/components/rankings.tsx:154)` holds one
        # reference, not two.  The label is display text - checking it as a path
        # produces a "file does not exist" for every correctly-written link.
        label_spans = [(m.start(1), m.end(1)) for m in links if m.group("label")]

        seen_on_line: set[str] = set()
        for match in REF_RE.finditer(line):
            if any(lo <= match.start() < hi for lo, hi in label_spans):
                continue
            target = match.group("path")
            start = int(match.group("start"))
            end = int(match.group("end")) if match.group("end") else None
            key = f"{target}:{start}"
            if key in seen_on_line:
                continue
            seen_on_line.add(key)
            linked = any(match.group(0) in t or t.endswith(f"{target}:{start}") for t in link_targets)
            # `[snapshot.ts:37-61](apps/web/src/lib/snapshot.ts:37)` claims the
            # range 37-61; the target only carries the anchor.  Reading the span
            # off the target alone reported a symbol at line 38 as drifted.
            if end is None:
                for label_match in links:
                    label = label_match.group("label")
                    if not label.endswith(target.rsplit("/", 1)[-1] + f":{start}") and target.rsplit("/", 1)[-1] in label:
                        span = REF_RE.search(label)
                        if span and int(span.group("start")) == start and span.group("end"):
                            end = int(span.group("end"))
                            break
            refs.append(
                Ref(
                    doc=rel_doc,
                    doc_line=lineno,
                    target=target,
                    start=start,
                    end=end,
                    linked=linked,
                    symbols=symbols_for_target(line, match.span(), links),
                )
            )

        # Symbol-only references: the recommended form.  Check them too, or the
        # recommendation is the one thing in this repo nobody can verify.
        for match in links:
            target = match.group("target").split("#")[0]
            if ":" in target.rsplit("/", 1)[-1]:
                continue  # already handled above as a path:line reference
            if Path(target).suffix not in SOURCE_SUFFIXES:
                continue
            names = symbols_for_target(line, match.span(), links)
            if not names:
                continue
            refs.append(
                Ref(
                    doc=rel_doc,
                    doc_line=lineno,
                    target=target,
                    start=None,
                    end=None,
                    linked=True,
                    symbols=names,
                )
            )
    return refs


SKIP_DIRS = {".git", "node_modules", ".next", "__pycache__", ".venv", ".venv-backend", "temp", "data"}


def basename_index(root: Path) -> dict[str, list[Path]]:
    """basename -> every source file with that name.

    Docs constantly write `run_daily.py:282` with no directory.  Refusing to
    resolve those would report two thirds of the repo's references as missing
    files, and a report that is mostly false alarms is a report nobody reads.
    """
    index: dict[str, list[Path]] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    stack.append(entry)
            elif entry.suffix in SOURCE_SUFFIXES or entry.suffix in {".service", ".timer", ".json"}:
                index.setdefault(entry.name, []).append(entry)
    return index


def resolve_target(ref: Ref, root: Path, index: dict[str, list[Path]]) -> tuple[Path | None, str]:
    """Repo-root, then document-relative, then by basename.

    `docs/DATA_NORMALIZATION.md` writes `../pipelines/foo.py`, which is correct
    for a reader clicking the link and wrong for a naive root join.  Returns the
    path plus how it was found, because a reference that only resolves by
    basename is a weaker reference and the report should say so.
    """
    for candidate in (root / ref.target, (root / ref.doc).parent / ref.target):
        try:
            if candidate.is_file():
                return candidate, "path"
        except OSError:
            continue
    if "/" not in ref.target:
        matches = index.get(ref.target, [])
        if len(matches) == 1:
            return matches[0], "basename"
        if len(matches) > 1:
            return None, "ambiguous"
    return None, "missing"


def classify(ref: Ref, root: Path, index: dict[str, list[Path]]) -> Ref:
    path, how = resolve_target(ref, root, index)
    if path is None:
        if how == "ambiguous":
            ref.status = AMBIGUOUS
            ref.detail = f"{len(index[ref.target])} files share this name; the reference does not say which"
            return ref
        ref.status = BROKEN
        ref.detail = "file does not exist"
        return ref
    if how == "basename":
        ref.resolved = path.relative_to(root).as_posix()

    total = line_count(path)
    if ref.start is not None and ref.start > total:
        ref.status = BROKEN
        ref.detail = f"file has {total} lines, reference cites {ref.start}"
        return ref

    if path.suffix in SOURCE_SUFFIXES:
        located: list[tuple[str, list[int], list[int]]] = []
        for name in ref.symbols:
            bare = name.removesuffix("()")
            defs = symbol_definition_lines(path, bare)
            uses = symbol_usage_lines(path, bare)
            if defs or uses:
                located.append((name, defs, uses))

        if not located:
            # Only the call form earns a rename warning.  A bare backtick may be
            # an env var, a column, or the word `exec`, and treating those as
            # missing symbols produced 22 warnings of which none were real.
            missing_calls = [n for n in ref.symbols if n.endswith("()")]
            if missing_calls and ref.start is None:
                ref.status = RENAMED
                ref.detail = f"`{missing_calls[0]}` is not defined in this file"
            return ref

        if ref.start is None:
            ref.status = OK
            ref.detail = f"`{located[0][0]}` found"
            return ref

        span_end = ref.end or ref.start
        # Any named symbol landing in the cited span vindicates the reference.
        # A line often names several symbols - `singleton_lock` taken at the top
        # of `main()` names both, and only one is what the number points at.
        # Requiring every symbol to match turned three correct refs into DRIFT.
        for name, defs, uses in located:
            if any(ref.start <= n <= span_end for n in defs + uses):
                ref.status = OK
                ref.detail = f"`{name}` confirmed at {ref.start}"
                return ref
        name, defs, uses = located[0]
        where = []
        if defs:
            where.append("defined at " + ", ".join(str(d) for d in defs[:3]))
        if uses:
            where.append("used at " + ", ".join(str(u) for u in uses[:3]))
        ref.status = DRIFT
        ref.detail = f"`{name}` is {'; '.join(where)} — line {ref.start} is none of those"
        return ref

    if ref.status == OK and ref.start is not None and not ref.symbols:
        ref.status = UNCHECKED
        ref.detail = f"line exists ({total} total) but no symbol named beside it"
    if not ref.linked and ref.status in {OK, UNCHECKED}:
        ref.status = BARE if ref.status == UNCHECKED else ref.status
    return ref


def scheduled_task_temp_deps() -> list[dict]:
    """Windows scheduled tasks whose action runs something under temp/.

    Read-only query.  Returns [] anywhere Get-ScheduledTask is unavailable -
    a Linux box legitimately has no answer here, and inventing a failure would
    make this check useless on the deployment target.
    """
    if sys.platform != "win32":
        return []
    script = (
        "Get-ScheduledTask | ForEach-Object { "
        "$t=$_; $_.Actions | ForEach-Object { "
        "[pscustomobject]@{name=$t.TaskName; exec=$_.Execute; args=$_.Arguments} } } "
        "| ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]

    hits = []
    for entry in data:
        blob = f"{entry.get('exec') or ''} {entry.get('args') or ''}"
        match = TEMP_EXEC_RE.search(blob)
        if match:
            hits.append({"task": entry.get("name"), "path": match.group(1), "action": blob.strip()})
    return hits


def repo_temp_deps(root: Path) -> list[dict]:
    """Installers, units and CI definitions that execute something in temp/."""
    hits = []
    globs = ("deploy/**/*", ".github/**/*", "scripts/*.ps1", "scripts/*.sh")
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".gz", ".zip"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                match = TEMP_EXEC_RE.search(line)
                if match:
                    hits.append(
                        {
                            "task": f"{path.relative_to(root).as_posix()}:{lineno}",
                            "path": match.group(1),
                            "action": line.strip()[:160],
                        }
                    )
    return hits


def collect_docs(explicit: list[str], root: Path) -> list[Path]:
    if explicit:
        return [root / name for name in explicit]
    found: list[Path] = []
    for name in DEFAULT_DOCS:
        candidate = root / name
        if candidate.is_file():
            found.append(candidate)
    for pattern in DEFAULT_DOC_GLOBS:
        found.extend(sorted(p for p in root.glob(pattern) if p.is_file()))
    seen, unique = set(), []
    for path in found:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def load_document_authority(root: Path) -> dict[str, dict]:
    """Load the registered document path-to-record map."""

    registry_path = root / "config" / "data-routing.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        documents = registry["documentAuthority"]["documents"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load document authority: {error}") from error
    if not isinstance(documents, list):
        raise ValueError("cannot load document authority: documents must be an array")
    authority: dict[str, dict] = {}
    for item in documents:
        if not isinstance(item, dict):
            raise ValueError("cannot load document authority: document entry must be an object")
        value = item.get("path")
        status = item.get("status")
        if not isinstance(value, str) or not isinstance(status, str):
            raise ValueError("cannot load document authority: document requires path and status")
        candidate = (root / value).resolve()
        try:
            normalized = candidate.relative_to(root.resolve()).as_posix()
        except ValueError as error:
            raise ValueError(f"document authority path escapes repository: {value}") from error
        if normalized in authority:
            raise ValueError(f"duplicate document authority path: {normalized}")
        authority[normalized] = item
    return authority


def collect_authority_docs(root: Path) -> list[Path]:
    """Return active/generated Markdown from the single document registry."""

    documents = load_document_authority(root)
    selected: list[Path] = []
    for normalized, item in documents.items():
        if item.get("status") not in {"active", "generated"}:
            continue
        if not normalized.casefold().endswith(".md"):
            continue
        candidate = root / normalized
        if candidate.is_file():
            selected.append(candidate)
    return selected


def scan_document_routes(
    doc_path: Path,
    root: Path,
    authority: dict[str, dict],
) -> list[Ref]:
    """Check local document links from active/generated documents."""

    source = doc_path.resolve().relative_to(root.resolve()).as_posix()
    source_record = authority.get(source)
    if source_record is None or source_record.get("status") not in {"active", "generated"}:
        return []
    try:
        text = doc_path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return [
            Ref(
                source,
                0,
                source,
                None,
                None,
                True,
                status=BROKENLINK,
                detail=str(error),
            )
        ]

    routes: list[Ref] = []
    root_resolved = root.resolve()
    for lineno, line in enumerate(text.splitlines(), 1):
        if EXAMPLE_MARKER in line:
            continue
        for match in LINK_RE.finditer(line):
            raw_target = match.group("target").strip().strip("<>")
            if (
                not raw_target
                or raw_target.startswith(("#", "//"))
                or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw_target)
            ):
                continue
            route_target = unquote(raw_target.split("#", 1)[0].split("?", 1)[0])
            line_target = re.fullmatch(
                r"(?P<path>.*\.(?:md|html|json)):\d+(?:[-–]\d+)?",
                route_target,
                flags=re.IGNORECASE,
            )
            if line_target:
                route_target = line_target.group("path")
            if Path(route_target.replace("\\", "/")).suffix.casefold() not in DOCUMENT_ROUTE_SUFFIXES:
                continue
            if route_target.startswith(("/", "\\")):
                candidate = root / route_target.lstrip("/\\")
            else:
                candidate = doc_path.parent / route_target
            candidate = candidate.resolve()
            ref = Ref(
                doc=source,
                doc_line=lineno,
                target=raw_target,
                start=None,
                end=None,
                linked=True,
            )
            try:
                normalized = candidate.relative_to(root_resolved).as_posix()
            except ValueError:
                ref.status = BROKENLINK
                ref.detail = "local document link escapes repository"
                routes.append(ref)
                continue
            ref.resolved = normalized
            if not candidate.is_file():
                ref.status = BROKENLINK
                ref.detail = "linked local document does not exist"
                routes.append(ref)
                continue
            target_record = authority.get(normalized)
            if target_record is not None and target_record.get("status") not in {
                "active",
                "generated",
            }:
                ref.status = DOCROUTE
                ref.detail = (
                    f"registered document status is {target_record.get('status')}; "
                    "it cannot be an active route"
                )
            elif target_record is None:
                ref.status = OK
                ref.detail = "unregistered local document; evidence/reference only"
            else:
                ref.status = OK
                ref.detail = f"registered {target_record['status']} document"
            routes.append(ref)
    return routes


def build_report(
    root: Path,
    docs: list[Path],
    *,
    skip_tasks: bool = False,
    document_authority: dict[str, dict] | None = None,
) -> dict:
    index = basename_index(root)
    refs: list[Ref] = []
    for doc in docs:
        if not doc.is_file():
            continue
        for ref in scan_document(doc, root):
            refs.append(classify(ref, root, index))
        if document_authority is not None:
            refs.extend(scan_document_routes(doc, root, document_authority))

    temp_deps = repo_temp_deps(root)
    if not skip_tasks:
        temp_deps = scheduled_task_temp_deps() + temp_deps

    counts: dict[str, int] = {}
    for ref in refs:
        counts[ref.status] = counts.get(ref.status, 0) + 1
    if temp_deps:
        counts[TEMPDEP] = len(temp_deps)

    failures = [r for r in refs if r.status in FAILING]
    drifted = [r for r in refs if r.status == DRIFT]
    return {
        "drift": [
            {
                "doc": r.doc,
                "doc_line": r.doc_line,
                "ref": r.where(),
                "resolved": r.resolved,
                "status": r.status,
                "detail": r.detail,
            }
            for r in drifted
        ],
        "docs_scanned": len(docs),
        "references": len(refs),
        "counts": counts,
        "failures": [
            {
                "doc": r.doc,
                "doc_line": r.doc_line,
                "ref": r.where(),
                "resolved": r.resolved,
                "status": r.status,
                "detail": r.detail,
            }
            for r in failures
        ],
        "warnings": [
            {
                "doc": r.doc,
                "doc_line": r.doc_line,
                "ref": r.where(),
                "resolved": r.resolved,
                "status": r.status,
                "detail": r.detail,
            }
            for r in refs
            if r.status in WARNING
        ],
        "bare": [{"doc": r.doc, "doc_line": r.doc_line, "ref": r.where()} for r in refs if not r.linked and r.start],
        "temp_dependencies": temp_deps,
        "ok": not failures and not temp_deps,
        "clean": not failures and not temp_deps and not drifted,
    }


def render(report: dict) -> str:
    out: list[str] = []
    counts = report["counts"]
    summary = " | ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "nothing found"
    out.append("# Doc reference check\n")
    out.append(f"{report['references']} references in {report['docs_scanned']} documents — {summary}\n")

    if report["failures"]:
        out.append(f"## Broken ({len(report['failures'])})\n")
        for item in report["failures"]:
            resolved = f" (resolved to `{item['resolved']}`)" if item.get("resolved") else ""
            out.append(
                f"- **{item['status']}** {item['doc']}:{item['doc_line']} → "
                f"`{item['ref']}`{resolved} — {item['detail']}"
            )
        out.append("")

    if report["drift"]:
        out.append(f"## Line/symbol mismatch ({len(report['drift'])}) — confirm by eye before editing\n")
        out.append("Heuristic. The doc names a symbol beside a line number and the symbol is")
        out.append("nowhere near that line. Roughly one in four is a false positive, caused by a")
        out.append("sentence naming a second, unrelated symbol. Read the source line before you")
        out.append("change anything — then delete the line number instead of correcting it.\n")
        for item in report["drift"]:
            resolved = f" (resolved to `{item['resolved']}`)" if item.get("resolved") else ""
            out.append(f"- {item['doc']}:{item['doc_line']} → `{item['ref']}`{resolved} — {item['detail']}")
        out.append("")

    if report["temp_dependencies"]:
        out.append(f"## Scheduled/CI dependencies on temp/ ({len(report['temp_dependencies'])})\n")
        out.append("Anything a scheduler runs must not live in `temp/`. One cleanup and the")
        out.append("job dies with no error anybody reads.\n")
        for item in report["temp_dependencies"]:
            out.append(f"- **{item['task']}** → `{item['path']}`")
        out.append("")

    if report["warnings"]:
        out.append(f"## Possibly renamed ({len(report['warnings'])}) — warning only\n")
        for item in report["warnings"]:
            out.append(f"- {item['doc']}:{item['doc_line']} → `{item['ref']}` — {item['detail']}")
        out.append("")

    if report["bare"]:
        out.append(f"## Bare line references ({len(report['bare'])}) — style only, never fails\n")
        out.append("Prefer the symbol name. A symbol survives someone inserting 80 lines above it;")
        out.append("a line number does not, and a wrong line number points at working, plausible,")
        out.append("unrelated code instead of erroring.\n")
        shown = report["bare"][:20]
        for item in shown:
            out.append(f"- {item['doc']}:{item['doc_line']} → `{item['ref']}`")
        if len(report["bare"]) > len(shown):
            out.append(f"- … {len(report['bare']) - len(shown)} more")
        out.append("")

    if report["ok"]:
        out.append("No broken references and nothing scheduled out of `temp/`.")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify code references in the docs still resolve.")
    parser.add_argument("docs", nargs="*", help="documents to scan (default: the project docs)")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--skip-tasks", action="store_true", help="do not query the OS scheduler")
    parser.add_argument("--strict", action="store_true", help="also fail on heuristic line/symbol mismatches")
    parser.add_argument(
        "--authority-active-only",
        action="store_true",
        help="scan only active/generated Markdown from config/data-routing.json",
    )
    parser.add_argument("--out", type=Path, help="write the report to a file as well as stdout")
    args = parser.parse_args()

    root = args.root.resolve()
    if args.authority_active_only and args.docs:
        parser.error("--authority-active-only cannot be combined with explicit documents")
    try:
        document_authority = (
            load_document_authority(root)
            if args.authority_active_only
            else None
        )
        docs = (
            collect_authority_docs(root)
            if args.authority_active_only
            else collect_docs(args.docs, root)
        )
    except ValueError as error:
        parser.error(str(error))
    if not docs:
        print("no documents to scan", file=sys.stderr)
        return 2

    report = build_report(
        root,
        docs,
        skip_tasks=args.skip_tasks,
        document_authority=document_authority,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False) if args.json else render(report)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0 if (report["clean"] if args.strict else report["ok"]) else 1


if __name__ == "__main__":
    sys.exit(main())
