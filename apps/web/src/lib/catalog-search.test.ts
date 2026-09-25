import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { buildCatalogIndex, catalogMatchesQuery, catalogToCard, searchCatalog } from "./catalog-search";
import type { MarketCardView, MarketViewSnapshot } from "./types";

function fakeCard(overrides: Partial<MarketCardView> & Pick<MarketCardView, "id" | "officialName" | "marketRank">): MarketCardView {
  return {
    rank: overrides.marketRank,
    viewRank: overrides.marketRank,
    tcg: "Pokémon",
    cardLanguage: "en",
    printingIdentity: { setCode: "SV8", finishCode: null },
    collectorNumber: "025/193",
    name: { en: overrides.officialName ?? "", "zh-TW": null, "zh-CN": null, ja: null, ko: null },
    setName: { en: "Surging Sparks", "zh-TW": null, "zh-CN": null, ja: null, ko: null },
    image: { url: "/x.webp", alt: overrides.officialName, kind: "raw_front" },
    pricePsa10: { value: 1, status: "ready", asOf: null },
    populationPsa10: { value: 1, status: "ready", asOf: null },
    marketCap: { value: 1, status: "ready", asOf: null },
    windows: {} as MarketCardView["windows"],
    historyDaily: [],
    salesSparkline: [],
    ...overrides,
  };
}

const top100 = fakeCard({ id: "cmc_top", officialName: "Charizard ex", marketRank: 1, collectorNumber: "234/091" });
const watchlist = fakeCard({
  id: "cmc_watch",
  officialName: "Gold Star Pikachu",
  marketRank: 412,
  collectorNumber: "104/101",
  name: { en: "Gold Star Pikachu", "zh-TW": "皮卡丘金星", "zh-CN": null, ja: null, ko: null },
});

const snapshot = {
  top100: [top100],
  watchlist: [watchlist],
} as Pick<MarketViewSnapshot, "top100" | "watchlist" | "sealed">;

describe("catalog search", () => {
  it("indexes watchlist cards, not just the current top 100", () => {
    const catalog = buildCatalogIndex(snapshot);
    assert.equal(catalog.length, 2);
    assert.ok(catalog.some((entry) => entry.id === "cmc_watch"));
  });

  it("finds a watchlist card that a top-100-only filter would miss", () => {
    const catalog = buildCatalogIndex(snapshot);
    const hits = searchCatalog(catalog, "Gold Star Pikachu", "en", { kind: "card" });
    assert.equal(hits.length, 1);
    assert.equal(hits[0]?.id, "cmc_watch");
    const viewOnly = snapshot.top100.filter((card) =>
      `${card.officialName} ${card.collectorNumber}`.toLowerCase().includes("gold star pikachu"),
    );
    assert.equal(viewOnly.length, 0);
  });

  it("matches localized names even when the UI locale is English", () => {
    const catalog = buildCatalogIndex(snapshot);
    const hits = searchCatalog(catalog, "皮卡丘金星", "en");
    assert.equal(hits[0]?.id, "cmc_watch");
  });

  it("matches collector numbers ahead of loose name includes", () => {
    const extra = fakeCard({ id: "cmc_num", officialName: "Random 104 mention", marketRank: 20, collectorNumber: "001/001" });
    const catalog = buildCatalogIndex({ top100: [top100, extra], watchlist: [watchlist] });
    const hits = searchCatalog(catalog, "104/101", "en");
    assert.equal(hits[0]?.id, "cmc_watch");
  });

  it("does not treat an empty query as a match filter", () => {
    const catalog = buildCatalogIndex(snapshot);
    assert.equal(catalog.every((entry) => catalogMatchesQuery(entry, "   ")), true);
  });

  it("can scope hits to one TCG", () => {
    const mixed = buildCatalogIndex({
      top100: [top100],
      watchlist: [
        watchlist,
        fakeCard({ id: "cmc_op", officialName: "Monkey D. Luffy", marketRank: 20, tcg: "One Piece", collectorNumber: "OP05-119" }),
      ],
    });
    const hits = searchCatalog(mixed, "Luffy", "en", { kind: "card", tcg: "Pokémon" });
    assert.equal(hits.length, 0);
    const op = searchCatalog(mixed, "Luffy", "en", { kind: "card", tcg: "One Piece" });
    assert.equal(op[0]?.id, "cmc_op");
  });

  it("hydrates a catalog hit into a ranking row", () => {
    const catalog = buildCatalogIndex(snapshot);
    const row = catalogToCard(catalog.find((entry) => entry.id === "cmc_watch")!);
    assert.equal(row?.id, "cmc_watch");
    assert.equal(row?.marketRank, 412);
    assert.ok(row?.pricePsa10);
  });
});
