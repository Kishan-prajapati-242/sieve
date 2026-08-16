// The 3D object.
//
// Resend's hero objects work because they are the product rendered as a
// solid — an envelope, a stack — not an abstract shape. So this is the thing
// Sieve actually produces: a ranked result list, given depth, with the two
// arms folding into the fused stack in the middle.
//
// CSS 3D rather than three.js or a canvas. `transform-style: preserve-3d`
// under a real `perspective` ancestor gives genuine depth, it is a few
// kilobytes instead of a few hundred, it inherits the theme's colour
// variables for free, and it degrades to a static diagram under reduced
// motion. A WebGL scene would do none of those and would cost a cold start
// on a free tier.
//
// The stack tilts toward the pointer with a spring, which is what makes it
// read as an object in space rather than a picture of one.
import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from "motion/react";
import type { PointerEvent } from "react";

const LAYERS = 7;

// Which arm each row of the fused stack came from — the same shape the real
// fused list has: two-armed at the top, single-armed in the tail.
const SOURCE: Array<"both" | "keyword" | "semantic"> = [
  "both",
  "both",
  "both",
  "semantic",
  "keyword",
  "semantic",
  "keyword",
];

export function Stack3D({ className = "" }: { className?: string }) {
  const reduce = useReducedMotion();
  // Raw pointer position, then a spring, so the object has mass. Tracking
  // the pointer directly reads as jitter; a spring reads as weight.
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const rotY = useSpring(useTransform(mx, [-0.5, 0.5], [-16, 16]), {
    stiffness: 110,
    damping: 18,
  });
  const rotX = useSpring(useTransform(my, [-0.5, 0.5], [14, -4]), {
    stiffness: 110,
    damping: 18,
  });

  function onMove(e: PointerEvent<HTMLDivElement>) {
    if (reduce) return;
    const r = e.currentTarget.getBoundingClientRect();
    mx.set((e.clientX - r.left) / r.width - 0.5);
    my.set((e.clientY - r.top) / r.height - 0.5);
  }
  function onLeave() {
    mx.set(0);
    my.set(0);
  }

  return (
    <div
      className={`scene flex items-center justify-center ${className}`}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
    >
      <motion.div
        className={`card3d relative ${reduce ? "" : "animate-float3d"}`}
        style={
          reduce
            ? { transform: "rotateX(12deg) rotateY(-12deg)" }
            : { rotateX: rotX, rotateY: rotY }
        }
      >
        {Array.from({ length: LAYERS }).map((_, i) => {
          const src = SOURCE[i];
          const z = (LAYERS - 1 - i) * 16;
          return (
            // The layout transform lives on a plain wrapper, NOT on the
            // motion element. Animating `z` makes Motion take ownership of
            // `transform`, which silently discarded the translate3d and
            // stacked all seven layers at the same point — one card where a
            // stack should be. Motion animates opacity only; the geometry is
            // CSS the library never touches.
            <div
              key={i}
              className="absolute left-1/2 top-1/2"
              style={{
                transform: `translate3d(-50%, -50%, ${z}px) translateY(${(i - LAYERS / 2) * 30}px)`,
              }}
            >
            <motion.div
              className="h-[46px] w-[280px] rounded-lg border backdrop-blur-sm"
              style={{
                borderColor:
                  src === "both"
                    ? "color-mix(in oklab, var(--color-semantic-400) 45%, transparent)"
                    : "color-mix(in oklab, var(--color-ink-100) 14%, transparent)",
                background:
                  src === "both"
                    ? "linear-gradient(100deg, color-mix(in oklab, var(--color-keyword-400) 22%, transparent), color-mix(in oklab, var(--color-semantic-500) 24%, transparent))"
                    : src === "keyword"
                      ? "color-mix(in oklab, var(--color-keyword-400) 13%, transparent)"
                      : "color-mix(in oklab, var(--color-semantic-500) 15%, transparent)",
                boxShadow: "0 18px 40px -22px rgba(0,0,0,0.75)",
              }}
              initial={reduce ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.7, delay: 0.1 + i * 0.09, ease: [0.2, 0.8, 0.2, 1] }}
            >
              {/* Rank, a title bar, and the arm chips — the anatomy of a real
                  result row, at a size where it reads as texture. */}
              <div className="flex h-full items-center gap-3 px-4">
                <span className="font-mono text-[10px] text-ink-400">{i + 1}</span>
                <div className="flex-1">
                  <div
                    className="h-[6px] rounded-full bg-ink-100/45"
                    style={{ width: `${88 - i * 7}%` }}
                  />
                  <div className="mt-2 flex gap-1.5">
                    {(src === "both" || src === "keyword") && (
                      <span className="h-[5px] w-8 rounded-full bg-keyword-400/80" />
                    )}
                    {(src === "both" || src === "semantic") && (
                      <span className="h-[5px] w-8 rounded-full bg-semantic-400/80" />
                    )}
                  </div>
                </div>
              </div>
            </motion.div>
            </div>
          );
        })}
      </motion.div>
    </div>
  );
}
