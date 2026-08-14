import { useEffect, useState } from "react";

/** True only once `active` has been continuously true for `delayMs`.
 *
 *  Loading feedback has two failure modes and they pull in opposite
 *  directions. Show it immediately and a warm 30ms query flashes a spinner
 *  on every mode toggle, which reads as instability. Never show it and a
 *  cold 1,611ms query looks like a frozen page — measured 2026-08-14, the
 *  stale list sat unchanged with no indication for the whole fetch, because
 *  placeholderData keeps the previous rows mounted.
 *
 *  So: nothing under the threshold, feedback over it. The demo's warm path
 *  stays silent; a reviewer's first cold click on Render gets an answer.
 */
export function useDelayedFlag(active: boolean, delayMs: number): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!active) {
      setShown(false);
      return;
    }
    const t = setTimeout(() => setShown(true), delayMs);
    return () => clearTimeout(t);
  }, [active, delayMs]);
  return shown;
}
