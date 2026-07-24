import { useId } from "react";
import { copy } from "@/lib/i18n";
import { formatInteger } from "@/lib/format";
import { graders, type Grader, type Locale, type MarketViewSnapshot } from "@/lib/types";

const SIZE = 132;
const STROKE = 18;
const CENTER = SIZE / 2;
const RADIUS = CENTER - STROKE / 2 - 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

interface ShareSlice {
  grader: Grader;
  value: number;
  share: number;
  offset: number;
}

export function GraderShareDonut({ snapshot, locale }: { snapshot: MarketViewSnapshot; locale: Locale }) {
  const labelId = useId();
  const t = copy[locale];

  const slices: ShareSlice[] = graders.map((grader) => {
    let value = 0;
    for (const card of snapshot.top100) {
      const metric = card.graderPopulations[grader].total;
      if (metric.status === "ready" && metric.value !== null && Number.isFinite(metric.value)) value += metric.value;
    }
    return { grader, value, share: 0, offset: 0 };
  });
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  let runningOffset = CIRCUMFERENCE / 4;
  for (const slice of slices) {
    slice.share = total > 0 ? (slice.value / total) * 100 : 0;
    slice.offset = runningOffset;
    runningOffset -= total > 0 ? (slice.value / total) * CIRCUMFERENCE : 0;
  }

  const ariaLabel = `${t.grader.marketShare} — ${slices.map((s) => `${s.grader} ${s.share.toFixed(1)}%`).join(", ")}`;

  return (
    <section className="grader-share" aria-labelledby={`${labelId}-heading`}>
      <div className="grader-share-heading">
        <p className="section-kicker">{t.grader.eyebrow}</p>
        <h2 id={`${labelId}-heading`}>{t.grader.marketShare}</h2>
      </div>
      <div className="grader-share-body">
        <div className="grader-share-chart">
          <svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label={ariaLabel}>
            {slices.map((slice) => {
              const length = total > 0 ? (slice.value / total) * CIRCUMFERENCE : 0;
              const dasharray = `${Math.max(0, length - 1)} ${CIRCUMFERENCE - Math.max(0, length - 1)}`;
              return (
                <circle
                  key={slice.grader}
                  className={`grading-${slice.grader.toLowerCase()}`}
                  cx={CENTER}
                  cy={CENTER}
                  r={RADIUS}
                  fill="none"
                  strokeWidth={STROKE}
                  strokeDasharray={dasharray}
                  strokeDashoffset={slice.offset}
                />
              );
            })}
          </svg>
          <div className="grader-share-center" aria-hidden="true">
            <strong>{total > 0 ? formatInteger(total, locale) : "—"}</strong>
            <span>{t.grader.totalPopulationShort}</span>
          </div>
        </div>
        <ul className="grader-share-legend">
          {slices.map((slice) => (
            <li key={slice.grader} className="grader-share-item">
              <span className={`grading-dot grading-${slice.grader.toLowerCase()}`} aria-hidden="true" />
              <span className="grading-name">{t.grader.names[slice.grader]}</span>
              <span className="grading-share-label">{slice.share.toFixed(1)}%</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
