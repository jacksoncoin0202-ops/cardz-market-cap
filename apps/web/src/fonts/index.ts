import localFont from "next/font/local";

/*
 * 全站唯一 web font：Inter Variable（拉丁 subset）。owner 2026-08-17 推翻 DESIGN.md §8 決定 5：
 * `--font-sans` 個 system stack 喺 Windows 冇一隻 Latin family 存在，實際跌落 Yu Gothic（日文字），
 * 拉丁字形又粗又唔啱，兼且 static weight 令 heading(500) 輕過 label(600)。呢個係 bug，唔係品味。
 *
 * 來源：npm `@fontsource-variable/inter@5.3.0` → `files/inter-latin-wght-normal.woff2`
 *   sha256 3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62（48,256 bytes，2026-08-17 取）
 *   覆蓋 U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329,
 *   U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD。
 *   唔收 latin-ext（+85 kB）：CJK / 諺文一個 glyph 都唔喺度，靠 `--font-sans` 後面嘅 OS 字逐字 fallback；
 *   ₩ ₹ ₱ ₫ ₪ ₺ ฿ 呢類貨幣符號一樣行 OS fallback（有得揀嗰啲 locale 先見到）。
 * 授權：SIL OFL 1.1，全文喺隔籬 OFL.txt（binary 派發必須同行）。
 *
 * 點解手抄 binary 唔 `npm i`：scripts/test-lockfile-prod-pins.mjs 講明任何 npm i 都可能 re-hoist
 * caniuse-lite / browserslist → 靜靜換咗 prod build 目標。commit 一份 woff2 = 同一 SHA 永遠同一份字。
 * 點解唔用 next/font/google：webhook `docker compose up --build` 嘅 build stage 要出外網攞字體，
 * 一次 DNS / egress 失敗 = deploy fail；而且 google 版最終都係 self-host，CSP 上零分別。
 *
 * 用法：只准喺 layout.tsx 落 `<html className={inter.variable}>`（一定要 <html>，因為 globals.css
 * `--font-sans` 住喺 :root，--font-inter 淨係喺 body 定義嘅話成條 --font-sans 變 invalid，全站跌 serif），
 * 再由 globals.css 個 --font-sans token 打頭 consume。call site 唔准直接用、唔准硬寫 Inter 個名
 * （next/font 出嘅 family 係 hash 名 `__Inter_xxxxxx`）。scripts/test-fe-font-contract.mjs 守住呢幾條。
 */
export const inter = localFont({
  src: [{ path: "./InterVariable-latin.woff2", weight: "100 900", style: "normal" }],
  variable: "--font-inter",
  display: "swap",
  preload: true,
  /* Arial 做 metric fallback：next/font 會出第二個 @font-face（size-adjust / ascent-override /
     descent-override 逐 metric 對齊 Inter），swap 一刻幾何近乎唔郁 → 守 CLS ≤ 0.01。 */
  adjustFontFallback: "Arial",
});
