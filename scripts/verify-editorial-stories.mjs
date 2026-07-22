import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const allowReviewRequired =
  process.argv.includes("--allow-review") ||
  process.argv.includes("--allow-review-required");
const packPath = path.join(root, "data", "editorial", "top100-stories.json");
const snapshotPath = path.join(root, "data", "public", "seed-snapshot.json");
const pack = JSON.parse(await readFile(packPath, "utf8"));
const snapshot = JSON.parse(await readFile(snapshotPath, "utf8"));
const errors = [];

const locales = ["en", "zhTW", "zhCN", "ja"];
const statuses = new Set(["ready", "review_required"]);
const tcgs = new Set(["pokemon", "one-piece"]);
const cardLanguages = new Set(["en", "ja", "zh-cn", "zh-tw"]);
const forbiddenFields = /(?:sourceUrl|sourceId|provider|upstream|privatePath)/i;
const weakPhrases = [
  /highly sought[- ]after/i,
  /\bgrail\b/i,
  /trader market view/i,
  /炒家市場觀察/,
  /备受追捧/,
  /備受追捧/,
];

function fail(condition, message) {
  if (condition) errors.push(message);
}

fail(pack.schemaVersion !== "1.0.0", "schemaVersion must be 1.0.0");
fail(!Array.isArray(pack.entries), "entries must be an array");
fail(snapshot.schemaVersion !== "2.0.0", "public snapshot must use schema 2.0.0");
fail(pack.snapshotEffectiveAt !== snapshot.generation?.effectiveAt, "snapshotEffectiveAt does not match the canonical snapshot");
fail(pack.snapshotContentSha256 !== snapshot.generation?.contentSha256, "snapshotContentSha256 does not match the canonical snapshot");

const entries = Array.isArray(pack.entries) ? pack.entries : [];
fail(entries.length !== 100, `expected 100 entries, received ${entries.length}`);
fail(!Array.isArray(snapshot.top100) || snapshot.top100.length !== 100, "public snapshot must contain 100 Top 100 cards");

const canonicalById = new Map((snapshot.top100 ?? []).map((card) => [card.id, card]));

const ids = new Set();
const ranks = new Set();
const storyBodies = new Set();
let readyCount = 0;
let reviewRequiredCount = 0;

for (const [index, entry] of entries.entries()) {
  const label = `entries[${index}]`;
  fail(!/^cmc_[0-9a-f]{24}$/.test(entry.id ?? ""), `${label}.id is not an opaque CARDZ ID`);
  fail(ids.has(entry.id), `${label}.id is duplicated`);
  ids.add(entry.id);

  fail(!Number.isInteger(entry.rankAtReview) || entry.rankAtReview < 1 || entry.rankAtReview > 100, `${label}.rankAtReview is invalid`);
  fail(ranks.has(entry.rankAtReview), `${label}.rankAtReview is duplicated`);
  ranks.add(entry.rankAtReview);

  fail(!tcgs.has(entry.tcg), `${label}.tcg is invalid`);
  fail(!cardLanguages.has(entry.cardLanguage), `${label}.cardLanguage is invalid`);
  fail(typeof entry.collectorNumber !== "string" || entry.collectorNumber.trim().length < 2, `${label}.collectorNumber is missing`);
  fail(!statuses.has(entry.status), `${label}.status is invalid`);
  fail(!/^[0-9a-f]{64}$/.test(entry.evidenceSha256 ?? ""), `${label}.evidenceSha256 is invalid`);
  fail(!Array.isArray(entry.evidence) || entry.evidence.length === 0, `${label}.evidence is empty`);

  const canonical = canonicalById.get(entry.id);
  fail(!canonical, `${label}.id is not in the canonical Top 100`);
  if (canonical) {
    fail(entry.rankAtReview !== canonical.rank, `${label}.rankAtReview does not match the canonical snapshot`);
    fail(entry.tcg !== canonical.tcg, `${label}.tcg does not match the canonical snapshot`);
    fail(entry.cardLanguage !== canonical.language, `${label}.cardLanguage does not match the canonical snapshot`);
    fail(entry.collectorNumber !== canonical.collectorNumber?.display, `${label}.collectorNumber does not match the canonical snapshot`);
  }

  if (entry.status === "ready") {
    readyCount += 1;
    fail(entry.reviewReason !== null, `${label}.reviewReason must be null when ready`);
    for (const locale of locales) {
      const story = entry.stories?.[locale];
      fail(typeof story !== "string" || story.trim().length < 45, `${label}.stories.${locale} is too short or missing`);
      if (typeof story === "string") {
        const normalized = story.toLocaleLowerCase().replace(/\s+/g, " ").trim();
        fail(storyBodies.has(normalized), `${label}.stories.${locale} duplicates another story`);
        storyBodies.add(normalized);
        for (const pattern of weakPhrases) {
          fail(pattern.test(story), `${label}.stories.${locale} contains generic market copy`);
        }
      }
    }
  } else {
    reviewRequiredCount += 1;
    fail(typeof entry.reviewReason !== "string" || entry.reviewReason.trim().length < 8, `${label}.reviewReason is missing`);
    for (const locale of locales) {
      fail(entry.stories?.[locale] !== null, `${label}.stories.${locale} must be null while review is required`);
    }
  }
}

const serialized = JSON.stringify(pack);
fail(forbiddenFields.test(serialized), "editorial pack contains a private-source field name");
fail(serialized.includes("http://") || serialized.includes("https://"), "editorial pack contains a URL");
fail(pack.readyCount !== readyCount, `readyCount says ${pack.readyCount}, counted ${readyCount}`);
fail(pack.reviewRequiredCount !== reviewRequiredCount, `reviewRequiredCount says ${pack.reviewRequiredCount}, counted ${reviewRequiredCount}`);
fail(readyCount + reviewRequiredCount !== entries.length, "status counts do not cover every entry");
fail(!allowReviewRequired && reviewRequiredCount > 0, `${reviewRequiredCount} Top 100 stories still require editorial review`);
fail(entries.some((entry) => !canonicalById.has(entry.id)), "editorial pack contains a non-Top-100 printing");
fail([...canonicalById].some(([id]) => !ids.has(id)), "canonical Top 100 contains a printing without an editorial entry");

if (errors.length > 0) {
  console.error(errors.join("\n"));
  process.exitCode = 1;
} else {
  console.log(JSON.stringify({ entries: entries.length, ready: readyCount, reviewRequired: reviewRequiredCount, status: "passed" }));
}
