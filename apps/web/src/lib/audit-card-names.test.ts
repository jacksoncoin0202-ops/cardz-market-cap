import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "vitest";
import { localizedCardName } from "./card-names";
import type { Locale } from "./types";

interface SeedCard {
  id: string;
  rank: number;
  names: { en: string; zhTW?: string | null; zhCN?: string | null; ja?: string | null; ko?: string | null };
  sets: { en: string };
}

const seed = JSON.parse(readFileSync(join(__dirname, "../../../../data/public/seed-snapshot.json"), "utf8"));
const cards: SeedCard[] = [...seed.top100, ...(seed.watchlist ?? [])];
const locales: Locale[] = ["zh-TW", "zh-CN", "ja", "ko"];

describe("card name audit (informational)", () => {
  it("dumps localization coverage for every seed card", () => {
    const rows: string[] = [];
    const queue: string[] = [];
    for (const card of cards) {
      const results = locales.map((l) => localizedCardName(card.names.en, card.names[l === "zh-TW" ? "zhTW" : l === "zh-CN" ? "zhCN" : l] as string | null | undefined, l));
      const hitType = results[0] === card.names.en ? "FALLBACK-EN" : /[A-Za-z]/.test(results[0].replace(/(VMAX|VSTAR|GX|SAR|S-P|SUR|ex|V|UR|AR|HR|PR|RR|RRR|SR|7-Eleven)/g, "")) ? "PARTIAL-LATIN" : "OK";
      rows.push(`${card.rank}\t${card.names.en}\t${card.sets.en}\t${results.join("\t")}\t${hitType}`);
      if (hitType !== "OK") queue.push(`${card.rank}\t${card.names.en}\t${card.sets.en}\t${results.join("\t")}\t${hitType}`);
    }
    console.log(`TOTAL=${cards.length} QUEUE=${queue.length}`);
    console.log("===CURATION QUEUE===");
    console.log(queue.join("\n"));
    console.log("===FULL DUMP===");
    console.log(rows.join("\n"));
  });
});
