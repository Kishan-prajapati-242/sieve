import { useCallback } from "react";

/** Feeds the pointer position into CSS custom properties.
 *
 *  Written to the element's own style rather than held in React state: this
 *  fires on every pointermove, and re-rendering a twenty-row list at pointer
 *  frequency is exactly how a hover effect becomes a performance problem.
 *  The values are read by `.spotlight::before`, so no JS runs on paint.
 */
export function useSpotlight() {
  return useCallback((e: React.PointerEvent<HTMLElement>) => {
    const el = e.currentTarget;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`);
    el.style.setProperty("--my", `${e.clientY - r.top}px`);
  }, []);
}
