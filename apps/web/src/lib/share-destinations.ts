/*
 * 「張圖送去邊」→「用邊個 format」嘅唯一一張表。
 *
 * owner 2026-08-20：「因應佢喺任何嘅地方，就俾返張最合適嘅圖。」送圖嗰條 cron 鏈
 * （HERMES）**唔喺呢個 repo** —— 佢做完自動鏈就 download 張圖再 upload 去 WhatsApp／
 * X／Threads。所以條鏈應該淨係寫佢送去邊（`?format=whatsapp`），「WhatsApp 用邊個
 * 尺寸」呢個決定留喺呢度。日後邊個平台要換比例 = 改呢張表，條鏈一行都唔使郁。
 *
 * ⚠️ 今日三個貼圖目的地全部指去 `post`（1080×1350，4:5），**唔係求其填**：
 *   · IG feed 直度上限就係 4:5（闊÷高 0.8），再高會裁。
 *   · Threads 跟 IG 一路。owner 2026-08-19 實測 9:16 貼 Threads／X 會縮到七八成闊
 *     （見 og route 檔頭「4:5 唔准改返 9:16」同 `lib/share-image.ts` SHARE_MIN_ASPECT）。
 *   · WhatsApp 對話／群組個氣泡保持比例唔裁，4:5 佔到最高，撳落去全屏放大。
 * 即係話「每個平台一個尺寸」呢件事，喺今日呢三個目的地上**答案啱啱好一樣**。
 * 硬要各出一個唔同比例，會有一兩個變差。
 *
 * 要拆嘅時候：喺 og route 加多個 ShareFormat（連 layout），再改呢度指過去 —— 唔係
 * 喺條鏈度改，亦唔係喺呢度亂作一個 route 唔識嘅 format 名（下面個 guard 會擋）。
 */
export const SHARE_FORMATS = ["wide", "post"] as const;
export type ShareFormat = (typeof SHARE_FORMATS)[number];

/*
 * key 一律小楷；`readShareFormat` 會幫叫方 lowercase，所以呢度唔准出現大楷。
 *
 * 分兩組，因為兩組係**兩件唔同嘅事**，唔好因為名似就撈埋：
 *  · 貼圖 = 真係 send 一張圖出去（人手 save 落相簿再貼，或者條鏈 upload）→ 4:5。
 *  · unfurl = 我哋淨係派條 link，對面自己爬 `og:image` → 1200×630，仲有 WhatsApp
 *    文檔嗰個 600KB 靜默閘要夾（見 og route `JPEG_QUALITY`）。
 * 同一個平台可以兩樣都做（WhatsApp send 圖 vs WhatsApp send link），所以呢張表認嘅
 * 係**動作**唔係公司名 —— `whatsapp` 指貼圖，派 link 嗰邊由 `og:image` 自己走 wide。
 */
const FORMAT_ALIASES: Record<string, ShareFormat> = {
  wide: "wide",
  post: "post",
  /* `story`：2026-08-19 早上出過一版 9:16，嗰陣派出去嘅 HTML 仲喺 CDN／用戶開住嘅
     tab 度，撳分享會照舊帶 `?format=story`。唔認佢就跌返 wide，用戶攞到一張橫圖。 */
  story: "post",
  /* 貼圖目的地 */
  whatsapp: "post",
  x: "post",
  twitter: "post",
  threads: "post",
  instagram: "post",
  ig: "post",
  line: "post",
  /* 派 link 俾人自己 unfurl */
  unfurl: "wide",
  facebook: "wide",
  og: "wide",
};

/*
 * ⚠️ 呢個 guard 唔係擺設：上面張表打錯一個 format 名（例如手多多寫 `"posts"`），
 * TypeScript 已經會嗌 —— 但**加一個 route 冇 layout 嘅新 format 名**就唔會，個 route
 * 會攞住個 undefined spec 炸喺 request 度。呢度炸喺 import 嗰刻 = `next build` 即刻紅。
 */
const unknown = Object.entries(FORMAT_ALIASES).filter(([, format]) => !SHARE_FORMATS.includes(format));
if (unknown.length > 0) {
  throw new Error(`share-destinations: 對去一個唔存在嘅 format（${unknown.map(([k, v]) => `${k}→${v}`).join("、")}）`);
}

/*
 * 認唔到（打錯字、新平台未加）就跌返 `wide` —— 唔准 500。
 *
 * 跌返 wide 唔係靜默：response 有 `x-og-format` 講返實際行咗邊個，`curl -sI` 就見到。
 * 對條鏈嚟講「攞到一張橫圖」好過「攞到 500 然之後今日冇圖出」。
 */
export function readShareFormat(value: string | null | undefined): ShareFormat {
  return (value ? FORMAT_ALIASES[value.toLowerCase()] : undefined) ?? "wide";
}

/** 俾 test 同文件用：所有認得嘅目的地名。 */
export function shareDestinations(): string[] {
  return Object.keys(FORMAT_ALIASES);
}
