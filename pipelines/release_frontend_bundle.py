#!/usr/bin/env python3
"""Deterministic CARDZ product-frontend bundle and release sync contract."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FRONTEND_POLICY_ID = "product-top100-no-graders-v1"
MANAGED_ROOTS = (
    Path("apps/web"),
    Path("packages/market-data"),
    Path("data/editorial"),
)
MANAGED_TOP_FILES = (
    Path("package.json"),
    Path("package-lock.json"),
    Path("tsconfig.base.json"),
    Path("compose.yaml"),
)
GENERATED_RELEASE_DIRS = (
    Path("apps/web/.next"),
    Path("apps/web/.open-next"),
    Path("apps/web/.preview"),
    Path("apps/web/.ruff_cache"),
    Path("apps/web/.wrangler"),
    Path("apps/web/data/runtime"),
    Path("apps/web/docs"),
    Path("apps/web/public/market-assets"),
    Path("apps/web/temp"),
    Path("packages/market-data/dist"),
)
FORBIDDEN_PRODUCT_FILES = (
    Path("apps/web/src/app/graders/[grader]/page.tsx"),
    Path("apps/web/src/app/api/v1/graders/[grader]/route.ts"),
    Path("apps/web/src/components/grading-pulse.tsx"),
    Path("apps/web/src/components/grading-pulse.test.tsx"),
    Path("apps/web/src/components/grader-page.tsx"),
    Path("apps/web/src/components/grader-share-donut.tsx"),
    Path("apps/web/src/lib/grader-share.ts"),
    Path("apps/web/src/lib/grader-share.test.ts"),
)
EXCLUDED_PARTS = {
    "node_modules",
    ".next",
    ".open-next",
    ".preview",
    ".ruff_cache",
    ".turbo",
    ".wrangler",
    "__pycache__",
    "coverage",
    "dist",
    "temp",
}
ASSET_ROOT = Path("apps/web/public/market-assets")
NON_PRODUCT_ROOTS = (
    Path("apps/web/data/runtime"),
    Path("apps/web/docs"),
)
UNSAFE_OR_GENERATED_RELEASE_FILES = (
    Path("apps/web/.env.local"),
    Path("apps/web/tsconfig.tsbuildinfo"),
)
EXPERIMENTAL_PUBLIC_FILES = {
    Path("apps/web/public/index.html"),
    Path("apps/web/public/heatmap-concepts.html"),
    Path("apps/web/public/heatmap-frame-a-wine.html"),
    Path("apps/web/public/heatmap-frame-b-vellum.html"),
    Path("apps/web/public/heatmap-frame-c-gallery.html"),
    Path("apps/web/public/real-cards.json"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_excluded(relative: Path) -> bool:
    if relative in EXPERIMENTAL_PUBLIC_FILES:
        return True
    if relative.name == ".env" or relative.name.startswith(".env."):
        return True
    if relative.name.endswith(".tsbuildinfo"):
        return True
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return True
    for root in NON_PRODUCT_ROOTS:
        try:
            relative.relative_to(root)
            return True
        except ValueError:
            pass
    try:
        relative.relative_to(ASSET_ROOT)
        return True
    except ValueError:
        return False


def managed_files(root: Path) -> dict[str, Path]:
    """Return the exact source files governed by the product frontend contract."""

    root = root.resolve()
    output: dict[str, Path] = {}
    for relative in MANAGED_TOP_FILES:
        path = root / relative
        if path.is_file():
            output[relative.as_posix()] = path
    for managed_root in MANAGED_ROOTS:
        base = root / managed_root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if not _is_excluded(relative):
                output[relative.as_posix()] = path
    return output


def assert_product_frontend(
    root: Path,
    files: dict[str, Path] | None = None,
) -> None:
    """Fail when a retired grader product surface is present in the release bundle."""

    root = root.resolve()
    forbidden = [relative.as_posix() for relative in FORBIDDEN_PRODUCT_FILES if (root / relative).exists()]
    if forbidden:
        raise RuntimeError("retired grader frontend files remain: " + ", ".join(forbidden))

    text_contracts = {
        Path("apps/web/src/components/header.tsx"): ('"/graders', "t.nav.graders"),
        Path("apps/web/src/components/market-page.tsx"): ("GradingPulse", "grading-pulse"),
        Path("apps/web/src/app/sitemap.ts"): ('"/graders',),
    }
    violations: list[str] = []
    for relative, needles in text_contracts.items():
        path = root / relative
        if not path.is_file():
            violations.append(f"missing:{relative.as_posix()}")
            continue
        text = path.read_text(encoding="utf-8-sig")
        for needle in needles:
            if needle in text:
                violations.append(f"{relative.as_posix()}:{needle}")
    if violations:
        raise RuntimeError("product frontend policy failed: " + ", ".join(violations))

    forbidden_needles = (
        '"/graders',
        "'/graders",
        "GradingPulse",
        "gradingPulse",
        "grading-pulse",
        "t.nav.graders",
    )
    for relative_text, path in (files if files is not None else managed_files(root)).items():
        relative = Path(relative_text)
        if relative.suffix not in {".ts", ".tsx", ".js", ".mjs"}:
            continue
        text = path.read_text(encoding="utf-8-sig")
        for needle in forbidden_needles:
            if needle in text:
                raise RuntimeError(f"retired grader product reference remains: {relative_text}:{needle}")


def bundle_manifest(root: Path) -> dict[str, Any]:
    """Hash file names and bytes so pass and release use the same frontend."""

    root = root.resolve()
    files = managed_files(root)
    assert_product_frontend(root, files)
    if not files:
        raise RuntimeError(f"frontend bundle is empty: {root}")
    digest = hashlib.sha256()
    for relative, path in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "policyId": FRONTEND_POLICY_ID,
        "sha256": digest.hexdigest(),
        "fileCount": len(files),
        "managedRoots": [path.as_posix() for path in MANAGED_ROOTS],
        "managedTopFiles": [path.as_posix() for path in MANAGED_TOP_FILES],
    }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _count_files(path: Path) -> int:
    return sum(1 for item in path.rglob("*") if item.is_file()) if path.is_dir() else 0


def sync_release_frontend(
    source_root: Path,
    release_root: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    """Mirror the authoritative frontend into one clean release worktree."""

    source_root = source_root.resolve()
    release_root = release_root.resolve()
    if source_root == release_root:
        raise RuntimeError("source and release roots must differ")
    if not (release_root / ".git").exists():
        raise RuntimeError(f"release root is not a git worktree: {release_root}")

    source_manifest = bundle_manifest(source_root)
    source_files = managed_files(source_root)
    target_files = managed_files(release_root)

    removed = 0
    for relative, target in sorted(target_files.items()):
        if relative not in source_files:
            target.resolve().relative_to(release_root)
            target.unlink()
            removed += 1
    for relative in sorted(EXPERIMENTAL_PUBLIC_FILES):
        target = (release_root / relative).resolve()
        target.relative_to(release_root)
        if target.is_file():
            target.unlink()
            removed += 1
    for relative in UNSAFE_OR_GENERATED_RELEASE_FILES:
        target = (release_root / relative).resolve()
        target.relative_to(release_root)
        if target.is_file():
            target.unlink()
            removed += 1

    generated_cleared = 0
    for relative in GENERATED_RELEASE_DIRS:
        generated = (release_root / relative).resolve()
        generated.relative_to(release_root)
        generated_cleared += _count_files(generated)
        if generated.exists():
            shutil.rmtree(generated)

    copied = 0
    for relative, source in sorted(source_files.items()):
        target = (release_root / Path(relative)).resolve()
        target.relative_to(release_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            shutil.copy2(source, target)
            copied += 1

    release_manifest = bundle_manifest(release_root)
    if release_manifest != source_manifest:
        raise RuntimeError("release frontend hash does not match the pass-bound source bundle")

    result = {
        "action": "sync-release-frontend",
        "asOf": _utc_now(),
        "frontendBundle": source_manifest,
        "releaseBundle": release_manifest,
        "copied": copied,
        "removedStale": removed,
        "generatedFilesCleared": generated_cleared,
        "releaseRoot": str(release_root),
    }
    _atomic_json(receipt_path.resolve(), result)
    return result
