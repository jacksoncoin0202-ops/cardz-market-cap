import type { NextConfig } from "next";
import { resolve } from "node:path";

/*
 * 已經出咗街、而家改咗嘅公開 card id。
 *
 * 呢幾張卡嘅 `catalog_variant.opaque_id` 帶住供應商前綴，而 opaque_id 就係公開
 * URL —— 三條已經入咗生產 sitemap，爬蟲同任何人 share 過嘅 link 都指住佢哋。
 * DB 側嘅修法係 public_card_alias（migration 041）：opaque_id 唔郁，投影層改出
 * 一個 cmc_ 公開 id。所以舊 URL 由嗰日起冇卡對得上，硬 404。呢張表就係補償。
 *
 * 放喺呢個檔入面唔係求其：呢張表唯一嘅消費者就係下面個 redirects()，而
 * pipelines/public_card_alias.py 鑄 alias 嗰陣會讀返呢個檔對數，鑄咗但冇寫落嚟
 * 就當今次冇做完。多開一個 module 只係多一個可以靜靜過期嘅位。
 */
const LEGACY_CARD_IDS: Record<string, string> = {
  // variant 1813 — One Piece 1st Anniversary Nami OP01-016（未上榜，補齊）
  g10_e4e84430353855ea95c1263e: "cmc_cc42023d0dfe45a89e9a7d34",
  // variant 1814 — One Piece 1st Anniversary Roronoa Zoro OP01-025
  g10_036812c0a409b0fef6ba5dff: "cmc_da97db523a08ba2b01132e2f",
  // variant 1865 — Pokemon Japanese McDonald's Charmander 004/018
  g10_c43dd6aa54b7671068938692: "cmc_4078d2704951804758fe7ca3",
  // variant 1866 — Pokemon Japanese McDonald's Squirtle 007/018
  g10_356a7d75453fba4d71586411: "cmc_909295e09fbe7e1f3c7b44f4",
};

const repositoryRoot = resolve(process.cwd(), "..", "..");
const isDevelopment = process.env.NODE_ENV === "development";
const requestedBuildId = process.env.CARDZ_PUBLIC_BUILD_ID?.trim() ?? "local";
const publicBuildId = /^[A-Za-z0-9._-]{1,64}$/.test(requestedBuildId)
  ? requestedBuildId
  : "invalid-build-id";

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  `script-src 'self' 'unsafe-inline'${isDevelopment ? " 'unsafe-eval'" : ""}`,
  "script-src-attr 'none'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self'${isDevelopment ? " ws: wss:" : ""}`,
  "worker-src 'self' blob:",
  "media-src 'self'",
  "manifest-src 'self'",
  "frame-src 'none'",
  ...(isDevelopment ? [] : ["upgrade-insecure-requests"]),
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-DNS-Prefetch-Control", value: "off" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  { key: "Origin-Agent-Cluster", value: "?1" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()",
  },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
  { key: "X-CARDZ-Build", value: publicBuildId },
];

/* CARDZ_BUILD_TARGET=node 出 .next/standalone 自帶 server.js（AWS / Docker）。
   唔設就照舊出標準 build，Cloudflare OpenNext 路徑不受影響。 */
const standaloneOutput = process.env.CARDZ_BUILD_TARGET === "node";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  /* 本機工程版：紅色 issue badge 同 build activity 遮內容，唔需要。 */
  devIndicators: false,
  /* dev server 自認 localhost；用 127.0.0.1 開頁時，帶 Origin 嘅 dev 資源請求
     （dynamic import / RSC fetch / font）會被 Next 16 預設 403，client hydration
     即死 —— SSR 內容照見、client-only 組件（heatmap）空白。實測 403 已重現。 */
  allowedDevOrigins: ["127.0.0.1"],
  productionBrowserSourceMaps: false,
  ...(standaloneOutput ? { output: "standalone" as const } : {}),
  outputFileTracingRoot: repositoryRoot,
  turbopack: { root: repositoryRoot },
  experimental: {
    optimizePackageImports: ["d3-hierarchy"],
  },
  generateBuildId: async () => publicBuildId,
  /* 改過公開 id 嘅卡：舊 URL 已經入咗生產 sitemap，冇呢啲就硬 404。
     308 唔係 301：Next 嘅 `permanent` 出 308，搜尋引擎當佢一樣係永久轉向，
     但唔准 client 將 POST 改寫做 GET —— 對 /api/v1/cards/* 嚟講先啱。 */
  async redirects() {
    return Object.entries(LEGACY_CARD_IDS).flatMap(([oldId, newId]) => [
      { source: `/card/${oldId}`, destination: `/card/${newId}`, permanent: true },
      { source: `/api/v1/cards/${oldId}`, destination: `/api/v1/cards/${newId}`, permanent: true },
    ]);
  },
  async headers() {
    const htmlCacheControl = {
      key: "Cache-Control",
      value: "public, s-maxage=300, stale-while-revalidate=300",
    };
    const htmlCacheSources = [
      "/",
      "/pokemon",
      "/one-piece",
      "/watchlist",
      "/card/:id",
    ];
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
      ...htmlCacheSources.map((source) => ({
        source,
        headers: [htmlCacheControl],
      })),
      {
        source: "/api/:path*",
        headers: [
          {
            key: "X-Robots-Tag",
            value: "noindex, nofollow, noarchive, nosnippet",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
