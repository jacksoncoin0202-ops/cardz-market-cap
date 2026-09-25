#!/usr/bin/env node
// Node 24: execute the production helpers with a mixed-TCG ranking, without a server or DB.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";

registerHooks({
  resolve(specifier, context, next) {
    return next(specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
      ? `${specifier}.ts`
      : specifier, context);
  },
});

const { cardFactSentence, cardShareLine, relatedCards } = await import("../apps/web/src/lib/related-cards.ts");
const { plainDescription } = await import("../apps/web/src/lib/plain-text.ts");
const detailSource = ts.createSourceFile("card-detail.tsx",
  readFileSync(new URL("../apps/web/src/components/card-detail.tsx", import.meta.url), "utf8"),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let descriptionExpression;
function findDescription(node) {
  if (ts.isPropertyAssignment(node) && node.name.getText(detailSource) === "description") {
    assert.equal(descriptionExpression, undefined, "only one card JSON-LD description");
    descriptionExpression = node.initializer.getText(detailSource);
  }
  ts.forEachChild(node, findDescription);
}
findDescription(detailSource);
assert.ok(descriptionExpression, "production card JSON-LD description exists");
const evaluateDescription = (expression) => new Function("cardFact", "story", "plainDescription",
  `return (${expression});`);
const describeCard = evaluateDescription(descriptionExpression);
const describeOldBug = evaluateDescription('plainDescription(story ?? "") || cardFact || undefined');
const metric = { value: 100, status: "ready" };
const makeCard = (id, tcg, marketRank) => ({
  id, tcg, marketRank,
  officialName: `2026 Example ${id} 001`,
  name: { en: id },
  setName: { en: "Example" },
  collectorNumber: "001",
  marketCap: metric,
  windows: { "7d": { marketCapChangePct: metric } },
});
const target = makeCard("target", "Pokémon", 3);
const snapshot = {
  top100: [makeCard("first", "One Piece", 1), makeCard("second", "One Piece", 2)],
  watchlist: [target, makeCard("unranked", "Pokémon", 0)],
};
const related = relatedCards(snapshot, target.id);
// #3 belongs to the one ranked Pokémon card, among three ranked cards across both TCGs.
assert.equal(related.indexRankedCount, 3);
assert.equal(relatedCards(snapshot, "missing"), null);
const oldTcgTotal = [...snapshot.top100, ...snapshot.watchlist]
  .filter((card) => card.tcg === target.tcg && card.marketRank >= 1).length;
assert.equal(oldTcgTotal, 1);
const input = {
  name: "Target", set: "Example", num: "001", cap: "$100", price: "$10", pop: "10",
  date: "2026-09-05", rank: target.marketRank, total: related.indexRankedCount, change: null,
};
const expected = {
  en: {
    fact: "Ranked #3 by market cap among 3 ranked cards across CardZ Marketcap.",
    share: "#3 overall / 3 ranked cards",
    noTotal: "#3 overall",
  },
  "zh-TW": {
    fact: "在 CardZ Marketcap 全站 3 張已排名卡牌之中，市值排名第 3。",
    share: "全站市值第 3 名／已排名 3 張",
    noTotal: "全站第 3 名",
  },
  "zh-CN": {
    fact: "在 CardZ Marketcap 全站 3 张已排名卡牌之中，市值排名第 3。",
    share: "全站市值第 3 名／已排名 3 张",
    noTotal: "全站第 3 名",
  },
  ja: {
    fact: "CardZ Marketcap 全体のランキング対象 3 枚中、時価総額 3 位。",
    share: "全体で時価総額 3 位／ランキング対象 3 枚",
    noTotal: "全体で 3 位",
  },
  ko: {
    fact: "CardZ Marketcap 전체 순위 대상 카드 3장 중 시가총액 3위.",
    share: "전체 시가총액 3위／순위 대상 3장",
    noTotal: "전체 3위",
  },
};
for (const [locale, copy] of Object.entries(expected)) {
  const checkFact = (value) => assert.ok(cardFactSentence(locale, value).endsWith(copy.fact),
    `${locale}: fact uses global rank and count`);
  const checkShare = (value) => assert.equal(cardShareLine(locale, value).split(" · ")[0], copy.share,
    `${locale}: share uses global rank and count`);
  checkFact(input);
  checkShare(input);
  const fact = cardFactSentence(locale, input);
  const checkCurrentDescription = (describe) => assert.equal(
    describe(fact, "Market rank 1444. No population or reference price is provided.", plainDescription),
    fact, `${locale}: JSON-LD repeats the complete visible current fact, not the old story`);
  checkCurrentDescription(describeCard);
  assert.throws(() => checkCurrentDescription(describeOldBug), assert.AssertionError,
    `${locale}: restoring story-first precedence is rejected`);
  const share = cardShareLine(locale, input);
  assert.ok(share.length <= 160, `${locale}: share length`);
  assert.equal(cardShareLine(locale, { ...input, total: null }).split(" · ")[0], copy.noTotal,
    `${locale}: unknown total still labels the rank globally`);
  const unranked = { ...input, rank: 0 };
  assert.ok(!cardFactSentence(locale, unranked).endsWith(copy.fact), `${locale}: unranked fact omits rank`);
  assert.ok(!cardShareLine(locale, unranked).startsWith(copy.share), `${locale}: unranked share omits rank`);
  // Plant the former caller bug in memory: global #3 paired with the TCG total of 1.
  // Both checks must reject the actual helper output; no production source is mutated.
  const wrongScope = { ...input, total: oldTcgTotal };
  assert.throws(() => checkFact(wrongScope), assert.AssertionError);
  assert.throws(() => checkShare(wrongScope), assert.AssertionError);
}
assert.equal(describeCard("Current facts ".repeat(30), "Old story", plainDescription),
  "Current facts ".repeat(30), "JSON-LD retains full facts beyond the metadata 160-character limit");
assert.equal(describeCard(null, "**Kabu** is a *Trainer*.", plainDescription),
  "Kabu is a Trainer.", "missing metrics retain the plain stable introduction");
assert.equal(describeCard(null, null, plainDescription), undefined, "missing metrics and story omit description");
console.log("PASS card rank scope: mixed TCG, top100 + watchlist, five-language facts/shares/JSON-LD, full current facts and missing-metric introduction, 15 old-bug mutations rejected");
