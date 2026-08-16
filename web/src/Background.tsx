// The always-on background.
//
// Runs continuously behind every route — not a hero accent that stops after
// its entrance. Four layers, each doing a different job:
//
//   aurora   three large colour pools, two per arm plus one where they
//            overlap, drifting on coprime cycles (37s / 43s / 29s) so they
//            meet and separate without ever settling into a visible loop
//   grid     a technical lattice sliding slowly, giving the drift something
//            to be measured against — without it the aurora reads as a
//            screensaver rather than as structure
//   grain    static SVG noise, which is what stops gradients this large from
//            banding on 8-bit panels
//
// Every animation is transform/opacity only, so the compositor owns it and
// the main thread stays free for search. `fixed` so it never grows the
// scroll area; `pointer-events-none` so it never eats a click.
import { useReducedMotion } from "motion/react";

export function Background() {
  const reduce = useReducedMotion();
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className={`bg-aurora bg-aurora-a ${reduce ? "" : "animate-drift-a"}`} />
      <div className={`bg-aurora bg-aurora-b ${reduce ? "" : "animate-drift-b"}`} />
      <div className={`bg-aurora bg-aurora-c ${reduce ? "" : "animate-drift-c"}`} />
      <div className={`bg-grid-drift ${reduce ? "" : "animate-grid"}`} />
      <div className="bg-grain" />
    </div>
  );
}
