import type { ShareLang } from "./share-copy";
import { RESOLUTION_RETRY_BUDGET_MS } from "./share-resolution";

/*
 * 張圖右上角嗰個時間戳。
 *
 * owner 2026-08-21：「右上角嗰個日子，一定要變返我截圖嗰一刻嘅日子，並不是呢個官方
 * 數據嘅日子。時間要跟返用戶當地嘅時間（例如 HKT）—— 譬如我呢度係 JST，就跟返 JST。」
 *
 * 所以個戳有**兩個模式**，唔係一個：
 *   `data` 資料日（`snapshot.effectiveAt`，UTC）—— og:image unfurl、HERMES cron 鏈用。
 *          嗰兩邊冇「我撳嗰一刻」呢回事，而且 `share-destinations.ts` 明文寫住條鏈
 *          「一日冇改 URL，攞到嘅嘢就一日唔准變樣」，所以佢係 route 嘅**預設**。
 *   `now`  出圖嗰一刻，跟叫方個時區。網站個分享掣同 CLI 明寫呢個。
 *
 * ── 時區點嚟 ────────────────────────────────────────────────────────────
 * 瀏覽器：`Intl.DateTimeFormat().resolvedOptions().timeZone` → `?tz=Asia/Tokyo`。
 * CLI：同一句，跟部機。
 * 冇俾／俾錯 → UTC（唔准 500，同 `readShareFormat` 一樣嘅道理）。
 *
 * ⚠️ 時區縮寫係 `Intl` 俾嘅，唔係我哋寫死一張表。Asia/Tokyo 出 `JST`，
 * Asia/Hong_Kong 出 `GMT+8`（Intl 對香港冇 `HKT` 呢個縮寫，唔准為咗個名靚自己砌一張
 * 對照表 —— 嗰種表一定會過時，而且 DST 地區會直接講錯）。`GMT+8` 一樣讀得明而且冇得
 * 拗，寧願樸素唔好靠估。
 *
 * ── 同 cache 嘅關係（重要）──────────────────────────────────────────────
 * `now` 模式之下，個戳**一定要入 cache key**，否則第二個人攞到嘅係第一個人嗰一刻嘅
 * 時間 —— 一張「你截圖嗰刻」嘅圖印住人哋嘅鐘，仲衰過印返資料日。精度做到分鐘：
 * 同一分鐘之內共用一張圖（hover warm + 撳掣 = 同一次 fetch），過咗分鐘就重出。
 */

/** 分鐘精度：`now` 模式嘅 cache key 同顯示文字都由呢個決定。 */
export const STAMP_MODES = ["data", "now"] as const;
export type StampMode = (typeof STAMP_MODES)[number];

export function readStampMode(value: string | null | undefined): StampMode {
  return value === "now" ? "now" : "data";
}

/**
 * 驗一個 IANA 時區名。認唔到就 `UTC`。
 *
 * ⚠️ 一定要真係試 format 一次 —— `Intl.supportedValuesOf("timeZone")` 喺舊 Node 冇，
 * 而 `new Intl.DateTimeFormat(..., { timeZone })` 對一個廢名會直接 `RangeError`。
 * 用 try/catch 係最平而且最準嘅驗法。
 */
export function readTimeZone(value: string | null | undefined): string {
  if (!value) return "UTC";
  const tz = String(value).trim();
  if (!tz) return "UTC";
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz }).format(new Date(0));
    return tz;
  } catch {
    return "UTC";
  }
}

/*
 * ── `at`：釘死「嗰一刻」，為咗 retry ──────────────────────────────────────
 *
 * 2026-08-21 喺真站 app.cardzmarketcap.com 實測到嘅事：
 *   4K 一次 request **必定**俾 gateway 60.1 秒斬（504），量咗三次都係 60 秒。
 *   但 server 其實冇停手 —— 斷線之後照做完、照寫 cache。等 150 秒再攞返
 *   同一條 URL：`HTTP 200 0.2s 1182847 bytes x-og-res=4k`。
 *   （同一刻 1080p 係 `200 5.7s 409905 bytes`，冇呢個問題。）
 *
 * 所以 4K 嘅正路唔係「等佢一次過返」，係「踢一腳 → 等 → 再攞返同一條 URL」。
 *
 * 呢度就係嗰個「同一條 URL」點解需要幫手：`stamp=now` 之下個 cache key 帶住分鐘，
 * 第一腳 07:44 第二腳 07:46 就係兩條唔同 key，永遠 miss，retry 變咗一次又一次
 * 由頭 render。叫方要有得講「用返我第一腳嗰一刻」，所以有 `?at=<epoch ms>`。
 *
 * 兩條硬條件，一條都唔准鬆：
 *   1. **歸到分鐘。** 顯示精度本來就係分鐘，唔歸就變成每毫秒一條 cache key。
 *   2. **夾窗。** 呢條 route 係公開嘅；唔夾窗，任何人都可以用 `at` 無限噴 cache
 *      檔。夾咗窗，key 數目封頂 = 窗口分鐘數（±60 分 = 121 條），有得計。
 * 出窗／垃圾值 → 當冇俾（用 server 而家），唔准 500 —— 同 `readShareFormat` 一樣。
 */
export const STAMP_AT_WINDOW_MS = 60 * 60_000;

/*
 * ⚠️ Guard：個 pin 喺 retry 迴圈**開頭**釘落去，最後一次 retry 喺開頭 + 成個預算
 * 之後先發生。所以個窗一定要闊過最長嗰個 retry 預算（留一倍位）—— 窄過就係 pin
 * 喺半路過期，跟住每次 retry 都係新 cache key，4K 由「慢」變成「死循環」。
 * 呢條係跨檔不變式：邊個調 `RESOLUTION_RETRY_BUDGET_MS` 都會即刻喺 import 度炸。
 */
const LONGEST_RETRY_BUDGET_MS = Math.max(...Object.values(RESOLUTION_RETRY_BUDGET_MS));
if (STAMP_AT_WINDOW_MS < LONGEST_RETRY_BUDGET_MS * 2) {
  throw new Error(
    `share-stamp: STAMP_AT_WINDOW_MS（${STAMP_AT_WINDOW_MS}ms）窄過最長 retry 預算 `
    + `（${LONGEST_RETRY_BUDGET_MS}ms）嘅兩倍 —— pin 會喺 retry 半路過期，4K 永遠 cache miss`,
  );
}

export interface StampAt {
  /** 真係用嚟出戳嗰個時刻 */
  date: Date;
  /** 叫方俾嘅 `at` 收唔收得？收唔到（冇俾／垃圾／出窗）就係 server 而家 */
  pinned: boolean;
}

export function readStampAt(value: string | null | undefined, now: Date = new Date()): StampAt {
  if (value === null || value === undefined) return { date: now, pinned: false };
  const raw = String(value).trim();
  if (!raw) return { date: now, pinned: false };
  const ms = Number(raw);
  if (!Number.isFinite(ms)) return { date: now, pinned: false };
  const floored = Math.floor(ms / 60_000) * 60_000;
  if (Math.abs(floored - now.getTime()) > STAMP_AT_WINDOW_MS) return { date: now, pinned: false };
  return { date: new Date(floored), pinned: true };
}

const INTL_LOCALE: Record<ShareLang, string> = {
  en: "en-US",
  "zh-TW": "zh-TW",
  "zh-CN": "zh-CN",
};

function parts(date: Date, tz: string, lang: ShareLang) {
  const fmt = new Intl.DateTimeFormat(INTL_LOCALE[lang], {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const out: Record<string, string> = {};
  for (const part of fmt.formatToParts(date)) out[part.type] = part.value;
  return out;
}

/*
 * 時區縮寫。
 *
 * owner 2026-08-21 要嘅係「HKT」「JST」呢種寫法，但 **Node 個 ICU 冇呢啲縮寫**：
 * `Intl` 對 Asia/Tokyo 一律出 `GMT+9`（`en-US` 同 `zh-TW` 一樣），得美洲幾個區先有
 * `EDT` 之類。所以要出 `JST` 就一定要自己有一張表。
 *
 * 一張人手表最易死喺兩件事上面，所以入表有**兩條硬條件**，唔准鬆：
 *   1. **冇夏令時間。** 有 DST 嘅區一個縮寫講兩個 offset（London 係 GMT/BST），
 *      寫死一個就係一年講錯半年。下面個 guard 會實測一月同七月嘅 offset，唔同就炸。
 *   2. **縮寫全球唔撞。** 所以冇 `CST`（中國同美國中部爭同一個縮寫）、冇 `IST`
 *      （印度／以色列／愛爾蘭三家爭）。撞名嘅寧願出 `GMT+8`，睇嘅人至少唔會誤會。
 *
 * 表入面搵唔到就跌返 `Intl` 嘅 `short`（`GMT+8` / `EDT`）—— 唔靚但唔會錯。
 */
const TZ_ABBREV: Record<string, string> = {
  "Asia/Tokyo": "JST",
  "Asia/Seoul": "KST",
  "Asia/Hong_Kong": "HKT",
  "Asia/Singapore": "SGT",
};

/** 某個時區喺某個月 1 號嘅 UTC offset（分鐘）。用嚟實測有冇 DST。 */
function offsetMinutes(tz: string, month: number): number {
  const probe = new Date(Date.UTC(2026, month, 1, 12, 0, 0));
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).formatToParts(probe);
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? "0");
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour") % 24, get("minute"), get("second"));
  return Math.round((asUtc - probe.getTime()) / 60000);
}

/*
 * ⚠️ Guard：上面張表**唔准**有 DST 區。加一個 `Europe/London` 落去就即刻炸喺 import，
 * 唔使等到十月換鐘先發現半年嘅圖全部寫錯時區。
 */
const dstZones = Object.keys(TZ_ABBREV).filter((tz) => offsetMinutes(tz, 0) !== offsetMinutes(tz, 6));
if (dstZones.length > 0) {
  throw new Error(`share-stamp: 呢啲時區有夏令時間，唔准寫死縮寫（${dstZones.join("、")}）`);
}

function zoneAbbrev(date: Date, tz: string): string {
  if (Object.hasOwn(TZ_ABBREV, tz)) return TZ_ABBREV[tz];
  const fmt = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "short" });
  return fmt.formatToParts(date).find((p) => p.type === "timeZoneName")?.value ?? "";
}

const MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * 出圖嗰一刻嘅戳，跟 `tz`：
 *   en     `Aug 21, 2026 · 20:41 JST`
 *   zh-TW  `2026年8月21日 20:41 JST`
 *   zh-CN  `2026年8月21日 20:41 JST`
 *
 * 有時間、有時區 —— 冇時區個「本地時間」就係一句廢話（睇嘅人唔知邊度嘅 20:41）。
 */
export function nowStamp(date: Date, tz: string, lang: ShareLang): string {
  const p = parts(date, tz, lang);
  const year = p.year ?? "";
  const month = Number(p.month ?? "1");
  const day = Number(p.day ?? "1");
  const clock = `${p.hour ?? "00"}:${p.minute ?? "00"}`;
  const zone = zoneAbbrev(date, tz);
  if (lang === "en") return `${MONTHS_EN[month - 1]} ${day}, ${year} · ${clock} ${zone}`.trim();
  return `${year}年${month}月${day}日 ${clock} ${zone}`.trim();
}

/**
 * cache key 用嘅戳身份。**一定要同顯示文字同一個精度**，唔係就會出現
 * 「key 一樣但畫面唔同」（永遠出舊時間）或者「key 每次唔同但畫面一樣」（永遠 cache miss）。
 * 所以直接攞顯示文字做 key，冇第二份精度定義。
 */
export function stampCacheKey(mode: StampMode, text: string): string {
  return mode === "now" ? `now:${text}` : "data";
}
