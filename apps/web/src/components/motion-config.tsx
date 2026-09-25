"use client";

import { MotionConfig } from "framer-motion";
import type { ReactNode } from "react";

/* 全站 framer-motion 跟 OS「減少動態效果」：transform 類動畫即刻落地，只留 opacity。
   layout.tsx 係 server component，所以要呢層 client wrapper。 */
export function AppMotionConfig({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
