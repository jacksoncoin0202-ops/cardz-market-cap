import { getMarketData, type MarketScope } from "@/lib/data/market";
import { parseRequestedPage } from "@/lib/pagination";

const scopes: MarketScope[] = ["all", "pokemon", "one-piece", "watchlist"];

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);
  const scope = searchParams.get("scope") ?? "all";
  if (!scopes.includes(scope as MarketScope)) {
    return Response.json({ error: `Unknown scope. Use one of: ${scopes.join(", ")}` }, { status: 400 });
  }
  /*
   * `page` / `pageSize` 同 /watchlist 行同一個 parser。
   *
   * 原本呢度自己有個 `positiveInt()`：`Number.parseInt` 見到前綴數字就收貨
   * （`"2zzz"` → 2），唔係數字就靜靜回 `undefined`，而 `undefined` 落
   * `scopeSnapshot` 就係「第 1 頁 / 200 條」。即係 `?page=abc` 同 `?page=1`
   * 回一模一樣嘅 200，叫方永遠唔知自己打錯。scope 打錯有 400，page 打錯冇，
   * 呢個唔一致本身就係個 bug。
   *
   * `searchParams.get()` 冇嗰個 key 會回 `null`（唔係 `undefined`），所以要
   * `?? undefined` 轉返做 parser 認嘅「冇傳」。
   */
  const rawPageSize = searchParams.get("pageSize");
  const page = parseRequestedPage(searchParams.get("page") ?? undefined);
  const pageSize = parseRequestedPage(rawPageSize ?? undefined);
  if (page === null || pageSize === null) {
    return Response.json({ error: "page and pageSize must be positive integers" }, { status: 400 });
  }
  const payload = await getMarketData(scope as MarketScope, {
    page,
    // parser 對「冇傳」回 1，嗰個 1 淨係 page 嘅正確預設；pageSize 冇傳就要留返
    // `undefined`，等 scopeSnapshot 用返佢自己個 200，唔係一版一張卡。
    pageSize: rawPageSize === null ? undefined : pageSize,
  });
  return Response.json(payload, { headers: { "Cache-Control": "public, max-age=60" } });
}
