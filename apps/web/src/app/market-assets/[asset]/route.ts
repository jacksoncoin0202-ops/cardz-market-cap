import { cloudflareEnv } from "@/lib/cloudflare-env";
import { MARKET_ASSET_CACHE_CONTROL, marketAssetHash, marketAssetObjectKey } from "@/lib/market-media";
import { parseSnapshotPointer } from "@/lib/snapshot-pointer";

interface MarketMediaObject {
  body: ReadableStream;
  httpEtag?: string;
  size?: number;
  text(): Promise<string>;
}

interface MarketMediaBucket {
  get(key: string): Promise<MarketMediaObject | null>;
}

interface StaticAssetsBinding {
  fetch(request: Request): Promise<Response>;
}

type MarketMediaEnvironment = {
  MARKET_DATA?: MarketMediaBucket;
  ASSETS?: StaticAssetsBinding;
  CARDZ_ENVIRONMENT?: string;
  MARKET_DATA_POINTER_KEY?: string;
};

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
  request: Request,
  { params }: { params: Promise<{ asset: string }> },
): Promise<Response> {
  const { asset } = await params;
  const key = marketAssetObjectKey(asset);
  const hash = marketAssetHash(asset);
  if (!key || !hash) return notFound();

  try {
    const environment = await cloudflareEnv<MarketMediaEnvironment>();
    if (!environment) throw new Error("Cloudflare bindings unavailable");
    const bucket = environment.MARKET_DATA;
    if (!bucket) throw new Error("Market data binding unavailable");
    const pointerKey = environment.MARKET_DATA_POINTER_KEY ?? "latest.json";
    const pointerObject = await bucket.get(pointerKey);
    if (!pointerObject) throw new Error("Snapshot pointer unavailable");
    const pointer = parseSnapshotPointer(JSON.parse(await pointerObject.text()));
    /* derivative（_200/_600）嘅 base hash 要喺 pointer.media.hashes 先入到嚟 */
    if (!pointer.media.hashes.includes(hash)) return notFound();
    const object = await bucket.get(key);

    if (object?.body) {
      const headers = new Headers({
        "Cache-Control": MARKET_ASSET_CACHE_CONTROL,
        "Content-Type": "image/webp",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
      });
      if (object.httpEtag) headers.set("ETag", object.httpEtag);
      if (typeof object.size === "number") headers.set("Content-Length", String(object.size));
      return new Response(object.body, { headers });
    }

    if (environment.CARDZ_ENVIRONMENT === "local" && environment.ASSETS) {
      return environment.ASSETS.fetch(request);
    }
  } catch {
    // `next dev` serves the same content-addressed files from public/ directly.
  }

  return notFound();
}
