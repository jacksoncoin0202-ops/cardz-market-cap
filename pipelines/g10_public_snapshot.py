#!/usr/bin/env python3
"""Build the sanitized CARDZ public v2 snapshot from the private G10 landing.

Provider-native IDs, URLs and payloads are consumed only while resolving the
canonical observation. The emitted document contains opaque CARDZ IDs and
content-addressed, semantically matched raw fronts only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlparse

from PIL import Image, ImageChops, ImageOps

from g10_ingest import (
    GRADERS,
    aggregate_sales,
    derive_price_windows,
    iso_utc,
    iter_constituents,
    normalize_grader_populations,
    normalize_psa10_sales,
    parse_effective_at,
    private_source_ref,
    read_json,
    sha256_bytes,
    sha256_file,
    load_landing_replay,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / "grade10-scraper" / "data"
DEFAULT_KADO = ROOT.parent / "kado-dump"
DEFAULT_OUTPUT = ROOT / "data" / "public" / "seed-snapshot.json"
DEFAULT_ASSETS = ROOT / "data" / "public" / "market-assets"
DEFAULT_IMAGE_MANIFEST = ROOT / "manifests" / "image-qc.json"
DEFAULT_LANDING = ROOT / "data" / "runtime" / "private-landing"

WINDOWS = ("1d", "7d", "30d")
LOCALES = ("en", "zhTW", "zhCN", "ja")
PRIVATE_TOKENS = ("g10_", "grade10", "gemrate", "snkrdunk", "altxyz", "ebay", "http://", "https://")


@dataclass(frozen=True)
class CollectorNumber:
    display: str
    normalized: str
    complete: bool


@dataclass(frozen=True)
class RawImage:
    path: Path
    evidence: str


def normalized_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def canonical_name_key(value: Any) -> str:
    """Normalize deterministic name variants without doing fuzzy matching."""

    text = str(value or "").casefold()
    text = re.sub(r"\b(?:full|special|alternate|illustration)\s+art\b", " ", text)
    text = re.sub(r"\b(?:ex|gx|v|vmax|vstar|holo|rare|secret|ultra|hyper|style|sur|sar)\b", " ", text)
    return normalized_text(text)


def visible_name(value: Any) -> str:
    text = str(value or "").split("·", 1)[0]
    return re.sub(r"\s*#[^#]+$", "", text).strip()


def row_number(value: Any) -> str:
    match = re.search(r"#([^·]+)", str(value or ""))
    return match.group(1).strip() if match else ""


def normalize_collector(raw: Any, language: str | None = None) -> CollectorNumber:
    value = re.sub(r"\s+", "", str(raw or "").upper())
    value = value.replace("＿", "-").replace("_", "-")
    if not value:
        return CollectorNumber("Unknown", "unknown", False)

    english_svp = re.fullmatch(r"SVPEN(\d{1,4})", value)
    if english_svp:
        display = f"{english_svp.group(1)}/SVP"
        return CollectorNumber(display, display.casefold(), True)
    japanese_svp = re.fullmatch(r"SVPJP(\d{1,4})", value)
    if japanese_svp:
        display = f"{japanese_svp.group(1)}/SV-P"
        return CollectorNumber(display, display.casefold(), True)

    suffixes = {
        "ADVP": "ADV-P",
        "PCGP": "PCG-P",
        "DPP": "DP-P",
        "DPTP": "DPt-P",
        "BWP": "BW-P",
        "XYP": "XY-P",
        "SMP": "SM-P",
        "SP": "S-P",
        "SVPJP": "SV-P",
    }
    match = re.fullmatch(r"(\d{1,4})(ADVP|PCGP|DPP|DPTP|BWP|XYP|SMP|SP|SVPJP)", value)
    if match:
        display = f"{match.group(1)}/{suffixes[match.group(2)]}"
        return CollectorNumber(display, display.casefold(), True)
    match = re.fullmatch(r"(ADV-P|PCG-P|DP-P|DPT-P|BW-P|XY-P|SM-P|SV-P|S-P)(?:EN|JP)?(\d{1,4})", value)
    if match:
        display = f"{match.group(2)}/{match.group(1)}"
        return CollectorNumber(display, display.casefold(), True)
    match = re.fullmatch(r"(ADVP|PCGP|DPP|DPTP|BWP|XYP|SMP|SP)(?:JP)?(\d{1,4})", value)
    if match:
        display = f"{match.group(2)}/{suffixes[match.group(1)]}"
        return CollectorNumber(display, display.casefold(), True)
    match = re.fullmatch(r"(\d{1,4})/(ADV-P|PCG-P|DP-P|DPT-P|BW-P|XY-P|SM-P|SV-P|S-P)", value)
    if match:
        return CollectorNumber(value, value.casefold(), True)
    match = re.fullmatch(r"(\d{1,4})/(SVP|MEP)", value)
    if match:
        display = f"{match.group(1)}/{match.group(2)}"
        return CollectorNumber(display, display.casefold(), True)
    match = re.fullmatch(r"(\d{1,4})(ADV-P|PCG-P|DP-P|DPT-P|BW-P|XY-P|SM-P|SV-P|S-P)", value)
    if match:
        display = f"{match.group(1)}/{match.group(2)}"
        return CollectorNumber(display, display.casefold(), True)

    compact_one_piece = re.fullmatch(r"(OP|ST|EB)(\d{2})(\d{3})", value)
    if compact_one_piece:
        value = f"{compact_one_piece.group(1)}{compact_one_piece.group(2)}-{compact_one_piece.group(3)}"
    if re.fullmatch(r"(?:OP|ST|EB)\d{2}-\d{3}[A-Z]?", value) or re.fullmatch(r"P-\d{3}[A-Z]?", value):
        return CollectorNumber(value, value.casefold(), True)

    numeric_set = re.fullmatch(r"(\d{1,4})/(\d{1,4})", value)
    if numeric_set:
        # Japanese set numbers use a three-digit numerator and denominator.
        # English eras do not share one global width (for example 78/73), so
        # preserve a complete upstream value and let the exact local printing
        # resolver restore any omitted set total formatting.
        if language == "ja":
            display = f"{numeric_set.group(1).zfill(3)}/{numeric_set.group(2).zfill(3)}"
        else:
            display = f"{numeric_set.group(1)}/{numeric_set.group(2)}"
        return CollectorNumber(display, display.casefold(), True)
    if re.fullmatch(r"[A-Z]+\d{1,4}/[A-Z]+\d{1,4}", value):
        return CollectorNumber(value, value.casefold(), True)

    # Gallery and promo namespaces are globally meaningful collector numbers.
    if re.fullmatch(r"(?:SWSH|SM|SV|GG|TG|RC|XY)\d{1,4}", value):
        return CollectorNumber(value, value.casefold(), True)

    return CollectorNumber(value, value.casefold(), False)


def opaque_id(tcg: str, language: str, set_name: str, collector: CollectorNumber, name: str) -> str:
    value = "\x1f".join(part.strip().casefold() for part in (tcg, language, set_name, collector.normalized, name))
    return f"cmc_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def metric(value: float | int | None, status: str, as_of: str | None, **extra: Any) -> dict[str, Any]:
    if status in {"accumulating", "unavailable"}:
        value = None
        if status == "unavailable":
            as_of = None
    return {"value": value, "status": status, "asOf": as_of, **extra}


def localized(english: str | None = None) -> dict[str, str | None]:
    return {"en": english, "zhTW": None, "zhCN": None, "ja": None}


def boolish(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def canonical_card_language(value: Any) -> str:
    language = str(value or "").strip().casefold().replace("_", "-")
    aliases = {
        "en": "en",
        "eng": "en",
        "english": "en",
        "ja": "ja",
        "jp": "ja",
        "jpn": "ja",
        "japanese": "ja",
        "zh-hant": "zhTW",
        "zh-tw": "zhTW",
        "traditional-chinese": "zhTW",
        "zh-hans": "zhCN",
        "zh-cn": "zhCN",
        "simplified-chinese": "zhCN",
    }
    return aliases.get(language, "")


def promo_namespace(set_row: Mapping[str, str], language: str) -> str | None:
    """Return the printed promo namespace for an exact canonical Kado set.

    Promo catalogues are open-ended namespaces, not numbered sets. Their
    ``total_cards`` (or the largest row currently mirrored) must never be used
    as a denominator such as ``085/225``.
    """

    name = re.sub(r"\s+", " ", str(set_row.get("name") or "").strip()).upper()
    if language == "en" and name == "SCARLET & VIOLET PROMOS":
        return "SVP"
    if language == "en" and name == "MEGA EVOLUTION PROMOS":
        return "MEP"
    aliases = {
        "ADV-P PROMOS": "ADV-P",
        "PCG-P PROMOS": "PCG-P",
        "DP-P PROMOS": "DP-P",
        "DPT-P PROMOS": "DPt-P",
        "BW-P PROMOS": "BW-P",
        "XY PROMOS": "XY-P",
        "XY-P PROMOS": "XY-P",
        "SM-P PROMOS": "SM-P",
        "S-P PROMOS": "S-P",
        "SV-P PROMOS": "SV-P",
        "SV-P PROMOTIONAL CARDS (SCTCG)": "SV-P",
    }
    return aliases.get(name)


def expected_japanese_promo_namespace(set_name: Any) -> str | None:
    """Return a namespace only when the private set evidence is explicit."""

    text = str(set_name or "")
    for namespace in ("SV-P", "SM-P", "XY-P", "BW-P", "DPt-P", "DP-P", "PCG-P", "ADV-P", "S-P"):
        if re.search(rf"(?<![A-Z]){re.escape(namespace)}(?![A-Z])", text, re.IGNORECASE):
            return namespace
    lowered = text.casefold()
    if not any(token in lowered for token in ("promo", "campaign", "special box", "stamp box", "master battle", "munch")):
        return None
    if "sword and shield" in lowered:
        return "S-P"
    if "scarlet and violet" in lowered:
        return "SV-P"
    if "sun and moon" in lowered:
        return "SM-P"
    if re.search(r"\bxy\b", lowered):
        return "XY-P"
    return None


class KadoRawResolver:
    """Exact Pokemon raw-front resolver over the local Kado dump."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.sets: dict[str, dict[str, str]] = {}
        self.cards: list[dict[str, str]] = []
        sets_path = root / "data" / "sets.csv"
        cards_path = root / "data" / "cards.csv"
        if not sets_path.is_file() or not cards_path.is_file():
            return
        with sets_path.open("r", encoding="utf-8-sig", newline="") as handle:
            self.sets = {row["id"]: row for row in csv.DictReader(handle)}
        with cards_path.open("r", encoding="utf-8-sig", newline="") as handle:
            self.cards = list(csv.DictReader(handle))

        numbers: dict[str, list[int]] = defaultdict(list)
        subset_numbers: dict[tuple[str, str], list[int]] = defaultdict(list)
        for card in self.cards:
            if not boolish(card.get("secret")) and re.fullmatch(r"\d{1,4}", card.get("card_number") or ""):
                numbers[card["set_id"]].append(int(card["card_number"]))
            subset = re.fullmatch(r"(GG|SV|TG|RC)(\d{1,4})", str(card.get("card_number") or "").upper())
            if subset:
                subset_numbers[(card["set_id"], subset.group(1))].append(int(subset.group(2)))
        for set_id, values in numbers.items():
            if values:
                self.sets[set_id]["printed_total"] = str(max(values))
        self.subset_totals = {
            key: max(values)
            for key, values in subset_numbers.items()
            if values
        }

        self.index: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
        for card in self.cards:
            set_row = self.sets.get(card.get("set_id") or "")
            if set_row is None:
                continue
            lang = canonical_card_language(set_row.get("language"))
            if not lang:
                continue
            key = (canonical_name_key(card.get("name")), normalized_text(card.get("card_number")), lang)
            self.index[key].append(card)

    def _image_path(self, row: Mapping[str, str]) -> Path | None:
        storage = str(row.get("image_storage_path") or "").strip()
        candidates: list[Path] = []
        if storage:
            raw = Path(storage)
            candidates.append(raw if raw.is_absolute() else self.root / raw)
        image_url = str(row.get("image_url") or "")
        if image_url:
            path = unquote(urlparse(image_url).path)
            marker = "/images/"
            if marker in path:
                relative = path.split(marker, 1)[1]
                # The local mirror preserves URL '+' separators in set folders.
                relative = relative.replace(" ", "+")
                candidates.append(self.root / "images" / "cards" / relative)
                candidates.append(
                    self.root
                    / "C_"
                    / "Users"
                    / "jackson0202"
                    / "Documents"
                    / "Playground"
                    / "kado-dump"
                    / "images"
                    / "cards"
                    / relative
                )
        return next((candidate for candidate in candidates if candidate.is_file()), None)

    def resolve(self, name: str, number: str, set_name: str, language: str) -> tuple[RawImage, CollectorNumber] | None:
        candidates = self.index.get((canonical_name_key(name), normalized_text(number), language), [])
        target_set = normalized_text(set_name)
        exact: list[tuple[dict[str, str], Path]] = []
        for card in candidates:
            set_row = self.sets.get(card["set_id"], {})
            local_set = normalized_text(set_row.get("name"))
            if not local_set or local_set not in target_set:
                continue
            image_path = self._image_path(card)
            if image_path is not None:
                exact.append((card, image_path))
        if not exact and len(candidates) == 1:
            image_path = self._image_path(candidates[0])
            if image_path is not None:
                exact.append((candidates[0], image_path))
        if len(exact) != 1:
            return None
        card, image_path = exact[0]
        card_number = str(card.get("card_number") or "").upper()
        set_row = self.sets.get(card["set_id"], {})
        subset = re.fullmatch(r"(GG|SV|TG|RC)(\d{1,4})", card_number)
        if subset and (card["set_id"], subset.group(1)) in self.subset_totals:
            total = self.subset_totals[(card["set_id"], subset.group(1))]
            width = max(len(subset.group(2)), len(str(total)))
            display = (
                f"{subset.group(1)}{subset.group(2).zfill(width)}/"
                f"{subset.group(1)}{str(total).zfill(width)}"
            )
            collector = CollectorNumber(display, display.casefold(), True)
        elif re.fullmatch(r"\d{1,4}", card_number) and (namespace := promo_namespace(set_row, language)):
            display = f"{card_number.zfill(3)}/{namespace}"
            collector = CollectorNumber(display, display.casefold(), True)
        else:
            collector = normalize_collector(card_number, language)
        if not collector.complete and re.fullmatch(r"\d{1,4}", card_number):
            denominator = set_row.get("printed_total")
            if denominator and int(card_number) <= max(int(denominator), int(card_number)):
                year = re.search(r"\b(20\d{2})\b", str(set_row.get("release_date") or set_name))
                modern_english = language == "en" and year is not None and int(year.group(1)) >= 2020
                if language == "ja" or modern_english:
                    display = f"{card_number.zfill(3)}/{str(denominator).zfill(3)}"
                else:
                    display = f"{card_number}/{denominator}"
                collector = CollectorNumber(display, display.casefold(), True)
        if not collector.complete:
            return None
        return RawImage(image_path, "canonical_exact"), collector


def storage_source(source_code: str) -> str:
    # Private storage uses a neutral internal directory name for this route.
    return "altxyz" if source_code == "ebay" else source_code


def card_source_dir(source_root: Path, row: Mapping[str, Any]) -> tuple[Path, str] | None:
    source_ref = private_source_ref(row)
    if source_ref is None:
        return None
    source_code, external_id = source_ref
    return source_root / "cards" / storage_source(source_code) / external_id, storage_source(source_code)


def source_image_path(source_root: Path, storage_code: str, external_id: str) -> Path | None:
    return next(iter(sorted((source_root / "images").glob(f"{storage_code}_{external_id}.*"))), None)


def semantically_same_name(left: str, right: str) -> bool:
    left_key = canonical_name_key(visible_name(left))
    right_key = canonical_name_key(visible_name(right))
    if left_key == right_key:
        return True
    left_tokens = sorted(set(re.findall(r"[a-z0-9]+", str(visible_name(left)).casefold().replace("'s", ""))))
    right_tokens = sorted(set(re.findall(r"[a-z0-9]+", str(visible_name(right)).casefold().replace("'s", ""))))
    ignored = {"a", "the", "ex", "gx", "v", "vmax", "vstar", "holo", "style", "munch"}
    return [token for token in left_tokens if token not in ignored] == [token for token in right_tokens if token not in ignored]


def collector_base(value: CollectorNumber) -> str:
    one_piece = re.search(r"(?:OP|ST|EB)\d{2}-\d{3}[A-Z]?", value.display)
    if one_piece:
        return one_piece.group(0)
    number = re.search(r"\d{1,4}", value.display)
    return str(int(number.group(0))) if number else normalized_text(value.display)


def collector_language_hint(value: CollectorNumber) -> str | None:
    if re.search(r"/(?:XY-P|SM-P|DP-P|SV-P|S-P)$", value.display):
        return "ja"
    if value.display.endswith("/SVP"):
        return "en"
    return None


def set_language_hint(value: Any) -> str | None:
    set_name = str(value or "").casefold()
    if "traditional chinese" in set_name:
        return "zhTW"
    if "simplified chinese" in set_name:
        return "zhCN"
    if "japanese" in set_name:
        return "ja"
    if "english" in set_name:
        return "en"
    return None


def raw_front_crop_box(path: Path) -> tuple[int, int, int, int] | None:
    """Reject slabs, label crops, sealed packs and marketing collages.

    Supported Pokémon and One Piece raw fronts share a narrow portrait aspect
    after transparent padding is removed. This is only a media-kind gate; card
    identity is independently checked against the source record.
    """

    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened)
            if "A" in image.getbands():
                mask = image.getchannel("A").point(lambda value: 255 if value > 10 else 0)
            else:
                white = Image.new("RGB", image.size, "white")
                mask = ImageChops.difference(image.convert("RGB"), white).convert("L").point(
                    lambda value: 255 if value > 20 else 0
                )
            box = mask.getbbox()
    except (OSError, ValueError):
        return None
    if box is None:
        return None
    width, height = box[2] - box[0], box[3] - box[1]
    if width < 180 or height < 250:
        return None
    if 0.66 <= (width / height) <= 0.78:
        return box

    # Some upstream canvases contain a raw card plus a detached enlarged code
    # detail. Select the longest portrait-height column run, then recompute its
    # foreground bounds. Marketing collages and slabs do not satisfy the card
    # aspect after this card-bound crop.
    cropped_mask = mask.crop(box)
    profile = list(cropped_mask.resize((width, 1), Image.Resampling.BOX).getdata())
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, occupancy in enumerate(profile + [0]):
        if occupancy >= 140 and start is None:
            start = index
        elif occupancy < 140 and start is not None:
            runs.append((start, index))
            start = None
    if not runs:
        return None
    run_start, run_end = max(runs, key=lambda run: run[1] - run[0])
    if run_end - run_start < 180:
        return None
    component = cropped_mask.crop((run_start, 0, run_end, height)).getbbox()
    if component is None:
        return None
    candidate = (
        box[0] + run_start + component[0],
        box[1] + component[1],
        box[0] + run_start + component[2],
        box[1] + component[3],
    )
    candidate_width = candidate[2] - candidate[0]
    candidate_height = candidate[3] - candidate[1]
    if candidate_width < 180 or candidate_height < 250:
        return None
    return candidate if 0.66 <= (candidate_width / candidate_height) <= 0.78 else None


def resolve_identity_and_image(
    source_root: Path,
    kado: KadoRawResolver,
    market: str,
    row: Mapping[str, Any],
    rejection_counts: dict[str, int] | None = None,
) -> tuple[dict[str, Any], CollectorNumber, RawImage, Path, str] | None:
    def reject(reason: str) -> None:
        if rejection_counts is not None:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        return None

    private_dir = card_source_dir(source_root, row)
    source_ref = private_source_ref(row)
    if private_dir is None or source_ref is None:
        return reject("identity_missing_source")
    card_dir, storage_code = private_dir
    _, external_id = source_ref
    asset_path = card_dir / "asset_info.json"
    if not asset_path.is_file():
        return reject("identity_missing_asset")
    asset = read_json(asset_path)
    if not isinstance(asset, Mapping):
        return reject("identity_invalid_asset")
    expected_language = canonical_card_language(row.get("lang"))
    row_collector = normalize_collector(row_number(row.get("name")), expected_language)
    asset_collector = normalize_collector(asset.get("cardId"), expected_language)
    language_hint = (
        collector_language_hint(row_collector)
        or collector_language_hint(asset_collector)
        or set_language_hint(row.get("setName"))
    )
    language = language_hint or canonical_card_language(asset.get("language"))
    if language != expected_language:
        return reject("identity_language_conflict")
    tcg = "one-piece" if market == "opcg" else "pokemon"

    collector = row_collector if row_collector.complete else asset_collector
    names_match = semantically_same_name(str(row.get("name") or ""), str(asset.get("cardName") or ""))
    collector_confirms_identity = (asset_collector.complete or row_collector.complete) and (
        not row_number(row.get("name")) or collector_base(row_collector) == collector_base(asset_collector)
    )
    if not names_match and not collector_confirms_identity:
        return reject("identity_name_conflict")

    raw_image: RawImage | None = None
    local_source_image = source_image_path(source_root, storage_code, external_id)
    if storage_code == "snkrdunk" and local_source_image is not None and raw_front_crop_box(local_source_image) is not None:
        raw_image = RawImage(local_source_image, "native_card_bound_raw_front")

    kado_match: tuple[RawImage, CollectorNumber] | None = None
    if tcg == "pokemon":
        number = row_number(row.get("name")) or str(asset.get("cardId") or "")
        number = re.sub(r"(?:XYP|SMP|DPP|SVP|SP)$", "", number.upper())
        number = number.split("/", 1)[0]
        kado_match = kado.resolve(str(asset.get("cardName") or visible_name(row.get("name"))), number, str(row.get("setName") or ""), language)
        if kado_match is not None:
            if raw_image is None:
                raw_image = kado_match[0]
            # The local exact printing record owns display formatting and set
            # totals, including gallery/subset denominators.
            collector = kado_match[1]

    expected_namespace = expected_japanese_promo_namespace(row.get("setName")) if language == "ja" else None
    if expected_namespace is not None and not collector.display.casefold().endswith(f"/{expected_namespace.casefold()}"):
        return reject("identity_set_namespace_conflict")

    if not collector.complete or raw_image is None:
        return reject("identity_incomplete_collector" if not collector.complete else "image_raw_front_unavailable")
    return dict(asset), collector, raw_image, card_dir, language


def encode_raw_front(source: Path) -> tuple[bytes, int, int]:
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        crop_box = raw_front_crop_box(source)
        if crop_box is None:
            raise ValueError(f"image did not pass raw-front geometry: {source}")
        image = image.crop(crop_box)
        image.thumbnail((1200, 1680), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        if "A" in image.getbands():
            image.save(output, format="WEBP", lossless=True, method=6)
        else:
            image.convert("RGB").save(output, format="WEBP", quality=92, method=6)
        return output.getvalue(), image.width, image.height


def stable_json(value: Any) -> bytes:
    def normalize(child: Any) -> Any:
        if isinstance(child, dict):
            return {key: normalize(child[key]) for key in sorted(child)}
        if isinstance(child, list):
            return [normalize(item) for item in child]
        if isinstance(child, float) and child.is_integer():
            return int(child)
        return child

    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_ingest_at(source_root: Path) -> datetime:
    state_path = source_root / "_state" / "last_run.json"
    if state_path.is_file():
        value = read_json(state_path)
        if isinstance(value, Mapping) and value.get("lastRun"):
            return parse_effective_at(str(value["lastRun"]))
    newest = max((path.stat().st_mtime for path in source_root.glob("index/*/constituents.json")), default=0)
    return datetime.fromtimestamp(newest, tz=timezone.utc)


def load_price_effective_at(source_root: Path) -> datetime:
    values: list[datetime] = []
    for market in ("ptcg", "opcg"):
        path = source_root / "index" / market / "summary.json"
        if not path.is_file():
            continue
        document = read_json(path)
        if isinstance(document, Mapping) and document.get("updatedAt"):
            values.append(parse_effective_at(str(document["updatedAt"])))
    if not values:
        raise RuntimeError("price effective time is absent from index summaries")
    # A combined market is only as fresh as its oldest constituent index.
    return min(values)


def file_observed_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def read_sales(card_dir: Path, identity: str) -> tuple[list[Any], datetime | None]:
    by_fingerprint: dict[str, Any] = {}
    observed_at: datetime | None = None
    for filename in ("apparel_grade_22.json", "ebay_PSA_10.json"):
        path = card_dir / filename
        if not path.is_file():
            continue
        fetched_at = file_observed_at(path)
        observed_at = max(observed_at, fetched_at) if observed_at is not None else fetched_at
        document = read_json(path)
        rows = document.get("saleHistory") if isinstance(document, Mapping) else None
        if isinstance(rows, list):
            normalized = normalize_psa10_sales(
                [row for row in rows if isinstance(row, Mapping)],
                fetched_at,
                identity,
            )
            # Identical upstream transactions exposed through both local files
            # share a fingerprint. Occurrence ordinals still preserve genuine
            # same-date/same-price multiple sales within either source list.
            for sale in normalized:
                by_fingerprint.setdefault(sale.fingerprint, sale)
    return list(by_fingerprint.values()), observed_at


def sales_metric(sales: Sequence[Any], observed_at: datetime | None, generated_at: datetime, days: int) -> dict[str, Any]:
    if observed_at is None:
        aggregate = {"coverage": "unavailable"}
    else:
        aggregate = aggregate_sales(sales, observed_at, days)
    if aggregate["coverage"] == "unavailable":
        return {
            "valueUsd": metric(None, "unavailable", None),
            "count": metric(None, "unavailable", None),
            "coverage": "unavailable",
            "asOf": iso_utc(observed_at) if observed_at is not None else None,
        }
    status = "stale" if generated_at - observed_at > timedelta(hours=48) else "ready"
    return {
        "valueUsd": metric(aggregate["valueUsd"], status, iso_utc(observed_at)),
        "count": metric(aggregate["count"], status, iso_utc(observed_at)),
        "coverage": "stale" if status == "stale" else "partial",
        "asOf": iso_utc(observed_at),
    }


def daily_change_metrics(daily_values: Mapping[date, float], current_status: str) -> dict[str, dict[str, Any]]:
    if not daily_values:
        return {window: metric(None, "unavailable", None) for window in WINDOWS}
    latest_date = max(daily_values)
    derived = derive_price_windows(daily_values, latest_date)
    result: dict[str, dict[str, Any]] = {}
    for window in WINDOWS:
        value = derived[window]
        status = str(value["status"])
        if status == "ready" and current_status == "stale":
            status = "stale"
        as_of = (
            iso_utc(datetime.combine(latest_date, time.min, tzinfo=timezone.utc))
            if status in {"ready", "stale"}
            else None
        )
        result[window] = metric(value.get("value"), status, as_of)
    return result


def add_population_windows(
    populations: dict[str, Any],
    source_ref: tuple[str, str],
    replay_populations: Mapping[tuple[str, str, str], Mapping[date, int]],
) -> dict[str, Any]:
    source_code, external_id = source_ref
    for grader in GRADERS:
        population = populations[grader]
        current = population["topGradePopulation"]
        daily = dict(replay_populations.get((source_code, external_id, grader), {}))
        if isinstance(current.get("value"), int) and current.get("asOf"):
            current_day = parse_effective_at(str(current["asOf"])).date()
            daily.setdefault(current_day, int(current["value"]))
        if current.get("status") == "unavailable":
            population["topGradePopulationChangePct"] = {
                window: metric(None, "unavailable", None) for window in WINDOWS
            }
        else:
            population["topGradePopulationChangePct"] = daily_change_metrics(daily, str(current["status"]))
    return populations


def daily_history(daily_prices: Mapping[date, float], price_status: str, sales: Sequence[Any]) -> list[dict[str, Any]]:
    buckets: dict[date, list[Any]] = defaultdict(list)
    sale_dates = [parse_effective_at(sale.sold_at).date() for sale in sales]
    timeline_dates = [*daily_prices.keys(), *sale_dates]
    if not timeline_dates:
        return []
    timeline_end = max(timeline_dates)
    cutoff = timeline_end - timedelta(days=30)
    for sale in sales:
        sold_at = parse_effective_at(sale.sold_at)
        if cutoff <= sold_at.date() <= timeline_end:
            buckets[sold_at.date()].append(sale)
    prices = {day: value for day, value in daily_prices.items() if cutoff <= day <= timeline_end}
    dates = set(buckets) | set(prices)
    latest_price_date = max(prices) if prices else None
    points: list[dict[str, Any]] = []
    for day in sorted(dates):
        rows = buckets.get(day, [])
        price = prices.get(day)
        points.append(
            {
                "at": iso_utc(datetime.combine(day, time.min, tzinfo=timezone.utc)),
                "priceUsd": price,
                "priceStatus": (price_status if day == latest_price_date else "ready") if price is not None else "unavailable",
                "trackedSalesValueUsd": round(sum(row.transaction_value_usd for row in rows), 6) if rows else None,
                "trackedSalesCount": len(rows) if rows else None,
                "salesCoverage": "partial" if rows else "unavailable",
            }
        )
    return points


def grader_populations(card_dir: Path, generated_at: datetime) -> dict[str, Any] | None:
    path = card_dir / "populations.json"
    if not path.is_file():
        return None
    document = read_json(path)
    if not isinstance(document, Mapping):
        return None
    observed_at = file_observed_at(path)
    values = normalize_grader_populations(document, observed_at)
    age = generated_at - observed_at
    freshness = "ready" if age <= timedelta(hours=48) else "stale" if age <= timedelta(days=7) else "unavailable"
    labels = {"PSA": "10", "BGS": "Black Label", "CGC": "10", "SGC": "10"}
    for grader, label in labels.items():
        values[grader]["topGrade"] = label
        for field in ("total", "topGradePopulation"):
            if values[grader][field]["status"] == "ready" and freshness != "ready":
                values[grader][field]["status"] = freshness
                if freshness == "unavailable":
                    values[grader][field]["value"] = None
                    values[grader][field]["asOf"] = None
    return values


def candidate_rows(source_root: Path) -> list[tuple[str, Mapping[str, Any]]]:
    deduped: dict[tuple[str, str], tuple[str, Mapping[str, Any]]] = {}
    priority = {"ptcg": 2, "ptcg100": 1, "opcg": 2}
    chosen_priority: dict[tuple[str, str], int] = {}
    for market, row in iter_constituents(source_root):
        source_ref = private_source_ref(row)
        if source_ref is None:
            continue
        key = (storage_source(source_ref[0]), source_ref[1])
        if priority[market] > chosen_priority.get(key, -1):
            deduped[key] = (market, row)
            chosen_priority[key] = priority[market]
    return list(deduped.values())


def build_snapshot(
    source_root: Path,
    kado_root: Path,
    assets_out: Path | None,
    landing_root: Path | None = None,
    max_cards: int = 500,
    private_mapping: list[dict[str, Any]] | None = None,
    private_gap_report: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    generated_at = datetime.now(timezone.utc)
    ingest_at = load_ingest_at(source_root)
    price_effective_at = load_price_effective_at(source_root)
    generated_iso = iso_utc(generated_at)
    price_effective_iso = iso_utc(price_effective_at)
    price_age = generated_at - price_effective_at
    price_status = "ready" if price_age <= timedelta(hours=30) else "stale"
    replay = load_landing_replay(landing_root) if landing_root is not None else load_landing_replay(DEFAULT_LANDING)
    kado = KadoRawResolver(kado_root)
    candidates: list[dict[str, Any]] = []
    ungated_market: list[dict[str, Any]] = []
    qc_records: list[dict[str, Any]] = []
    rejected = defaultdict(int)
    stages = {
        "deduped": len(candidate_rows(source_root)),
        "populationGate": 0,
        "positiveIndexPrice": 0,
        "exactCompleteRawFront": 0,
        "finalCandidates": 0,
    }

    for market, row in candidate_rows(source_root):
        private_dir = card_source_dir(source_root, row)
        if private_dir is None:
            rejected["population"] += 1
            continue
        preflight_populations = grader_populations(private_dir[0], generated_at)
        if preflight_populations is None:
            rejected["population"] += 1
            continue
        preflight_psa = preflight_populations["PSA"]["topGradePopulation"]
        if preflight_psa["status"] not in {"ready", "stale"} or not isinstance(preflight_psa["value"], int) or preflight_psa["value"] < 1000:
            rejected["population"] += 1
            continue
        stages["populationGate"] += 1
        price = row.get("priceUsd")
        if not isinstance(price, (int, float)) or price <= 0:
            rejected["price"] += 1
            continue
        stages["positiveIndexPrice"] += 1
        source_ref = private_source_ref(row)
        expected_language = canonical_card_language(row.get("lang"))
        raw_collector = normalize_collector(row_number(row.get("name")), expected_language)
        if not raw_collector.complete:
            asset_path = private_dir[0] / "asset_info.json"
            if asset_path.is_file():
                asset_document = read_json(asset_path)
                if isinstance(asset_document, Mapping):
                    raw_collector = normalize_collector(asset_document.get("cardId"), expected_language)
        ungated_record = {
            "marketCapUsd": float(price) * int(preflight_psa["value"]),
            "tcg": "one-piece" if market == "opcg" else "pokemon",
            "name": visible_name(row.get("name")),
            "collectorNumber": {"display": raw_collector.display, "complete": raw_collector.complete},
            "sourceScope": storage_source(source_ref[0]) if source_ref else "unknown",
            "sourceId": source_ref[1] if source_ref else "unknown",
            "publicId": None,
            "rejectionReason": None,
        }
        ungated_market.append(ungated_record)
        row_rejected: dict[str, int] = {}
        resolved = resolve_identity_and_image(source_root, kado, market, row, row_rejected)
        if resolved is None:
            reason = next(iter(row_rejected), "identity_or_image_unresolved")
            rejected[reason] += 1
            ungated_record["rejectionReason"] = reason
            continue
        stages["exactCompleteRawFront"] += 1
        asset, collector, raw_image, card_dir, language = resolved
        populations = preflight_populations
        if populations is None:
            rejected["population"] += 1
            continue
        psa_population = populations["PSA"]["topGradePopulation"]
        if psa_population["status"] not in {"ready", "stale"} or not isinstance(psa_population["value"], int) or psa_population["value"] < 1000:
            rejected["population"] += 1
            continue

        tcg = "one-piece" if market == "opcg" else "pokemon"
        name = str(asset.get("cardName") or visible_name(row.get("name"))).strip()
        # The One Piece constituent label sometimes prefixes the franchise
        # inception year (2022) rather than the printing/event year. Its exact
        # asset set label is the canonical evidence for those promos.
        set_name = str(
            (asset.get("setName") if tcg == "one-piece" else row.get("setName"))
            or row.get("setName")
            or asset.get("setName")
            or ""
        ).strip()
        public_id = opaque_id(tcg, language, set_name, collector, name)
        ungated_record["publicId"] = public_id
        if private_mapping is not None:
            source_ref = private_source_ref(row)
            if source_ref is None:
                raise RuntimeError(f"missing private source reference for resolved card {public_id}")
            private_mapping.append(
                {
                    "publicId": public_id,
                    "sourceScope": storage_source(source_ref[0]),
                    "sourceId": source_ref[1],
                    "tcg": tcg,
                    "language": language,
                    "collectorNumber": collector.display,
                    "name": name,
                    "set": set_name,
                }
            )
        image_bytes, width, height = encode_raw_front(raw_image.path)
        image_hash = sha256_bytes(image_bytes)
        image_filename = f"{image_hash}.webp"
        if assets_out is not None:
            assets_out.mkdir(parents=True, exist_ok=True)
            destination = assets_out / image_filename
            if not destination.is_file():
                destination.write_bytes(image_bytes)
            elif hashlib.sha256(destination.read_bytes()).hexdigest() != image_hash:
                raise RuntimeError(f"public asset content mismatch: {destination}")
        image = {
            "src": f"/market-assets/{image_filename}",
            "sha256": image_hash,
            "kind": "raw_front",
            "width": width,
            "height": height,
            "alt": localized(name),
            "qcAt": generated_iso,
        }
        qc_records.append(
            {
                "publicId": public_id,
                "contentSha256": image_hash,
                "imageKind": "raw_front",
                "semanticMatchStatus": "metadata_exact_unreviewed",
                "cardNumberMatch": True,
                "languageMatch": True,
                "tcgMatch": True,
                "publicAllowed": True,
                "width": width,
                "height": height,
                "qcAt": generated_iso,
                "qcVersion": "raw-front-v3",
                "resolverEvidence": {
                    "collectorMatch": True,
                    "languageMetadataMatch": True,
                    "tcgMetadataMatch": True,
                    "sourceContentSha256": sha256_file(raw_image.path),
                    "method": raw_image.evidence,
                },
            }
        )
        sales, sales_observed_at = read_sales(card_dir, public_id)
        if source_ref is None:
            raise RuntimeError(f"missing source identity for {public_id}")
        daily_prices = dict(replay.prices.get(source_ref, {}))
        # A standalone/demo build still exposes one truthful current close. In
        # the unattended staging flow the just-written immutable batch already
        # supplies this date, so setdefault also preserves first-write-wins on
        # a same-day retry.
        daily_prices.setdefault(price_effective_at.date(), float(price))
        derived_changes = daily_change_metrics(daily_prices, price_status)
        windows: dict[str, Any] = {}
        for window in WINDOWS:
            if derived_changes[window]["status"] in {"ready", "stale"}:
                change = derived_changes[window]
            elif window == "30d" and isinstance(row.get("change30dPct"), (int, float)):
                change = metric(round(float(row["change30dPct"]), 6), price_status, price_effective_iso)
            else:
                change = derived_changes[window]
            windows[window] = {
                "changePct": change,
                "trackedSales": sales_metric(sales, sales_observed_at, generated_at, int(window[:-1])),
            }

        population_value = int(psa_population["value"])
        market_cap = round(float(price) * population_value, 6)
        candidates.append(
            {
                "id": public_id,
                "rank": 0,
                "tcg": tcg,
                "language": language,
                "collectorNumber": {"display": collector.display, "normalized": collector.normalized, "complete": True},
                "identityStatus": "demo_observed",
                "names": localized(name),
                "sets": localized(set_name),
                "stories": localized(),
                "image": image,
                "pricePsa10": metric(float(price), price_status, price_effective_iso),
                "populationPsa10": dict(psa_population),
                "marketCap": metric(market_cap, price_status, price_effective_iso),
                "windows": windows,
                "graderPopulations": add_population_windows(populations, source_ref, replay.grader_populations),
                "historyDaily": daily_history(daily_prices, price_status, sales),
            }
        )

    image_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in candidates:
        image_groups[card["image"]["sha256"]].append(card)
    duplicate_variant_ids: set[str] = set()
    for cards in image_groups.values():
        identities = {
            (
                card["tcg"],
                card["language"],
                card["collectorNumber"]["normalized"],
                card["sets"]["en"],
            )
            for card in cards
        }
        if len(identities) > 1:
            duplicate_variant_ids.update(card["id"] for card in cards)
    if duplicate_variant_ids:
        rejected["image_duplicate_variant_unproven"] += len(duplicate_variant_ids)
        candidates = [card for card in candidates if card["id"] not in duplicate_variant_ids]
        qc_records = [record for record in qc_records if record["publicId"] not in duplicate_variant_ids]
        for record in ungated_market:
            if record["publicId"] in duplicate_variant_ids:
                record["publicId"] = None
                record["rejectionReason"] = "image_duplicate_variant_unproven"

    stages["finalCandidates"] = len(candidates)

    candidates.sort(key=lambda card: (-float(card["marketCap"]["value"]), card["id"]))
    candidates = candidates[:max_cards]
    for rank, card in enumerate(candidates, start=1):
        card["rank"] = rank
    if private_mapping is not None:
        ranks = {card["id"]: card["rank"] for card in candidates}
        private_mapping[:] = sorted(
            ({**row, "rank": ranks[row["publicId"]]} for row in private_mapping if row["publicId"] in ranks),
            key=lambda row: row["rank"],
        )
    if len(candidates) < 100:
        raise RuntimeError(f"production gate: only {len(candidates)} exact, complete, raw-front candidates")
    top100 = candidates[:100]
    watchlist = candidates[100:500]
    ungated_market.sort(key=lambda value: (-float(value["marketCapUsd"]), str(value["sourceId"])))
    for rank, record in enumerate(ungated_market, start=1):
        record["ungatedRank"] = rank
    ungated_top100 = ungated_market[:100]
    ungated_summary = {
        "candidateCount": len(ungated_market),
        "top100MarketCapUsd": round(sum(float(value["marketCapUsd"]) for value in ungated_top100), 2),
        "top100TcgMix": {
            tcg: sum(value["tcg"] == tcg for value in ungated_top100)
            for tcg in ("pokemon", "one-piece")
        },
        "scope": "positive_index_price_and_psa10_population_at_least_1000_before_identity_and_image_gates",
    }
    if private_gap_report is not None:
        public_ids = {card["id"] for card in top100}
        missing = [record for record in ungated_top100 if record["publicId"] not in public_ids]
        promoted = [
            {
                "publicRank": card["rank"],
                "publicId": card["id"],
                "name": card["names"]["en"],
                "collectorNumber": card["collectorNumber"]["display"],
            }
            for card in top100
            if next(
                (record["ungatedRank"] for record in ungated_market if record["publicId"] == card["id"]),
                0,
            ) > 100
        ]
        private_gap_report.update(
            {
                "schemaVersion": 1,
                "generatedAt": generated_iso,
                "priceEffectiveAt": price_effective_iso,
                "ungatedTop100MarketCapUsd": ungated_summary["top100MarketCapUsd"],
                "publicGatedTop100MarketCapUsd": round(sum(card["marketCap"]["value"] for card in top100), 2),
                "missingCount": len(missing),
                "missing": missing,
                "promotedReplacementCount": len(promoted),
                "promotedReplacements": promoted,
                "note": "Private QC evidence only. The public-gated Top 100 is not equivalent to the ungated market Top 100.",
            }
        )

    change_ready = {window: sum(card["windows"][window]["changePct"]["status"] == "ready" for card in top100) for window in WINDOWS}
    sales_ready = {window: sum(card["windows"][window]["trackedSales"]["valueUsd"]["status"] == "ready" for card in top100) for window in WINDOWS}
    grader_ready = {
        grader: sum(card["graderPopulations"][grader]["topGradePopulation"]["status"] == "ready" for card in top100)
        for grader in GRADERS
    }
    grader_change_ready = {
        grader: {
            window: sum(
                card["graderPopulations"][grader]["topGradePopulationChangePct"][window]["status"] == "ready"
                for card in top100
            )
            for window in WINDOWS
        }
        for grader in GRADERS
    }
    localized_story_count = {locale: sum(bool(card["stories"][locale]) for card in top100) for locale in LOCALES}
    snapshot: dict[str, Any] = {
        "schemaVersion": "2.0.0",
        "generation": {
            # A generation is an immutable derivation, not merely a source
            # observation date. Rebuilding the same daily close after a code or
            # QC correction must publish under a new key instead of overwriting
            # an earlier generation that may still be needed for rollback.
            "id": f"daily_{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}",
            "generatedAt": generated_iso,
            # Freshness is evaluated against the actual derivation run, never
            # against the price observation itself. Otherwise an expired price
            # could appear age zero after a mode/eligibility flip.
            "effectiveAt": generated_iso,
            "contentSha256": "",
            "mode": "demo",
            "productionEligible": False,
            "blockers": [
                "canonical_identity_review_pending",
                "image_semantic_qc_review_pending",
                "grader_supply_universe_pending",
                "four_locale_editorial_review_pending",
                "currency_rate_feed_pending",
                "production_database_cutover_pending",
                *( ["price_reference_over_48h"] if price_age > timedelta(hours=48) else [] ),
            ],
        },
        "universe": {
            "populationMin": 1000,
            "grade": "PSA 10",
            "rankingMetric": "psa10_market_cap_usd",
            "windows": list(WINDOWS),
            "salesCoverage": "partial",
        },
        "coverage": {
            "top100Count": len(top100),
            "watchlistCount": len(watchlist),
            "changeReady": change_ready,
            "salesReady": sales_ready,
            "graderPopulationReady": grader_ready,
            "graderPopulationChangeReady": grader_change_ready,
            # Coverage describes the published Top 100, not every candidate.
            # Demo rows remain deliberately unconfirmed until the JLP exact
            # identity gate has accepted them.
            "completeIdentityCount": sum(card["identityStatus"] == "confirmed" for card in top100),
            "localizedStoryCount": localized_story_count,
        },
        "currencies": {
            "base": "USD",
            "supported": ["USD", "HKD", "CNY", "GBP", "TWD"],
            "rates": {
                "USD": metric(1, "ready", generated_iso),
                "HKD": metric(None, "unavailable", None),
                "CNY": metric(None, "unavailable", None),
                "GBP": metric(None, "unavailable", None),
                "TWD": metric(None, "unavailable", None),
            },
            "asOf": generated_iso,
        },
        "top100": top100,
        "watchlist": watchlist,
    }
    snapshot["generation"]["contentSha256"] = sha256_bytes(stable_json(snapshot))
    manifest = {
        "schemaVersion": "2.0.0",
        "generatedAt": generated_iso,
        "priceEffectiveAt": price_effective_iso,
        "marketUniverseBeforePublicGates": ungated_summary,
        "publishableSubset": {
            "candidateCount": len(candidates),
            "top100MarketCapUsd": round(sum(card["marketCap"]["value"] for card in top100), 2),
            "top100TcgMix": {
                tcg: sum(card["tcg"] == tcg for card in top100)
                for tcg in ("pokemon", "one-piece")
            },
        },
        "landingReplay": {
            "batchCount": replay.batch_count,
            "acceptedPriceAnchors": replay.accepted_price_anchors,
            "acceptedPopulationAnchors": replay.accepted_population_anchors,
            "ignoredSameDateRetries": replay.ignored_same_date_retries,
        },
        "rejected": dict(rejected),
        "records": sorted(qc_records, key=lambda record: record["publicId"]),
    }
    summary = {
        "top100": len(top100),
        "watchlist": len(watchlist),
        "minimumPopulation": min(card["populationPsa10"]["value"] for card in top100),
        "marketCapUsd": round(sum(card["marketCap"]["value"] for card in top100), 2),
        "marketUniverseBeforePublicGates": ungated_summary,
        "tcgMix": {tcg: sum(card["tcg"] == tcg for card in top100) for tcg in ("pokemon", "one-piece")},
        "incompleteCollectorNumbers": sum(not card["collectorNumber"]["complete"] for card in top100),
        "nonRawImages": sum(card["image"]["kind"] != "raw_front" for card in top100),
        "privateTokens": sum(token in stable_json(snapshot).decode("utf-8").casefold() for token in PRIVATE_TOKENS),
        "changeReady": change_ready,
        "salesReady": sales_ready,
        "graderPopulationReady": grader_ready,
        "graderPopulationChangeReady": grader_change_ready,
        "landingReplay": {
            "batchCount": replay.batch_count,
            "acceptedPriceAnchors": replay.accepted_price_anchors,
            "acceptedPopulationAnchors": replay.accepted_population_anchors,
            "ignoredSameDateRetries": replay.ignored_same_date_retries,
        },
        "stages": stages,
        "priceEffectiveAt": price_effective_iso,
        "ingestAt": iso_utc(ingest_at),
        "priceStatus": price_status,
        "japanesePromoLanguageMismatches": sum(
            bool(re.search(r"/(?:ADV-P|PCG-P|DP-P|DPT-P|BW-P|XY-P|SM-P|SV-P|S-P)$", card["collectorNumber"]["display"], re.IGNORECASE))
            and card["language"] != "ja"
            for card in candidates
        ),
        "englishSvpCollector": normalize_collector("SVP EN 085").display,
        "englishMepCollectors": sorted(
            card["collectorNumber"]["display"]
            for card in candidates
            if card["language"] == "en" and card["collectorNumber"]["display"].endswith("/MEP")
        ),
        "collectorQc": {
            str(card["rank"]): card["collectorNumber"]["display"]
            for card in top100
            if card["rank"] in {30, 32, 43, 45, 48, 51, 52, 53, 55, 60, 62, 72, 83, 88, 94}
        },
        "promoCollectorQc": {
            str(card["rank"]): card["collectorNumber"]["display"]
            for card in top100
            if card["rank"] in {1, 3, 4, 7, 9, 19, 21, 27, 36, 37, 38, 44, 63, 70, 77, 100}
        },
        "quarantinedDuplicateVariantImageGroups": sum(
            len(cards) > 1
            for cards in image_groups.values()
            if len(
                {
                    (card["tcg"], card["language"], card["collectorNumber"]["normalized"], card["sets"]["en"])
                    for card in cards
                }
            ) > 1
        ),
        "publicDuplicateVariantImageGroups": sum(
            len({card["id"] for card in cards}) > 1
            for cards in (
                [card for card in candidates if card["image"]["sha256"] == image_hash]
                for image_hash in {card["image"]["sha256"] for card in candidates}
            )
        ),
        "rejected": dict(rejected),
    }
    return snapshot, manifest, summary


def quarantine_unreferenced_assets(assets_out: Path, snapshot: Mapping[str, Any], quarantine_root: Path) -> int:
    if not assets_out.is_dir():
        return 0
    expected = {
        Path(card["image"]["src"]).name
        for card in [*snapshot["top100"], *snapshot["watchlist"]]
    }
    stale = [path for path in assets_out.iterdir() if path.is_file() and path.name not in expected]
    if not stale:
        return 0
    resolved_assets = assets_out.resolve()
    if resolved_assets.name != "market-assets" or resolved_assets.parent.name != "public":
        raise RuntimeError(f"refusing to quarantine unexpected asset directory: {resolved_assets}")
    quarantine_root.mkdir(parents=True, exist_ok=True)
    for path in stale:
        shutil.move(str(path), str(quarantine_root / path.name))
    return len(stale)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--kado-root", type=Path, default=DEFAULT_KADO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--assets-out", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--manifest-out", type=Path, default=DEFAULT_IMAGE_MANIFEST)
    parser.add_argument("--landing-root", type=Path, default=DEFAULT_LANDING)
    parser.add_argument("--private-mapping-out", type=Path)
    parser.add_argument("--private-gap-report-out", type=Path)
    parser.add_argument("--clean-assets", action="store_true")
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    kado_root = args.kado_root.resolve()
    if not source_root.is_dir():
        raise SystemExit(f"source root does not exist: {source_root}")
    if not kado_root.is_dir():
        raise SystemExit(f"Kado root does not exist: {kado_root}")
    private_mapping: list[dict[str, Any]] | None = [] if args.private_mapping_out else None
    private_gap_report: dict[str, Any] | None = {} if args.private_gap_report_out else None
    snapshot, manifest, summary = build_snapshot(
        source_root,
        kado_root,
        None if args.self_test else args.assets_out.resolve(),
        landing_root=args.landing_root.resolve(),
        private_mapping=private_mapping,
        private_gap_report=private_gap_report,
    )
    if args.self_test:
        print(json.dumps(summary, sort_keys=True))
        return
    write_json(args.output.resolve(), snapshot)
    write_json(args.manifest_out.resolve(), manifest)
    if args.private_mapping_out:
        mapping_output = args.private_mapping_out.resolve()
        runtime_root = (ROOT / "data" / "runtime").resolve()
        if runtime_root not in mapping_output.parents:
            raise RuntimeError(f"private mapping must stay under {runtime_root}")
        write_json(mapping_output, {"schemaVersion": 1, "top100": (private_mapping or [])[:100]})
    if args.private_gap_report_out:
        gap_output = args.private_gap_report_out.resolve()
        runtime_root = (ROOT / "data" / "runtime").resolve()
        if runtime_root not in gap_output.parents:
            raise RuntimeError(f"private gap report must stay under {runtime_root}")
        write_json(gap_output, private_gap_report or {})
    quarantined = 0
    if args.clean_assets:
        quarantine = ROOT / "data" / "runtime" / "private-quarantine" / "legacy-public-assets" / snapshot["generation"]["id"]
        quarantined = quarantine_unreferenced_assets(args.assets_out.resolve(), snapshot, quarantine)
    print(json.dumps({**summary, "output": str(args.output.resolve()), "quarantinedAssets": quarantined}, sort_keys=True))


if __name__ == "__main__":
    main()
