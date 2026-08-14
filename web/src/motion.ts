// The motion vocabulary, in one place so the timings stay consistent and a
// reviewer can read the whole choreography without opening five components.
//
// Decisions this encodes (Kishan, 2026-08-13):
//
//   Reorder is SIMULTANEOUS. The mode toggle's claim is that hybrid FUSES two
//   rankings — one operation, not a sequence — so staging exits, then moves,
//   then arrivals narrates list mechanics instead. Measured support: the
//   sequential variant produced a second delta peak mid-transition, i.e. two
//   events for the eye to track (.design-review/choreography-proof.html).
//
//   EXCEPT arriving into an EMPTY list, which is staggered per index. Eight
//   rows landing at once after "No papers matched." reads as a dump rather
//   than a reveal, and that case is on the demo path: the jargon query returns
//   zero under bm25 and eight under vector.
//
//   Hover is CSS-only. Spotlight effects need a mousemove listener per card,
//   and both the results list and the screening queue run to hundreds of rows
//   — the same restraint-inside-long-lists argument that shaped everything
//   else here.

export const EASE = [0.2, 0.8, 0.2, 1] as const;

export const DUR = {
  move: 0.42,
  enter: 0.26,
  exit: 0.18,
  route: 0.22,
  badge: 0.3,
} as const;

/** Per-row variants. `stagger` is the index delay, applied only when the
 *  previous list was empty; every other transition passes 0. */
export const rowVariants = {
  initial: { opacity: 0, y: 8 },
  animate: (stagger: number) => ({
    opacity: 1,
    y: 0,
    transition: { duration: DUR.enter, ease: EASE, delay: stagger },
  }),
  exit: { opacity: 0, x: -12, transition: { duration: DUR.exit, ease: EASE } },
};

/** Cap the stagger so a long arrival does not turn into a slow cascade. */
export const STAGGER_STEP = 0.035;
export const STAGGER_MAX = 10;

export function staggerFor(index: number, fromEmpty: boolean): number {
  if (!fromEmpty) return 0;
  return Math.min(index, STAGGER_MAX) * STAGGER_STEP;
}

/** Route transitions: crossfade plus a small y-offset. Card -> detail expand
 *  is deferred on cost (docs/plans/ui-assembly-plan.md). */
export const routeVariants = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0, transition: { duration: DUR.route, ease: EASE } },
  exit: { opacity: 0, y: -6, transition: { duration: DUR.route * 0.7, ease: EASE } },
};
