/*
 * Cloudflare bindings 只喺 Workers runtime 存在。
 *
 * 喺標準 Node server（AWS ECS / EC2 / Docker）唔准直接叫 `getCloudflareContext`：
 * 佢喺 Node runtime 會 `await import("wrangler")` 再開一個 workerd/miniflare
 * 子進程去模擬 binding。node_modules 冇 wrangler 就每個 request 白拋一次
 * MODULE_NOT_FOUND；有 wrangler（例如喺 repo checkout 直接 `next start`）
 * 就會靜靜雞起一個 workerd 讀 wrangler.jsonc。兩樣喺 AWS 上都唔應該發生。
 *
 * 設 `CARDZ_RUNTIME=node` 就完全跳過呢條路，回 null，
 * 上層照原本「binding unavailable」嘅 fallback 行。
 */

export function isNodeRuntime(): boolean {
  return process.env.CARDZ_RUNTIME === "node";
}

export async function cloudflareEnv<T>(): Promise<T | null> {
  if (isNodeRuntime()) return null;
  try {
    const { getCloudflareContext } = await import("@opennextjs/cloudflare");
    const context = await getCloudflareContext({ async: true });
    return context.env as unknown as T;
  } catch {
    return null;
  }
}
