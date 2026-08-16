import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import "@/app/styles/empty-state.css";

/*
 * 共用空狀態（FE05 WS4）。
 *
 * 之前五個位各寫各嘅：`<p class="empty-state">` 一句、`<div class="empty-state">`
 * 兩句 + 一粒掣、`.history-empty` 一句、`.empty-detail` 一句 + primary-action。
 * 同一件事四種形狀，改文案／加圖示要改四處。
 *
 * 兩個唔郁嘅嘢：
 * 1. **文案一個字都冇改**，全部照用返原本嗰個 i18n key（冇加新 key）。
 * 2. `.empty-state` 個盒（globals.css:2053，虛線框 + margin/padding）照舊，
 *    呢度只係喺入面加圖示同排版；`className` 留返俾 caller 補 `fade-up` 之類。
 *
 * `action` 收 ReactNode（唔係 `{label, onClick}`）：rankings 要一粒 `<button>`
 * 做 router.push、卡頁要一條 `<Link>`，兩種行為唔應該屈埋同一個 prop。
 */
export function EmptyState({ icon: Icon, title, hint, action, className }: {
  icon: LucideIcon;
  title: string;
  hint?: string | null;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`empty-state empty-state-block${className ? ` ${className}` : ""}`}>
      <Icon className="empty-state-icon" aria-hidden="true" size={22} strokeWidth={1.5} />
      <p className="empty-state-title">{title}</p>
      {hint ? <p className="empty-state-hint">{hint}</p> : null}
      {action}
    </div>
  );
}
