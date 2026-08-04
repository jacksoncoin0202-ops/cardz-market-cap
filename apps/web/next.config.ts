import type { NextConfig } from "next";
import { resolve } from "node:path";

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
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
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
