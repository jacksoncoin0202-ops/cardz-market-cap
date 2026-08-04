import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { copy } from "@/lib/i18n";
import { getSeedSnapshot } from "@/lib/snapshot";
import type { MarketCardView } from "@/lib/types";
import { DETAIL_PRINT_FIELDS, PrintAttributeChips, PrintLanguageBadge, printIdentityRows } from "./print-badge";

function seedCard(): MarketCardView {
  return structuredClone(getSeedSnapshot().top100[0]);
}

describe("print identity surface", () => {
  it("renders nothing at all when the print identity is absent", () => {
    const card: MarketCardView = { ...seedCard(), cardLanguage: null, printingIdentity: null };

    expect(renderToStaticMarkup(<PrintLanguageBadge card={card} locale="zh-TW" />)).toBe("");
    expect(renderToStaticMarkup(<PrintAttributeChips card={card} locale="zh-TW" />)).toBe("");
    expect(printIdentityRows(card, "zh-TW")).toEqual([]);
  });

  it("never falls back to an unavailable string when the identity is absent", () => {
    const card: MarketCardView = { ...seedCard(), cardLanguage: null, printingIdentity: null };
    const markup =
      renderToStaticMarkup(<PrintLanguageBadge card={card} locale="zh-TW" />)
      + renderToStaticMarkup(<PrintAttributeChips card={card} locale="zh-TW" />);
    expect(markup).not.toContain(copy["zh-TW"].status.unavailable);
    expect(markup).not.toContain("print-chips");
    expect(markup).not.toContain("print-badge");
  });

  it("the dev seed snapshot carries no print identity, so dev renders no badge", () => {
    const seed = getSeedSnapshot();
    expect(seed.top100.length).toBeGreaterThan(0);
    expect(seed.top100.every((card) => card.printingIdentity === null)).toBe(true);
    expect(seed.top100.every((card) => card.cardLanguage === null)).toBe(true);
  });

  it("drops the fields the producer left empty instead of rendering empty chips", () => {
    const card: MarketCardView = {
      ...seedCard(),
      cardLanguage: "ja",
      printingIdentity: {
        setName: "OP-09",
        setCode: "OP09",
        collectorNumber: "OP05-119",
        rarityCode: null,
        parallelCode: "parallel",
        finishCode: null,
        printingCode: "base",
      },
    };

    const chips = renderToStaticMarkup(<PrintAttributeChips card={card} locale="zh-TW" />);
    expect(chips).toContain("OP09");
    expect(chips).toContain("parallel");
    expect(chips).not.toContain("base");
    expect(chips.match(/print-chip"/g)?.length).toBe(2);

    expect(renderToStaticMarkup(<PrintLanguageBadge card={card} locale="zh-TW" />)).toContain("日文版");
    expect(printIdentityRows(card, "zh-TW").map((row) => row.key)).toEqual([
      "language",
      "setCode",
      "parallelCode",
    ]);
  });

  it("keeps the set code out of the collector number", () => {
    const card: MarketCardView = {
      ...seedCard(),
      collectorNumber: "OP05-119",
      cardLanguage: "ja",
      printingIdentity: {
        setName: "OP-09",
        setCode: "OP09",
        collectorNumber: "OP05-119",
        rarityCode: null,
        parallelCode: null,
        finishCode: null,
        printingCode: null,
      },
    };
    const chips = renderToStaticMarkup(<PrintAttributeChips card={card} locale="en" fields={["setCode"]} />);
    expect(chips).not.toContain("OP09 · OP05-119");
    expect(chips).toContain(">OP09<");
  });

  it("shows the pack source row only under the detail whitelist, title-cased", () => {
    const card: MarketCardView = {
      ...seedCard(),
      cardLanguage: "ja",
      printingIdentity: {
        setName: "OP-09",
        setCode: "OP09",
        collectorNumber: "OP05-119",
        editionCode: "booster pack awakening of the new era",
        rarityCode: null,
        parallelCode: null,
        finishCode: null,
        printingCode: null,
      },
    };
    const detailRows = printIdentityRows(card, "zh-TW", DETAIL_PRINT_FIELDS);
    expect(detailRows.map((row) => row.key)).toEqual(["language", "editionCode", "setCode"]);
    expect(detailRows[1]).toEqual({ key: "editionCode", label: "卡包來源", value: "Booster Pack Awakening of the New Era" });
    /* 公開白名單（grader chips / Top 100）永遠唔出卡包名。 */
    expect(printIdentityRows(card, "zh-TW").map((row) => row.key)).toEqual(["language", "setCode"]);
  });
});
