import { MARKET_ASSET_CACHE_CONTROL, marketAssetObjectKey } from "@/lib/market-media";
import { loadNodeMarketAsset } from "@/lib/server-snapshot";

function notFound(): Response {
  return new Response("Not found", {
    status: 404,
    headers: {
      "Cache-Control": "public, max-age=60",
      "Content-Type": "text/plain; charset=utf-8",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ asset: string }> },
): Promise<Response> {
  const { asset } = await params;
  if (!marketAssetObjectKey(asset)) return notFound();

  const localAsset = await loadNodeMarketAsset(asset);
  if (!localAsset) return notFound();

  return new Response(localAsset.body, {
    headers: {
      "Cache-Control": MARKET_ASSET_CACHE_CONTROL,
      "Content-Length": String(localAsset.body.byteLength),
      "Content-Type": "image/webp",
      "Cross-Origin-Resource-Policy": "same-origin",
      "X-CARDZ-Generation": localAsset.generation,
      "X-Content-Type-Options": "nosniff",
    },
  });
}
