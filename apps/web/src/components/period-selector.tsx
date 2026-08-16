"use client";

/* interactionkit Tabs 移植（https://github.com/armondschneider/interactionkit, MIT）：
   選中項底下 layoutId 滑動膠囊。每個 instance 用 useId 做 layoutId 後綴，
   避免同頁多個 selector 嘅 pill 互相飛越。 */
import { motion } from "framer-motion";
import { useId } from "react";
import { tap } from "@/lib/haptic";
import { copy } from "@/lib/i18n";
import { marketWindows, type MarketWindow } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

export function PeriodSelector({ compact = false, period: periodOverride, onChange }: {
  compact?: boolean;
  /* 手機 heatmap 用自己嘅 local period（唔寫 URL），所以俾 caller 直接控制。 */
  period?: MarketWindow;
  onChange?: (period: MarketWindow) => void;
}) {
  const { locale, period, update } = useMarketSettings();
  const t = copy[locale];
  const pillId = `period-pill-${useId()}`;
  const activePeriod = periodOverride ?? period;
  return (
    <div className={`period-selector${compact ? " period-selector-compact" : ""}`} role="group" aria-label={t.labels.change}>
      {marketWindows.map((item) => (
        <button
          key={item}
          type="button"
          aria-pressed={activePeriod === item}
          onClick={() => {
            /* 已選中嗰個再撳唔震：冇嘢變就唔好扮有回饋 */
            if (item !== activePeriod) tap.select();
            if (onChange) onChange(item);
            else update({ period: item });
          }}
        >
          {activePeriod === item && (
            <motion.span
              layoutId={pillId}
              className="period-pill"
              transition={{ type: "spring", bounce: 0.18, duration: 0.35 }}
            />
          )}
          <span className="period-label">{t.periods[item]}</span>
        </button>
      ))}
    </div>
  );
}
