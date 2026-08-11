"""Fail closed when the shared CARDZ runtime junction is missing or redirected."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _assert_expected_target(
    *, runtime_root: Path, expected_target: Path, resolved_target: Path,
) -> None:
    if resolved_target != expected_target:
        raise RuntimeError(
            "CARDZ runtime path is not the approved shared target:"
            f" path={runtime_root} resolved={resolved_target} expected={expected_target}"
        )


def assert_runtime_root(project_root: Path) -> Path:
    """Prove the runtime path resolves to the old checkout and is writable."""

    runtime_root = project_root / "data" / "runtime"
    expected = project_root.parent / "cardz-market-cap" / "data" / "runtime"
    if not runtime_root.exists() or not runtime_root.is_dir():
        raise RuntimeError(f"CARDZ runtime path is missing or not a directory: {runtime_root}")
    if not expected.exists() or not expected.is_dir():
        raise RuntimeError(f"CARDZ runtime target is missing or not a directory: {expected}")
    resolved = runtime_root.resolve(strict=True)
    expected_resolved = expected.resolve(strict=True)
    _assert_expected_target(
        runtime_root=runtime_root,
        expected_target=expected_resolved,
        resolved_target=resolved,
    )
    probe_path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=".cardz-runtime-write-probe-",
            dir=resolved,
        )
        os.close(descriptor)
        probe_path = Path(name)
    except OSError as error:
        raise RuntimeError(f"CARDZ runtime target is not writable: {resolved}: {error}") from error
    finally:
        if probe_path is not None:
            probe_path.unlink(missing_ok=True)
    return resolved

