#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guard the printed-set-code policy and prove both gates actually read it.

GemRate names the PRODUCT a card was sold in; a One Piece card prints the code
of the set it FIRST appeared in. An alternate-art Kaido pulled from the OP05
booster is filed as "OP05-...044" and prints OP04-044. Measured 2026-08-10:
all 123 One Piece cards stuck in qualified_market_pending failed for one
reason -- both discovery lanes found the right provider listing and threw it
away with `set_code:['op04']!=['op05']`, refused for printing the truth.

`data/policy/op-printed-codes.json` records the printed code proved off the
Limitless page for the product GemRate itself named. Three things have to hold
or it becomes a way to bind the wrong card:

1. The two tiers stay apart. `codes` is evidence a gate may lean on; the
   `advisory` promo scan is a lead for a human. An agreeing set code makes the
   binder skip product agreement (snk_identity_discover.py:351), which for a
   promo is the only check left, so advisory must never reach a gate.
2. It only ever fills a BLANK catalog set_code. Nothing here may overrule what
   the catalog already says -- that would be a relaxed gate, not a fixed input.
3. Both lanes actually read it. A policy file no call site consults is not a
   policy; the toggles below clear the loaded map and require the answer to
   change, so a silent disconnection fails this test rather than passing it.

Run: python -X utf8 scripts/test_op_printed_codes.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as R  # noqa: E402
import op_identity_rules  # noqa: E402
from op_identity_rules import printed_set_code  # noqa: E402
from snk_identity_discover import rule_candidate  # noqa: E402

POLICY_PATH = ROOT / "data" / "policy" / "op-printed-codes.json"
CODE_RE = re.compile(r"^[A-Z]{1,4}\d{0,2}-\d{2,4}$")
GATE_PROOFS = {"sole_number_in_product", "card_name"}

failures: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"FAIL {label}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {label}")


def reload_policy() -> None:
    """Drop the memoised map so the next read reflects what is on disk."""
    op_identity_rules._PRINTED_CODES = None


# --- 1. the file exists and says what it claims to be ----------------------
check("policy file exists", POLICY_PATH.is_file(), True)
policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
check("contract", policy.get("contract"), "op-printed-code-v1")
check("source", policy.get("source"), "onepiece.limitlesstcg.com")

codes = policy.get("codes") or {}
advisory = policy.get("advisory") or {}
check("codes tier is not empty (an empty policy proves nothing)", bool(codes), True)

bad_shape = [
    key for key, rec in {**codes, **advisory}.items()
    if not str(key).isdigit()
    or not CODE_RE.match(str((rec or {}).get("printedCode") or ""))
    or not str((rec or {}).get("evidenceUrl") or "").startswith("https://")
]
check("every entry keys a variant id and carries a code + evidence url", bad_shape, [])

# --- 2. the two tiers stay apart -------------------------------------------
check("nothing weaker than a proof is in the gate-usable tier",
      sorted({str((rec or {}).get("provenBy")) for rec in codes.values()} - GATE_PROOFS), [])
check("the advisory tier is only ever the promo scan",
      sorted({str((rec or {}).get("provenBy")) for rec in advisory.values()} - {"promo_scan"}), [])
check("no variant sits in both tiers", sorted(set(codes) & set(advisory)), [])

reload_policy()
check("a gate-usable entry resolves to its prefix",
      printed_set_code(int(next(iter(codes)))),
      next(iter(codes.values()))["printedCode"].rpartition("-")[0])
if advisory:
    check("an advisory entry resolves to nothing",
          printed_set_code(int(next(iter(advisory)))), "")
check("an unknown variant resolves to nothing", printed_set_code(-1), "")
check("a missing variant id resolves to nothing", printed_set_code(None), "")

# --- 3. pick a real reprint to drive both call sites with -------------------
# Chosen off the file rather than written down: the entry has to be one whose
# printed code differs from the code spelled in its own GemRate set name,
# because that difference IS the defect, and a hard-coded id would rot the
# week the policy is regenerated.
#
# It also has to be one Limitless can NAME. `limitless_product_name` is built
# out of the `product` slug each record carries -- the product GemRate filed
# the card under -- so it knows the sold-in code and only knows a printed code
# when some other card was sold in that product too. Measured 2026-08-11 over
# the 93 gate-usable entries: 46 printed codes are nameable and differ from
# the sold-in name, 19 are not nameable at all (v19 prints ST01, and no
# starter deck is anybody's GemRate product). For those 19 the printed code
# reaches the SNKRDUNK lane and contributes nothing to the PriceCharting one,
# so driving 3b with one of them proves nothing about either.
reprint = next(
    (
        (int(key), rec) for key, rec in codes.items()
        if (found := op_identity_rules.SET_CODE_RE.search(str(rec["canonicalName"]).upper()))
        and found.group(1) != rec["printedCode"].rpartition("-")[0]
        and op_identity_rules.limitless_product_name(rec["printedCode"].rpartition("-")[0])
    ),
    None,
)
check("the policy still holds at least one reprint to test with", reprint is not None, True)

if reprint is not None and not failures:
    variant_id, record = reprint
    printed = record["printedCode"].rpartition("-")[0]
    product = op_identity_rules.SET_CODE_RE.search(record["canonicalName"].upper()).group(1)
    # The identity fields are the gap card's real ones; everything else is
    # blank because rule_candidate reads the whole target row and a blank says
    # "we know nothing here", which is what these cards genuinely look like.
    row = {
        "variant_id": variant_id,
        "set_name": record["canonicalName"],
        "collector_number": record["collectorNumber"],
        "card_language": "ja",
        "tcg_code": "one-piece",
        "v_set_code": "",
        "p_set_code": "",
        "canonical_printing_sha256": "",
        "fp_name": "",
        "fp_parallel": "",
        "parallel_code": "",
        "printing_code": "",
        "v_printing_code": "",
        "p_tcg_code": "",
        "p_card_language": "",
        "p_collector_number": "",
        "p_edition_code": "",
        "p_finish_code": "",
    }

    # --- 3a. the SNKRDUNK lane ---------------------------------------------
    # A provider master that spells the PRINTED code -- the exact shape that
    # was being refused. Only the set_code verdict is read: the synthetic
    # payload will fail other checks and should, since widening which code
    # counts as ours must not widen anything else.
    payload = {
        "quantity_variant_id": 1,
        "product_number": record["printedCode"],
        "source_payload": {"master": {
            "name": f"Test SP [{record['printedCode']}](Booster Pack)",
            "localizedName": "",
        }},
    }
    reload_policy()
    _, with_policy, _ = rule_candidate(row, payload)
    op_identity_rules._PRINTED_CODES = {}  # the disconnected world
    _, without_policy, _ = rule_candidate(row, payload)
    reload_policy()
    check(f"snk lane refuses v{variant_id} on set_code when the policy is unread",
          "set_code:" in without_policy, True)
    check(f"snk lane stops refusing v{variant_id} on set_code once it reads it",
          "set_code:" in with_policy, False)

    # --- 3b. the PriceCharting lane ----------------------------------------
    # Its widening runs through set_names_a_card_could_carry, which before this
    # could only read a code written INTO the collector number. These cards
    # carry a bare "044", so it returned one name and the reprint's own set
    # page -- the only page PriceCharting files the card on -- was never read.
    reload_policy()
    printed_product = op_identity_rules.limitless_product_name(printed)
    sold_product = op_identity_rules.limitless_product_name(
        op_identity_rules.sold_in_set_code(variant_id)
    )
    names_with = R.set_names_a_card_could_carry(row)
    op_identity_rules._PRINTED_CODES = {}
    names_without = R.set_names_a_card_could_carry(row)
    reload_policy()
    # Only the printed map is cleared; the sold-in map is a separate read and
    # keeps answering. So whatever the toggle costs the lane IS the printed
    # code's contribution, with nothing else moving underneath it.
    check("pc lane reads the pulled-from set when the policy is unread",
          names_without, [n for n in (record["canonicalName"], sold_product) if n])
    check("pc lane also reads the printed set's page once it reads it",
          names_with,
          [n for n in (record["canonicalName"], printed_product, sold_product) if n])
    check("clearing the policy costs the pc lane exactly the printed set's name",
          [n for n in names_with if n not in names_without], [printed_product])
    # Every name here now comes from a Limitless product slug -- one string
    # written by Limitless carrying both the code and the name. A catalog
    # majority map used to stand behind it and answered 9 of 9 uncovered
    # one-piece/en codes with ANOTHER set's name; removing the parameter is
    # what makes that inference unrepresentable rather than merely unused.
    check("set_names_a_card_could_carry takes the row and nothing else",
          R.set_names_a_card_could_carry.__code__.co_argcount, 1)
    check("a bare collector number is what makes that widening necessary",
          bool(R.NUMBER_SET_CODE_RE.match(str(record["collectorNumber"]).upper())), False)

# --- 4. it fills blanks, it never overrules the catalog --------------------
try:
    conn = R.connect(R.DAILY_CREDENTIALS_ENV)
except Exception as error:  # noqa: BLE001 -- no database here is not a failure
    print(f"skip  catalog contradiction check (no database: {error})")
else:
    ids = sorted(int(key) for key in {**codes, **advisory})
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, set_code FROM catalog_variant WHERE id IN (%s)"
            % ",".join(["%s"] * len(ids)),
            tuple(ids),
        )
        catalog = {int(r["id"]): str(r["set_code"] or "").upper() for r in cursor.fetchall()}
    check("every entry names a variant that exists", sorted(set(ids) - set(catalog)), [])
    contradictions = sorted(
        (int(key), catalog.get(int(key), ""), rec["printedCode"].rpartition("-")[0])
        for key, rec in {**codes, **advisory}.items()
        if catalog.get(int(key)) and catalog[int(key)] != rec["printedCode"].rpartition("-")[0]
    )
    check("no entry contradicts a set code the catalog already states", contradictions, [])
    filled = [key for key in codes if not catalog.get(int(key))]
    check("and it is filling blanks, not restating (an all-restating policy is a no-op)",
          bool(filled), True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    raise SystemExit(1)
print("all printed-set-code rules hold")
