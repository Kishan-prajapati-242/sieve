// What the reader can actually see, as a value rather than as elapsed time.
//
// Two bugs made this necessary and they are the same bug. The header read
// "20 shown of 202" about 84ms after a toggle while THREE rows were on
// screen, because the count came from a scalar in the response and the rows
// waited on animation state — two clocks driving a number and the thing it
// counts. And gating arrivals behind the survivors' move emptied the page:
// with 15 arrivals and 5 survivors the list visibly collapsed to 5, sat
// nearly empty, then expanded to 20.
//
// So presentation is modelled explicitly. A row is presented once its
// entrance has begun; the header counts presented rows and nothing else.
// Both derive from one state, which is what makes the invariant testable by
// STEPPING the state rather than by sampling a running animation.

export type Phase = "settled" | "moving" | "arriving";

export interface Presentation {
  /** Rows the reader can see, in list order. */
  presentedIds: number[];
  phase: Phase;
}

/** The transition, as a sequence of states rather than a set of durations.
 *
 *  Survivors are presented immediately — they are already on screen and are
 *  only changing position, so hiding them would be a lie in the other
 *  direction. Arrivals join progressively.
 */
export function presentationSteps(
  survivorIds: number[],
  arrivalIds: number[],
  finalOrder: number[],
): Presentation[] {
  const steps: Presentation[] = [];
  const visible = new Set(survivorIds);
  const inOrder = (s: Set<number>) => finalOrder.filter((id) => s.has(id));

  if (survivorIds.length > 0) {
    steps.push({ presentedIds: inOrder(visible), phase: "moving" });
  }
  // Arrivals land bottom-up: the tail fills while the top stays quiet, so
  // the row climbing to #1 is never crossed by a row landing above it.
  for (const id of [...arrivalIds].reverse()) {
    visible.add(id);
    steps.push({ presentedIds: inOrder(visible), phase: "arriving" });
  }
  steps.push({ presentedIds: [...finalOrder], phase: "settled" });
  return steps;
}

/** The invariant the header must satisfy at EVERY step, not just at the ends.
 *
 *  Frames found this category; arithmetic could not, because the count
 *  contradicted no other number — it described a different moment. This is
 *  the guard that holds it closed (findings.md 2026-08-14).
 */
export function headerCount(p: Presentation): number {
  return p.presentedIds.length;
}
