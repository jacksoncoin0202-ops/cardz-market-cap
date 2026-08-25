#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load the Windows-owned CARDZ MySQL settings from its compose authority.

The database container is owned by the host checkout, not by this pipeline
checkout.  That compose file is the one durable configuration that starts the
3308 instance.  Secret values never leave this process and are never logged.
"""
from __future__ import annotations

import os
import re
import json
import subprocess
from pathlib import Path


WINDOWS_COMPOSE = Path(
    r"C:\Users\jackson0202\Documents\Playground\cardz-market-cap\compose.backend.yaml"
)
WSL_COMPOSE = Path(
    "/mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap/compose.backend.yaml"
)
COMPOSE_VARIABLE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}$")
MYSQL_CONTAINER = "cardz-market-cap-db-1"
DOCKER_CLI = "docker" if os.name == "nt" else "docker.exe"


def canonical_compose_path() -> Path:
    path = WINDOWS_COMPOSE if os.name == "nt" else WSL_COMPOSE
    if not path.is_file():
        raise RuntimeError(f"canonical CARDZ DB compose file missing: {path}")
    return path


def _compose_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    match = COMPOSE_VARIABLE.fullmatch(value)
    if not match:
        return value
    name, default = match.groups()
    resolved = os.environ.get(name)
    if resolved not in (None, ""):
        return str(resolved)
    if default is None or default == "":
        raise RuntimeError(f"canonical CARDZ DB compose variable is unset: {name}")
    return default


def compose_db_env(path: Path | None = None) -> dict[str, str]:
    compose = path or canonical_compose_path()
    values: dict[str, str] = {}
    inside_environment = False
    environment_indent = 0
    port_expression = ""
    for raw in compose.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        if stripped == "environment:":
            inside_environment = True
            environment_indent = indent
            continue
        if inside_environment and stripped and indent <= environment_indent:
            inside_environment = False
        if inside_environment and ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            if key in {"MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD"}:
                values[key] = _compose_value(value)
        if "127.0.0.1:" in stripped and ":3306" in stripped:
            match = re.search(r"127\.0\.0\.1:(\$\{[^}]+\}|[0-9]+):3306", stripped)
            if match:
                port_expression = match.group(1)

    port = _compose_value(port_expression) if port_expression else "3308"
    if port != "3308":
        raise RuntimeError(f"canonical CARDZ DB compose port must be 3308, got {port}")

    # The persistent volume predates the current compose defaults.  MySQL
    # applies MYSQL_PASSWORD only on first initialization, so the running (or
    # stopped) container's saved Config.Env is the only value that can
    # authenticate to that volume.  `docker inspect` works for a stopped
    # container too and no value is written to stdout or disk.
    inspected = subprocess.run(
        [DOCKER_CLI, "inspect", "--format", "{{json .Config.Env}}", MYSQL_CONTAINER],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if inspected.returncode != 0:
        raise RuntimeError("canonical CARDZ DB container configuration is unavailable")
    try:
        entries = json.loads(inspected.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("canonical CARDZ DB container configuration is invalid") from exc
    container_values = {
        key: value
        for entry in entries if isinstance(entry, str) and "=" in entry
        for key, value in [entry.split("=", 1)]
        if key in {"MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD"}
    }
    missing = [
        name for name in ("MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")
        if not container_values.get(name)
    ]
    if missing:
        raise RuntimeError(
            "canonical CARDZ DB container environment incomplete: " + ", ".join(missing)
        )
    return {
        "CARDZ_DB_HOST": "127.0.0.1",
        "CARDZ_DB_PORT": port,
        "CARDZ_DB_USER": container_values["MYSQL_USER"],
        "CARDZ_DB_PASSWORD": container_values["MYSQL_PASSWORD"],
        "CARDZ_DB_NAME": container_values["MYSQL_DATABASE"],
    }


def export_db_env(path: Path | None = None) -> dict[str, str]:
    values = compose_db_env(path)
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values
