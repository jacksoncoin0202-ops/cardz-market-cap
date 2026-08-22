"use client";

/*
 * 「分享圖片」→ 先問去邊，再按目的地出啱嗰個尺寸。
 *
 * owner 2026-08-20：「彈個 button 出嚟問你想分享去邊：WhatsApp、Threads、X.COM、IG
 * 定係其他地方？因應分享去唔同地方，要配合返唔同嘅最佳 social media size。」
 *
 * 熱力圖同卡片內頁**共用呢一個 component**：兩邊出圖嘅方法唔同（熱力圖係即場 canvas、
 * 卡頁係 server `/api/og/card`），但「有邊啲目的地、邊個目的地用邊個比例、個選單點樣
 * 揀」係同一件事，寫兩次一定有一邊漏（AGENTS.md 規矩 13）。目的地表本身仲要再高一層 ——
 * 喺 `lib/share-destinations.ts`，連 HERMES 條 cron 鏈都係讀同一張表。
 *
 * 呢度**唔知**點出圖：叫方俾一個 `onPick(target)`，攞住 `target.format`（卡頁）或者
 * `target.aspect`（熱力圖）自己做。
 *
 * owner 2026-08-21：「人地下載可以揀 4K 嗎？我想 1080p 同埋 4K 兩隻分別嘅啫。」
 * → 選單頂多咗一行清晰度（`quality` prop，唔傳就冇呢一行，卡片內頁一如以往）。
 * 有得揀嗰陣每個目的地會**同時**報返真實闊×高 —— 「4K」係級數唔係像素（`wide` 揀 4K
 * 出 2400×1260），淨係俾個 tier 名人睇就係講緊一個唔啱嘅數字。
 *
 * owner 2026-08-23：選單尾再加兩行「淨係要呢個比例」（IG 直向 3:4 / 橫向 16:9）——
 * 佢哋唔係目的地，住喺 `SHARE_EXTRA_TARGETS`，所以七個目的地一個字都冇郁。
 *
 * ⚠️ **user activation**：`navigator.share` 一定要喺撳掣嗰下嘅 activation 之內叫
 * （見 `lib/share-file.ts`）。卡頁張圖要 fetch 幾百 KB，撳完先攞就過咗期 → iOS Safari
 * 唔彈 share sheet、直接落載。所以 `onWarm` 喺**兩個**時機 fire：選單一開就 warm 預設
 * 嗰個 format（七個目的地入面五個都係 `post`），逐列 hover / focus / pointerdown 再
 * warm 佢自己嗰個。叫方要令 `onWarm` idempotent —— 一次互動會 fire 幾次。
 */

import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronDown, Grid3x3, Loader2, Monitor, MoreHorizontal, RectangleHorizontal, Share2, Smartphone, X as XIcon } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { tap } from "@/lib/haptic";
import { SHARE_MENU_TARGETS, type ShareTarget, type ShareTargetId } from "@/lib/share-destinations";
import type { ShareOutcome } from "@/lib/share-file";
import { RESOLUTION_IS_SLOW, RESOLUTION_LABEL, RESOLUTION_RETRY_BUDGET_MS, resolutionPixels, type ShareResolution } from "@/lib/share-resolution";

/* 收埋 menu 前留 120ms 俾 `.select-menu-exit` 做退場動畫（同 select-control.tsx 一樣） */
const MENU_EXIT_MS = 120;

/*
 * ⚠️ 死鎖閘 —— 同 `components/copy-button.tsx` `COPY_TIMEOUT_MS` 一模一樣嘅理由
 * （owner 2026-08-19 報：分享圖撳落去轉圈轉好耐，之後直頭冇反應）。條 promise 一日
 * 唔 settle，個掣就一日停喺 busy = disabled，用戶連再撳嘅機會都冇。45 秒係俾用戶喺
 * OS share sheet 度慢慢揀 app 嘅時間，唔係俾 fetch 嘅（fetch 自己有 20 秒 signal）。
 */
const PICK_TIMEOUT_MS = 45_000;

/* 塊板同畫面邊之間最少留幾多 px 先當「有位」。頁面 gutter 係 16，留 12 有少少鬆動。 */
const EDGE_GUTTER = 12;

function withTimeout<T>(task: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([
    task,
    new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`share-menu: ${ms}ms 都未 settle`)), ms);
    }),
  ]).finally(() => { if (timer) clearTimeout(timer); });
}

/*
 * 品牌圖示：手寫 inline path（Simple Icons，CC0），`fill="currentColor"` 跟住列嘅
 * 顏色走。**唔准為咗四粒 icon 加 dependency** —— 呢個 repo 嘅 lockfile 係釘死嘅
 * （`scripts/test-lockfile-prod-pins.mjs`），而 lucide-react 本身早就唔收品牌 icon。
 * 單色唔上品牌色：六列排埋一齊，四隻公司色（紫紅／黑／綠）會即刻蓋過個熱力圖。
 */
function BrandGlyph({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" width={15} height={15} aria-hidden="true" focusable="false" fill="currentColor">
      <path d={path} />
    </svg>
  );
}

const INSTAGRAM_PATH = "M12 0C8.74 0 8.333.015 7.053.072 5.775.132 4.905.333 4.14.63c-.789.306-1.459.717-2.126 1.384S.935 3.35.63 4.14C.333 4.905.131 5.775.072 7.053.012 8.333 0 8.74 0 12s.015 3.667.072 4.947c.06 1.277.261 2.148.558 2.913.306.788.717 1.459 1.384 2.126.667.666 1.336 1.079 2.126 1.384.766.296 1.636.499 2.913.558C8.333 23.988 8.74 24 12 24s3.667-.015 4.947-.072c1.277-.06 2.148-.262 2.913-.558.788-.306 1.459-.718 2.126-1.384.666-.667 1.079-1.335 1.384-2.126.296-.765.499-1.636.558-2.913.06-1.28.072-1.687.072-4.947s-.015-3.667-.072-4.947c-.06-1.277-.262-2.149-.558-2.913-.306-.789-.718-1.459-1.384-2.126C21.319 1.347 20.651.935 19.86.63c-.765-.297-1.636-.499-2.913-.558C15.667.012 15.26 0 12 0zm0 2.16c3.203 0 3.585.016 4.85.071 1.17.055 1.805.249 2.227.415.562.217.96.477 1.382.896.419.42.679.819.896 1.381.164.422.36 1.057.413 2.227.057 1.266.07 1.646.07 4.85s-.015 3.585-.074 4.85c-.061 1.17-.256 1.805-.421 2.227-.224.562-.479.96-.899 1.382-.419.419-.824.679-1.38.896-.42.164-1.065.36-2.235.413-1.274.057-1.649.07-4.859.07-3.211 0-3.586-.015-4.859-.074-1.171-.061-1.816-.256-2.236-.421-.569-.224-.96-.479-1.379-.899-.421-.419-.69-.824-.9-1.38-.165-.42-.359-1.065-.42-2.235-.045-1.26-.061-1.649-.061-4.844 0-3.196.016-3.586.061-4.861.061-1.17.255-1.814.42-2.234.21-.57.479-.96.9-1.381.419-.419.81-.689 1.379-.898.42-.166 1.051-.361 2.221-.421 1.275-.045 1.65-.06 4.859-.06l.045.03zm0 3.678a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm7.846-10.405a1.441 1.441 0 01-2.88 0 1.44 1.44 0 012.88 0z";
const THREADS_PATH = "M12.186 24h-.007c-3.581-.024-6.334-1.205-8.184-3.509C2.35 18.44 1.5 15.586 1.472 12.01v-.017c.03-3.579.879-6.43 2.525-8.482C5.845 1.205 8.6.024 12.18 0h.014c2.746.02 5.043.725 6.826 2.098 1.677 1.29 2.858 3.13 3.509 5.467l-2.04.569c-1.104-3.96-3.898-5.984-8.304-6.015-2.91.022-5.11.936-6.54 2.717C4.307 6.504 3.616 8.914 3.589 12c.027 3.086.718 5.496 2.057 7.164 1.43 1.783 3.631 2.698 6.54 2.717 2.623-.02 4.358-.631 5.8-2.045 1.647-1.613 1.618-3.593 1.09-4.798-.31-.71-.873-1.3-1.634-1.75-.192 1.352-.622 2.446-1.284 3.272-.886 1.102-2.14 1.704-3.73 1.79-1.202.065-2.361-.218-3.259-.801-1.063-.689-1.685-1.74-1.752-2.964-.065-1.19.408-2.285 1.33-3.082.88-.76 2.119-1.207 3.583-1.291a13.853 13.853 0 0 1 3.02.142c-.126-.742-.375-1.332-.75-1.757-.513-.586-1.308-.883-2.359-.89h-.029c-.844 0-1.992.232-2.721 1.32L7.734 7.847c.98-1.454 2.568-2.256 4.478-2.256h.044c3.194.02 5.097 1.975 5.287 5.388.108.046.216.094.321.142 1.49.7 2.58 1.761 3.154 3.07.797 1.82.871 4.79-1.548 7.158-1.85 1.81-4.094 2.628-7.277 2.65Zm1.235-11.997c-.202 0-.407.006-.615.018-1.836.103-2.977.946-2.916 2.049.064 1.153 1.336 1.689 2.56 1.623 1.126-.06 2.592-.498 2.84-3.42a10.15 10.15 0 0 0-1.869-.27Z";
const X_PATH = "M18.901 1.153h3.68l-8.04 9.19L24 22.846h-7.406l-5.8-7.584-6.638 7.584H.474l8.6-9.83L0 1.154h7.594l5.243 6.932ZM17.61 20.644h2.039L6.486 3.24H4.298Z";
const WHATSAPP_PATH = "M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.481-8.413Z";

/*
 * 四個平台名係專有名詞，五個語言一模一樣 —— 唔准入 i18n（入咗就係四條永遠唔會譯、
 * 但每次加語言都要抄多四次嘅字串）。`other` / `desktop` 係真descriptive 字，行 i18n。
 */
function TargetGlyph({ id }: { id: ShareTargetId }) {
  if (id === "instagram") return <BrandGlyph path={INSTAGRAM_PATH} />;
  if (id === "threads") return <BrandGlyph path={THREADS_PATH} />;
  if (id === "x") return <BrandGlyph path={X_PATH} />;
  if (id === "whatsapp") return <BrandGlyph path={WHATSAPP_PATH} />;
  /* `status` = 全屏面（IG 限時動態／WhatsApp Status）。用直度電話唔用某間公司個 logo：
     呢一行係「邊個**面**」唔係「邊間公司」，掛咗 logo 就即刻同上面四行撞概念。 */
  if (id === "status") return <Smartphone aria-hidden="true" size={15} strokeWidth={1.8} />;
  if (id === "desktop") return <Monitor aria-hidden="true" size={15} strokeWidth={1.8} />;
  /* 尾二行係「淨係揀個比例」（`SHARE_EXTRA_TARGETS`）—— 用形狀 icon 唔用公司 logo：
     3:4 係 IG 個 grid 格仔、16:9 係一塊橫screen。掛咗 IG logo 落 3:4 就會同上面
     第一行（IG = 1:1 feed post）撞，用戶要估邊行先係「真」IG。 */
  if (id === "ig-portrait") return <Grid3x3 aria-hidden="true" size={15} strokeWidth={1.8} />;
  if (id === "widescreen") return <RectangleHorizontal aria-hidden="true" size={15} strokeWidth={1.8} />;
  return <MoreHorizontal aria-hidden="true" size={15} strokeWidth={1.8} />;
}

const BRAND_NAME = {
  instagram: "Instagram",
  threads: "Threads",
  x: "X",
  whatsapp: "WhatsApp",
} as const satisfies Partial<Record<ShareTargetId, string>>;
type BrandedTargetId = keyof typeof BRAND_NAME;

/*
 * 其餘每一行由邊條 copy 出名。
 *
 * ⚠️ **用 `Record<Exclude<…>>` 唔用 `switch` + `default`**（2026-08-23 加兩行嗰陣改）。
 * 舊寫法係一條 `id === "status" ? … : id === "desktop" ? … : copy.other` 嘅鏈 ——
 * 加一個新目的地而唔加返佢個名，就會靜靜跌落最尾嗰個 `copy.other`，選單出現兩行
 * 「其他 App」，tsc 綠、test 綠、冇 error。而家漏咗就係 tsc 紅。
 */
const DESCRIPTIVE_NAME: Record<Exclude<ShareTargetId, BrandedTargetId>, keyof ShareMenuCopy> = {
  status: "status",
  other: "other",
  desktop: "desktop",
  "ig-portrait": "portrait",
  widescreen: "widescreen",
};

function targetName(id: ShareTargetId, copy: ShareMenuCopy): string {
  const brand: string | undefined = BRAND_NAME[id as BrandedTargetId];
  return brand ?? copy[DESCRIPTIVE_NAME[id as Exclude<ShareTargetId, BrandedTargetId>]];
}

export interface ShareMenuCopy {
  /** trigger 文字（`labels.shareImage`，「分享圖片」） */
  label: string;
  /** 選單頂嗰行 kicker，「分享去邊」 */
  pick: string;
  /** 全屏面（IG 限時動態／WhatsApp Status） */
  status: string;
  /** 其他 app */
  other: string;
  /** 電腦／部落格（闊版） */
  desktop: string;
  /** IG 直向 3:4（`SHARE_EXTRA_TARGETS`，唔係目的地，係「我要呢個比例」） */
  portrait: string;
  /** 真 16:9 橫向 */
  widescreen: string;
  /** 熱力圖嘅闊版唔係固定比例，比例位出呢句（「跟畫面」） */
  frame: string;
  done: string;
  error: string;
}

type PickState = "idle" | "busy" | "done" | "error";

/*
 * 清晰度（1080p / 4K）。**成舊嘢一齊俾，唔准散開幾個 optional prop** ——
 * 散開就會出現「俾咗 options 但漏咗 label」呢種狀態：TypeScript 收貨，畫面靜靜咁
 * 唔出個掣，冇人知。整舊一齊，要就要齊。
 *
 * 卡片內頁唔傳呢個 prop，所以卡頁一如以往冇呢一行。
 */
export interface ShareMenuQuality {
  /** 「清晰度」kicker */
  label: string;
  /** 慢嗰級旁邊嗰粒字（「慢」）。邊級算慢由 `RESOLUTION_IS_SLOW` 講，唔係叫方。 */
  slowNote: string;
  options: readonly ShareResolution[];
  value: ShareResolution;
  onChange: (res: ShareResolution) => void;
}

export function ShareMenu({ surface, copy, quality, onPick, onWarm, onOpen, triggerClassName }: {
  /** 熱力圖同卡頁只有一處分別：闊版嗰列個比例標（卡頁固定 1.91:1、熱力圖跟畫面） */
  surface: "card" | "heatmap";
  copy: ShareMenuCopy;
  /** 冇就冇呢一行（卡片內頁）。見 `ShareMenuQuality`。 */
  quality?: ShareMenuQuality;
  /*
   * 回 `ShareOutcome` 嘅話呢度會**睇**佢 —— `"dismissed"`（用戶自己撳走 OS share
   * sheet）唔准當成功。回 `void` 就一律當成功，所以新叫方應該回返個 outcome。
   */
  onPick: (target: ShareTarget) => ShareOutcome | void | Promise<ShareOutcome | void>;
  /** 「就快撳呢個目的地」，叫方預先攞張圖。一定要 idempotent（見檔頭）。 */
  onWarm?: (target: ShareTarget) => void;
  /*
   * 「開咗個選單」= 一次新嘅分享。喺 `onWarm` 之前 fire。
   *
   * ⚠️ 呢個唔係俾人做 analytics —— 熱力圖靠佢**倒空自己個 blob cache**。張圖右上角
   * 個戳係「出圖嗰一刻」，唔倒就會出現：朝早十點開過個選單，下晝兩點再開、撳落去
   * 攞返朝早十點嗰張 blob，張圖印住四個鐘之前嘅鐘。一個 menu session = 一個時刻。
   */
  onOpen?: () => void;
  triggerClassName: string;
}) {
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [state, setState] = useState<PickState>("idle");
  const [align, setAlign] = useState<"right" | "left">("right");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const exitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const busyRef = useRef(false);
  const baseId = useId();
  const listId = `${baseId}-list`;
  const labelId = `${baseId}-label`;
  const qualityId = `${baseId}-quality`;

  /*
   * 鍵盤 roving index 行嘅係**成塊板**：清晰度粒掣排最前，跟住先係七個目的地。
   * 兩截分開數就會變成「Up/Down 上到最頂就停喺第一個目的地」，清晰度嗰行永遠撳唔到
   * —— 即係鍵盤同讀屏用戶等於冇咗個 4K 掣。
   */
  const resOptions = quality?.options ?? [];
  const navCount = resOptions.length + SHARE_MENU_TARGETS.length;

  /*
   * 死鎖閘要跟得住揀咗嘅清晰度。`PICK_TIMEOUT_MS` 淨係「人喺 OS share sheet 度慢慢揀」
   * 嗰段時間；出圖嗰段係 `RESOLUTION_RETRY_BUDGET_MS`（**唔係**單次 fetch timeout）——
   * 4K 一定俾 gateway 喺 60 秒斬一次，要 retry 到 100–126 秒先攞到，所以要用成個
   * retry 預算，唔係單次。用單次嗰個數 = 「撳 4K 等一陣然後必定出紅色交叉」。
   */
  const pickTimeoutMs = quality ? RESOLUTION_RETRY_BUDGET_MS[quality.value] + PICK_TIMEOUT_MS : PICK_TIMEOUT_MS;

  const clearExit = useCallback(() => {
    if (exitTimer.current) clearTimeout(exitTimer.current);
    exitTimer.current = null;
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setClosing(true);
    clearExit();
    exitTimer.current = setTimeout(() => {
      setClosing(false);
      exitTimer.current = null;
    }, MENU_EXIT_MS);
  }, [clearExit]);

  useEffect(() => () => {
    if (exitTimer.current) clearTimeout(exitTimer.current);
    if (resetTimer.current) clearTimeout(resetTimer.current);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) close();
    };
    /* Escape 要 stopPropagation：熱力圖 kiosk 模式喺 document 上面都聽住 Escape，
       唔擋住就係「撳一下 Escape，menu 同全屏一齊收」。 */
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      close();
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [close, open]);

  const openMenu = () => {
    clearExit();
    setClosing(false);
    setOpen(true);
    setActiveIndex(0);
    /* 先講「開咗新一次」（叫方倒 cache），再 warm —— 掉轉就即刻倒走啱啱 warm 嗰張。 */
    onOpen?.();
    /* 一開就 warm 預設 format：七個目的地入面五個都係 `post`，撳落去就已經攞緊。
       ⚠️ 要第一行（IG／`post` 嗰批），唔係新加嗰兩行 —— 所以行 `SHARE_MENU_TARGETS[0]`
       而唔係最尾。加新行係加喺**尾**（見 `SHARE_EXTRA_TARGETS`），呢句唔使跟住改。 */
    onWarm?.(SHARE_MENU_TARGETS[0]);
  };

  const pick = async (target: ShareTarget) => {
    if (busyRef.current) return;
    busyRef.current = true;
    close();
    triggerRef.current?.focus();
    if (resetTimer.current) clearTimeout(resetTimer.current);
    setState("busy");
    try {
      const outcome = await withTimeout(Promise.resolve(onPick(target)), pickTimeoutMs);
      busyRef.current = false;
      /*
       * ⚠️ 用戶喺 OS share sheet 撳「取消」= `"dismissed"` = **乜都冇分享到**。
       * 唔准出綠色剔：下面個 `role="status"` aria-live 會即刻讀「圖片已匯出」俾讀屏
       * 用戶聽，而佢啱啱先自己取消咗。靜靜返 idle 先啱 —— `lib/share-file.ts` 自己
       * 都寫住「唔係錯，叫方唔好報 error，亦唔好再做後續動作」。
       * （2026-08-20 上街後審計捉到：兩個叫方都掉咗個 return value。）
       */
      if (outcome === "dismissed") {
        setState("idle");
        return;
      }
      setState("done");
      tap.success();
    } catch {
      busyRef.current = false;
      setState("error");
      tap.error();
    }
    resetTimer.current = setTimeout(() => setState("idle"), 1800);
  };

  const onTriggerKeyDown = (event: React.KeyboardEvent) => {
    if (open) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openMenu();
      setActiveIndex(event.key === "ArrowUp" ? navCount - 1 : 0);
    }
  };

  const onListKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % navCount);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + navCount) % navCount);
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(navCount - 1);
    } else if (event.key === "Tab") {
      close();
    }
  };

  /*
   * 塊板預設貼住 trigger 右邊（分享掣通常喺右上角）。但熱力圖喺手機個掣係喺**左**邊
   * ——右貼就等於成塊板飛咗出畫面左邊，個名同比例齊齊斬走（2026-08-20 390px 截圖影到）。
   * 唔寫死 breakpoint 都唔重覆抄 CSS 個 min-width：開嗰下度返真身闊度，唔夠位先反去左貼。
   * 度嘅係 `offsetWidth`（受 max-width clamp 影響、但同貼邊方向無關），所以計出嚟嘅結果
   * 唔會反過來影響下次量度 —— 一個 pass 就穩定，唔會左右左右咁彈。
   */
  const measureAlign = useCallback(() => {
    const trigger = triggerRef.current;
    const panel = panelRef.current;
    if (!trigger || !panel) return;
    setAlign(trigger.getBoundingClientRect().right - panel.offsetWidth < EDGE_GUTTER ? "left" : "right");
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    measureAlign();
  }, [open, measureAlign]);

  /*
   * 開住個 menu 轉橫屏／拉窗口，個掣會由畫面左邊行去右邊（熱力圖手機直度個掣喺左，
   * 橫置之後喺右）。唔重量就仲貼住舊嗰邊 —— 390→844 實測塊板凸出畫面右邊 ~28px，
   * 比例標成列切走兼多咗橫向 overflow。轉屏唔會 fire pointerdown，所以個 menu 唔會
   * 自己收，一定要自己聽（2026-08-20 上街後審計捉到）。
   */
  useEffect(() => {
    if (!open) return;
    window.addEventListener("resize", measureAlign);
    window.addEventListener("orientationchange", measureAlign);
    return () => {
      window.removeEventListener("resize", measureAlign);
      window.removeEventListener("orientationchange", measureAlign);
    };
  }, [open, measureAlign]);

  /* 鍵盤行到邊列，個 DOM focus 就跟住去邊列 —— menu 入面每列都係真 <button>，
     用真 focus 好過 aria-activedescendant：唔使自己畫高亮，`:focus-visible` 就係。 */
  useEffect(() => {
    if (!open) return;
    document.getElementById(`${listId}-${activeIndex}`)?.focus({ preventScroll: true });
  }, [open, activeIndex, listId]);

  const busy = state === "busy";
  const menuVisible = open || closing;

  return (
    <div ref={rootRef} className="share-menu">
      <button
        ref={triggerRef}
        type="button"
        className={`${triggerClassName} share-menu-trigger`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        data-state={state}
        /*
         * ⚠️ 用 `aria-disabled` 唔用 `disabled`。瀏覽器將一個**正攞住 focus** 嘅 button
         * 設成 `disabled` 會即刻 blur 佢，`activeElement` 跌返 `<body>` —— 而 `pick()`
         * 正正就係 focus 個 trigger 之後即刻 `setState("busy")`。結果：鍵盤／讀屏用戶
         * 分享完一次就跌返文件開頭，下一個 Tab 由 skip-link 重新數起。
         * 唔准撳嘅保障喺 `busyRef`（`pick()` 第一句）同下面個 onClick，唔靠 DOM disabled。
         */
        aria-disabled={busy}
        aria-busy={busy}
        title={state === "error" ? copy.error : state === "done" ? copy.done : undefined}
        onClick={() => { if (busy) return; if (open) close(); else openMenu(); }}
        onKeyDown={onTriggerKeyDown}
      >
        <AnimatePresence mode="wait" initial={false}>
          {state === "done" ? (
            <motion.span key="done" className="copy-icon" initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }} transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}>
              <Check aria-hidden="true" size={14} strokeWidth={2.2} />
            </motion.span>
          ) : state === "error" ? (
            <motion.span key="error" className="copy-icon" initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }} transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}>
              <XIcon aria-hidden="true" size={14} strokeWidth={2.2} />
            </motion.span>
          ) : busy ? (
            <motion.span key="busy" className="copy-icon copy-icon-busy" initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }} transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}>
              <Loader2 aria-hidden="true" size={14} strokeWidth={2} />
            </motion.span>
          ) : (
            <motion.span key="idle" className="copy-icon" initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }} transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}>
              <Share2 aria-hidden="true" size={14} strokeWidth={1.8} />
            </motion.span>
          )}
        </AnimatePresence>
        <span>{copy.label}</span>
        {/* 個掣本身唔再係「即刻做嘢」，係「開個選單」——冇支箭嘴就讀唔出呢個分別 */}
        <ChevronDown className="share-menu-chevron" data-open={open ? "true" : "false"} aria-hidden="true" size={13} strokeWidth={2} />
      </button>
      {/* 結果讀屏用：sibling status，trigger 個 label 唔郁（唔會跳闊度） */}
      <span role="status" aria-live="polite" className="sr-only">
        {state === "done" ? copy.done : state === "error" ? copy.error : ""}
      </span>
      {menuVisible ? (
        <div
          ref={panelRef}
          id={listId}
          role="menu"
          aria-labelledby={labelId}
          className={`share-menu-panel${open ? "" : " select-menu-exit"}`}
          data-align={align}
          onKeyDown={onListKeyDown}
        >
          {quality ? (
            <>
              <p id={qualityId} className="share-menu-kicker">{quality.label}</p>
              {/* 揀清晰度**唔會**收 menu：揀完仲要揀去邊。所以呢度冇 `pick()`。 */}
              <div className="share-menu-chips" role="group" aria-labelledby={qualityId}>
                {quality.options.map((res, index) => (
                  <button
                    key={res}
                    id={`${listId}-${index}`}
                    type="button"
                    role="menuitemradio"
                    aria-checked={res === quality.value}
                    tabIndex={index === activeIndex ? 0 : -1}
                    className="share-menu-chip"
                    data-res={res}
                    data-active={res === quality.value ? "true" : "false"}
                    onPointerEnter={() => setActiveIndex(index)}
                    /*
                     * ⚠️ 呢度**唔准**順手叫 `onWarm` —— `onWarm` 係上面 render 個 closure，
                     * 仲攞住舊嗰級（`onChange` 只係排咗個 setState，未 re-render），warm 出嚟
                     * 嘅係啱啱撳走嗰級。4K 要 warm 就 `onChange` 入面自己 warm（叫方先知道
                     * 新嗰級係咩）。
                     */
                    onClick={() => { tap.select(); quality.onChange(res); }}
                  >
                    <span className="share-menu-chip-label">{RESOLUTION_LABEL[res]}</span>
                    {RESOLUTION_IS_SLOW[res] ? (
                      <span className="share-menu-chip-note">{quality.slowNote}</span>
                    ) : null}
                  </button>
                ))}
              </div>
            </>
          ) : null}
          <p id={labelId} className="share-menu-kicker">{copy.pick}</p>
          {SHARE_MENU_TARGETS.map((target, offset) => {
            const index = resOptions.length + offset;
            const name = targetName(target.id, copy);
            const ratio = surface === "heatmap" && target.frameOnHeatmap ? copy.frame : target.ratio;
            /*
             * ⚠️ 有得揀清晰度嗰陣，一定要同時報返**真實闊×高**。
             * 「4K」係個 tier 名，唔係像素：`wide` 揀 4K 出嘅係 2400×1260，唔係 3840。
             * 淨係俾個 tier 名人睇 = 講緊一個唔啱嘅數字（`lib/share-resolution.ts` 檔頭
             * 寫住呢條規矩，2026-08-20 個比例標已經踩過一次同款窿）。
             */
            const px = quality ? resolutionPixels(target.format, quality.value) : null;
            return (
              <button
                key={target.id}
                id={`${listId}-${index}`}
                type="button"
                role="menuitem"
                tabIndex={index === activeIndex ? 0 : -1}
                className="share-menu-item"
                data-target={target.id}
                onPointerEnter={() => { setActiveIndex(index); onWarm?.(target); }}
                onFocus={() => onWarm?.(target)}
                onPointerDown={() => onWarm?.(target)}
                onClick={() => { tap.select(); void pick(target); }}
              >
                <span className="share-menu-icon"><TargetGlyph id={target.id} /></span>
                <span className="share-menu-name">{name}</span>
                <span className="share-menu-meta">
                  {px ? <span className="share-menu-px">{px.width}×{px.height}</span> : null}
                  <span className="share-menu-ratio">{ratio}</span>
                </span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
