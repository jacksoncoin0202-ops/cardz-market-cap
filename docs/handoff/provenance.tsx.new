"use client";

import { copy } from "@/lib/i18n";
import { useMarketSettings } from "@/lib/use-market-settings";

/*
 * 出街可見嘅方法說明。兩個規矩：
 *
 * 1. 只講「點計」，唔講數據由邊度嚟。供應商代號唔准曝光（owner 明示），
 *    ⚠️ 呢條線目前冇自動 gate（canary-public.mjs 已刪），全靠人手守。
 * 2. 日期要 ISO 而且同 label 喺同一個 text node。抽取器讀嘅係 rendered text，
 *    `<time datetime>` 個 attribute 佢哋唔會讀；而 `formatObservationDate` 出
 *    「Aug 10, 2026」呢種 medium 格式，機器認唔到係日期。所以呢度自己切 ISO，
 *    畀人睇嘅 medium 日期喺 `.data-time` 嗰行照舊。
 */
function isoDay(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return null;
  return date.toISOString().slice(0, 10);
}

export function Provenance({ updatedAt }: { updatedAt: string | null | undefined }) {
  const { locale } = useMarketSettings();
  const t = copy[locale];
  const iso = isoDay(updatedAt);

  return (
    <section className="provenance-panel" aria-labelledby="provenance-heading">
      <p className="section-kicker">{t.provenance.kicker}</p>
      <h2 id="provenance-heading">{t.provenance.title}</h2>
      <p className="provenance-body">{t.provenance.body}</p>
      <dl className="provenance-steps">
        {t.provenance.steps.map((step) => (
          <div key={step.term}>
            <dt>{step.term}</dt>
            <dd>{step.detail}</dd>
          </div>
        ))}
      </dl>
      {iso && (
        <p className="provenance-updated">
          <time dateTime={iso}>{`${t.provenance.updated} ${iso}`}</time>
        </p>
      )}
      <p className="provenance-byline">{t.provenance.byline}</p>
    </section>
  );
}
