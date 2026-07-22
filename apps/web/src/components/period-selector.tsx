"use client";

import { copy } from "@/lib/i18n";
import { marketWindows } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

export function PeriodSelector({ compact = false }: { compact?: boolean }) {
  const { locale, period, update } = useMarketSettings();
  const t = copy[locale];
  return (
    <div className={`period-selector${compact ? " period-selector-compact" : ""}`} role="group" aria-label={t.labels.change}>
      {marketWindows.map((item) => (
        <button
          key={item}
          type="button"
          aria-pressed={period === item}
          onClick={() => update({ period: item })}
        >
          {t.periods[item]}
        </button>
      ))}
    </div>
  );
}
