import type { MetadataRoute } from "next";
import { DEFAULT_RANKING_PAGE_SIZE, rankingPageCount } from "@/lib/pagination";
import { PUBLIC_SITE_URL } from "@/lib/public-site";
import { seoRoutes } from "@/lib/seo-routes";
import { loadMarketSnapshot } from "@/lib/server-snapshot";
import type { MarketCardView } from "@/lib/types";

/* volume 換咗 seed 之後要跟 runtime 讀，唔可以 bake 死喺 next build。 */
export const dynamic = "force-dynamic";

const siteUrl = PUBLIC_SITE_URL;

/*
 * 內容頁（/methodology、/about、/faq…）冇 snapshot 日期可跟 —— 佢哋係人手寫嘅文，
 * 唔應該日日扮更新（每日假 lastmod 係搜尋引擎最快學識忽略你 sitemap 嘅方法）。
 * 改咗文案先手動 bump 呢個常數。owner GEO 批：2026-08-16。
 */
const CONTENT_UPDATED = "2026-08-16";

/* 單一 sitemap 上限係 50,000 條。過 45,000 就要開 generateSitemaps() 分片。 */
const SPLIT_THRESHOLD = 45_000;

const locales = {
  en: "",
  "zh-Hant": "lang=zh-TW",
  "zh-Hans": "lang=zh-CN",
  ja: "lang=ja",
  ko: "lang=ko",
  "x-default": "",
} as const;

function withQuery(path: string, query: string): string {
  if (!query) return path;
  return path.includes("?") ? `${path}&${query}` : `${path}?${query}`;
}

/*
 * Next MetadataRoute 目前會將 alternate href 原樣寫入 XML；帶第二個 query
 * parameter 時，裸 `&` 會令嚴格 XML parser（Naver）直接拒收整份 sitemap。
 */
function xmlAttributeUrl(url: string): string {
  return url.replaceAll("&", "&amp;");
}

interface EntryOptions {
  images?: string[];
  priority?: number;
}

/* lib/seo-routes.ts 由同一批 GEO 嘅另一位 owner 出；呢個就係約定嘅回傳形狀。 */
interface SeoRouteEntry {
  path: string;
  lastModified?: string;
  priority?: number;
}

function entry(path: string, lastModified: string, options: EntryOptions = {}): MetadataRoute.Sitemap[number] {
  return {
    url: `${siteUrl}${path}`,
    lastModified,
    ...(options.priority === undefined ? {} : { priority: options.priority }),
    ...(options.images?.length ? { images: options.images } : {}),
    alternates: {
      languages: Object.fromEntries(
        Object.entries(locales).map(([locale, query]) => [
          locale,
          xmlAttributeUrl(`${siteUrl}${withQuery(path, query)}`),
        ]),
      ),
    },
  };
}

/*
 * 一張卡嘅 lastmod = 佢最後一個每日歷史點嘅日期，唔係 snapshot 日期。
 * 全站 1,900+ 條 URL 同一日改一次，搜尋引擎會當你成個 sitemap 嘅 lastmod 冇資訊；
 * 逐卡跟返自己條歷史，先分得出邊張真係郁咗。攞唔到就 fallback snapshot effectiveAt。
 */
function cardLastModified(card: MarketCardView, fallback: string): string {
  const last = card.historyDaily.at(-1)?.at;
  return last || card.marketCap.asOf || card.pricePsa10.asOf || fallback;
}

function cardImages(card: MarketCardView): string[] {
  /* placeholder 唔入 sitemap：submit 一張 svg 佔位圖對圖片搜尋冇價值，仲會拉低信號。 */
  return card.image.kind === "raw_front" ? [`${siteUrl}${card.image.url}`] : [];
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const snapshot = await loadMarketSnapshot();
  const rankingPages = rankingPageCount(snapshot.top100.length + snapshot.watchlist.length, DEFAULT_RANKING_PAGE_SIZE);
  const boxAsOf = snapshot.sealed?.asOf || snapshot.effectiveAt;

  /* 榜頁／市場面：跟 snapshot 日期，佢真係日日重算。 */
  const marketPaths = [
    "/",
    "/pokemon",
    "/one-piece",
    ...Array.from({ length: Math.max(rankingPages - 1, 0) }, (_, index) => `/?page=${index + 2}`),
  ];
  if (snapshot.sealed?.products.length) marketPaths.push("/box");

  /* 人手內容頁：跟 CONTENT_UPDATED。 */
  const contentPaths = [
    "/methodology",
    "/about",
    "/faq",
    "/glossary",
    "/data",
    "/rankings",
    "/market-report",
  ];

  const cards = [...snapshot.top100, ...snapshot.watchlist];
  const boxes = snapshot.sealed?.products ?? [];

  const collected: MetadataRoute.Sitemap = [
    ...marketPaths.map((path) => entry(path, snapshot.effectiveAt, { priority: path === "/" ? 1 : 0.8 })),
    ...contentPaths.map((path) => entry(path, CONTENT_UPDATED, { priority: 0.7 })),
    /* 程式化路由（/rankings/*、/pokemon/set/*…）由 lib/seo-routes.ts 出，唔喺呢度硬砌。 */
    ...seoRoutes(snapshot).map((route: SeoRouteEntry) =>
      entry(route.path, route.lastModified || CONTENT_UPDATED, { priority: route.priority ?? 0.6 }),
    ),
    ...cards.map((card) =>
      entry(`/card/${card.id}`, cardLastModified(card, snapshot.effectiveAt), {
        images: cardImages(card),
        priority: card.marketRank >= 1 && card.marketRank <= 100 ? 0.9 : 0.5,
      }),
    ),
    ...boxes.map((product) => entry(`/box/${product.id}`, boxAsOf, { priority: 0.5 })),
  ];

  /*
   * 去重：`/rankings` 同 `/market-report` 兩邊都出（呢度嘅內容頁清單 + lib/seo-routes.ts）。
   * 同一條 URL 喺一份 sitemap 出兩次係硬錯。後寫嘅贏 —— seoRoutes 用 snapshot effectiveAt，
   * 而嗰兩版真係由 snapshot 生出嚟，跟數據日期啱過跟人手 CONTENT_UPDATED。
   * Map.set 覆蓋值但唔郁位置，所以次序照舊。
   */
  const merged = new Map<string, MetadataRoute.Sitemap[number]>();
  for (const item of collected) merged.set(item.url, item);
  const urls = [...merged.values()];

  /*
   * 2026-08-16 實測（seed-snapshot db3308_74effa09a06a3746）：1,925 條 + seoRoutes
   * ＝ 1,599 張卡（全部帶圖）+ 307 個 box + 榜頁分頁 + 7 條內容頁，
   * 離 45,000 好遠。
   * 呢個 warn 唔係裝飾：卡數係日日變嘅，過咗閂就要真係開 generateSitemaps() 分片，
   * 唔係扮睇唔到。
   */
  if (urls.length > SPLIT_THRESHOLD) {
    console.warn(`sitemap: ${urls.length} URLs > ${SPLIT_THRESHOLD} —— 要開 generateSitemaps() 分片`);
  }
  return urls;
}
