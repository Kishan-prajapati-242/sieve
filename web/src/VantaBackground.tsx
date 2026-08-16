// The live background: Vanta NET over three.js.
//
// Kishan's reference is vantajs.com and his standing instruction is to adapt
// demos rather than invent, so this is Vanta itself rather than a hand-rolled
// shader. NET is the effect that earns its place here: a field of points
// joined by lines is what a vector index IS, so the background is a picture
// of the mechanism rather than decoration borrowed from a gallery.
//
// Three things this has to get right, none of which the Vanta demo handles:
//
//   cost      three.js is ~600 KB. It is imported DYNAMICALLY after first
//             paint, so the hero renders on the CSS gradient and the WebGL
//             layer arrives a beat later. A landing page that blocks on
//             600 KB to look alive has traded the thing it was buying.
//   theme     colours come from the live CSS variables and the instance is
//             rebuilt on theme change, otherwise dark-mode points sit
//             invisibly on a white page.
//   failure   WebGL is unavailable on some machines and in most headless
//             browsers. The CSS aurora stays mounted underneath, so failure
//             degrades to the previous background instead of a black hole.
import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "motion/react";
import { useTheme } from "./theme";

function cssColor(name: string, fallback: string): number {
  if (typeof window === "undefined") return parseInt(fallback, 16);
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!raw) return parseInt(fallback, 16);
  // Resolve through the browser so oklab()/color-mix() land as rgb.
  const probe = document.createElement("div");
  probe.style.color = raw;
  document.body.appendChild(probe);
  const rgb = getComputedStyle(probe).color.match(/\d+/g);
  probe.remove();
  if (!rgb) return parseInt(fallback, 16);
  return (Number(rgb[0]) << 16) | (Number(rgb[1]) << 8) | Number(rgb[2]);
}

export function VantaBackground() {
  const holder = useRef<HTMLDivElement>(null);
  const effect = useRef<{ destroy: () => void } | null>(null);
  const reduce = useReducedMotion();
  const { resolved } = useTheme();
  const [live, setLive] = useState(false);

  useEffect(() => {
    if (reduce || !holder.current) return;
    let cancelled = false;

    (async () => {
      try {
        const THREE = await import("three");
        const mod = await import("vanta/dist/vanta.net.min.js");
        if (cancelled || !holder.current) return;
        const NET = (mod as { default: (o: Record<string, unknown>) => { destroy: () => void } })
          .default;

        effect.current?.destroy();
        effect.current = NET({
          el: holder.current,
          THREE,
          // Points are the semantic arm's colour, lines the keyword arm's:
          // the two things the product fuses, drawn as the thing that joins
          // them.
          // Light mode needs the deeper arm colour: semantic-400 on a white
          // page measured 2.7:1 in the contrast audit and a NET drawn in it
          // is invisible for the same reason.
          color: cssColor(
            resolved === "light" ? "--color-semantic-ink" : "--color-semantic-400",
            "a78bfa",
          ),
          backgroundColor: cssColor("--color-ink-950", "08080a"),
          // Transparent so the CSS aurora shows THROUGH the net rather than
          // being painted over — the two layers compose.
          backgroundAlpha: 0,
          // Tuned for PRESENCE, not taste. Vanta's defaults (10 points,
          // spacing 20) are a texture you notice on inspection; the whole
          // complaint is that the background could not be found. More points,
          // tighter spacing and longer links make it a structure you see
          // immediately.
          points: resolved === "light" ? 16 : 20,
          maxDistance: 30,
          spacing: 13,
          showDots: true,
          mouseControls: true,
          touchControls: true,
          gyroControls: false,
          scale: 1,
          scaleMobile: 1,
        });
        setLive(true);
      } catch {
        // WebGL refused, or the chunk failed. The CSS background underneath
        // is already running; nothing to do but leave it.
        setLive(false);
      }
    })();

    return () => {
      cancelled = true;
      effect.current?.destroy();
      effect.current = null;
    };
    // Rebuilt on theme change: Vanta bakes colours at construction, so a
    // theme swap without this leaves dark points on a white page.
  }, [reduce, resolved]);

  return (
    <div
      ref={holder}
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 -z-10"
      style={{ opacity: live ? 1 : 0, transition: "opacity 1.2s var(--ease-out-soft)" }}
    />
  );
}
