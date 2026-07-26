import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execute = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
// Ubuntu 24.04 冇 /usr/bin/python，Windows 嘅 python3 又係 Store 假 alias，所以兩邊各用各嘅名。
const PYTHON = process.env.CARDZ_PYTHON ?? (process.platform === "win32" ? "python" : "python3");

const behavioralProbe = String.raw`
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "pipelines"))
market_source_sync = types.ModuleType("market_source_sync")
market_source_sync.DEFAULT_GEMRATE = Path("unused")
market_source_sync.DEFAULT_LANDING = Path("unused")
market_source_sync.deduped_rows = lambda _root: {}
market_source_sync.gemrate_populations = lambda *_args: {}
market_source_sync.g10_psa_population = lambda *_args: None
market_source_sync.latest = lambda *_args: None
sys.modules["market_source_sync"] = market_source_sync
import active_universe as active

now = datetime(2026, 7, 23, tzinfo=timezone.utc)
active.load_landing_replay = lambda _root: {}
active.deduped_rows = lambda _root: {
    ("ptcg", "asset-1"): (
        "ptcg",
        {"lang": "en", "setName": "English market row", "priceUsd": 10},
    )
}
active.choose_population = lambda *_args: (1500, now, "official_snapshot")
active.price_metrics = lambda *_args: (
    10.0,
    now,
    "canonical_daily",
    {"1d": 1.0, "7d": 2.0, "30d": 3.0},
)

crosswalk = {
    "cards": [
        {
            "canonicalSourceCode": "ptcg",
            "canonicalExternalId": "asset-1",
            "gemrateId": "gemrate-1",
            "snkItemId": 1,
            "language": "ja",
            "name": "Pikachu",
            "setName": "Japanese exact asset",
            "collectorNumberRaw": "001/100",
        }
    ]
}
enriched, rejected = active.enrich_candidates(Path("unused"), crosswalk, Path("unused"), Path("unused"), now)

rows = []
for index in range(30):
    rows.append(
        {
            "pokedexId": f"core-{index:03d}",
            "populationPsa10": 1000 + index,
            "marketCapUsd": 100000 - index,
            "change7dPct": 0.0,
            "change30dPct": 0.0,
        }
    )
for index in range(300):
    rows.append(
        {
            "pokedexId": f"watch-{index:03d}",
            "populationPsa10": 900,
            "marketCapUsd": 50000 - index,
            "change7dPct": 1.0,
            "change30dPct": 2.0,
        }
    )
selected = active.select_segment(rows, top_limit=100, segment_limit=300, near_population=700)

base_card = {
    "pokedexId": "card-1",
    "canonicalSourceCode": "ptcg",
    "canonicalExternalId": "asset-1",
    "tcg": "pokemon",
    "language": "ja",
    "segment": "pokemon:ja",
    "role": "top100",
}

def validation_error(**changes):
    card = {**base_card, **changes}
    document = {"cards": [card], "payloadSha256": active.stable_hash([card])}
    try:
        active.validate_active_universe(document, require_lock=False)
    except ValueError as error:
        return str(error)
    raise AssertionError("invalid active-universe member was accepted")

print(
    json.dumps(
        {
            "sourceLanguage": enriched[0]["language"],
            "sourceSegment": enriched[0]["segment"],
            "rejected": dict(rejected),
            "coreCount": sum(row["role"] == "top100" for row in selected),
            "watchCount": sum(row["role"] == "watchlist" for row in selected),
            "totalCount": len(selected),
            "errors": {
                "segment": validation_error(segment="pokemon:en"),
                "language": validation_error(language="th", segment="pokemon:th"),
                "role": validation_error(role="candidate"),
            },
        },
        sort_keys=True,
    )
)
`;

test("active universe trusts exact crosswalk language and caps a short-core watchlist at 200", async () => {
  const { stdout } = await execute(PYTHON, ["-c", behavioralProbe], { cwd: root });
  const result = JSON.parse(stdout.trim());

  assert.equal(result.sourceLanguage, "ja");
  assert.equal(result.sourceSegment, "pokemon:ja");
  assert.deepEqual(result.rejected, {});
  assert.equal(result.coreCount, 30);
  assert.equal(result.watchCount, 200);
  assert.equal(result.totalCount, 230);
});

test("active universe rejects invalid segment, language, and role", async () => {
  const { stdout } = await execute(PYTHON, ["-c", behavioralProbe], { cwd: root });
  const { errors } = JSON.parse(stdout.trim());

  assert.match(errors.segment, /invalid language segment/);
  assert.match(errors.language, /invalid language segment/);
  assert.match(errors.role, /invalid member role/);
});
