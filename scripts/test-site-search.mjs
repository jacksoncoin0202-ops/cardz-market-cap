#!/usr/bin/env node
// Exercise the real component's rendered states and keyboard handlers offline.
// Hooks and I/O are controlled; React elements and the five locale copies are real.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(ROOT, "apps/web/package.json"));
const ts = require("typescript");
const React = require("react");
const jsx = require("react/jsx-runtime");
const source = readFileSync(join(ROOT, "apps/web/src/components/site-search.tsx"), "utf8");

function load(sourceText, imports, globals = {}) {
  const module = { exports: {} };
  const output = ts.transpileModule(sourceText, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  runInNewContext(output, {
    module, exports: module.exports,
    require: (name) => {
      assert.ok(name in imports, `unexpected import: ${name}`);
      return imports[name];
    },
    ...globals,
  });
  return module.exports;
}

const { copy } = load(readFileSync(join(ROOT, "apps/web/src/lib/i18n.ts"), "utf8"), {});
const cards = ["moon-one", "moon-two"].map((id, index) => ({
  id, kind: "card", href: `/card/${id}`, name: { en: id },
  image: { url: "/fixture.webp" }, marketRank: index + 1, tcg: "Pokémon",
}));

function nodes(element) {
  if (Array.isArray(element)) return element.flatMap(nodes);
  if (!React.isValidElement(element)) return [];
  return [element, ...nodes(element.props.children)];
}

function text(element) {
  if (Array.isArray(element)) return element.map(text).join("");
  if (React.isValidElement(element)) return text(element.props.children);
  return typeof element === "string" ? element : "";
}

function harness(sourceText, locale = "en") {
  const states = [], effects = [], requests = [], navigations = [];
  let stateIndex, effectIndex, tree;
  const hooks = {
    useId: () => "search-fixture",
    useRef: () => ({ current: null }),
    useState: (initial) => {
      const slot = stateIndex++;
      if (!(slot in states)) states[slot] = initial;
      return [states[slot], (value) => { states[slot] = typeof value === "function" ? value(states[slot]) : value; }];
    },
    useEffect: (setup, deps) => {
      const slot = effectIndex++;
      const previous = effects[slot];
      if (!previous || deps.some((value, index) => !Object.is(value, previous.deps[index]))) {
        effects[slot] = { setup, deps, cleanup: previous?.cleanup, pending: true };
      }
    },
  };
  const { SiteSearch } = load(sourceText, {
    react: hooks, "react/jsx-runtime": jsx,
    "next/link": { default: "a" },
    "lucide-react": { Search: "svg", X: "svg" },
    "./card-image": { handleCardImageError() {} },
    "@/lib/catalog-client": {
      prefetchCatalog() {},
      loadCatalog: () => new Promise((resolve, reject) => requests.push({ resolve, reject })),
    },
    "@/lib/catalog-search": {
      CATALOG_HEADER_CAP: 8,
      displayCatalogName: (entry) => entry.name.en,
      searchCatalog: (entries, query) => entries.filter((entry) => entry.name.en.includes(query)),
    },
    "@/lib/haptic": { tap: { select() {} } },
    "@/lib/i18n": { copy },
    "@/lib/use-market-settings": { useMarketSettings: () => ({ locale, href: (path) => path }) },
  }, {
    window: { addEventListener() {}, removeEventListener() {}, location: { assign: (url) => navigations.push(url) } },
    document: { addEventListener() {}, removeEventListener() {} },
    queueMicrotask: (callback) => callback(),
  });
  const render = () => {
    stateIndex = effectIndex = 0;
    tree = SiteSearch();
    for (const effect of effects) if (effect.pending) {
      effect.cleanup?.();
      effect.cleanup = effect.setup();
      effect.pending = false;
    }
  };
  const find = (predicate) => nodes(tree).find(predicate);
  const key = (key, nativeEvent = {}) => {
    let prevented = false;
    find((node) => node.type === "input").props.onKeyDown({
      key, nativeEvent: { isComposing: false, keyCode: 0, ...nativeEvent },
      preventDefault: () => { prevented = true; },
    });
    render();
    return prevented;
  };
  render();
  return {
    requests, navigations, key, find,
    toggle() { find((node) => node.props.className === "select-control site-search-toggle").props.onClick(); render(); },
    type(value) { find((node) => node.type === "input").props.onChange({ target: { value } }); render(); },
    async settle() { await new Promise(setImmediate); render(); },
    selected() { return find((node) => node.props.role === "option" && node.props["aria-selected"])?.props.href; },
  };
}

const isEmpty = (h) => Boolean(h.find((node) => node.props.className === "site-search-empty"));

async function stateContract(sourceText, locale) {
  const h = harness(sourceText, locale);
  h.toggle();
  h.type("moon");
  assert.equal(text(h.find((node) => node.props.role === "status")), copy[locale].labels.searchLoading);
  assert.equal(isEmpty(h), false, `${locale}: loading is not an empty result`);
  h.requests[0].reject(new Error("offline fixture"));
  await h.settle();
  assert.equal(text(h.find((node) => node.props.role === "alert")), copy[locale].labels.searchLoadError);
  assert.equal(isEmpty(h), false, `${locale}: fetch failure is not an empty result`);
  h.toggle();
  h.toggle();
  assert.equal(text(h.find((node) => node.props.role === "status")), copy[locale].labels.searchLoading);
  assert.equal(h.requests.length, 2, "reopening uses the existing catalog loading path");
  h.requests[1].resolve({ entries: [] });
  await h.settle();
  assert.equal(isEmpty(h), true, `${locale}: a successful empty catalog shows no results`);
  assert.equal(h.find((node) => node.props.role === "alert"), undefined);
  h.type("");
  assert.equal(isEmpty(h), false, "a blank query does not report an empty result");
}

async function keyboardContract(sourceText) {
  const h = harness(sourceText);
  h.toggle();
  h.type("moon");
  h.requests[0].resolve({ entries: cards });
  await h.settle();
  assert.equal(h.selected(), "/card/moon-one");
  for (const composing of [{ isComposing: true }, { keyCode: 229 }]) {
    for (const key of ["Enter", "ArrowDown", "ArrowUp", "Escape"]) {
      assert.equal(h.key(key, composing), false, `IME ${key} retains its native behavior`);
      assert.equal(h.selected(), "/card/moon-one", `IME ${key} cannot change the selected result or close search`);
      assert.equal(h.navigations.length, 0, "confirming IME text cannot navigate");
    }
  }
  assert.equal(h.key("ArrowDown"), true);
  assert.equal(h.selected(), "/card/moon-two");
  assert.equal(h.key("Enter"), true);
  assert.deepEqual(h.navigations, ["/card/moon-two"]);
  assert.equal(h.key("ArrowUp"), true);
  assert.equal(h.selected(), "/card/moon-one");
  assert.equal(h.key("Escape"), true);
  assert.equal(h.find((node) => node.type === "input"), undefined);
}

for (const locale of Object.keys(copy)) await stateContract(source, locale);
await keyboardContract(source);

// Plant each original defect only in memory; behavioral assertions must reject it.
const mutations = [
  ["IME navigation", 'if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;', "", keyboardContract],
  ["pending catalog shown as empty", "&& Array.isArray(entries) && !hits.length", "&& !hits.length", (value) => stateContract(value, "en")],
  ["catalog failure shown as empty", 'setEntries("error")', "setEntries([])", (value) => stateContract(value, "en")],
];
for (const [label, before, after, contract] of mutations) {
  const mutated = source.replace(before, after);
  assert.notEqual(mutated, source, `mutation target exists: ${label}`);
  await assert.rejects(() => contract(mutated), { name: "AssertionError" }, `regression catches ${label}`);
}
console.log("PASS site-search: five locales, loading/error/empty states, IME and normal keyboard behavior; 3 original defects rejected in memory");
