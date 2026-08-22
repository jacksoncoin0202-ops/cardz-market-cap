/*
 * 「張圖送去邊」→「用邊個尺寸」嘅唯一一張表。
 *
 * 兩類叫方，同一張表：
 *  · **用戶**撳「分享圖片」→ 彈個目的地選單（`components/share-menu.tsx`）。owner
 *    2026-08-20：「彈個 button 出嚟問你想分享去邊：WhatsApp、Threads、X.COM、IG 定係
 *    其他地方？因應分享去唔同地方，要配合返唔同嘅最佳 social media size」。
 *  · **HERMES 條 cron 鏈**（唔喺呢個 repo）做完自動鏈就 download 張圖再 upload 去
 *    WhatsApp／X／Threads —— 佢淨係寫佢送去邊（`?format=whatsapp`），尺寸留喺呢度決定。
 *
 * 即係話「邊個平台用邊個比例」呢個知識**得一份**：改呢張表，選單同條鏈一齊跟，
 * 兩邊都唔使郁（AGENTS.md 規矩 13）。
 *
 * 今日四個比例：
 *   · `square` 1080×1080（1:1）—— **IG feed**。owner 2026-08-22：「IG 原來係正方形出
 *     POST，所以唔係 4:5」。IG feed 收 1:1／4:5／1.91:1 三款，4:5 佔螢幕最高，但 owner
 *     要嘅係方，噉就照方 —— 佢係出 post 嗰個人。1:1 亦係全世界最唔會出事嗰款（任何
 *     grid、任何 embed 都唔會裁），代價係比 4:5 少 20% 螢幕面積。
 *   · `post` 1080×1350（4:5）—— Threads / X / WhatsApp 氣泡。三家對直度圖都有高度上限，
 *     超過**唔裁、改為按高度縮細**，張圖就永遠得七八成闊（owner 2026-08-19 實測）。
 *     鎖 0.8 三家都唔會再縮。
 *   · `status` 1080×1920（9:16）—— IG 限時動態 / WhatsApp Status 嗰種**全屏**面。
 *     ⚠️ 佢係「邊個**面**」唔係「邊間公司」：同一個 WhatsApp，對話氣泡行 4:5、Status
 *     行 9:16。
 *     ⚠️ 呢個比例**淨係**俾 status／story 面用。貼落 feed（Threads / X / IG post）就係
 *     上面講嗰個「縮到七八成闊」嘅陷阱，所以下面張表冇一個 feed 目的地指去佢。
 *   · `wide` 1200×630（1.91:1）—— 社交 unfurl（`og:image`）同埋電腦／部落格 embed。
 *     （**唔係** 16:9。1.91:1 係 og:image 嘅標準闊高比。）
 */
export const SHARE_FORMATS = ["wide", "square", "post", "status"] as const;
export type ShareFormat = (typeof SHARE_FORMATS)[number];

/*
 * 每個 format 嘅真實像素 —— **呢度係唯一真身**。`app/api/og/card/[id]/route.tsx`
 * 讀返呢張表出圖（佢自己只留低 defaultTheme）。
 *
 * 點解要抽上嚟：選單右邊嗰粒比例標本來係人手寫，同 route 入面嗰組數字冇任何連繫。
 * 2026-08-20 就出過一次街——`desktop` 標住「16:9」，但 `wide` 真身係 1200×630＝1.91:1，
 * 塊 UI 向用戶講咗個假數字而全部 test 照綠。而家兩者同源，加埋下面第三個 guard，
 * 標錯 = import 即刻炸。
 *
 * ⚠️ `wide` 1200×630 **唔准**為咗個標籤靚啲改成 1200×675。1200×630 係 og:image 標準
 * 尺寸，`lib/route-metadata.ts` 宣告咗、`scripts/test-fe-og-post-layout.mjs` T5 釘住，
 * 成張 wide layout（同 WhatsApp unfurl 600KB 靜默閘）都係照住 630 高度身砌。
 */
export const FORMAT_SIZES: Record<ShareFormat, { width: number; height: number }> = {
  wide: { width: 1200, height: 630 },
  /*
   * `square` 1080×1080（1:1）—— IG feed。短邊同 `post` 一樣係 1080，所以「1080p / 4K」
   * 兩個 tier 名對佢嚟講一樣係字面像素（`lib/share-resolution.ts` 個 Guard 2 兩個都釘）。
   *
   * ⚠️ 高度由 1350 跌到 1080 = **少咗 270px**，唔係「照 post 個 layout 塞埋去就得」。
   * 卡圖舞台同走勢圖都要收（見 route 嗰邊 `TALL_GEO.square`），而收幾多唔准估 ——
   * `scripts/test-fe-og-post-layout.mjs --live` 逐 px 掃返個 ink bbox 先算數。
   */
  square: { width: 1080, height: 1080 },
  post: { width: 1080, height: 1350 },
  /*
   * `status` 1080×1920（9:16）—— WhatsApp Status / IG 限時動態嗰種**全屏**面。
   *
   * ⚠️ 呢個尺寸**淨係**俾 status／story 面用，唔准做通用分享圖。貼落 feed
   * （Threads / X / IG post）三家都唔會裁，而係按高度縮細 → 張圖得七八成闊。
   */
  status: { width: 1080, height: 1920 },
};

/*
 * 熱力圖人手分享同宣傳鏈／cron **同一條** `GET /api/og/heatmap?...`
 * （`apps/web/src/app/api/og/heatmap/route.tsx` + `lib/heatmap-og.ts`）。
 * 對 `x-og-generation`。唔開 browser、唔截 :3900。
 *   square = 1:1、post / portrait = 4:5、status = 9:16、wide / landscape = 1.91:1。
 */
export const SHARE_ASPECTS = ["post", "wa", "frame"] as const;
export type ShareAspect = (typeof SHARE_ASPECTS)[number];

export type ShareTargetId = "instagram" | "threads" | "x" | "whatsapp" | "status" | "other" | "desktop";

export interface ShareTarget {
  id: ShareTargetId;
  /** 卡片內頁：server 出圖，`?format=` 就係佢（見 `app/api/og/card/[id]/route.tsx`） */
  format: ShareFormat;
  /** 熱力圖選單仍然帶 aspect（舊 canvas 槽）；出圖而家跟 `format` 行 OG。 */
  aspect: ShareAspect;
  /** 選單右邊嗰粒比例標。純數字，唔使 i18n。 */
  ratio: string;
  /**
   * 熱力圖嗰邊唔係固定比例（`frame` = 跟畫面），所以上面個 `ratio` 只講得卡片內頁。
   * 有呢個 flag 嘅列喺熱力圖選單會出本地化嘅「跟畫面」。
   */
  frameOnHeatmap?: true;
}

/*
 * 選單次序 = 呢個 array 次序。owner 2026-08-20 點名嗰四個平台行先，跟住兩個兜底。
 *
 * ⚠️ 「send 一張圖俾人睇」嗰批**唔係求其填比例**：Threads 跟 IG 一路、X 一樣
 * （owner：「X.COM 好明顯係 4:5」）、WhatsApp 對話／群組個氣泡保持比例唔裁，4:5 佔到
 * 最高（owner 2026-08-20，e61c1aa9），所以呢三個連同「其他」一齊行 4:5。
 *
 * ⚠️ **IG 自己一行 1:1**（owner 2026-08-22 更正：「IG 原來係正方形出 POST」）。佢係
 * 唯一一個由 owner 點名要方嘅目的地 —— 唔准順手將 Threads／X 一齊拉埋落 square，
 * 嗰三家 4:5 係實測出嚟嘅（見上面），一齊改就係為咗表面整齊而令三張圖變差。
 *
 * ⚠️ **9:16 唔係「WhatsApp 嗰行」，係「全屏面嗰行」。** WhatsApp 有兩個面：對話氣泡
 * （4:5，上面）同 Status（全屏 9:16）；IG 一樣分 feed 同限時動態。所以 9:16 自己一行
 * `status`，唔准掛落任何一個公司名 —— 掛咗就即係「揀 WhatsApp = 一定係 Status」，
 * 而條 cron 鏈嘅 `?format=whatsapp` 講嘅正正係對話氣泡嗰個面（見下面 alias 表）。
 */
export const SHARE_TARGETS: readonly ShareTarget[] = [
  { id: "instagram", format: "square", aspect: "post", ratio: "1:1" },
  { id: "threads", format: "post", aspect: "post", ratio: "4:5" },
  { id: "x", format: "post", aspect: "post", ratio: "4:5" },
  { id: "whatsapp", format: "post", aspect: "post", ratio: "4:5" },
  { id: "status", format: "status", aspect: "wa", ratio: "9:16" },
  { id: "other", format: "post", aspect: "post", ratio: "4:5" },
  { id: "desktop", format: "wide", aspect: "frame", ratio: "1.91:1", frameOnHeatmap: true },
];

/*
 * key 一律小楷；`readShareFormat` 會幫叫方 lowercase，所以呢度唔准出現大楷。
 *
 * 分兩組，因為兩組係**兩件唔同嘅事**，唔好因為名似就撈埋：
 *  · 貼圖 = 真係 send 一張圖出去（人手 save 落相簿再貼，或者條鏈 upload）。
 *  · unfurl = 我哋淨係派條 link，對面自己爬 `og:image` → 1200×630，仲有 WhatsApp
 *    文檔嗰個 600KB 靜默閘要夾（見 og route `JPEG_QUALITY`）。
 * 同一個平台可以兩樣都做（WhatsApp send 圖 vs WhatsApp send link），所以呢張表認嘅
 * 係**動作**唔係公司名 —— `whatsapp` 指貼圖，派 link 嗰邊由 `og:image` 自己走 wide。
 */
const FORMAT_ALIASES: Record<string, ShareFormat> = {
  wide: "wide",
  square: "square",
  "1x1": "square",
  post: "post",
  status: "status",
  /*
   * ⚠️ `story` **故意仍然指住 `post`，唔准改去 `status`。** 2026-08-19 早上出過一版
   * 9:16 通用分享圖，嗰陣派出去嘅 HTML 仲喺 CDN／用戶開住嘅 tab 度，撳分享會照舊帶
   * `?format=story` —— 但嗰粒掣係**通用**分享（貼 Threads／X／IG feed），9:16 落嗰啲
   * feed 就係「縮到七八成闊」嗰個陷阱。今日 9:16 有返個真 format（`status`），但佢
   * 只准由明確揀咗 WhatsApp Status 嘅人攞到，唔准由一粒舊掣靜靜攞到。
   */
  story: "post",
  /* 橫／直口語 alias：auto-update 同人手都可以寫 landscape / portrait，唔使記 format 名。 */
  landscape: "wide",
  portrait: "post",
  tall: "status",
  /* 貼圖目的地 —— 同 SHARE_TARGETS 同一組答案，條 cron 鏈寫平台名就得。
     ⚠️ `whatsapp` = **對話／群組氣泡**，唔係 Status：條 cron 鏈（唔喺呢個 repo）就係
     upload 去對話／群組（owner 2026-08-20，e61c1aa9：「WhatsApp 氣泡保持比例唔裁」），
     所以佢一路都係、而且要繼續係 `post` 4:5。想攞全屏 9:16 就明寫 `?format=status`
     —— 條鏈一日冇改 URL，佢攞到嘅嘢就一日唔准變樣。 */
  whatsapp: "post",
  x: "post",
  twitter: "post",
  threads: "post",
  /* ⚠️ IG 兩個名一齊指去 `square`（owner 2026-08-22）。條 cron 鏈（唔喺呢個 repo）
     寫 `?format=instagram` 攞到嘅嘢由呢一行話事 —— 佢一日冇改 URL，就一日要由呢度
     跟返選單。上面第二個 guard 就係釘住「選單同 alias 唔准分叉」。 */
  instagram: "square",
  ig: "square",
  line: "post",
  other: "post",
  /* 派 link 俾人自己 unfurl */
  unfurl: "wide",
  facebook: "wide",
  desktop: "wide",
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
 * ⚠️ 第二個 guard：選單每一個目的地都一定要喺上面張 alias 表度搵到自己個 id，而且
 * 兩邊答案要一樣。冇呢句嘅話，「選單叫 og route 出 4:5、條 cron 鏈叫同一個名出 9:16」
 * 呢種靜默分叉就會出得街 —— 兩邊都係 200，冇人會發現。同樣炸喺 import 嗰刻。
 */
const drifted = SHARE_TARGETS.filter((target) => FORMAT_ALIASES[target.id] !== target.format);
if (drifted.length > 0) {
  throw new Error(
    `share-destinations: 目的地同 alias 表講唔同嘢（${drifted.map((t) => `${t.id}: 選單 ${t.format} vs alias ${FORMAT_ALIASES[t.id]}`).join("、")}）`,
  );
}

/*
 * ⚠️ 第三個 guard：選單右邊嗰粒比例標，一定要同 `FORMAT_SIZES` 講嘅真實闊高比對得上。
 *
 * 2026-08-20 出過街：`desktop` 標「16:9」但實際出 1200×630（1.91:1）。個標籤純手寫、
 * 冇人核對，於是 UI 向用戶報咗個假數字，三個 test 全綠。而家講大話 = import 炸 =
 * `next build` 紅，唔使再靠人眼。
 *
 * 容差 1%：「1.91:1」係業界叫法（1200÷630 = 1.9048，差 0.28%），唔逼人寫 40:21。
 */
const RATIO_TOLERANCE = 0.01;
const mislabelled = SHARE_TARGETS.filter((target) => {
  const [labelW, labelH] = target.ratio.split(":").map(Number);
  if (!Number.isFinite(labelW) || !Number.isFinite(labelH) || labelH === 0) return true;
  const { width, height } = FORMAT_SIZES[target.format];
  const real = width / height;
  return Math.abs(labelW / labelH - real) / real > RATIO_TOLERANCE;
});
if (mislabelled.length > 0) {
  throw new Error(
    `share-destinations: 比例標同真實尺寸唔夾（${mislabelled
      .map((t) => `${t.id}: 標 ${t.ratio} 但 ${t.format} 係 ${FORMAT_SIZES[t.format].width}×${FORMAT_SIZES[t.format].height}`)
      .join("、")}）`,
  );
}

/*
 * 認唔到（打錯字、新平台未加）就跌返 `wide` —— 唔准 500。
 *
 * 跌返 wide 唔係靜默：response 有 `x-og-format` 講返實際行咗邊個，`curl -sI` 就見到。
 * 對條鏈嚟講「攞到一張橫圖」好過「攞到 500 然之後今日冇圖出」。
 */
export function readShareFormat(value: string | null | undefined): ShareFormat {
  if (!value) return "wide";
  /*
   * ⚠️ 一定要行 `Object.hasOwn`，唔准直接 index 落去。`FORMAT_ALIASES` 係普通 object
   * literal，行住 `Object.prototype` —— `?format=constructor` / `?format=__proto__`
   * 直接 index 會攞到繼承嚟嘅 `Object` 建構函數（truthy，`??` 接唔到手），route 就會
   * 攞住個 undefined spec 喺 request 度炸。2026-08-20 喺出街站實測 `?format=constructor`
   * → **HTTP 500**，正正打爆上面「唔准 500」嗰句。
   */
  const key = value.toLowerCase();
  return Object.hasOwn(FORMAT_ALIASES, key) ? FORMAT_ALIASES[key] : "wide";
}

/** 俾 test 同文件用：所有認得嘅目的地名。 */
export function shareDestinations(): string[] {
  return Object.keys(FORMAT_ALIASES);
}
