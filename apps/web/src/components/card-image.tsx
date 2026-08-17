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
  /*
   * 內在尺寸（baked payload 嘅 `image.width/height`）。出咗呢一對，`<img>` 由
   * parse 嗰刻就有 aspect-ratio，圖未到之前唔會由 0×0 撐開 —— CLS 就係喺呢度嚟。
   * 兩個都係 primitive number，`memo` 照樣淨係比較值（DESIGN.md §5）。
   * 冇尺寸就兩個一齊唔傳：得一半冇 aspect-ratio，填 0 個盒真係會塌。
   */
  width?: number;
  height?: number;
}

/* 原始版：淨係食 primitive（heatmap tile 用，memo 只比較字串） */
export function CardImg({ src, srcSet: set, sizes, loading = "lazy", fetchPriority, alt, className, width, height }: CardImgProps) {
  return (
    <img
      src={src}
      srcSet={set}
      sizes={sizes}
      alt={alt}
      width={width}
      height={height}
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

/*
 * placeholder（`/card-placeholder.svg`）冇 baked 尺寸，唔准借卡嘅比例度佢 ——
 * 借咗就係一個講大話嘅盒。爛圖 fallback 之後（`handleCardImageError` 換 src）
 * attribute 就故意留住：個盒已經佔咗位，拆走反而多一次 shift。
 */
export function CardImage({ image, ...rest }: Omit<CardImgProps, "src" | "srcSet"> & { image: CardImageImage }) {
  const sized = image.kind !== "placeholder";
  return (
    <CardImg
      src={image.url}
      srcSet={srcSet(image)}
      width={sized ? image.width : undefined}
      height={sized ? image.height : undefined}
      {...rest}
    />
  );
}
