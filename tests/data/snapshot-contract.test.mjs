import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { publicSnapshotContentSha256, validatePublicSnapshot } from "../../packages/market-data/dist/index.js";
import { RELEASE_POLICY_PROFILES } from "../../packages/market-data/dist/release-policy.generated.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function sortObject(value) {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortObject(value[key])]));
  }
  return value;
}

async function snapshot() {
  return JSON.parse(await readFile(path.join(root, "data/public/seed-snapshot.json"), "utf8"));
}

function latestObservedAt(value) {
  let latest = -Infinity;
  const visit = (entry) => {
    if (Array.isArray(entry)) {
      entry.forEach(visit);
      return;
    }
    if (!entry || typeof entry !== "object") return;
    for (const [key, nested] of Object.entries(entry)) {
      if ((key === "asOf" || key === "at") && typeof nested === "string") {
        const timestamp = Date.parse(nested);
        if (Number.isFinite(timestamp)) latest = Math.max(latest, timestamp);
      }
      visit(nested);
    }
  };
  visit(value);
  return latest;
}

function expectedJapanesePromoNamespace(setName) {
  for (const namespace of ["SV-P", "SM-P", "XY-P", "BW-P", "DPt-P", "DP-P", "PCG-P", "ADV-P", "S-P"]) {
    if (new RegExp(`(^|[^A-Z])${namespace.replace("-", "\\-")}([^A-Z]|$)`, "i").test(setName)) return namespace;
  }
  if (!/promo|campaign|special box|stamp box|master battle|munch/i.test(setName)) return null;
  if (/sword and shield/i.test(setName)) return "S-P";
  if (/scarlet and violet/i.test(setName)) return "SV-P";
  if (/sun and moon/i.test(setName)) return "SM-P";
  if (/\bxy\b/i.test(setName)) return "XY-P";
  return null;
}

test("demo seed satisfies the public data contract", async () => {
  const value = await snapshot();
  assert.deepEqual(validatePublicSnapshot(value), []);
  assert.equal(value.schemaVersion, "2.0.0");
  // effectiveAt (the day the index represents) and generatedAt (wall clock at export) are distinct
  // concepts, so they are not asserted equal: HEAD's equality was an artifact of a hand-built demo
  // fixture, and restoring it would require lying in one field or the other. The invariants that
  // actually catch bugs are ordering plus a bounded publish lag. 48h matches the price freshness
  // SLA enforced in packages/market-data/src/validate.ts.
  const publishLagHours = (Date.parse(value.generation.generatedAt) - Date.parse(value.generation.effectiveAt)) / 3_600_000;
  assert.ok(publishLagHours >= 0, `generatedAt precedes effectiveAt by ${-publishLagHours}h`);
  assert.deepEqual(value.universe.windows, ["1d", "7d", "30d"]);
  assert.equal(value.top100.length, 100);
  assert.deepEqual(
    {
      claim: value.coverage.claim,
      requestedCount: value.coverage.requestedCount,
      verifiedCount: value.coverage.verifiedCount,
    },
    { claim: "verified-top-n", requestedCount: 100, verifiedCount: 0 },
  );
  assert.equal(value.generation.qcReceiptSha256, "");
  assert.equal(value.generation.mode, "demo");
  assert.equal(value.generation.productionEligible, false);
  assert.ok(value.generation.blockers.includes("strict_qc_receipt_missing"));
  assert.ok(value.top100.every((card) => card.rank === card.viewRank));
  assert.ok(value.top100.every((card) => Number.isInteger(card.marketRank) && card.marketRank > 0));
  assert.ok(value.top100.every((card) => card.populationPsa10.value >= 1000));
  assert.ok(value.top100.every((card) => card.populationPsa10.estimated === false));
  assert.ok(value.top100.every((card) => ["ready", "stale"].includes(card.pricePsa10.status)));
  assert.ok(value.top100.every((card) => card.collectorNumber.complete));
  assert.ok(value.top100.every((card) => card.image.kind === "raw_front"));
  assert.ok(value.top100.every((card) => card.image.src.startsWith("/market-assets/")));
  assert.ok(value.top100.every((card) => ["1d", "7d", "30d"].every((window) => window in card.windows)));
  assert.ok(value.top100.every((card) => ["PSA", "BGS", "CGC", "SGC"].every((grader) => grader in card.graderPopulations)));
  assert.ok(value.top100.every((card) => Object.values(card.graderPopulations).every((grader) =>
    ["1d", "7d", "30d"].every((window) => window in grader.topGradePopulationChangePct))));
  assert.ok(value.top100.every((card) => !("change30dPct" in card) && !("sales7d" in card)));
  assert.ok(value.top100.every((card) => !/\/(?:ADV-P|PCG-P|DP-P|DPT-P|BW-P|XY-P|SM-P|SV-P|S-P)$/i.test(card.collectorNumber.display) || card.language === "ja"));
  assert.ok(value.top100.every((card) => !/\/(?:SVP|MEP)$/.test(card.collectorNumber.display) || card.language === "en"));
  assert.ok(value.top100.every((card) => card.tcg !== "pokemon" || card.language !== "ja" || !/^\d+\/\d+$/.test(card.collectorNumber.display) || /^\d{3}\/\d{3}$/.test(card.collectorNumber.display)));
  const cards = [...value.top100, ...value.watchlist];
  let promoNamespaceChecks = 0;
  for (const card of cards) {
    if (card.tcg !== "pokemon" || card.language !== "ja") continue;
    const expected = expectedJapanesePromoNamespace(card.sets.en ?? "");
    if (expected === null) continue;
    promoNamespaceChecks += 1;
    assert.ok(card.collectorNumber.display.toLowerCase().endsWith(`/${expected.toLowerCase()}`), `${card.rank} ${card.collectorNumber.display} does not match ${expected}`);
  }
  // Every collector-number rule above and in validate.ts is conditional, so all of them pass
  // vacuously the moment the pipeline stops emitting a card family. HEAD guarded that with pinned
  // cards ("023/MEP") and a pinned rank -> collector-number map, which is exactly why they broke
  // when the catalog was rebuilt: they asserted which cards the seed happens to hold, not that the
  // rules stay live. These assert the fixture still exercises each rule, naming no card or rank.
  const displays = cards.map((card) => card.collectorNumber.display);
  assert.ok(
    displays.some((display) => /\/(?:ADV-P|PCG-P|DP-P|DPT-P|BW-P|XY-P|SM-P|SV-P|S-P)$/i.test(display)),
    "no Japanese promo-namespace card remains, so the namespace/language rule is vacuous",
  );
  assert.ok(
    displays.some((display) => /\/(?:SVP|MEP)$/.test(display)),
    "no English promo-namespace card remains, so the namespace/language rule is vacuous",
  );
  assert.ok(
    cards.some((card) => card.tcg === "pokemon" && card.language === "ja" && /^\d+\/\d+$/.test(card.collectorNumber.display)),
    "no Japanese plain set number remains, so the three-digit padding rule is vacuous",
  );
  assert.ok(
    promoNamespaceChecks > 0,
    "no Japanese promo set name remains, so the namespace/set-name cross-check is vacuous",
  );
  // This tracked file is deliberately a blocked demo candidate. Duplicate
  // artwork remains evidence for strict QC; it is not rewritten or presented
  // as a verified release fixture here.
});

test("content hash is deterministic across runtimes", async () => {
  const value = await snapshot();
  const expected = value.generation.contentSha256;
  value.generation.contentSha256 = "";
  const actual = createHash("sha256").update(JSON.stringify(sortObject(value))).digest("hex");
  assert.equal(actual, expected);
});

test("unready metrics never contain a fake zero", async () => {
  const value = await snapshot();
  for (const card of [...value.top100, ...value.watchlist]) {
    const windowMetrics = Object.values(card.windows).flatMap((window) => [
      window.changePct,
      window.trackedSales.valueUsd,
      window.trackedSales.count,
    ]);
    const populationMetrics = Object.values(card.graderPopulations).flatMap((grader) => [
      grader.total,
      grader.topGradePopulation,
      ...Object.values(grader.topGradePopulationChangePct),
    ]);
    for (const metric of [...windowMetrics, ...populationMetrics]) {
      if (metric.status === "accumulating" || metric.status === "unavailable") {
        assert.equal(metric.value, null);
      }
    }
  }
});

// 由 demo seed 砌一份**真係乾淨**嘅 production candidate。
//
// 下面幾個測試嘅套路都係「clean 應該零 error → 髒一個欄應該爆嗰個 error」。
// 如果 clean 本身唔乾淨，第一句 assert 就死，後面嘅判別力等於冇測過。
// 舊版只翻咗 generation 三個欄，卡層面照舊 demo_observed + 價格 81.8h 舊，
// 所以 identity / freshness 兩個測試喺 HEAD 一直係紅嘅。
//
// generation 以外仲要洗嘅，就係 `validate.ts` production 段查嘅卡層面欄位：
// identityStatus、pricePsa10 48h、populationPsa10 168h。asOf 直接對齊
// effectiveAt（age = 0）——測試要嘅係「唔逾期」，唔係模擬真實延遲；真延遲
// 由後面 withinSla / stale 兩個 case 自己砌。
async function productionCandidate() {
  const value = await snapshot();
  const effectiveAt = latestObservedAt(value);
  value.generation.generatedAt = new Date(effectiveAt).toISOString();
  value.generation.effectiveAt = new Date(effectiveAt).toISOString();
  value.generation.mode = "production";
  value.generation.productionEligible = true;
  value.generation.blockers = [];
  value.generation.qcReceiptSha256 = "f".repeat(64);
  value.generation.dbQc = {
    runId: "daily_fixture_20260731",
    receiptSha256: "b".repeat(64),
    database: "cardz_market_cap",
    universeCandidateSha256: "a".repeat(64),
  };
  value.generation.releaseProfile = "strict-v1";
  value.generation.policySha256 = RELEASE_POLICY_PROFILES["strict-v1"].policySha256;
  value.generation.dbFingerprint = "d".repeat(64);
  value.generation.evaluationId = 1;
  value.coverage.claim = value.top100.length === 100 ? "verified-top-100" : "verified-top-n";
  value.coverage.verifiedCount = value.top100.length;
  for (const card of [...value.top100, ...value.watchlist]) {
    card.identityStatus = "confirmed";
    const cardLanguage = card.cardLanguage === "ja" || card.cardLanguage === "ko"
      || card.cardLanguage === "zhCN" || card.cardLanguage === "zhTW"
      ? card.cardLanguage
      : "en";
    card.cardLanguage = cardLanguage;
    const printingIdentity = {
      setName: card.sets.en,
      collectorNumber: card.collectorNumber.normalized,
      editionCode: "fixture-edition",
      parallelCode: "fixture-parallel",
      finishCode: "fixture-finish",
      cardLanguage,
      evidenceSha256: "e".repeat(64),
    };
    // 7-part: tcg|lang|set|collector|edition|parallel|finish (lang keeps canonical casing)
    const printingKey = [
      String(card.tcg ?? "").trim().toLocaleLowerCase(),
      cardLanguage,
      String(printingIdentity.setName ?? "").trim().toLocaleLowerCase(),
      String(printingIdentity.collectorNumber ?? "").trim().toLocaleLowerCase(),
      String(printingIdentity.editionCode ?? "").trim().toLocaleLowerCase(),
      String(printingIdentity.parallelCode ?? "").trim().toLocaleLowerCase(),
      String(printingIdentity.finishCode ?? "").trim().toLocaleLowerCase(),
    ].join("|");
    card.printingIdentity = {
      ...printingIdentity,
      canonicalPrintingSha256: createHash("sha256").update(printingKey).digest("hex"),
    };
    for (const metric of [card.pricePsa10, card.populationPsa10]) {
      if (metric?.asOf) metric.asOf = value.generation.effectiveAt;
    }
    for (const population of Object.values(card.graderPopulations)) {
      for (const metric of [population.total, population.topGradePopulation]) {
        if (metric?.asOf) metric.asOf = value.generation.effectiveAt;
      }
    }
  }
  for (const card of value.top100) {
    const sales = card.windows["30d"].trackedSales;
    sales.coverage = "partial";
    sales.asOf = value.generation.effectiveAt;
    sales.valueUsd = {
      value: typeof sales.valueUsd.value === "number" && sales.valueUsd.value > 0 ? sales.valueUsd.value : 1,
      status: "ready",
      asOf: value.generation.effectiveAt,
    };
    sales.count = {
      value: typeof sales.count.value === "number" && sales.count.value > 0 ? sales.count.value : 1,
      status: "ready",
      asOf: value.generation.effectiveAt,
    };
  }
  value.generation.contentSha256 = publicSnapshotContentSha256(value);
  return value;
}

async function relaxedProductionCandidate() {
  const value = await productionCandidate();
  value.generation.releaseProfile = "relaxed-launch-v1";
  value.generation.policySha256 = RELEASE_POLICY_PROFILES["relaxed-launch-v1"].policySha256;
  value.coverage.verifiedCount = value.top100.length + value.watchlist.length;
  for (const card of [...value.top100, ...value.watchlist]) {
    card.identityStatus = "provisional";
    card.windows["30d"].trackedSales = {
      valueUsd: { value: 1, status: "ready", asOf: value.generation.effectiveAt },
      count: { value: 5, status: "ready", asOf: value.generation.effectiveAt },
      coverage: "partial",
      asOf: value.generation.effectiveAt,
    };
  }
  return reseal(value);
}

function reseal(value) {
  value.generation.contentSha256 = publicSnapshotContentSha256(value);
  return value;
}

test("non-production candidate may remain unverified until strict QC", async () => {
  const value = await productionCandidate();
  value.generation.mode = "demo";
  value.generation.productionEligible = false;
  value.generation.blockers = ["strict_qc_receipt_missing"];
  value.generation.qcReceiptSha256 = "";
  value.coverage.claim = "verified-top-n";
  value.coverage.verifiedCount = 0;
  const errors = validatePublicSnapshot(reseal(value));
  for (const forbidden of [
    "generation QC receipt hash is invalid",
    "coverage.verifiedCount must equal top100 length",
    "coverage.verifiedCount must be between 0 and top100 length",
    "coverage.claim must equal verified-top-n",
  ]) {
    assert.ok(!errors.includes(forbidden), forbidden);
  }
});

test("production validation blocks a demo-mode seed from release", async () => {
  const value = await snapshot();
  const errors = validatePublicSnapshot(value, { production: true });
  assert.ok(errors.some((error) => error.includes("generation mode")));
  assert.ok(errors.some((error) => error.includes("release blocked")));
});

test("production validation rejects unconfirmed identity", async () => {
  const clean = await productionCandidate();
  assert.ok(!validatePublicSnapshot(clean, { production: true }).some((error) => error.includes("identity is not confirmed")));

  const value = structuredClone(clean);
  value.top100[0].identityStatus = "demo_observed";
  value.watchlist[0].identityStatus = "demo_observed";
  const errors = validatePublicSnapshot(reseal(value), { production: true });
  assert.ok(errors.includes("top100[0] identity is not confirmed"));
  assert.ok(errors.includes("watchlist[0] identity is not confirmed"));
});

test("production validation requires a complete hash-bound printing identity", async () => {
  const clean = await productionCandidate();
  assert.ok(
    !validatePublicSnapshot(clean, { production: true })
      .some((error) => error.includes("printingIdentity")),
  );

  const missing = structuredClone(clean);
  delete missing.top100[0].printingIdentity;
  assert.ok(
    validatePublicSnapshot(reseal(missing), { production: true })
      .includes("top100[0].printingIdentity is required for production"),
  );

  const incomplete = structuredClone(clean);
  incomplete.top100[0].printingIdentity.finishCode = "";
  const errors = validatePublicSnapshot(reseal(incomplete), { production: true });
  assert.ok(errors.includes("top100[0].printingIdentity.finishCode is required"));
  assert.ok(errors.includes("top100[0].printingIdentity canonical hash is inconsistent"));

  const languageMissing = structuredClone(clean);
  const printing = languageMissing.top100[0].printingIdentity;
  delete languageMissing.top100[0].cardLanguage;
  delete printing.cardLanguage;
  const languageMissingKey = [
    String(languageMissing.top100[0].tcg ?? "").trim().toLocaleLowerCase(),
    "",
    String(printing.setName ?? "").trim().toLocaleLowerCase(),
    String(printing.collectorNumber ?? "").trim().toLocaleLowerCase(),
    String(printing.editionCode ?? "").trim().toLocaleLowerCase(),
    String(printing.parallelCode ?? "").trim().toLocaleLowerCase(),
    String(printing.finishCode ?? "").trim().toLocaleLowerCase(),
  ].join("|");
  printing.canonicalPrintingSha256 = createHash("sha256")
    .update(languageMissingKey)
    .digest("hex");
  const languageErrors = validatePublicSnapshot(
    reseal(languageMissing),
    { production: true },
  );
  assert.ok(
    languageErrors.includes("top100[0].cardLanguage is required for production"),
  );
  assert.ok(
    languageErrors.includes(
      "top100[0].printingIdentity.cardLanguage is required for production",
    ),
  );
});

test("production validation measures price freshness from the run effective time", async () => {
  const clean = await productionCandidate();
  const effectiveAt = Date.parse(clean.generation.effectiveAt);
  assert.ok(!validatePublicSnapshot(clean, { production: true }).some((error) => error.includes("price exceeds 48h freshness SLA")));

  const withinSla = structuredClone(clean);
  withinSla.top100[0].pricePsa10.asOf = new Date(effectiveAt - 24 * 3_600_000).toISOString();
  assert.ok(!validatePublicSnapshot(reseal(withinSla), { production: true }).some((error) => error.includes("price exceeds 48h freshness SLA")));

  const stale = structuredClone(clean);
  stale.top100[0].pricePsa10.asOf = new Date(effectiveAt - 72 * 3_600_000).toISOString();
  const errors = validatePublicSnapshot(reseal(stale), { production: true });
  assert.ok(errors.includes("top100[0] price exceeds 48h freshness SLA"));
});

test("validation rejects metrics dated after generation.effectiveAt", async () => {
  const value = await productionCandidate();
  value.top100[0].pricePsa10.asOf = new Date(
    Date.parse(value.generation.effectiveAt) + 60_000,
  ).toISOString();
  const errors = validatePublicSnapshot(reseal(value), { production: true });
  assert.ok(errors.includes("top100[0].pricePsa10.asOf is after generation.effectiveAt"));
});

test("production validation requires at least ten 30d PSA10 tracked sales", async () => {
  const value = await productionCandidate();
  value.top100[0].windows["30d"].trackedSales = {
    valueUsd: { value: null, status: "unavailable", asOf: null },
    count: { value: null, status: "unavailable", asOf: null },
    coverage: "unavailable",
    asOf: null,
  };
  const errors = validatePublicSnapshot(reseal(value), { production: true });
  assert.ok(errors.includes("top100[0] requires at least 10 pure PSA10 tracked sales in 30d"));

  const insufficient = await productionCandidate();
  insufficient.top100[0].windows["30d"].trackedSales.count.value = 9;
  const insufficientErrors = validatePublicSnapshot(reseal(insufficient), { production: true });
  assert.ok(insufficientErrors.includes("top100[0] requires at least 10 pure PSA10 tracked sales in 30d"));

  const exactMinimum = await productionCandidate();
  exactMinimum.top100[0].windows["30d"].trackedSales.count.value = 10;
  const exactMinimumErrors = validatePublicSnapshot(reseal(exactMinimum), { production: true });
  assert.ok(!exactMinimumErrors.includes("top100[0] requires at least 10 pure PSA10 tracked sales in 30d"));
});

test("relaxed launch binds its policy and applies 30d / five-sale rules to every public card", async () => {
  const clean = await relaxedProductionCandidate();
  assert.deepEqual(validatePublicSnapshot(clean, { production: true }), []);

  const missingBinding = structuredClone(clean);
  delete missingBinding.generation.policySha256;
  assert.ok(
    validatePublicSnapshot(reseal(missingBinding), { production: true })
      .includes("generation release profile metadata is required for production"),
  );

  const fourSales = structuredClone(clean);
  fourSales.watchlist[0].windows["30d"].trackedSales.count.value = 4;
  assert.ok(
    validatePublicSnapshot(reseal(fourSales), { production: true })
      .includes("watchlist[0] requires at least 5 pure PSA10 tracked sales in 30d"),
  );

  const effectiveAt = Date.parse(clean.generation.effectiveAt);
  const withinThirtyDays = structuredClone(clean);
  withinThirtyDays.watchlist[0].pricePsa10.asOf = new Date(
    effectiveAt - 30 * 24 * 3_600_000,
  ).toISOString();
  assert.ok(
    !validatePublicSnapshot(reseal(withinThirtyDays), { production: true })
      .some((error) => error.includes("price exceeds 720h freshness SLA")),
  );

  const beyondThirtyDays = structuredClone(clean);
  beyondThirtyDays.watchlist[0].pricePsa10.asOf = new Date(
    effectiveAt - (30 * 24 + 1) * 3_600_000,
  ).toISOString();
  assert.ok(
    validatePublicSnapshot(reseal(beyondThirtyDays), { production: true })
      .includes("watchlist[0] price exceeds 720h freshness SLA"),
  );

  const overCapacity = structuredClone(clean);
  const source = overCapacity.watchlist.at(-1);
  assert.ok(source, "relaxed fixture requires a watchlist source card");
  overCapacity.watchlist = Array.from({ length: 901 }, (_, index) => ({
    ...structuredClone(source),
    id: `cmc_relaxed${String(index).padStart(7, "0")}`,
    rank: index + 101,
    marketRank: index + 101,
    viewRank: index + 101,
    marketCap: { ...source.marketCap, value: (source.marketCap.value ?? 1_000_000) - index },
  }));
  overCapacity.coverage.watchlistCount = overCapacity.watchlist.length;
  overCapacity.coverage.verifiedCount = overCapacity.top100.length + overCapacity.watchlist.length;
  const capacityErrors = validatePublicSnapshot(reseal(overCapacity), { production: true });
  assert.ok(capacityErrors.includes("watchlist must contain at most 900 cards"));
  assert.ok(capacityErrors.includes("public snapshot exceeds 1000 card capacity"));
});

// 2026-07-22..07-26 真實故障：TAG catalog dump 日日爆，POP 凍喺 07-22，
// 但 `populationPsa10` 只由 PSA 觀測填，而 PSA 日日新，所以成個 168h 閘
// 由頭到尾冇響過——訪客一路睇住五日前嘅 TAG 數。呢個測試釘死「每個評級行
// 都要各自計齡」，唔可以再靠 PSA 一支公代表五個。
test("production validation ages every grader population, not just PSA", async () => {
  const clean = await productionCandidate();
  const effectiveAt = Date.parse(clean.generation.effectiveAt);
  const staleAt = new Date(effectiveAt - 200 * 3_600_000).toISOString();
  assert.ok(!validatePublicSnapshot(clean, { production: true }).some((error) => error.includes("topGradePopulation exceeds")));

  // 覆蓋率 ≠ 新鮮度：seed 全部 360 張卡 TAG 都係 `unavailable`（asOf null），
  // 新鮮度閘唔可以順手殺埋佢哋，否則五格硬合約當場崩。
  assert.ok(
    [...clean.top100, ...clean.watchlist].some((card) =>
      Object.values(card.graderPopulations)
        .some((grader) => grader.topGradePopulation.status === "unavailable")),
  );

  const stale = structuredClone(clean);
  stale.top100[0].graderPopulations.BGS.topGradePopulation = {
    value: 1,
    status: "ready",
    asOf: staleAt,
    estimated: false,
  };
  stale.watchlist[0].graderPopulations.CGC.topGradePopulation = {
    value: 1,
    status: "ready",
    asOf: staleAt,
    estimated: false,
  };
  const errors = validatePublicSnapshot(reseal(stale), { production: true });
  assert.ok(errors.includes("top100[0].graderPopulations.BGS.topGradePopulation exceeds 168h freshness SLA"));
  assert.ok(errors.includes("watchlist[0].graderPopulations.CGC.topGradePopulation exceeds 168h freshness SLA"));

  // 72h 係 run_daily.py 容許嘅 last-good catalog 重播窗，正常回退日唔可以誤報。
  const withinSla = structuredClone(clean);
  withinSla.top100[0].graderPopulations.BGS.topGradePopulation = {
    value: 1,
    status: "ready",
    asOf: new Date(effectiveAt - 72 * 3_600_000).toISOString(),
    estimated: false,
  };
  assert.ok(!validatePublicSnapshot(reseal(withinSla), { production: true }).some((error) => error.includes("topGradePopulation exceeds")));
});

test("watchlist may contain zero to 400 contiguous eligible cards without padding", async () => {
  const value = await snapshot();
  value.watchlist = [];
  value.coverage.watchlistCount = 0;
  value.generation.contentSha256 = publicSnapshotContentSha256(value);
  assert.deepEqual(validatePublicSnapshot(value), []);
});

test("verified Top N may contain 1 to 99 cards without padding", async () => {
  const value = await snapshot();
  value.top100 = value.top100.slice(0, 7);
  value.coverage.claim = "verified-top-n";
  value.coverage.verifiedCount = value.top100.length;
  value.coverage.top100Count = value.top100.length;
  assert.deepEqual(validatePublicSnapshot(reseal(value)), []);
});

test("verified Top N rejects an empty list and a false Top 100 claim", async () => {
  const value = await snapshot();
  value.top100 = [];
  value.coverage.claim = "verified-top-100";
  value.coverage.verifiedCount = 0;
  value.coverage.top100Count = 0;
  const errors = validatePublicSnapshot(reseal(value));
  assert.ok(errors.includes("top100 must contain 1 to 100 cards"));
  assert.ok(errors.includes("coverage.claim must equal verified-top-n"));
});

test("watchlist rejects rank gaps and more than 400 cards", async () => {
  const value = await snapshot();
  const source = value.watchlist[0] ?? value.top100.at(-1);
  value.watchlist = Array.from({ length: 401 }, (_, index) => ({
    ...structuredClone(source),
    id: `cmc_watch${String(index).padStart(7, "0")}`,
    rank: index + 101,
    marketRank: index + 101,
    viewRank: index + 101,
    marketCap: { ...source.marketCap, value: source.marketCap.value - index },
  }));
  value.watchlist[1].rank = 103;
  value.watchlist[1].viewRank = 103;
  value.watchlist[1].marketCap.value = source.marketCap.value + 1;
  value.coverage.watchlistCount = value.watchlist.length;
  value.generation.contentSha256 = publicSnapshotContentSha256(value);
  const errors = validatePublicSnapshot(value);
  assert.ok(errors.includes("watchlist must contain at most 400 cards"));
  assert.ok(errors.includes("watchlist view ranks must start at 101 and be contiguous"));
  assert.ok(errors.includes("top100 and watchlist are not ordered by market cap"));
});

test("generation IDs cannot escape the versioned snapshot namespace", async () => {
  const value = await snapshot();
  value.generation.id = "..";
  value.generation.contentSha256 = publicSnapshotContentSha256(value);
  assert.ok(validatePublicSnapshot(value).includes("generation id is unsafe"));
});
