#!/usr/bin/env node
/*
 * apps/web/src/lib/live-db-snapshot.ts: a human-rejected (variant, sha) can
 * never ship. Part 1 runs the extracted assertNoRejectedImagePublished on
 * fixtures shaped on the 2026-09-25 registry; part 2 proves the build calls it
 * unconditionally before the snapshot is returned. Every check has a plant.
 */
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const failed = [];
const check = (label, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${!ok && detail ? `: ${detail}` : ""}`);
  if (!ok) failed.push(label);
};

const source = readFileSync(new URL("../apps/web/src/lib/live-db-snapshot.ts", import.meta.url), "utf8");
const parse = (text) => ts.createSourceFile("live-db-snapshot.ts", text, ts.ScriptTarget.ES2022, true);
const fnNamed = (tree, name) =>
  tree.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === name);

function compile(declarationText) {
  const javascript = ts.transpileModule(`${declarationText}\nglobalThis.__gate = assertNoRejectedImagePublished;`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const sandbox = { exports: {} };
  vm.runInNewContext(javascript, sandbox);
  if (typeof sandbox.__gate !== "function") throw new Error("assertNoRejectedImagePublished did not compile");
  return sandbox.__gate;
}

const tree = parse(source);
const declaration = fnNamed(tree, "assertNoRejectedImagePublished");
if (!declaration) throw new Error("assertNoRejectedImagePublished declaration missing");
const gate = compile(declaration.getText(tree));

// 1. The rule, on the two cross-variant reuses measured against the live registry.
const sha = (prefix) => prefix + "0".repeat(64 - prefix.length);
const DAE7 = sha("dae7a026ec6c");
const S739 = sha("739a706860ba");
const REGISTRY = [
  { variant_id: 260, content_sha256: DAE7 },
  { variant_id: 1039, content_sha256: S739 },
];
const throws = (fn, published) => {
  try {
    fn(published, REGISTRY);
    return null;
  } catch (error) {
    return String(error.message);
  }
};

const same = throws(gate, [{ variantId: 260, sha256: DAE7 }]);
check("a rejected (variant, sha) throws", same !== null);
check("the throw says contract mismatch (terminal in the V2 classifier)", Boolean(same?.includes("contract mismatch")), same);
check("the throw names the pair", Boolean(same?.includes("260:dae7a026ec6c")), same);
const crossVariant = [{ variantId: 661, sha256: DAE7 }, { variantId: 652, sha256: S739 }];
check("the same scan on another variant passes (live reuse 661/652)", throws(gate, crossVariant) === null);
check("an uppercase sha still throws", throws(gate, [{ variantId: 260, sha256: DAE7.toUpperCase() }]) !== null);
check("a card with no image is ignored", throws(gate, [{ variantId: 260, sha256: "" }]) === null);
check("a string variant id from the driver still matches",
  throws((p) => gate(p, REGISTRY.map((r) => ({ ...r, variant_id: String(r.variant_id) }))), [{ variantId: 260, sha256: DAE7 }]) !== null);

// Plant: a sha-only key would red the live 661/652 reuse; the fixture must see it.
const shaOnly = compile(
  declaration.getText(tree).replace(
    "const key = (variantId: unknown, sha: unknown) => `${Number(variantId)}:${String(sha ?? \"\").toLowerCase()}`;",
    "const key = (_variantId: unknown, sha: unknown) => String(sha ?? \"\").toLowerCase();",
  ),
);
check("N(key): a sha-only key is caught by the cross-variant fixture", throws(shaOnly, crossVariant) !== null);
const noop = compile(declaration.getText(tree).replace("if (hits.length) {", "if (false) {"));
check("N(throw): without the throw the rejected fixture is caught", throws(noop, [{ variantId: 260, sha256: DAE7 }]) === null);

// 2. The build calls it, unconditionally, on every card, before returning.
function callSiteOk(text) {
  const build = fnNamed(parse(text), "buildLiveDbSnapshot");
  const attempt = build?.body?.statements.find((node) => ts.isTryStatement(node));
  if (!attempt) return false;
  const statements = attempt.tryBlock.statements;
  const at = (predicate) => statements.findIndex(predicate);
  const call = at((node) =>
    ts.isExpressionStatement(node) && ts.isCallExpression(node.expression)
    && node.expression.expression.getText() === "assertNoRejectedImagePublished");
  const read = at((node) => node.getText().includes('"SELECT variant_id,content_sha256 FROM market_image_rejection_registry"'));
  const ret = at((node) => ts.isReturnStatement(node) && node.expression?.getText() === "normaliseSnapshot(snapshot)");
  if (call < 0 || read < 0 || ret < 0 || !(read < call && call < ret)) return false;
  const [published, rejected] = statements[call].expression.arguments;
  return Boolean(published?.getText().startsWith("coreRows.map(") && published.getText().includes("images.get(")
    && rejected?.getText() === "rejectedImages");
}

const callText = source.slice(source.indexOf("    assertNoRejectedImagePublished("), source.indexOf("    // metric_accepted_at"));
check("the build calls the gate on every card before returning", callSiteOk(source));
check("N(call site): with the call removed the check fails", !callSiteOk(source.replace(callText, "")));
check("N(call site): behind a condition the check fails",
  !callSiteOk(source.replace(callText, `    if (process.env.CARDZ_IMAGE_GATE) {\n${callText}    }\n`)));
check("N(call site): on the top-100 slice only the check fails",
  !callSiteOk(source.replace(callText, callText.replace("coreRows.map(", "coreRows.slice(0, 100).map("))));

if (failed.length) {
  console.error(`\n${failed.length} FAILED: ${failed.join(" | ")}`);
  process.exit(1);
}
console.log("\nimage rejection publish gate holds");
