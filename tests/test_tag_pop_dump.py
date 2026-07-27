# -*- coding: utf-8 -*-
"""TAG catalog dump degradation contract.

One unusable set must never abort the multi-year crawl (the 2026-07-22..07-26
outage), and a systemically broken set index must never be promoted silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import tag_pop_data  # noqa: E402


class FakeClient:
    """Minimal TagClient stand-in driven by a declarative set index."""

    def __init__(self, sets_by_year, *, cards_per_set=40, failing=()):
        self.sets_by_year = sets_by_year
        self.cards_per_set = cards_per_set
        self.failing = set(failing)
        self.card_calls = []

    def pops_by_year(self, category):
        return [{"cardYear": year, "totalGraded": 10} for year in self.sets_by_year]

    def pops_by_set(self, category, year):
        return self.sets_by_year[str(year)]

    def pops_by_card(self, category, year, brand_name, set_name):
        self.card_calls.append((str(year), brand_name, set_name))
        if (str(year), brand_name, set_name) in self.failing:
            raise RuntimeError("upstream 500")
        return [
            {"cardName": f"{brand_name}-{set_name}-{index}", "cardNumber": str(index), "variation": "", "grades": {"10": 1}}
            for index in range(self.cards_per_set)
        ]


def build_index(years, sets_per_year, *, unnamed=0, brandless=0):
    index = {}
    for year in years:
        rows = [{"brandName": f"Brand {year}-{i}", "cardSetName": f"Set {year}-{i}"} for i in range(sets_per_year)]
        for i in range(unnamed):
            rows.append({"brandName": f"Promo {year}-{i}", "cardSetName": ""})
        for i in range(brandless):
            rows.append({"brandName": "", "cardSetName": f"Orphan {year}-{i}"})
        index[str(year)] = rows
    return index


@pytest.fixture
def patched(monkeypatch):
    def install(client):
        monkeypatch.setattr(tag_pop_data, "TagClient", lambda delay=0.0: client)
        return client

    return install


def test_unnamed_set_is_crawled_not_dropped(tmp_path, patched):
    """An empty cardSetName is real TAG promo data; dropping it is silent loss."""

    client = patched(FakeClient(build_index(range(1997, 2007), 3, unnamed=1)))
    result = tag_pop_data.dump_fresh("Pokémon", tmp_path / "catalog.jsonl", 0.0)

    assert result["totalSets"] == 40
    assert result["unnamedSets"] == 10
    assert result["unusableSets"] == 0
    assert result["rows"] == 40 * 40
    assert ("1997", "Promo 1997-0", "") in client.card_calls
    written = (tmp_path / "catalog.jsonl").read_text(encoding="utf-8")
    assert '"setName":""' in written.replace(" ", "")


def test_one_broken_set_does_not_abort_the_dump(tmp_path, patched, capsys):
    """The 2026-07-22 outage: a single bad row aborted every other year."""

    index = build_index(range(1997, 2007), 15)
    index["1997"].append({"brandName": "", "cardSetName": ""})
    failing = {("2003", "Brand 2003-1", "Set 2003-1")}
    patched(FakeClient(index, failing=failing))

    result = tag_pop_data.dump_fresh("Pokémon", tmp_path / "catalog.jsonl", 0.0)

    assert result["totalSets"] == 151
    assert result["unusableSets"] == 2
    assert result["rows"] == 149 * 40  # every other year still crawled
    warnings = capsys.readouterr().err
    assert "WARN skipping set year=1997" in warnings
    assert "field=brandName" in warnings
    assert "WARN skipping set year=2003" in warnings
    assert "upstream 500" in warnings


def test_degraded_set_index_fails_instead_of_promoting_a_partial_catalog(tmp_path, patched):
    """A 5% unusable index is a schema break, not normal attrition."""

    patched(FakeClient(build_index(range(1997, 2007), 19, brandless=1)))

    with pytest.raises(RuntimeError) as error:
        tag_pop_data.dump_fresh("Pokémon", tmp_path / "catalog.jsonl", 0.0)

    assert "degraded beyond tolerance" in str(error.value)
    assert "10/200" in str(error.value)
    assert not (tmp_path / "catalog.jsonl").exists()


def test_threshold_boundary_tolerates_measured_attrition(tmp_path, patched):
    """2026-07-26 live index: 2637 sets, 28 unnamed, 0 unusable -> must pass."""

    assert tag_pop_data.TAG_MAX_UNUSABLE_SET_RATIO == 0.02
    patched(FakeClient(build_index(range(1997, 2007), 49, brandless=1)))

    result = tag_pop_data.dump_fresh("Pokémon", tmp_path / "catalog.jsonl", 0.0)

    assert result["unusableSets"] == 10
    assert result["totalSets"] == 500
