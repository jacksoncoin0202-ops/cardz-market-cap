#!/usr/bin/env python3
"""Executable contract for the rebuild_036 structural split."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as rebuild  # noqa: E402
import rebuild_036_identity_rules as identity  # noqa: E402
import rebuild_036_reverify as reverify  # noqa: E402


assert rebuild.operator_ruling is identity.operator_ruling
assert rebuild._pc_print_signature_ok is reverify._pc_print_signature_ok
assert rebuild.cmd_pc_identity_reverify is reverify.cmd_pc_identity_reverify
assert rebuild.cmd_snk_identity_reverify is reverify.cmd_snk_identity_reverify
assert identity._PC_BRACKET_SYNONYMS is rebuild._PC_BRACKET_SYNONYMS
assert reverify._PC_BRACKET_SYNONYMS is rebuild._PC_BRACKET_SYNONYMS

# An imported callable is a real stage dependency. The checkpoint hash must
# include its module bytes, otherwise a rule edit can incorrectly stage-skip.
original = rebuild._CODE_SHA_CACHE.copy()
try:
    rebuild._CODE_SHA_CACHE.clear()
    before = rebuild._code_sha(rebuild._pc_print_signature_ok)
    rebuild._CODE_SHA_CACHE.clear()
    original_path = rebuild._workspace_code_path
    rebuild._workspace_code_path = lambda value: None  # type: ignore[assignment]
    without_external_module = rebuild._code_sha(rebuild._pc_print_signature_ok)
finally:
    rebuild._workspace_code_path = original_path
    rebuild._CODE_SHA_CACHE.clear()
    rebuild._CODE_SHA_CACHE.update(original)

assert before != without_external_module
print("POSITIVE_OK rebuild exports stay compatible and split rule code participates in checkpoint hashes")
