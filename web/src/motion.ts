// The motion vocabulary, in one place so the timings stay consistent.
//
// CHOREOGRAPHY, re-derived 2026-08-14 at the real k=20 rather than the
// top-8 slice the app never performs. Measured transitions on the demo
// query:
//
//   bm25   -> hybrid:  0 leaving,  5 staying, 15 ARRIVING
//   hybrid -> bm25  : 15 leaving,  5 staying,  0 arriving
//   bm25   -> vector:  5 leaving,  0 staying, 20 ARRIVING   (total replacement)
//   vector -> hybrid:  5 leaving, 15 staying,  5 arriving
//
// So the question was never exit-vs-enter — bm25 -> hybrid has no
// departures. It is whether the 5 survivors finish moving before the 15
// arrivals land. Both earlier variants failed it: "simultaneous" was
// picked assuming 3 arrivals, and "sequential" still began arrivals at 55%
// of the move, so the demo row's climb from #5 to #1 competed with fifteen
// rows appearing around it.
//
// The mechanism: survivors move ALONE, arrivals are GATED on the move
// completing, and arrivals fill from the BOTTOM UP so the top of the list
// stays quiet while the tail populates — a row landing at position 2 would
// pull the eye straight off the row that just arrived at 1.
//
// Cost, stated rather than buried: the transition goes from ~420 ms to
// ~800 ms. That is slower, and correct here, because the transition IS the
// demo.
//
// When there are no survivors (bm25 -> vector), the gate collapses to zero
// and it becomes a pure staggered entrance. That falls out of the rule
// rather than needing a special case.

export const EASE = [0.2, 0.8, 0.2, 1] as const;

export const DUR = {
  move: 0.42,
  enter: 0.26,
  exit: 0.18,
  route: 0.22,
  badge: 0.3,
} as const;

/** Total time the arrival stagger may span, however many rows arrive. At
 *  15 arrivals a flat 25ms step would run 375ms on its own. */
export const STAGGER_SPAN = 0.25;

/**
 * Delay for one arriving row.
 *
 * @param indexAmongArrivals 0 = topmost arrival
 * @param arrivalCount       how many rows are arriving
 * @param hasSurvivors       whether any row is moving (gates the arrivals)
 */
export function arrivalDelay(
  indexAmongArrivals: number,
  arrivalCount: number,
  hasSurvivors: boolean,
): number {
  const gate = hasSurvivors ? DUR.move : 0;
  if (arrivalCount <= 1) return gate;
  const step = Math.min(0.025, STAGGER_SPAN / (arrivalCount - 1));
  // Bottom-up: the LAST arrival in list order goes first, the topmost waits.
  const fromBottom = arrivalCount - 1 - indexAmongArrivals;
  return gate + fromBottom * step;
}

/** The full set of motion props for a result row, in one place.
 *
 *  Reduced motion has to suppress the ENTRANCE, not just `layout`. Disabling
 *  layout alone still leaves `initial: { opacity: 0, y: 8 }` plus an arrival
 *  delay of up to ~670ms, so a reader who asked for no motion still gets
 *  rows fading up from nothing, staggered. `initial={false}` tells Motion to
 *  mount at the animate state — no entrance at all — and the delay is
 *  dropped with it.
 *
 *  This is a pure function so the reduced-motion branch is testable without
 *  driving a browser. It is the primary path for a reviewer on a phone or
 *  looking at a screenshot, and it is exactly the kind of thing a refactor
 *  breaks in silence. */
export function rowMotionProps(reduce: boolean, delay: number) {
  return reduce
    ? { layout: false as const, initial: false as const, custom: 0 }
    : { layout: "position" as const, initial: "initial", custom: delay };
}

export const rowVariants = {
  initial: { opacity: 0, y: 8 },
  animate: (delay: number) => ({
    opacity: 1,
    y: 0,
    transition: { duration: DUR.enter, ease: EASE, delay },
  }),
  exit: { opacity: 0, x: -12, transition: { duration: DUR.exit, ease: EASE } },
};

/** Route transitions: crossfade plus a small y-offset. */
export const routeVariants = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0, transition: { duration: DUR.route, ease: EASE } },
  exit: { opacity: 0, y: -6, transition: { duration: DUR.route * 0.7, ease: EASE } },
};
