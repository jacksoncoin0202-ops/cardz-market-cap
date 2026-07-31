#!/usr/bin/env python3
"""Render PROJECT_STATE.md from the read-only machine status envelope."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "PROJECT_STATE.md"


def text(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def scalar_rows(value: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [
        (str(key), text(item))
        for key, item in value.items()
        if not isinstance(item, (Mapping, list))
    ]


def table(title: str, rows: list[tuple[str, str]]) -> list[str]:
    lines = [f"## {title}", "", "| Field | Value |", "| --- | --- |"]
    lines.extend(f"| `{key}` | {value} |" for key, value in rows)
    lines.append("")
    return lines


def render(status: Mapping[str, Any], *, as_of: datetime) -> str:
    database = status.get("database") if isinstance(status.get("database"), Mapping) else {}
    universe = (
        status.get("universeIntegrity")
        if isinstance(status.get("universeIntegrity"), Mapping)
        else {}
    )
    qc = status.get("qc") if isinstance(status.get("qc"), Mapping) else {}
    pending = status.get("pending") if isinstance(status.get("pending"), Mapping) else {}
    generation = (
        status.get("generation")
        if isinstance(status.get("generation"), Mapping)
        else {}
    )
    release = (
        status.get("releaseGate")
        if isinstance(status.get("releaseGate"), Mapping)
        else {}
    )
    blockers = release.get("blockers") if isinstance(release.get("blockers"), list) else []
    as_of_text = (
        as_of.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    lines = [
        "# PROJECT_STATE — CARDZ Market Cap",
        "",
        "> MACHINE-GENERATED；唔好手改數字。唯一來源係 `python -X utf8 scripts/backend.py status --json`。",
        f"> asOf: **{as_of_text}**",
        "",
        "```powershell",
        "cd C:\\Users\\jackson0202\\Documents\\Playground\\cardz-market-cap",
        "python -X utf8 scripts\\backend.py status --json",
        "python -X utf8 scripts\\render_project_state.py",
        "```",
        "",
        "## Release gate",
        "",
        f"- Status: **{text(status.get('status')).upper()}**",
        f"- Eligible: **{text(release.get('eligible')).upper()}**",
        f"- Read-only status latency: **{text(status.get('elapsedMs'))} ms** (target < {text(status.get('targetMs'))} ms)",
        "",
        "## Release blockers",
        "",
    ]
    lines.extend(
        [f"- `{str(blocker)}`" for blocker in blockers]
        if blockers
        else ["- None"]
    )
    lines.append("")
    lines.extend(
        table(
            "Canonical database",
            [
                ("authority", text(database.get("authority"))),
                ("name", text(database.get("name"))),
                ("connected", text(database.get("connected"))),
            ],
        )
    )
    lines.extend(table("Universe integrity", scalar_rows(universe)))
    lines.extend(table("QC coverage", scalar_rows(qc)))
    lines.extend(table("Pending reviews", scalar_rows(pending)))
    lines.extend(table("Generation", scalar_rows(generation)))
    lines.extend(
        [
            "## Control plane",
            "",
            "- Ownership、SLA、tool owner 同 work-item evidence："
            "[generated tool registry](docs/generated/TOOL_REGISTRY.md)",
            "- Data flow："
            "[generated lineage](docs/generated/DATA_LINEAGE.html)",
            "- 修復前手寫狀態已原樣封存："
            "[PROJECT_STATE_PRE_QC_20260729.md](docs/archive/PROJECT_STATE_PRE_QC_20260729.md)",
            "- Production promotion、writer/timer enablement 仍要 DADDY 明確批准。",
            "",
        ]
    )
    return "\n".join(lines)


def load_live_status() -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(ROOT / "scripts" / "backend.py"),
            "status",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=8,
        check=True,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("machine status is not a JSON object")
    return value


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-json", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.status_json:
        value = json.loads(args.status_json.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("--status-json must contain an object")
        status = value
    else:
        status = load_live_status()
    content = render(status, as_of=datetime.now(timezone.utc))
    atomic_write(args.output.resolve(), content)
    print(
        json.dumps(
            {
                "status": "generated",
                "output": str(args.output.resolve()),
                "releaseEligible": bool(
                    isinstance(status.get("releaseGate"), Mapping)
                    and status["releaseGate"].get("eligible")
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
