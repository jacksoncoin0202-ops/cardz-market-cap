/**
 * CARDZ Image Picker — human chooses one canonical image per card.
 * Shows multi-source market prices + external links for identification.
 */

const API = (window.IMAGE_PICKER_API || "").replace(/\/$/, "");
const STORAGE_KEY = "cardz_image_picker_v1";

let worklist = null;
let selections = { schemaVersion: 1, updatedAt: null, updatedBy: null, selections: {} };
let idx = 0;

const $ = (id) => document.getElementById(id);

function actor() {
  return ($("actor").value || localStorage.getItem("cardz_picker_actor") || "anon").trim() || "anon";
}
function saveActor() {
  localStorage.setItem("cardz_picker_actor", actor());
}
/** Active pick (not cleared tombstone). */
function getPick(variantId) {
  const s = selections.selections[String(variantId)];
  if (!s || s.cleared) return null;
  return s;
}

function progress() {
  const cards = worklist?.cards || [];
  const done = cards.filter((c) => getPick(c.variantId)).length;
  return { total: cards.length, done, pct: cards.length ? (100 * done) / cards.length : 0 };
}
function updateStats() {
  const p = progress();
  const autoN = worklist?.meta?.nAuto ?? "—";
  const pol = worklist?.meta?.policy || "";
  $("stats").textContent = `爭議佇列 ${p.done} / ${p.total} (${p.pct.toFixed(1)}%)\nauto SNK/G10：${autoN}\nindex ${idx}`;
  $("progressFill").style.width = `${p.pct}%`;
  $("queueInfo").textContent = `未揀爭議：${p.total - p.done} · auto ${autoN} · ${pol} · 操作者：${actor()} · API：${API || "local only"}`;
}
function currentCard() {
  return worklist?.cards?.[idx] || null;
}
function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pricePanel(card) {
  const prices = card.prices || [];
  const sales = card.sales30d || [];
  const links = card.links || [];
  const head = `
    <div class="price-hero">
      <div class="price-big">USD ${escapeHtml(card.displayPriceUsd || "—")}</div>
      <div class="price-src">顯示口價來源：<strong>${escapeHtml(card.displayPriceSource || "—")}</strong>
        · mcap USD ${escapeHtml(card.marketCapUsd || "—")}
        · PSA10 POP ${escapeHtml(card.psa10Population ?? "—")}
        · rank ${escapeHtml(card.rankPosition ?? "—")}
      </div>
      <div class="price-note">${escapeHtml(worklist?.meta?.priceNote || "")}</div>
      <div class="price-note">入 queue原因：<strong>${escapeHtml(card.reviewReason || "—")}</strong>
        · trusted ${escapeHtml(card.trustedCount ?? "—")} / untrusted ${escapeHtml(card.untrustedCount ?? "—")}
        · distinct SHA ${escapeHtml(card.candidateCount ?? "—")}</div>
    </div>`;

  const rows =
    prices.length === 0
      ? `<tr><td colspan="4">DB 無 price_observation</td></tr>`
      : prices
          .map(
            (p) => `<tr>
        <td>${escapeHtml(p.label || p.sourceCode)}</td>
        <td class="num">$${escapeHtml(p.priceUsd)}</td>
        <td>${escapeHtml((p.effectiveAt || "").slice(0, 19))}</td>
        <td>${escapeHtml(p.metricStatus || "")}${
              p.nativePrice ? ` · native ${escapeHtml(p.nativePrice)} ${escapeHtml(p.nativeCurrency || "")}` : ""
            }</td>
      </tr>`
          )
          .join("");

  const saleRows =
    sales.length === 0
      ? `<tr><td colspan="4">30d 無成交列</td></tr>`
      : sales
          .map(
            (s) => `<tr>
        <td>${escapeHtml(s.sourceCode)}</td>
        <td class="num">${s.count30d}</td>
        <td class="num">$${escapeHtml(s.minUsd)} – $${escapeHtml(s.maxUsd)}</td>
        <td class="num">avg $${escapeHtml(s.avgUsd)}</td>
      </tr>`
          )
          .join("");

  const linkHtml = links
    .map((l) => {
      if (l.url) {
        return `<a class="ext" href="${escapeHtml(l.url)}" target="_blank" rel="noopener">${escapeHtml(l.label)}</a>`;
      }
      return `<span class="ext off" title="${escapeHtml(l.note || "")}">${escapeHtml(l.label)}: ${escapeHtml(
        l.entityId || ""
      )}</span>`;
    })
    .join(" ");

  const idHtml = (card.identities || [])
    .map(
      (i) =>
        `<code title="${escapeHtml(i.matchStatus)}">${escapeHtml(i.sourceCode)}=${escapeHtml(
          i.externalEntityId
        )}</code>`
    )
    .join(" ");

  return `
    <aside class="side">
      ${head}
      <h3>多源市價（DB）</h3>
      <table class="ptable">
        <thead><tr><th>來源</th><th>USD</th><th>effectiveAt</th><th>狀態</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <h3>30d 成交（認流動性）</h3>
      <table class="ptable">
        <thead><tr><th>source</th><th>n</th><th>min–max</th><th>avg</th></tr></thead>
        <tbody>${saleRows}</tbody>
      </table>
      <h3>外鏈（由 identity 組）</h3>
      <div class="links">${linkHtml || "—"}</div>
      <h3>Identity 綁定</h3>
      <div class="ids">${idHtml || "—"}</div>
    </aside>`;
}

function render() {
  const card = currentCard();
  const main = $("main");
  if (!card) {
    main.innerHTML = `<p class="empty">Worklist 空或已完成。可 Export JSON。</p>`;
    updateStats();
    return;
  }
  const sel = getPick(card.variantId);
  const doneBadge = sel
    ? `<span class="badge done">已揀 ${escapeHtml(sel.choiceKey)}${
        sel.skipped ? " (skip)" : ""
      } · ${escapeHtml(sel.actor || "?")}</span>`
    : `<span class="badge">未揀</span>`;
  const rePickHint = sel
    ? `<p class="repick-hint">已揀過 — 再撳另一張圖即<strong>改揀</strong>（唔會跳走）；或撳「重揀」清掉。</p>`
    : `<p class="repick-hint muted">首次揀完會自動跳未完成；Prev 返嚟可改 / U 重揀。</p>`;

  const cands = card.candidates || [];
  let body;
  if (!cands.length) {
    body = `<div class="empty">本機冇可見圖檔。請用右側口價 + 外鏈認卡，然後 Skip 或稍後補圖。已 Skip 可撳「重揀」清返。</div>`;
  } else {
    body = `<div class="candidates">${cands
      .map((c) => {
        const selected = sel && !sel.skipped && sel.contentSha256 === c.contentSha256 ? "selected" : "";
        const prov = c.provenance || {};
        return `
        <div class="cand ${selected}" tabindex="0" data-key="${c.key}" data-sha="${c.contentSha256}" role="button">
          <img src="${c.url}" alt="${c.key}" loading="lazy" />
          <div class="cap">
            <div><span class="key">${c.key}</span> ${escapeHtml(c.semanticMatchStatus || "")} ${
          c.publicAllowed ? "public" : ""
        }</div>
            <div class="prov">${escapeHtml(prov.script || "?")} · ${escapeHtml(prov.channel || "?")}</div>
            <div class="prov">${escapeHtml((c.contentSha256 || "").slice(0, 16))}…</div>
          </div>
        </div>`;
      })
      .join("")}</div>`;
  }

  main.innerHTML = `
    <div class="layout">
      <div class="left">
        <div class="card-head">
          <div class="meta">
            <h2>#${card.index} · ${escapeHtml(card.name || "")} ${doneBadge}</h2>
            ${rePickHint}
            <dl>
              <dt>variantId</dt><dd>${card.variantId}</dd>
              <dt>opaqueId</dt><dd>${escapeHtml(card.opaqueId || "")}</dd>
              <dt>TCG / set</dt><dd>${escapeHtml(card.tcg || "")} · ${escapeHtml(card.setName || "")}</dd>
              <dt>collector</dt><dd>${escapeHtml(card.collectorNumber || "")}</dd>
              <dt>candidates</dt><dd>${card.visibleCount} visible / ${card.candidateCount} in DB</dd>
            </dl>
          </div>
        </div>
        ${body}
      </div>
      ${pricePanel(card)}
    </div>`;

  main.querySelectorAll(".cand").forEach((el) => {
    el.addEventListener("click", () => pick(el.dataset.key));
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick(el.dataset.key);
      }
    });
  });
  updateClearBtn();
  updateStats();
  $("jump").value = String(idx);
}

/**
 * Clear current card selection (undo / 重揀). Stays on same card.
 * Uses tombstone (cleared:true + newer at) so CF KV merge won't resurrect old pick.
 */
async function clearPick() {
  const card = currentCard();
  if (!card) return;
  if (!getPick(card.variantId)) return;
  const vid = String(card.variantId);
  const at = new Date().toISOString();
  selections.selections[vid] = {
    variantId: card.variantId,
    opaqueId: card.opaqueId,
    name: card.name,
    choiceKey: null,
    contentSha256: null,
    cleared: true,
    actor: actor(),
    at,
  };
  selections.updatedAt = at;
  selections.updatedBy = actor();
  persistLocal();
  await pushRemote();
  render();
}

/**
 * Pick candidate key for current card.
 * - First pick on unfinished card → auto-advance to next unfinished
 * - Re-pick / change existing → stay on card (regret-friendly)
 */
async function pick(key) {
  const card = currentCard();
  if (!card) return;
  const cand = (card.candidates || []).find((c) => c.key === key);
  if (!cand) return;
  const vid = String(card.variantId);
  const had = !!getPick(card.variantId);
  const entry = {
    variantId: card.variantId,
    opaqueId: card.opaqueId,
    name: card.name,
    choiceKey: key,
    contentSha256: cand.contentSha256,
    assetId: cand.assetId,
    provenance: cand.provenance,
    displayPriceUsd: card.displayPriceUsd,
    displayPriceSource: card.displayPriceSource,
    cleared: false,
    actor: actor(),
    at: new Date().toISOString(),
  };
  selections.selections[vid] = entry;
  selections.updatedAt = entry.at;
  selections.updatedBy = entry.actor;
  persistLocal();
  await pushRemote();
  if (had) {
    // 改揀：留喺呢張，方便核對
    render();
    return;
  }
  const next = findNextUnfinished(idx + 1);
  idx = next === -1 ? Math.min(idx + 1, (worklist.cards.length || 1) - 1) : next;
  render();
}

function updateClearBtn() {
  const btn = $("btnClear");
  if (!btn) return;
  const card = currentCard();
  const has = card && !!getPick(card.variantId);
  btn.disabled = !has;
  btn.title = has ? "清除本卡揀選，可重揀（快捷鍵 U）" : "本卡未揀，無需清除";
}

function findNextUnfinished(from) {
  const cards = worklist.cards || [];
  for (let i = from; i < cards.length; i++) {
    if (!getPick(cards[i].variantId)) return i;
  }
  for (let i = 0; i < from; i++) {
    if (!getPick(cards[i].variantId)) return i;
  }
  return -1;
}

function persistLocal() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(selections));
}

async function pullRemote() {
  if (!API) return;
  try {
    const r = await fetch(`${API}/api/selections`, { cache: "no-store" });
    if (!r.ok) return;
    const remote = await r.json();
    if (remote && remote.selections) {
      const merged = { ...selections.selections };
      for (const [k, v] of Object.entries(remote.selections)) {
        const local = merged[k];
        if (!local || (v.at && (!local.at || v.at > local.at))) merged[k] = v;
      }
      selections = {
        schemaVersion: 1,
        updatedAt: remote.updatedAt || selections.updatedAt,
        updatedBy: remote.updatedBy || selections.updatedBy,
        selections: merged,
      };
      persistLocal();
    }
  } catch (e) {
    console.warn("pullRemote failed", e);
  }
}

async function pushRemote() {
  if (!API) return;
  try {
    await fetch(`${API}/api/selections`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(selections),
    });
  } catch (e) {
    console.warn("pushRemote failed", e);
  }
}

async function load() {
  const wl = await fetch("./data/worklist.json").then((r) => r.json());
  worklist = wl;
  const local = localStorage.getItem(STORAGE_KEY);
  if (local) {
    try {
      selections = JSON.parse(local);
    } catch {
      /* ignore */
    }
  }
  await pullRemote();
  const n = findNextUnfinished(0);
  idx = n === -1 ? 0 : n;
  render();
}

function exportJson() {
  const blob = new Blob([JSON.stringify(selections, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `cardz-image-selections-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
}

document.addEventListener("DOMContentLoaded", () => {
  $("actor").value = localStorage.getItem("cardz_picker_actor") || "";
  $("actor").addEventListener("change", saveActor);
  $("btnNext").onclick = () => {
    idx = Math.min(idx + 1, worklist.cards.length - 1);
    render();
  };
  $("btnPrev").onclick = () => {
    idx = Math.max(idx - 1, 0);
    render();
  };
  $("btnSkip").onclick = () => {
    const card = currentCard();
    if (card) {
      const vid = String(card.variantId);
      const had = !!getPick(card.variantId);
      selections.selections[vid] = {
        variantId: card.variantId,
        opaqueId: card.opaqueId,
        name: card.name,
        choiceKey: "SKIP",
        contentSha256: null,
        skipped: true,
        cleared: false,
        actor: actor(),
        at: new Date().toISOString(),
      };
      selections.updatedAt = selections.selections[vid].at;
      selections.updatedBy = actor();
      persistLocal();
      pushRemote();
      // 已揀過再 skip = 改狀態，留喺度；首次 skip 先跳
      if (had) {
        render();
        return;
      }
    }
    const n = findNextUnfinished(idx + 1);
    idx = n === -1 ? Math.min(idx + 1, worklist.cards.length - 1) : n;
    render();
  };
  $("btnClear").onclick = () => clearPick();
  $("btnJump").onclick = () => {
    const j = parseInt($("jump").value, 10);
    if (!Number.isNaN(j) && j >= 0 && j < worklist.cards.length) {
      idx = j;
      render();
    }
  };
  $("btnExport").onclick = exportJson;
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input,textarea")) return;
    const k = e.key.toUpperCase();
    if (k >= "A" && k <= "H") {
      e.preventDefault();
      pick(k);
    } else if (k === "N") {
      e.preventDefault();
      $("btnNext").click();
    } else if (k === "P") {
      e.preventDefault();
      $("btnPrev").click();
    } else if (k === "S") {
      e.preventDefault();
      $("btnSkip").click();
    } else if (k === "U" || k === "BACKSPACE" || e.key === "Delete") {
      e.preventDefault();
      clearPick();
    } else if (k === "R") {
      e.preventDefault();
      pullRemote().then(render);
    }
  });
  load();
  setInterval(() => pullRemote().then(updateStats), 20000);
});
