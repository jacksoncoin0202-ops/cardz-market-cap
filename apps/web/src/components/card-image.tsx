"use client";

import type { SyntheticEvent } from "react";
import type { MarketCardView } from "@/lib/types";

type CardImageImage = MarketCardView["image"];

export function srcSet(image: CardImageImage): string | undefined {
  if (!image.variants) return undefined;
  const entries: string[] = [];
  if (image.variants["200"]) entries.push(`${image.variants["200"]} 200w`);
  if (image.variants["600"]) entries.push(`${image.variants["600"]} 600w`);
  return entries.length ? entries.join(", ") : undefined;
}

export function handleCardImageError(event: SyntheticEvent<HTMLImageElement>): void {
  const target = event.currentTarget;
  if (target.dataset.cardzFallback === "true") return;
  target.dataset.cardzFallback = "true";
  target.removeAttribute("srcset");
  target.src = "/card-placeholder.svg";
  /* 爛圖一樣要放行：late tile 嘅卡圖 load 好先淡入（.tile-img-gated），
     onerror 唔標 ready 就會永遠透明，格入面淨返個影。 */
  target.classList.add("tile-img-ready");
}

/* hoist 出嚟：100 格每 render 都建新 closure 會令 <img> props 逐次唔同，memo 白做。 */
function markImageReady(event: SyntheticEvent<HTMLImageElement>): void {
  event.currentTarget.classList.add("tile-img-ready");
}
function markReadyIfCached(el: HTMLImageElement | null): void {
  // 瀏覽器 cache 命中會直接 complete、onLoad 唔再觸發，mount 時補一刀
  if (el && el.complete && el.naturalWidth > 0) el.classList.add("tile-img-ready");
}

export interface CardImgProps {
  src: string;
  srcSet?: string;
  sizes: string;
  loading?: "eager" | "lazy";
  /* heatmap tile：頭 12 名／大格 high，其餘 low，等瀏覽器先拉睇得清嗰批 */
  fetchPriority?: "high" | "low" | "auto";
  alt: string;
  className?: string;
}

/* 原始版：淨係食 primitive（heatmap tile 用，memo 只比較字串） */
export function CardImg({ src, srcSet: set, sizes, loading = "lazy", fetchPriority, alt, className }: CardImgProps) {
  return (
    <img
      src={src}
      srcSet={set}
      sizes={sizes}
      alt={alt}
      loading={loading}
      fetchPriority={fetchPriority}
      decoding="async"
      className={className}
      onError={handleCardImageError}
      onLoad={markImageReady}
      ref={markReadyIfCached}
    />
  );
}

export function CardImage({ image, ...rest }: Omit<CardImgProps, "src" | "srcSet"> & { image: CardImageImage }) {
  return <CardImg src={image.url} srcSet={srcSet(image)} {...rest} />;
}
