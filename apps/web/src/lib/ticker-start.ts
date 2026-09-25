/*
 * CapTicker 起點（fe05(cjk)，2026-08-17）：單位由目標值一次過決定。
 * compact 格式嘅單位（$2.7B / $12.31億 / ₩1.2조）係 Intl 按值揀，由 0 滾上去會途中換字
 * （$0 → $999M → $1.0B、万 → 億），字串長度跟住跳。format 係 caller 俾嘅黑盒，所以只靠字串
 * 「單位簽名」（拆走數字／分隔符／空格）：起點揀同一簽名範圍最低嘅 10 冪（$1.0B → $2.7B、
 * 1億 → 12.31億、$1K → $850K）；retarget 跨單位就由該範圍嘅邊緣接落去（$2.7B → $850M 變
 * $999.9M → $850M）。非 compact 格式（卡頁價格 $1,234）簽名唔變，起點跌到 1，同以前由 0 起冇分別。
 * 純函數，scripts/test-fe-ticker-unit.mjs 直接 import 呢個檔測。
 */
/*
 * 動畫進度 → ease-out cubic，**進度一定要夾喺 [0,1]**。
 * 點解要夾低位：`start = performance.now()` 喺 layout effect 度攞，但 rAF callback 收到嘅 `now`
 * 係嗰一 frame 開始嗰刻嘅時間戳 —— 如果 effect 同 rAF 喺同一 frame 入面行，`now` 會早過 `start`，
 * `elapsed` 變負數。ease-out cubic `1-(1-p)³` 喺 p<0 時回負值 → 顯示值跌到起點以下。
 * 實測（dev :3901 `/` en/USD）：起點 $1B、目標 $2.7B，第一 frame 出咗 **$993.75M**
 * （p ≈ -0.0012 → eased ≈ -0.0037）—— 單位由 B 跳返 M，正正係 tickerStart 想修嗰個 glitch。
 */
export function tickerEase(elapsed: number, duration: number): number {
  if (!(duration > 0)) return 1;
  const p = Math.min(1, Math.max(0, elapsed / duration));
  return 1 - Math.pow(1 - p, 3);
}

export const unitSignature = (text: string): string => text.replace(/[\d.,\s'’]/g, ""); // \s 已包 U+00A0 / U+202F（Intl 貨幣分隔）

function powerOfTenAtOrBelow(n: number): number {
  let p = Math.pow(10, Math.floor(Math.log10(n)));
  if (p * 10 <= n) p *= 10; else if (p > n) p /= 10; // log10 浮點誤差（1000 → 2.9999…）
  return p;
}

export function tickerStart(from: number | null, target: number, format: (n: number) => string): number {
  if (!(target > 0) || !Number.isFinite(target)) return from ?? 0;
  const sig = unitSignature(format(target));
  if (from !== null && unitSignature(format(from)) === sig) return from; // 同單位：由顯示緊嘅值滾
  let low = powerOfTenAtOrBelow(target);
  if (unitSignature(format(low)) !== sig) return target; // 邊界怪例（rounding 令 10 冪落另一單位）：唔滾，直接落
  while (low >= 10 && unitSignature(format(low / 10)) === sig) low /= 10;
  if (from === null || from < target) return low;
  // 由上面跌落嚟跨單位：由本單位範圍嘅頂接落去（999.9M），搵唔到就由 low 起
  let hi = low;
  while (unitSignature(format(hi * 10)) === sig) hi *= 10;
  for (const delta of [1e-4, 1e-3, 1e-2, 0.1]) {
    const candidate = hi * 10 * (1 - delta);
    if (candidate > target && unitSignature(format(candidate)) === sig) return candidate;
  }
  /*
   * target 貼住單位範圍嘅頂（頭 0.01%，例如 $999.99M）：搵唔到一個「大過 target 又仲係同單位」嘅起點。
   * 以前呢度回 `low`（範圍底），即係由 $12B 顯示緊嘅值一嘢跳落 $1M 再向上滾去 $999.99M ——
   * 明明係跌價，畫面卻由低過三個數量級嘅位升上去。冇位滾就唔滾：回 target，CapTicker 直接落值
   * （同上面 :37 邊界怪例同一處理）。scripts/test-fe-ticker-unit.mjs 有無條件方向斷言守住。
   */
  return target;
}
