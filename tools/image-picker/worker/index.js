/**
 * Cloudflare Worker: shared selection store for image picker.
 * Binding: SELECTIONS_KV (KV namespace)
 *
 * GET  /api/selections  → full JSON state
 * PUT  /api/selections  → merge+save (body: selections doc)
 * GET  /                → static asset from ASSETS (Pages)
 */

const KEY = "image-picker-selections-v1";

function cors(res) {
  const headers = new Headers(res.headers);
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Access-Control-Allow-Methods", "GET,PUT,OPTIONS");
  headers.set("Access-Control-Allow-Headers", "content-type");
  return new Response(res.body, { status: res.status, headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return cors(new Response(null, { status: 204 }));
    }

    if (url.pathname === "/api/selections") {
      if (request.method === "GET") {
        const raw = (await env.SELECTIONS_KV.get(KEY)) || null;
        const body =
          raw ||
          JSON.stringify({
            schemaVersion: 1,
            updatedAt: null,
            updatedBy: null,
            selections: {},
          });
        return cors(
          new Response(body, {
            headers: { "content-type": "application/json; charset=utf-8" },
          })
        );
      }
      if (request.method === "PUT") {
        const incoming = await request.json();
        const existingRaw = await env.SELECTIONS_KV.get(KEY);
        let existing = { selections: {} };
        if (existingRaw) {
          try {
            existing = JSON.parse(existingRaw);
          } catch {
            existing = { selections: {} };
          }
        }
        const merged = { ...(existing.selections || {}) };
        for (const [k, v] of Object.entries(incoming.selections || {})) {
          const prev = merged[k];
          if (!prev || (v.at && (!prev.at || v.at >= prev.at))) merged[k] = v;
        }
        const doc = {
          schemaVersion: 1,
          updatedAt: new Date().toISOString(),
          updatedBy: incoming.updatedBy || "remote",
          selections: merged,
        };
        await env.SELECTIONS_KV.put(KEY, JSON.stringify(doc));
        return cors(
          new Response(JSON.stringify(doc), {
            headers: { "content-type": "application/json; charset=utf-8" },
          })
        );
      }
      return cors(new Response("method not allowed", { status: 405 }));
    }

    // Static assets from Pages/ASSETS binding if present
    if (env.ASSETS) {
      return env.ASSETS.fetch(request);
    }
    return new Response("image-picker worker ok — mount static via Pages", { status: 200 });
  },
};
