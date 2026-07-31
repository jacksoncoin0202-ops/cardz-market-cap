"""Cheap source-lineage SAMPLE pre-filter for card-front images.

This policy decides only SAMPLE risk.  A source that is clean for this narrow
purpose still needs card identity, geometry, rights, and human/vision QC.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import unquote, urlparse


POLICY_ID = "cardz-source-sample-v1"
POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "config/image-source-policy.json"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _percent_decode(value: str) -> str:
    for _ in range(4):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def _normalise(value: str | None) -> str:
    return _percent_decode(str(value or "").strip()).replace("\\", "/").casefold()


def _source_url(value: str) -> str:
    if value.casefold().startswith(("snkrdunk:", "snk_harvest_cache:")):
        marker = value.find("http")
        return value[marker:] if marker >= 0 else value
    return value


def _canonical_source(value: str) -> tuple[str, str]:
    parsed = urlparse(_percent_decode(_source_url(value)))
    return (
        (parsed.hostname or "").casefold(),
        _normalise(parsed.path),
    )


def _host(value: str) -> str:
    return _canonical_source(value)[0]


@lru_cache(maxsize=1)
def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("schemaVersion") != 1
        or not isinstance(value.get("rules"), list)
        or not isinstance(value.get("contentRules", []), list)
    ):
        raise ValueError(f"invalid image source policy: {path}")
    for rule in value.get("contentRules") or []:
        if (
            not isinstance(rule, Mapping)
            or not SHA256_RE.fullmatch(_normalise(rule.get("contentSha256")))
            or rule.get("status") != "reject"
            or not str(rule.get("reason") or "").strip()
        ):
            raise ValueError(f"invalid image content rule: {path}")
    return value


def _rule_matches(
    rule: Mapping[str, Any],
    *,
    value: str,
    tcg: str,
    size: tuple[int, int],
) -> bool:
    scope = _normalise(rule.get("tcg"))
    if scope not in {"*", tcg}:
        return False
    host, path = _canonical_source(value)
    hosts = {_normalise(item) for item in rule.get("hostIn") or []}
    if hosts and host not in hosts:
        return False
    path_contains = _normalise(rule.get("pathContains"))
    if path_contains and path_contains not in path:
        return False
    if any(_normalise(token) not in value for token in rule.get("containsAll") or []):
        return False
    choices = [_normalise(token) for token in rule.get("containsAny") or []]
    if choices and not any(token in value for token in choices):
        return False
    suffix = _normalise(rule.get("suffix"))
    if suffix and not value.endswith(suffix):
        return False
    sizes = {
        (int(item[0]), int(item[1]))
        for item in rule.get("sizeIn") or []
        if isinstance(item, list) and len(item) == 2
    }
    return not sizes or size in sizes


def classify_source(
    source_path: str | None,
    *,
    tcg_code: str | None,
    width_px: int | None,
    height_px: int | None,
) -> dict[str, Any]:
    """Classify source SAMPLE risk without opening the image."""

    value = _normalise(source_path)
    tcg = _normalise(tcg_code)
    size = (int(width_px or 0), int(height_px or 0))
    policy = load_policy()
    for rule in policy["rules"]:
        if _rule_matches(rule, value=value, tcg=tcg, size=size):
            return {
                "policyId": str(policy.get("policyId") or POLICY_ID),
                "ruleId": rule.get("id"),
                "family": rule.get("family"),
                "status": rule.get("status"),
                "reason": rule.get("reason"),
                "nextAction": rule.get("nextAction"),
            }

    return {
        "policyId": str(policy.get("policyId") or POLICY_ID),
        "ruleId": None,
        "family": _host(value) or PurePosixPath(value).parts[0] if value else "unknown",
        "status": "scan_required",
        "reason": None,
        "nextAction": "sample_ocr",
    }


def classify_content_sha256(content_sha256: str | None) -> dict[str, Any]:
    """Classify immutable bytes that have already been visually rejected."""

    digest = _normalise(content_sha256)
    policy = load_policy()
    for rule in policy.get("contentRules") or []:
        if digest == _normalise(rule.get("contentSha256")):
            return {
                "policyId": str(policy.get("policyId") or POLICY_ID),
                "ruleId": rule.get("id"),
                "family": rule.get("family"),
                "status": rule.get("status"),
                "reason": rule.get("reason"),
                "nextAction": rule.get("nextAction"),
            }
    return {
        "policyId": str(policy.get("policyId") or POLICY_ID),
        "ruleId": None,
        "family": None,
        "status": "scan_required" if SHA256_RE.fullmatch(digest) else "invalid",
        "reason": None if SHA256_RE.fullmatch(digest) else "content_sha256_invalid",
        "nextAction": "sample_ocr" if SHA256_RE.fullmatch(digest) else "recompute_hash",
    }
