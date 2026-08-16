import type { MetadataRoute } from "next";

/*
 * PWA manifest（/manifest.webmanifest）。theme/background 揀 light `--paper`：
 * manifest 冇 media query，dark 由 <meta name="theme-color"> 喺 runtime 跟返（ThemeScript）。
 * icons 由 public/brand/icon-transparent.png 用 sharp 出（scripts 冇入 repo，一次過生成）；
 * maskable 嗰張內容縮到 80%，留 20% 安全區。
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "CardZ Marketcap",
    short_name: "CardZ",
    description: "Art market intelligence for collectible cards.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#f7f7f5",
    theme_color: "#f7f7f5",
    lang: "en",
    categories: ["finance", "shopping", "entertainment"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512-maskable.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
