import { FORMAT_SIZES, type ShareFormat } from "./share-destinations";

/*
 * 「張圖出幾大」—— **唯一一張表**。
 *
 * `share-destinations.ts` 管嘅係**比例**（去 IG 用 4:5、去 Status 用 9:16），
 * 呢度管嘅係**同一個比例出幾多粒像素**。兩件唔同嘅事，唔好撈埋：加多一個平台唔應該
 * 要諗清晰度，揀 4K 亦唔應該連比例都變。
 *
 * owner 2026-08-21：「人地下載可以揀 4K 嗎？我想 1080p 同埋 4K 兩隻分別嘅啫。」
 * 所以得兩級，冇 2K —— 多一級 = 多一倍 render 時間、多一堆 cache、多一格 UI，
 * 而冇人要。
 *
 * ── 個名點解係「1080p / 4K」而唔係「1× / 2×」──────────────────────────────
 * 用戶講嘅係短邊：`post` 1080×1350 短邊就係 1080，×2 = 2160×2700，短邊 2160 = 4K 級。
 * 下面個 guard 就係釘死呢句話 —— `post` 係七個目的地入面五個嘅預設，佢一日叫
 * 「1080p」就一日要真係 1080。
 *
 * ⚠️ `wide` 1200×630 短邊係 630，唔係 1080。所以個 tier 名對 `wide` 嚟講係**級數**
 * （×1／×2 = 2400×1260）唔係字面像素。UI、CLI、response header 全部同時報返**真實
 * 闊×高**，唔准淨係俾個 tier 名用戶睇 —— 講個唔啱嘅數字出街，呢個 repo 2026-08-20
 * 喺比例標度已經做過一次（見 share-destinations.ts 第三個 guard）。
 *
 * ── 代價（實測，唔好扮唔知）─────────────────────────────────────────────
 * 2026-08-21 喺 dev server（Windows、40 格、post）量：
 *   1080p 未 cache  4.3–5.3 秒
 *   4K    未 cache  53.7–56.8 秒   ← 打橫 4 倍像素，但慢 11 倍
 *   任何一級 cache 命中           ~0.1 秒
 * 拆開睇：4K 嗰 54 秒入面，得 12.9 秒係畫塊板，其餘 41 秒係 resvg 逐格畫嗰 40 張卡圖
 * （試過改 JPEG data URI，56.8 秒，冇用 —— 唔係 decode 慢，係 canvas 大咗之後每張圖
 * 嘅 draw 都貴咗）。所以 4K **唔適合**擺喺會 timeout 嘅 gateway 後面等人撳；
 * `scripts/heatmap-download.mjs`（CLI，可以慢慢等）先係 4K 嘅正路。
 */
export const SHARE_RESOLUTIONS = ["1080p", "4k"] as const;
export type ShareResolution = (typeof SHARE_RESOLUTIONS)[number];

export const DEFAULT_SHARE_RESOLUTION: ShareResolution = "1080p";

/** tier → 相對 `FORMAT_SIZES` 真身嘅倍數。route 攞佢做 `scale`，成張佈局跟住放大。 */
export const RESOLUTION_SCALE: Record<ShareResolution, number> = {
  "1080p": 1,
  "4k": 2,
};

/** UI／CLI／header 顯示用。`4k` 寫成大楷 `4K`（人手寫個標籤好易變成 "4k"）。 */
export const RESOLUTION_LABEL: Record<ShareResolution, string> = {
  "1080p": "1080p",
  "4k": "4K",
};

/**
 * 慢到要出提示嘅 tier。UI 撳落去之前要話俾人知等幾耐，唔好扮即刻有。
 * 見上面實測數字。
 */
export const RESOLUTION_IS_SLOW: Record<ShareResolution, boolean> = {
  "1080p": false,
  "4k": true,
};

/*
 * 等幾耐先放棄。**唔准兩級共用一個數字。**
 *
 * 原本熱力圖分享寫死 45 秒（`SHARE_FETCH_TIMEOUT_MS`）—— 而 4K 實測 50–57 秒，
 * 即係 45 秒呢個閘會**次次**喺就快出到嗰陣斬咗佢，用戶睇到嘅係「撳 4K 永遠失敗」。
 * 加一級清晰度而唔加返時間 = 加咗個一定壞嘅掣。
 *
 * 180 秒係俾最壞情況（冷 cache + 40 格 + 部機同時 bake）留位，唔係目標。
 */
export const RESOLUTION_TIMEOUT_MS: Record<ShareResolution, number> = {
  "1080p": 45_000,
  "4k": 180_000,
};

/*
 * ── 一次 request 攞唔到 4K：真站實測 ─────────────────────────────────────
 *
 * 2026-08-21 喺 app.cardzmarketcap.com（deploy 之後即刻量）：
 *   1080p  `HTTP 200  5.7s  409,905 bytes`      —— 一次過，冇事。
 *   4K     `504 Gateway Timeout @ 60.1s`        —— 量三次，60.1 / 60.0 / 60.1，
 *          即係 gateway 一個 60 秒硬閘，唔關 code 事，repo 呢邊改唔到。
 *   但**斷線之後 server 冇停手**：等 150 秒再攞返同一條 URL，
 *          `HTTP 200  0.2s  1,182,847 bytes  x-og-res=4k`。
 *
 * 所以 4K 唔係「等耐啲就得」，係「踢一腳 → 等佢自己做完 → 再攞返同一條 URL」。
 * `RESOLUTION_TIMEOUT_MS` 係**一次** request 等幾耐；下面呢個係**成個 retry 迴圈**
 * 嘅預算（踢 + 等 + 攞）。兩個數係兩件事，唔准合併。
 *
 * ⚠️ retry 要撞到 cache，就一定要每次都行同一條 cache key。`stamp=now` 個 key 帶
 * 住分鐘，所以叫方要用 `?at=` 釘死嗰一刻 —— 見 `lib/share-stamp.ts`。
 */
export const RESOLUTION_RETRY_BUDGET_MS: Record<ShareResolution, number> = {
  "1080p": 60_000,
  "4k": 600_000,
};

/*
 * 邊啲 status 值得再試。呢啲全部係「前面條 gateway 唔想等」，唔係「你參數錯」——
 * 後面 server 通常仲喺度做緊嘢。其餘 4xx 同 500 唔喺度：retry 幾多次都係同一個答案。
 * 網站同 CLI 兩邊都讀呢一張表，唔准各寫一份。
 */
export const SHARE_RETRY_STATUSES = [408, 502, 503, 504, 522, 524] as const;

/** 兩次之間等幾耐。撞到 cache 係 0.2 秒，所以密啲冇著數，15 秒夠。 */
export const SHARE_RETRY_POLL_MS = 15_000;

/*
 * 踢完第一腳之後**特別等耐啲**先問第二次。
 *
 * 點解要分開一個數：每一次 cache miss 嘅 request 都會喺 server 度開多一個 render，
 * 唔會排隊，唔會共用。即係問得太密＝同一張圖同時 render 幾次，部機更加慢。
 * 實測：gateway 60 秒斬，render 100–126 秒完。60 + 50 = 110 秒，啱啱落喺條帶入面，
 * 所以正常情況下總共只會 render 一次（第二次問就已經撞到 cache）。
 */
export const SHARE_RETRY_FIRST_WAIT_MS = 50_000;

/** 某個比例 × 某個清晰度 = 真實像素。UI／CLI／header 一律報呢個，唔准報 tier 名。 */
export function resolutionPixels(format: ShareFormat, res: ShareResolution): { width: number; height: number } {
  const spec = FORMAT_SIZES[format];
  const scale = RESOLUTION_SCALE[res];
  return { width: Math.round(spec.width * scale), height: Math.round(spec.height * scale) };
}

/*
 * 口語 alias。CLI 同 query string 都行呢張表，所以 `--res 4K`／`?res=2160`／`?res=2x`
 * 全部認得，唔使人記死一個寫法。
 * key 一律小楷 —— `readShareResolution` 會幫叫方 lowercase。
 */
const RESOLUTION_ALIASES: Record<string, ShareResolution> = {
  "1080p": "1080p",
  "1080": "1080p",
  hd: "1080p",
  fhd: "1080p",
  "1x": "1080p",
  sd: "1080p",
  "4k": "4k",
  "2160": "4k",
  "2160p": "4k",
  uhd: "4k",
  "2x": "4k",
};

/**
 * 認唔到就跌返 1080p —— **唔准 500**，同 `readShareFormat` 一樣嘅理由：
 * 對條 cron 鏈嚟講「攞到張細啲嘅圖」好過「今日冇圖」。
 *
 * ⚠️ 一定要 `Object.hasOwn`：`?res=constructor` 直接 index 落 object literal 會攞到
 * `Object` 建構函數（truthy，`??` 接唔到），route 就會攞住個垃圾 scale 炸喺 request
 * 度。2026-08-20 `?format=constructor` 實測過 HTTP 500，同一個窿唔准再挖多次。
 */
export function readShareResolution(value: string | null | undefined): ShareResolution {
  if (!value) return DEFAULT_SHARE_RESOLUTION;
  const key = String(value).toLowerCase();
  return Object.hasOwn(RESOLUTION_ALIASES, key) ? RESOLUTION_ALIASES[key] : DEFAULT_SHARE_RESOLUTION;
}

/** 俾 test 同 CLI `--list` 用：所有認得嘅寫法。 */
export function resolutionAliases(): string[] {
  return Object.keys(RESOLUTION_ALIASES);
}

/*
 * ⚠️ Guard 1：alias 表唔准指去一個唔存在嘅 tier。炸喺 import 嗰刻 = `next build` 即紅。
 */
const unknownAlias = Object.entries(RESOLUTION_ALIASES).filter(([, res]) => !SHARE_RESOLUTIONS.includes(res));
if (unknownAlias.length > 0) {
  throw new Error(
    `share-resolution: alias 指去唔存在嘅 tier（${unknownAlias.map(([k, v]) => `${k}→${v}`).join("、")}）`,
  );
}

/*
 * ⚠️ Guard 2：**個 tier 名唔准講大話。**
 *
 * `post`（1080×1350，七個目的地入面五個嘅預設）短邊一定要等如 tier 名嗰個數：
 * `1080p` → 1080、`4k` → 2160。有人改細 `FORMAT_SIZES.post`（例如為咗慳時間改成
 * 864×1080）而個掣仲寫住「1080p」，呢度即刻炸，唔使等用戶落載完自己數像素。
 *
 * 只釘 `post`：`status` 短邊一樣係 1080（順帶保住），`wide` 630 係 og:image 標準尺寸，
 * 佢個 tier 名本來就係級數唔係像素（見檔頭）。
 */
/*
 * ⚠️ Guard 3：**慢嘅一級唔准用快嗰級嘅 timeout。**
 *
 * 呢條唔係為咗靚 —— 4K 實測 50–57 秒，如果有人（包括我）貪方便將 `4k` 嘅 timeout
 * 抄返 45 秒，個掣就變成「撳落去等 45 秒然後必定紅」，而 code review 睇落完全正常。
 * 120 秒係實測最壞值嘅一倍有多。
 */
const SLOW_TIER_MIN_TIMEOUT_MS = 120_000;
const tightTimeout = SHARE_RESOLUTIONS.filter(
  (res) => RESOLUTION_IS_SLOW[res] && RESOLUTION_TIMEOUT_MS[res] < SLOW_TIER_MIN_TIMEOUT_MS,
);
if (tightTimeout.length > 0) {
  throw new Error(
    `share-resolution: 慢嘅 tier timeout 太窄，實測要 50–57 秒（${tightTimeout
      .map((res) => `${res}: ${RESOLUTION_TIMEOUT_MS[res]}ms < ${SLOW_TIER_MIN_TIMEOUT_MS}ms`)
      .join("、")}）`,
  );
}

/*
 * ⚠️ Guard：成個 retry 預算唔可以細過單次 timeout —— 細過即係「第一腳都未等完就
 * 收工」，個 retry 迴圈變裝飾品。
 */
const shortBudget = SHARE_RESOLUTIONS.filter(
  (res) => RESOLUTION_RETRY_BUDGET_MS[res] < RESOLUTION_TIMEOUT_MS[res],
);
if (shortBudget.length > 0) {
  throw new Error(
    `share-resolution: retry 預算細過單次 timeout（${shortBudget
      .map((res) => `${res}: ${RESOLUTION_RETRY_BUDGET_MS[res]}ms < ${RESOLUTION_TIMEOUT_MS[res]}ms`)
      .join("、")}）`,
  );
}

const EXPECTED_POST_SHORT_SIDE: Record<ShareResolution, number> = { "1080p": 1080, "4k": 2160 };
const lyingTier = SHARE_RESOLUTIONS.filter((res) => {
  const { width, height } = resolutionPixels("post", res);
  return Math.min(width, height) !== EXPECTED_POST_SHORT_SIDE[res];
});
if (lyingTier.length > 0) {
  throw new Error(
    `share-resolution: tier 名同真實像素唔夾（${lyingTier
      .map((res) => {
        const { width, height } = resolutionPixels("post", res);
        return `${res}: 應該短邊 ${EXPECTED_POST_SHORT_SIDE[res]} 但 post 出 ${width}×${height}`;
      })
      .join("、")}）`,
  );
}
