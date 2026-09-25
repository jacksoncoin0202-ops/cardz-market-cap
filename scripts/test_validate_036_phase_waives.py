#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""036 validator must waive 034-era snapshot sizes, not live catalog counts."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_psa_identity_repair as V  # noqa: E402

need = {
    "catalogExactly1782",
    "auditExactly1782Unique",
    "planExactly762Unique",
    "active762IdentityAndProvenanceResolved",
    "sheet70ExactMapping",
    "green6AcceptedIdentity",
}
missing = sorted(need - set(V.PHASE_WAIVED_036))
assert not missing, f"036 still gates 034 snapshot sizes: {missing}"
print("POSITIVE_OK 036 waives 034-era catalog/audit/plan snapshot sizes")
