import { useId } from "react";
import type { PricePoint } from "@/lib/types";

interface SparklineProps {
  points: PricePoint[];
  label: string;
}

export function Sparkline({ points, label }: SparklineProps) {
  const gradientId = useId();
  const values = points
    .map((point) => point.trackedSalesValueUsd)
    .filter((value): value is number => value !== null && Number.isFinite(value));
  if (values.length < 3) return null;
  const width = 72;
  const height = 22;
  const pad = 2;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = (width - pad * 2) / (values.length - 1);
  const coords = values.map((value, index) => ({
    x: pad + step * index,
    y: height - pad - ((value - min) / span) * (height - pad * 2),
  }));
  const line = coords.map((coord) => `${coord.x.toFixed(1)},${coord.y.toFixed(1)}`).join(" ");
  const area = `${pad},${height - pad} ${line} ${(width - pad).toFixed(1)},${height - pad}`;
  const rising = values.at(-1)! >= values[0];
  return (
    <svg className={`sparkline ${rising ? "sparkline-up" : "sparkline-down"}`} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopOpacity="0.28" />
          <stop offset="100%" stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon className="sparkline-area" points={area} fill={`url(#${gradientId})`} stroke="none" />
      <polyline className="sparkline-line" points={line} fill="none" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
