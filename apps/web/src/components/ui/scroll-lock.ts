/*
 * 共用 scroll lock（bottom sheet / desktop dialog / tune panel 三個 overlay 都行呢條）。
 *
 * - `html{overflow-x:clip}` 令 <html> 先係 scroller，所以 class 要落 documentElement，
 *   落 body 係 no-op（以前 `body.sheet-open` 就係咁樣冇效）。CSS：`html.sheet-open{overflow:hidden}`。
 * - 觸控機（iOS Safari）唔一定理 overflow:hidden，所以 touch device 額外用 body
 *   position:fixed;top:-scrollY 釘住，關嗰陣還返 scrollY（instant，唔可以俾
 *   html{scroll-behavior:smooth} 拖住慢慢碌返去）。
 * - 有 depth 計數：sheet 入面再開 panel 都唔會提早解鎖。
 */
let depth = 0;
let savedY = 0;
let fixedBody = false;

const touchQuery = "(hover: none), (pointer: coarse)";

export function lockScroll(): void {
  if (typeof document === "undefined") return;
  if (depth++ > 0) return;
  const root = document.documentElement;
  root.classList.add("sheet-open");
  if (!window.matchMedia(touchQuery).matches) return;
  savedY = window.scrollY;
  const body = document.body;
  body.style.position = "fixed";
  body.style.top = `-${savedY}px`;
  body.style.left = "0";
  body.style.right = "0";
  body.style.width = "100%";
  fixedBody = true;
}

export function unlockScroll(): void {
  if (typeof document === "undefined") return;
  if (depth === 0) return;
  if (--depth > 0) return;
  document.documentElement.classList.remove("sheet-open");
  if (!fixedBody) return;
  fixedBody = false;
  const body = document.body;
  body.style.position = "";
  body.style.top = "";
  body.style.left = "";
  body.style.right = "";
  body.style.width = "";
  window.scrollTo({ top: savedY, left: 0, behavior: "instant" });
}
