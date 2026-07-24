"use client";

import { useCallback, useEffect, useState } from "react";
import { CardImage } from "./card-image";
import { copy } from "@/lib/i18n";
import type { MarketCardView } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

/* interactionkit image-expand 概念：撳卡圖開全屏 lightbox，Esc / 撳背景關返 */
export function CardImageModal({ card }: { card: MarketCardView }) {
  const { locale } = useMarketSettings();
  const t = copy[locale];
  const [open, setOpen] = useState(false);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    document.body.classList.add("sheet-open");
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("sheet-open");
    };
  }, [open, close]);

  return (
    <>
      <button
        type="button"
        className="detail-art-button"
        onClick={() => setOpen(true)}
        aria-label={t.labels.expandImage}
      >
        <CardImage image={card.image} sizes="(max-width: 680px) 90vw, 560px" loading="eager" alt={card.image.alt[locale] || t.labels.imageAlt} />
      </button>
      {open && (
        <div className="image-modal" role="dialog" aria-modal="true" aria-label={card.name[locale] || t.labels.imageAlt} onClick={close}>
          <button type="button" className="image-modal-close" onClick={close}>{t.labels.close}</button>
          <div className="image-modal-stage" onClick={(event) => event.stopPropagation()}>
            <CardImage image={card.image} sizes="92vmin" loading="eager" alt={card.image.alt[locale] || t.labels.imageAlt} />
          </div>
          <p className="image-modal-caption">{card.name[locale] || t.status.unavailable} · {card.collectorNumber}</p>
        </div>
      )}
    </>
  );
}
