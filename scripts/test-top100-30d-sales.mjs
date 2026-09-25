#!/usr/bin/env node
/*
 * A card with no qualifying PSA10 sale in its 30d window holds no Top100 seat
 * (daddy 2026-07-29, #47). The rule is one definition, hasQualifying30dSale in
 * apps/web/src/lib/snapshot.ts. Part 1 runs the real global path on fixtures:
 * the history builder (with a real quarantine receipt through
 * loadSaleQuarantine), windowMetrics, then live-db-snapshot.ts seatTop100.
 * Part 2 proves buildLiveDbSnapshot calls seatTop100 unconditionally on every
 * card and publishes its split. Part 3 runs the real per-game path
 * (server-snapshot.ts scopeSnapshot -> gameUniverse) and checks its call site.
 * Every check has a plant.
 */
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import vm from "node:vm";
import ts from "typescript";

const failed = [];
const check = (label, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${!ok && detail ? `: ${detail}` : ""}`);
  if (!ok) failed.push(label);
};

const read = (path) => readFileSync(new URL(`../apps/web/src/lib/${path}`, import.meta.url), "utf8");
const source = read("live-db-snapshot.ts");
const ruleSource = read("snapshot.ts");
const serverSource = read("server-snapshot.ts");
const parse = (text) => ts.createSourceFile("lifted.ts", text, ts.ScriptTarget.ES2022, true);
const fnNamed = (tree, name) =>
  tree.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === name);

// Real declarations only, lifted by name: a copy in this file would stay green after the source moved.
const RULE = ["TOP100_SEATS", "hasQualifying30dSale"]; // snapshot.ts: the one definition
const LIVE = [
  "WINDOWS", "LONG_WINDOWS", "MAX_WINDOW_RATIO",
  "ANCHOR_CARRY_FLOOR_D", "ANCHOR_CARRY_FRACTION", "SALE_ANCHOR_MAX_AGE_D", "MARKET_ANCHOR_MAX_AGE_D", "anchorWithheld",
  "repoRoot", "readSaleQuarantineEntries", "loadSaleQuarantine", "iso", "day", "numberValue", "readyMetric",
  "chartLaneOf", "anchorCandidate", "latestBefore", "percentage",
  "salesTotal", "windowMetrics", "seatTop100",
];
const GAME = ["canonicalCards", "gameUniverse", "scopedCoverage", "scopeSnapshot"]; // server-snapshot.ts
function declarations(text, wanted, file) {
  const needed = new Set(wanted);
  const tree = parse(text);
  const picked = tree.statements.filter((node) =>
    (ts.isFunctionDeclaration(node) && needed.has(node.name?.text))
    || (ts.isVariableStatement(node) && node.declarationList.declarations.some((d) => needed.has(d.name.getText(tree)))));
  const names = new Set(picked.flatMap((node) => ts.isFunctionDeclaration(node)
    ? [node.name.text]
    : node.declarationList.declarations.map((d) => d.name.getText(tree))));
  const missing = [...needed].filter((name) => !names.has(name));
  if (missing.length) throw new Error(`${file} lost: ${missing.join(", ")}`);
  return picked.map((node) => node.getText(tree)).join("\n");
}
// A second definition of the rule anywhere else would let one board drift from the other.
const definesRule = (text) => {
  const tree = parse(text);
  return tree.statements.some((node) =>
    (ts.isFunctionDeclaration(node) && RULE.includes(node.name?.text))
    || (ts.isVariableStatement(node) && node.declarationList.declarations.some((d) => RULE.includes(d.name.getText(tree)))));
};

// The history builder is inline in buildLiveDbSnapshot; lift it between the same
// markers scripts/test-fe-sale-quarantine-day.mjs uses and inject the quarantine map.
const BLOCK_START = "    type HistoryDraft = DailyHistoryPoint & {";
const BLOCK_END = "\n    const cards: PublicCard[] = coreRows.map((row) => {";
function historyBuilder(text) {
  const slice = text.slice(text.indexOf(BLOCK_START), text.indexOf(BLOCK_END));
  const lines = slice.split("\n").filter((line) => line.trim().startsWith("const saleQuarantine ="));
  if (text.indexOf(BLOCK_START) < 0 || text.indexOf(BLOCK_END) < 0 || lines.length !== 1) {
    throw new Error("history builder markers moved in live-db-snapshot.ts");
  }
  return `function buildHistories(salesRows, injectedQuarantine) {\n${
    slice.replace(lines[0], "    const saleQuarantine = injectedQuarantine;")}\n  return histories;\n}`;
}

function run(program) {
  const javascript = ts.transpileModule(program, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const sandbox = { exports: {}, require: createRequire(import.meta.url), process };
  vm.runInNewContext(javascript, sandbox);
  return sandbox.__lib;
}
function compile(text, rule = ruleSource) {
  return run(`${declarations(rule, RULE, "snapshot.ts")}\n${declarations(text, LIVE, "live-db-snapshot.ts")}\n${historyBuilder(text)}\n`
    + "globalThis.__lib = { windowMetrics, loadSaleQuarantine, buildHistories, seatTop100, hasQualifying30dSale, TOP100_SEATS };");
}
// scopeSnapshot's projections (listCard, withoutSealed, normalisePageSize) only
// slim fields and clamp paging; stubbed so the real ranking path runs alone.
function compileGame(text, rule = ruleSource) {
  return run(`${declarations(rule, RULE, "snapshot.ts")}\n${declarations(text, GAME, "server-snapshot.ts")}\n`
    + "const listCard = (card) => card;\nconst withoutSealed = (snapshot) => snapshot;\n"
    + "const normalisePageSize = (size) => size ?? 100;\n"
    + "globalThis.__lib = { scopeSnapshot };");
}

// ── Fixture ──────────────────────────────────────────────────────────────────
// 103 ranked cards + 1 unranked. Current price as-of 2026-09-25T09:29:29Z, so the
// 30d window is 2026-08-27..2026-09-25 (day buckets, salesTotal).
const AS_OF = "2026-09-25T09:29:29Z";
const LANE = "pricecharting";
// Every other ranked card sold once on 2026-09-20 (inside 7d as well).
const ZERO_30D = 3; //         last sale 2026-08-22, outside the window
const QUARANTINED_ONLY = 5; // its one 30d sale (2026-09-18) is in the quarantine receipt
const PART_QUARANTINED = 7; // 2 sales on 2026-09-20, 1 quarantined: 1 real sale left
const SALE_20D_AGO = 9; //     one sale 2026-09-05: inside 30d, outside 7d
const UNRANKED = 999;
const RANKED = 103;

const row = (variantId, date, count, value) => ({
  variant_id: variantId, observed_date: date, sales_count: count, sales_value_usd: value,
  sales_coverage_status: "partial", sales_verified_zero: 0, sales_source_codes: LANE,
});
const salesRows = [];
for (let v = 1; v <= RANKED; v += 1) {
  if (v === ZERO_30D) salesRows.push(row(v, "2026-08-22", 1, 1000));
  else if (v === QUARANTINED_ONLY) salesRows.push(row(v, "2026-08-01", 1, 1000), row(v, "2026-09-18", 1, 1000));
  else if (v === PART_QUARANTINED) salesRows.push(row(v, "2026-09-20", 2, 2000));
  else if (v === SALE_20D_AGO) salesRows.push(row(v, "2026-09-05", 1, 1000));
  else salesRows.push(row(v, "2026-09-20", 1, 1000));
}
salesRows.push(row(UNRANKED, "2026-09-20", 1, 1000));
const RECEIPT = {
  entries: [
    { saleObservationId: 9001, variantId: QUARANTINED_ONLY, observedDate: "2026-09-18", transactionValueUsd: 1000, quantity: 1 },
    { saleObservationId: 9002, variantId: PART_QUARANTINED, observedDate: "2026-09-20", transactionValueUsd: 1000, quantity: 1 },
  ],
};

const root = mkdtempSync(join(tmpdir(), "cardz-top100-30d-"));
mkdirSync(join(root, "data/runtime/operator/audit"), { recursive: true });
writeFileSync(join(root, "data/runtime/operator/audit/pc_sale_title_quarantine_current.json"), JSON.stringify(RECEIPT));

function board(lib, rankedCount = RANKED) {
  const saved = process.env.CARDZ_REPO_ROOT;
  process.env.CARDZ_REPO_ROOT = root;
  let quarantine;
  try {
    quarantine = lib.loadSaleQuarantine(new Set());
  } finally {
    if (saved === undefined) delete process.env.CARDZ_REPO_ROOT;
    else process.env.CARDZ_REPO_ROOT = saved;
  }
  const histories = lib.buildHistories([salesRows], quarantine);
  const card = (variantId, rank) => {
    const drafts = [...(histories.get(variantId)?.values() ?? [])].sort((a, b) => a.at.localeCompare(b.at));
    return {
      id: `v${variantId}`, rank, marketRank: rank, viewRank: rank,
      windows: lib.windowMetrics(drafts, rank > 0 ? 1000 : null, 1000, AS_OF, LANE, []),
    };
  };
  const cards = Array.from({ length: rankedCount }, (_, index) => card(index + 1, index + 1));
  cards.push(card(UNRANKED, 0));
  return { cards, seat: () => lib.seatTop100(cards) };
}

const ids = (list) => list.map((card) => card.id);
// A plant must change who is seated, not merely throw: null = it threw.
const seatedIds = (lib) => {
  try {
    return ids(board(lib).seat().top100);
  } catch {
    return null;
  }
};

try {
  const lib = compile(source);
  const { cards, seat } = board(lib);
  const count30 = (variantId) => cards.find((card) => card.id === `v${variantId}`).windows["30d"].trackedSales.count.value;
  check("fixture: the zero-30d card has no 30d count", count30(ZERO_30D) === null, String(count30(ZERO_30D)));
  check("fixture: the quarantined-only card nets to no 30d count", count30(QUARANTINED_ONLY) === null, String(count30(QUARANTINED_ONLY)));
  check("fixture: the part-quarantined card nets to 1 sale", count30(PART_QUARANTINED) === 1, String(count30(PART_QUARANTINED)));

  const { top100, watchlist } = seat();
  const top = ids(top100);
  check("a zero-30d card loses its Top100 seat", !top.includes(`v${ZERO_30D}`));
  check("a card whose only 30d sale is quarantined loses its seat", !top.includes(`v${QUARANTINED_ONLY}`));
  check("a card with one real sale left after quarantine keeps its seat", top.includes(`v${PART_QUARANTINED}`));
  check("a sale 20 days ago counts (30d window, not 7d)", top.includes(`v${SALE_20D_AGO}`));
  check("the board still has 100 seats", top100.length === 100, String(top100.length));
  check("Top100 ranks are 1..100, contiguous, rank = marketRank = viewRank",
    top100.every((card, index) => card.rank === index + 1 && card.marketRank === index + 1 && card.viewRank === index + 1));
  check("the next eligible cards move up (101 -> 99, 102 -> 100)",
    top100[98]?.id === "v101" && top100[99]?.id === "v102", `${top100[98]?.id},${top100[99]?.id}`);
  const rankedWatch = watchlist.filter((card) => card.marketRank > 0);
  check("dropped cards head the watchlist in canonical order at 101, 102",
    rankedWatch[0]?.id === `v${ZERO_30D}` && rankedWatch[0]?.marketRank === 101
      && rankedWatch[1]?.id === `v${QUARANTINED_ONLY}` && rankedWatch[1]?.marketRank === 102,
    JSON.stringify(rankedWatch.slice(0, 2).map((card) => [card.id, card.marketRank])));
  check("watchlist ranks continue contiguously from 101",
    rankedWatch.every((card, index) => card.marketRank === 101 + index && card.rank === card.marketRank && card.viewRank === card.marketRank));
  check("the unranked card stays rank 0 at the end", watchlist.at(-1)?.id === `v${UNRANKED}` && watchlist.at(-1)?.marketRank === 0);
  check("no card is lost (detail pages stay)",
    JSON.stringify([...top, ...ids(watchlist)].sort()) === JSON.stringify(ids(cards).sort()));
  check("a zero count does not qualify either",
    !lib.hasQualifying30dSale({ windows: { "30d": { trackedSales: { count: { value: 0 } } } } }));

  // Fewer seatable cards than seats: the boards would fill by rank with no-sale cards.
  let short = null;
  try {
    board(lib, 100).seat();
  } catch (error) {
    short = String(error.message);
  }
  check("fewer seatable cards than seats throws", Boolean(short?.includes("Top100 liquidity seat rule")), short ?? "no throw");

  // Plants.
  const seatSource = fnNamed(parse(source), "seatTop100").getText();
  const plant = (from, to) => {
    if (!source.includes(from)) throw new Error(`plant anchor missing: ${from}`);
    return compile(source.replace(from, to));
  };
  const plantRule = (from, to) => {
    if (!ruleSource.includes(from)) throw new Error(`plant anchor missing in snapshot.ts: ${from}`);
    return compile(source, ruleSource.replace(from, to));
  };
  const noRule = seatedIds(plant(seatSource, seatSource.replace("hasQualifying30dSale(card)", "true")));
  check("N(rule removed): the zero-30d card keeps its seat", Boolean(noRule?.includes(`v${ZERO_30D}`)));
  const counted = seatedIds(plant("if (quarantined && dayValue !== null && dayCount !== null) {", "if (false) {"));
  check("N(quarantined sales counted): the quarantined-only card keeps its seat",
    Boolean(counted?.includes(`v${QUARANTINED_ONLY}`)));
  const week = seatedIds(plantRule('card.windows["30d"].trackedSales', 'card.windows["7d"].trackedSales'));
  check("N(7d window): the 20-day-old sale no longer seats", Boolean(week && !week.includes(`v${SALE_20D_AGO}`)));
} finally {
  rmSync(root, { recursive: true, force: true });
}

// ── Call site ────────────────────────────────────────────────────────────────
// buildLiveDbSnapshot seats every built card, unconditionally, and publishes that split.
function callSiteOk(text) {
  const tree = parse(text);
  const build = fnNamed(tree, "buildLiveDbSnapshot");
  const attempt = build?.body?.statements.find((node) => ts.isTryStatement(node));
  if (!attempt) return false;
  const statements = attempt.tryBlock.statements;
  const declared = (node, name) => ts.isVariableStatement(node)
    && node.declarationList.declarations.length === 1
    && node.declarationList.declarations[0].name.getText(tree) === name;
  const init = (node) => node.declarationList.declarations[0].initializer;
  const cardsAt = statements.findIndex((node) => declared(node, "cards") && init(node)?.getText(tree).startsWith("coreRows.map("));
  const seatAt = statements.findIndex((node) => ts.isVariableStatement(node)
    && node.declarationList.declarations.length === 1
    && ts.isCallExpression(init(node) ?? node)
    && init(node).expression.getText(tree) === "seatTop100"
    && init(node).arguments.length === 1
    && init(node).arguments[0].getText(tree) === "cards");
  const snapAt = statements.findIndex((node) => declared(node, "snapshot"));
  if (cardsAt < 0 || seatAt < 0 || snapAt < 0 || !(cardsAt < seatAt && seatAt < snapAt)) return false;
  const boardName = statements[seatAt].declarationList.declarations[0].name.getText(tree);
  const literal = init(statements[snapAt]);
  if (!literal || !ts.isObjectLiteralExpression(literal)) return false;
  const prop = (name) => literal.properties.find((p) => ts.isPropertyAssignment(p) && p.name.getText(tree) === name)?.initializer.getText(tree);
  return prop("top100") === `${boardName}.top100` && prop("watchlist") === `${boardName}.watchlist`;
}

const seatLine = "    const board = seatTop100(cards);\n";
check("the build seats every card before the snapshot is published", callSiteOk(source));
check("N(call site): the old slice split fails the check",
  !callSiteOk(source.replace("      top100: board.top100,\n      watchlist: board.watchlist,",
    "      top100: cards.slice(0, 100),\n      watchlist: cards.slice(100),")));
check("N(call site): with the call removed the check fails", !callSiteOk(source.replace(seatLine, "")));
check("N(call site): behind a condition the check fails",
  !callSiteOk(source.replace(seatLine, `    if (process.env.CARDZ_TOP100_RULE) {\n  ${seatLine}    }\n`)));
check("N(call site): on the top-100 slice only the check fails",
  !callSiteOk(source.replace(seatLine, seatLine.replace("seatTop100(cards)", "seatTop100(cards.slice(0, 100))"))));

// ── One definition ───────────────────────────────────────────────────────────
check("the rule is defined once, in snapshot.ts", definesRule(ruleSource) && !definesRule(source) && !definesRule(serverSource));
check("N(one definition): a local copy in server-snapshot.ts is caught",
  definesRule(`${serverSource}\nfunction hasQualifying30dSale(card) { return true; }\n`));

// ── Per-game boards (server-snapshot.ts) ─────────────────────────────────────
// View cards as scopeSnapshot sees them after the global rule. One Piece mirrors
// 2026-09-25: 103 ranked, 3 without a 30d sale at game positions 4, 24, 93, so
// the global rule left them inside the game's first 100. Pokémon interleaves to
// prove the per-game filter, and is all qualifying.
const viewCard = (id, tcg, marketRank, count) => ({
  id, tcg, marketRank, rank: marketRank, viewRank: marketRank,
  windows: { "30d": { trackedSales: { count: { value: count } } } },
});
function gameSnapshot(onePieceCount, zeroAt) {
  const cards = [];
  for (let i = 1; i <= onePieceCount; i += 1) {
    cards.push(viewCard(`op${i}`, "One Piece", 2 * i, zeroAt.includes(i) ? null : 1));
    cards.push(viewCard(`pk${i}`, "Pokémon", 2 * i - 1, 3));
  }
  cards.push(viewCard("op-unranked", "One Piece", 0, 1));
  return { top100: cards.slice(0, 100), watchlist: cards.slice(100), coverage: {} };
}
const FULL_ZERO = [4, 24, 93];
const SHORT_ZERO = [2, 30];
const gameBoard = (lib, count, zeroAt, pageSize = 500) => {
  try {
    return lib.scopeSnapshot(gameSnapshot(count, zeroAt), "one-piece", { pageSize });
  } catch (error) {
    return { threw: String(error.message) };
  }
};
const leadIds = (board) => (board.lead100 ?? []).map((card) => card.id);

const game = compileGame(serverSource);
const full = gameBoard(game, 103, FULL_ZERO);
check("per-game: the board does not throw", !full.threw, full.threw);
check("per-game: zero-30d cards leave the game's first 100",
  FULL_ZERO.every((i) => !leadIds(full).includes(`op${i}`)), JSON.stringify(leadIds(full)));
check("per-game: still 100 seats, ranks 1..100 contiguous",
  full.lead100?.length === 100 && full.lead100.every((card, index) => card.rank === index + 1 && card.viewRank === index + 1));
check("per-game: the next qualifying cards move up (101, 102, 103 -> 98, 99, 100)",
  leadIds(full).slice(97).join() === "op101,op102,op103", leadIds(full).slice(97).join());
const fullList = full.top100 ?? [];
check("per-game: dropped cards follow in marketRank order at 101, 102, 103",
  FULL_ZERO.map((i) => fullList.find((card) => card.id === `op${i}`)?.rank).join() === "101,102,103");
check("per-game: every ranked card of the game stays listed, no other game's card",
  fullList.length === 103 && fullList.every((card) => card.tcg === "One Piece" && card.marketRank > 0));
check("per-game: page 1 of 100 is exactly the seated cards",
  gameBoard(game, 103, FULL_ZERO, 100).top100?.map((card) => card.id).join() === leadIds(full).join());
const shortBoard = gameBoard(game, 50, SHORT_ZERO);
check("per-game short board: no throw", !shortBoard.threw, shortBoard.threw);
check("per-game short board: lead100 holds only the 48 qualifying cards, not padded",
  shortBoard.lead100?.length === 48 && SHORT_ZERO.every((i) => !leadIds(shortBoard).includes(`op${i}`)),
  String(shortBoard.lead100?.length));
check("per-game short board: the non-qualifying rank from 101, not inside the first 100",
  SHORT_ZERO.map((i) => shortBoard.top100?.find((card) => card.id === `op${i}`)?.rank).join() === "101,102");

// Plants: each must change the per-game board.
const plantGame = (from, to) => {
  if (!serverSource.includes(from)) throw new Error(`plant anchor missing in server-snapshot.ts: ${from}`);
  return compileGame(serverSource.replace(from, to));
};
const universeSource = fnNamed(parse(serverSource), "gameUniverse").getText();
const noGameRule = gameBoard(plantGame(universeSource, universeSource.replace("hasQualifying30dSale(card)", "true")), 103, FULL_ZERO);
check("N(per-game rule removed): a zero-30d card keeps its game seat", leadIds(noGameRule).includes(`op${FULL_ZERO[0]}`));
const RANKED_LINE = "  const ranked = gameUniverse(canonical, expected);\n";
const INLINE_RANK = "  const ranked = canonical.filter((card) => card.tcg === expected && card.marketRank > 0)"
  + ".sort((a, b) => a.marketRank - b.marketRank).map((card, index) => ({ ...card, rank: index + 1, viewRank: index + 1 }));\n";
const bypassed = gameBoard(plantGame(RANKED_LINE, INLINE_RANK), 103, FULL_ZERO);
check("N(per-game path bypasses gameUniverse): a zero-30d card keeps its game seat", leadIds(bypassed).includes(`op${FULL_ZERO[0]}`));
const LEAD_LINE = "  const lead100 = ranked.filter((card) => card.rank <= TOP100_SEATS).map(listCard);\n";
const padded = gameBoard(plantGame(LEAD_LINE, "  const lead100 = ranked.slice(0, 100).map(listCard);\n"), 50, SHORT_ZERO);
check("N(per-game lead100 padded): the short board shows no-sale cards", SHORT_ZERO.some((i) => leadIds(padded).includes(`op${i}`)));

// Call site: the per-game branch of scopeSnapshot ranks through gameUniverse,
// unconditionally, and both the list and lead100 come from that ranking.
function gameCallSiteOk(text) {
  const tree = parse(text);
  const body = fnNamed(tree, "scopeSnapshot")?.body?.statements ?? [];
  const initOf = (name) => body.find((node) => ts.isVariableStatement(node)
    && node.declarationList.declarations.length === 1
    && node.declarationList.declarations[0].name.getText(tree) === name)?.declarationList.declarations[0].initializer;
  const ranked = initOf("ranked");
  const lead = initOf("lead100")?.getText(tree) ?? "";
  const cards = initOf("cards")?.getText(tree) ?? "";
  return Boolean(ranked && ts.isCallExpression(ranked) && ranked.expression.getText(tree) === "gameUniverse"
    && ranked.arguments.map((arg) => arg.getText(tree)).join() === "canonical,expected"
    && lead.startsWith("ranked.") && cards.startsWith("ranked."));
}
check("per-game call site: scopeSnapshot ranks each game through gameUniverse", gameCallSiteOk(serverSource));
check("N(per-game call site): an inline re-rank fails the check", !gameCallSiteOk(serverSource.replace(RANKED_LINE, INLINE_RANK)));
check("N(per-game call site): behind a condition the check fails",
  !gameCallSiteOk(serverSource.replace(RANKED_LINE,
    "  const ranked = process.env.CARDZ_TOP100_RULE ? gameUniverse(canonical, expected) : canonical;\n")));
check("N(per-game call site): lead100 from elsewhere fails the check",
  !gameCallSiteOk(serverSource.replace(LEAD_LINE, "  const lead100 = canonical.slice(0, 100).map(listCard);\n")));

// ── Board readers ────────────────────────────────────────────────────────────
// Page 1 of a short per-game list continues into rank 101+, so a reader that
// shows "the top N" must take scopeSnapshot's seated board (lead100), never its
// page (top100). Every top100/lead100 read of a scopeSnapshot result, direct or
// through a variable, must be lead100, and the expected scopes must be covered.
const OG_ROUTE = "apps/web/src/app/api/og/heatmap/route.tsx";
const LLMS_ROUTE = "apps/web/src/app/llms-full.txt/route.ts";
const readRepo = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const ogSource = readRepo(OG_ROUTE);
const llmsSource = readRepo(LLMS_ROUTE);
function boardReads(text, file) {
  const tree = ts.createSourceFile(file, text, ts.ScriptTarget.ES2022, true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const unwrap = (node) => (ts.isParenthesizedExpression(node) ? unwrap(node.expression) : node);
  const isScopeCall = (node) => ts.isCallExpression(node) && node.expression.getText(tree) === "scopeSnapshot";
  const bound = new Set();
  const reads = [];
  const visit = (node) => {
    if (ts.isVariableDeclaration(node) && node.initializer && isScopeCall(unwrap(node.initializer))) bound.add(node.name.getText(tree));
    ts.forEachChild(node, visit);
  };
  const collect = (node) => {
    if (ts.isPropertyAccessExpression(node) && ["top100", "lead100"].includes(node.name.text)) {
      const base = unwrap(node.expression);
      if (isScopeCall(base) || (ts.isIdentifier(base) && bound.has(base.text))) {
        reads.push({ prop: node.name.text, scope: isScopeCall(base) ? base.arguments[1]?.getText(tree) : base.text });
      }
    }
    ts.forEachChild(node, collect);
  };
  visit(tree);
  collect(tree);
  return reads;
}
const seatedOnly = (text, file, scopes) => {
  const reads = boardReads(text, file);
  return reads.length > 0 && reads.every((read) => read.prop === "lead100")
    && scopes.every((scope) => reads.some((read) => read.scope === scope));
};
check("OG heatmap draws the seated board, never page 1", seatedOnly(ogSource, OG_ROUTE, ["scoped"]));
check("N(OG): back on page 1 the check fails",
  !seatedOnly(ogSource.replace("(scoped.lead100 ?? []).slice(0, show)", "scoped.top100.slice(0, show)"), OG_ROUTE, ["scoped"]));
const LLMS_SCOPES = ['"all"', '"pokemon"', '"one-piece"'];
check("llms-full lists the seated boards, never page 1", seatedOnly(llmsSource, LLMS_ROUTE, LLMS_SCOPES));
check("N(llms-full): the One Piece table back on page 1 fails the check",
  !seatedOnly(llmsSource.replace('scopeSnapshot(snapshot, "one-piece").lead100', 'scopeSnapshot(snapshot, "one-piece").top100'),
    LLMS_ROUTE, LLMS_SCOPES));
check("N(llms-full): a page read through a variable fails the check",
  !seatedOnly(`${llmsSource}\nconst page = scopeSnapshot(snapshot, "pokemon");\nconst leak = page.top100;\n`, LLMS_ROUTE, LLMS_SCOPES));

if (failed.length) {
  console.error(`\n${failed.length} FAILED: ${failed.join(" | ")}`);
  process.exit(1);
}
console.log("\nTop100 30d-sales seat rule holds");
