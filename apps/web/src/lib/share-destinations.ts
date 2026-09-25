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
 * 今日六個比例（頭四個係目的地表用嘅，尾二 2026-08-23 加，見下面 SHARE_EXTRA_TARGETS）：
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
 *     （**唔係** 16:9。1.91:1 係 og:image 嘅標準闊高比 —— 真 16:9 係下面 `widescreen`。）
 *   · `portrait` 1080×1440（3:4）—— IG 今日個 feed／grid 比例。owner 2026-08-23。
 *   · `widescreen` 1920×1080（16:9）—— YouTube 縮圖／簡報／橫向螢幕。owner 2026-08-23。
 *     呢兩個**唔掛喺任何目的地**：`SHARE_TARGETS` 一個字都冇郁（Threads／X／WhatsApp
 *     仲係 4:5、IG 仲係 1:1），佢哋係選單尾嗰兩行「我知我要咩比例」嘅明確揀項。
 */
/*
 * 2026-08-23 加咗兩個（owner：X／Threads／WhatsApp／IG 四個尺寸之外，仲要 IG 而家個
 * feed 比例同一個真 16:9，唔想再人手裁圖）：
 *   · `portrait` 1080×1440（3:4）—— **IG 今日個 feed／grid 比例**。介乎 square 同
 *     post 之間：比 1:1 高 33%（螢幕面積多），但唔似 4:5 咁高（IG 2025 之後個 grid
 *     縮圖就係 3:4，貼 4:5 落去個 grid 會裁走上下）。
 *   · `widescreen` 1920×1080（16:9）—— YouTube 縮圖／簡報／橫向螢幕嗰種真 16:9。
 *     ⚠️ **唔係** `wide`。`wide` 1200×630 係 og:image 標準（1.91:1），兩個都係「橫」
 *     但差 7%，而 2026-08-20 就係因為有人將 `wide` 標做「16:9」而出過街（見下面
 *     第三個 guard）。而家 16:9 有咗自己個真身，更加唔准撈埋。
 */
export const SHARE_FORMATS = ["wide", "square", "post", "status", "portrait", "widescreen"] as const;
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
  /*
   * `portrait` 1080×1440（3:4）—— IG 今日個 feed／grid。短邊同 post／square 一樣係
   * 1080，所以「1080p / 4K」兩個 tier 名對佢一樣係字面像素（4K = 2160×2880）。
   *
   * ⚠️ 佢**唔係** post 高咗一截，亦唔係 square 拉長 —— 三個都係 1080 闊，分別淨係
   * 高度 1080 / 1440 / 1350。所以熱力圖嗰邊 3:4 同 4:5 出嚟嘅欄數一樣，只係格仔高啲。
   * ⚠️ 唔准為咗「靚啲」改成 1200×1600：短邊一離開 1080，`share-resolution.ts`
   * 個 tier 名就即刻講緊大話（嗰邊 Guard 2 釘住 post／square，呢個一齊釘落去）。
   */
  portrait: { width: 1080, height: 1440 },
  /*
   * `widescreen` 1920×1080（16:9）—— 真 16:9：YouTube 縮圖、簡報、橫向螢幕。
   *
   * ⚠️ 同 `wide`（1200×630 = 1.91:1）**係兩個 format，唔准合併**。差 7% 睇落好似
   * 冇所謂，但 og:image 有 1200×630 呢個標準尺寸同 WhatsApp 600KB 靜默閘要夾
   * （`lib/route-metadata.ts` 宣告咗、`test-fe-og-post-layout.mjs` T5 釘住），而
   * 16:9 落 og:image 位會俾 unfurl 自己再裁一次。一個係「派 link」，一個係「派圖」。
   * ⚠️ 亦都係全表**唯一**橫過 1200 嘅尺寸：出圖時間跟像素面積走，4K（3840×2160）
   * 比 post 4K 大 43%，唔好擺喺會 timeout 嘅 gateway 後面等人撳（見 share-resolution.ts）。
   */
  widescreen: { width: 1920, height: 1080 },
};

/*
 * 熱力圖人手分享同宣傳鏈／cron **同一條** `GET /api/og/heatmap?...`
 * （`apps/web/src/app/api/og/heatmap/route.tsx` + `lib/heatmap-og.ts`）。
 * 對 `x-og-generation`。唔開 browser、唔截 :3900。
 *   square = 1:1、post / portrait(alias) = 4:5、status = 9:16、wide / landscape = 1.91:1、
 *   3x4 = 3:4、16x9 = 16:9。
 */
export type ShareTargetId =
  | "instagram" | "threads" | "x" | "whatsapp" | "status" | "other" | "desktop"
  /* 2026-08-23 加嘅兩粒「淨係揀個比例」列 —— 見下面 `SHARE_EXTRA_TARGETS` */
  | "ig-portrait" | "widescreen";

export interface ShareTarget {
  id: ShareTargetId;
  /** 卡片同熱力圖都按 format 由 server OG route 出圖。 */
  format: ShareFormat;
  /** 選單右邊嗰粒比例標。純數字，唔使 i18n。 */
  ratio: string;
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
  { id: "instagram", format: "square", ratio: "1:1" },
  { id: "threads", format: "post", ratio: "4:5" },
  { id: "x", format: "post", ratio: "4:5" },
  { id: "whatsapp", format: "post", ratio: "4:5" },
  { id: "status", format: "status", ratio: "9:16" },
  { id: "other", format: "post", ratio: "4:5" },
  { id: "desktop", format: "wide", ratio: "1.91:1" },
];

/*
 * 「唔講去邊，淨係要呢個比例」嗰兩列（owner 2026-08-23）。
 *
 * 點解**唔**塞入上面 `SHARE_TARGETS`：嗰張表答嘅係「呢個目的地最啱用邊個尺寸」，
 * 七個 id 每個都對住一個真實平台／面，而條 HERMES cron 鏈亦係讀嗰批 id 做 `?format=`。
 * 呢兩列答嘅係另一條問題 ——「我知我要咩比例，直接俾我」。撈埋一張表就會出現
 * 「`widescreen` 係咪一個平台？」呢種永遠答唔到嘅問題，而且下次有人加平台就會照抄
 * 呢兩行嘅寫法。
 *
 * ⚠️ 但係佢哋要行**同一套 guard**（下面三個 guard 掃嘅係 `GUARDED_TARGETS`，
 * 兩張表加埋）—— 唔係就變成「新加嗰兩列個比例標冇人核對」，正正係 2026-08-20
 * `desktop` 標錯 16:9 嗰單嘅翻版。
 */
export const SHARE_EXTRA_TARGETS: readonly ShareTarget[] = [
  { id: "ig-portrait", format: "portrait", ratio: "3:4" },
  { id: "widescreen", format: "widescreen", ratio: "16:9" },
];

/*
 * 選單真正 render 嗰條 list（`components/share-menu.tsx` 讀佢）。目的地行先，
 * 「淨係要個比例」嗰兩列包尾 —— 撳分享嘅人九成係想揀平台，唔係想揀數字。
 */
export const SHARE_MENU_TARGETS: readonly ShareTarget[] = [...SHARE_TARGETS, ...SHARE_EXTRA_TARGETS];

/*
 * 橫度 format（`wide` 1.91:1、`widescreen` 16:9）。
 *
 * ⚠️ 呢個唔係方便 helper，係**擋一個具體嘅窿**：`app/api/og/card/[id]/route.tsx`
 * 成個 layout 分支寫住 `format === "wide" ? 橫版 : 直版`。加咗 `widescreen` 之後
 * 嗰句就會將一個 1920×1080 嘅橫畫布餵落直度 layout（卡圖舞台 620px 高、走勢圖
 * 968px 闊）—— 唔會 500，會出一張排爆咗嘅橫圖。所以「邊啲係橫」只准有一個答案。
 */
export const WIDE_FORMATS = ["wide", "widescreen"] as const satisfies readonly ShareFormat[];
export type WideShareFormat = (typeof WIDE_FORMATS)[number];
/** 直度／方形 format（`square` / `post` / `status` / `portrait`）。 */
export type TallShareFormat = Exclude<ShareFormat, WideShareFormat>;

export function isWideFormat(format: ShareFormat): format is WideShareFormat {
  return (WIDE_FORMATS as readonly ShareFormat[]).includes(format);
}

/*
 * ⚠️ Guard 0：`WIDE_FORMATS` 要同真實闊高比對得返。有人將來加一個 2:3 format 而
 * 手多多寫落呢個 array（或者反過嚟，加咗個 21:9 而唔記得寫落去），上面個分支就會
 * 靜靜將佢餵落錯嗰個 layout。橫 = width > height，冇第二個定義。
 */
const wrongOrientation = SHARE_FORMATS.filter(
  (format) => isWideFormat(format) !== (FORMAT_SIZES[format].width > FORMAT_SIZES[format].height),
);
if (wrongOrientation.length > 0) {
  throw new Error(
    `share-destinations: WIDE_FORMATS 同真實闊高唔夾（${wrongOrientation
      .map((f) => `${f} ${FORMAT_SIZES[f].width}×${FORMAT_SIZES[f].height} 但 isWideFormat=${isWideFormat(f)}`)
      .join("、")}）`,
  );
}

/** 三個 guard 一齊掃嘅範圍：目的地 + 「淨係要個比例」兩列。 */
const GUARDED_TARGETS: readonly ShareTarget[] = SHARE_MENU_TARGETS;

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
  /* 橫／直口語 alias：auto-update 同人手都可以寫 landscape / portrait，唔使記 format 名。
     ⚠️⚠️ **`portrait` 同 `landscape` 呢兩粒字唔准搬去新嗰兩個 format。**
     2026-08-23 加 `portrait` 3:4 同 `widescreen` 16:9 嗰陣，最順手嘅寫法就係
     `portrait: "portrait"` —— 但呢兩粒 alias 由第一日起就係「直／橫」嘅口語名，
     auto-update 同人手都寫得出，改咗就係**條 URL 一個字都唔使郁、照 200、
     `x-og-format` 照出新名**，然之後每日靜靜出咗一批 3:4 落 X／Threads／WhatsApp
     嘅氣泡（嗰三家 4:5 係實測出嚟嘅，見上面）。同 `story` 嗰粒完全一樣嘅道理。
     新格式各自有自己嘅明確 alias（`3x4` / `ig-portrait` / `grid` 同
     `16x9` / `widescreen` / `hd` / `youtube`），想要就明寫。 */
  landscape: "wide",
  portrait: "post",
  tall: "status",
  /* 3:4（IG feed／grid）。`grid` 係因為 owner 叫佢做「IG 個格仔」。 */
  "3x4": "portrait",
  "ig-portrait": "portrait",
  grid: "portrait",
  /* 真 16:9。`hd` / `youtube` 係口語入口。
     ⚠️ `hd` 喺 `share-resolution.ts` 嗰張表**另有其人**（= 1080p 清晰度）——
     兩張表兩件事（比例 vs 像素密度），`?format=hd` 同 `?res=hd` 各讀各嘅，
     唔准因為撞名而合併。 */
  "16x9": "widescreen",
  widescreen: "widescreen",
  hd: "widescreen",
  youtube: "widescreen",
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
 * ⚠️ Guard 1b：**每個 format 都要至少有一個 alias 叫得到佢。**
 *
 * 反方向嘅窿，2026-08-23 差啲就踩到：新加 `portrait` 3:4 嗰陣，`portrait` 呢粒
 * alias 已經名花有主（指住 `post` 4:5，唔准郁，見上面），所以個 format 加咗落
 * `SHARE_FORMATS`、加咗落 `FORMAT_SIZES`、route 寫埋 layout、選單出埋一行 ——
 * 但如果冇人記得加 `3x4` / `ig-portrait` / `grid`，`readShareFormat` 就**永遠**
 * 攞唔到佢：`?format=portrait` 跌返 post，其他寫法跌返 wide，兩邊都係 200。
 * 一個叫唔到嘅 format = 一堆死 code，而冇任何 test 會紅。
 */
const unreachable = SHARE_FORMATS.filter((format) => !Object.values(FORMAT_ALIASES).includes(format));
if (unreachable.length > 0) {
  throw new Error(
    `share-destinations: 呢啲 format 冇任何 alias 叫得到（${unreachable.join("、")}）—— 加返個明確 alias，唔好搶舊 alias`,
  );
}

/*
 * ⚠️ 第二個 guard：選單每一個目的地都一定要喺上面張 alias 表度搵到自己個 id，而且
 * 兩邊答案要一樣。冇呢句嘅話，「選單叫 og route 出 4:5、條 cron 鏈叫同一個名出 9:16」
 * 呢種靜默分叉就會出得街 —— 兩邊都係 200，冇人會發現。同樣炸喺 import 嗰刻。
 */
const drifted = GUARDED_TARGETS.filter((target) => FORMAT_ALIASES[target.id] !== target.format);
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
const mislabelled = GUARDED_TARGETS.filter((target) => {
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
/*
 * 「呢個 format 喺 URL 度要點叫」—— **唔等於**佢自己個名。
 *
 * ⚠️ `portrait` 呢個字喺 `?format=` 嗰個 alias namespace 度由第一日起就係 4:5 嘅
 * `post`（HERMES 條 cron 鏈日日叫緊），所以 2026-08-23 加嘅 3:4 format 雖然自己
 * 叫 `portrait`，過 wire 嗰陣一定要寫 `3x4`。
 *
 * 冇呢張表就係：CLI 一句 `--format portrait` 送 `?format=portrait` → server 認得、
 * 照 200、出返 4:5，`x-og-format` 仲會講「post」。差 90px 高，冇 error 冇 log，
 * 要等有人肉眼度返先知。**任何叫方（CLI／腳本／條鏈）要由 format 名砌 URL，
 * 都行呢張表，唔准直接塞個名落 `?format=`。**
 */
export const FORMAT_QUERY_NAME: Record<ShareFormat, string> = {
  wide: "wide",
  square: "square",
  post: "post",
  status: "status",
  portrait: "3x4",
  widescreen: "widescreen",
};

/*
 * ⚠️ 第五個 guard：張表寫嘅嘢一定要真係叫得返同一個 format。用 `readShareFormat`
 * 自己行一次 —— 唔係比字串，係行真嗰條解析路。炸喺 import 嗰刻 = `next build` 即紅。
 */
const wrongQueryName = SHARE_FORMATS.filter((format) => readShareFormat(FORMAT_QUERY_NAME[format]) !== format);
if (wrongQueryName.length > 0) {
  throw new Error(
    `share-destinations: FORMAT_QUERY_NAME 叫唔返同一個 format（${wrongQueryName
      .map((f) => `${f} → ?format=${FORMAT_QUERY_NAME[f]} → ${readShareFormat(FORMAT_QUERY_NAME[f])}`)
      .join("、")}）`,
  );
}

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
