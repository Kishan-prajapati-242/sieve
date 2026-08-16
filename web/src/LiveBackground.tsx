// The live background, canvas-2D.
//
// This replaces Vanta/three.js after four rounds shipped without visual
// confirmation. The canvas was provably at 0,0,viewport with position:fixed
// and no ancestor breaking it — Vanta was drawing its mesh into a thin band
// at the top of its own canvas, which is internal to the library and not
// something a stylesheet can reach. Tuning around that a fifth time was not a
// plan.
//
// Same effect, adapted from the standard "constellation" demo (particles.js,
// Vanta NET): drifting points joined by lines when they come close. What
// changes is that every pixel here is placed by code in this file, so the
// output can be screenshotted, measured, and proven before anyone looks at it.
//
// Design decisions rather than defaults:
//   * two populations, amber and violet, because the product fuses two arms —
//     links BETWEEN populations are drawn as a gradient, so the fusion is the
//     thing the background actually depicts
//   * density scales with viewport area, so a laptop and a phone get the same
//     visual weight rather than the same point count
//   * the pointer pushes points apart, which is what makes it feel live
//     rather than looped
//   * DPR-aware, so it is not soft on a retina display
import { useEffect, useRef } from "react";
import { useReducedMotion } from "motion/react";
import { useTheme } from "./theme";

interface Point {
  x: number;
  y: number;
  vx: number;
  vy: number;
  arm: 0 | 1; // 0 = keyword/amber, 1 = semantic/violet
}

const LINK_DIST = 150;
const POINTS_PER_MPX = 95; // points per million device-independent pixels

function readRgb(varName: string, fallback: [number, number, number]): [number, number, number] {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  if (!raw) return fallback;
  const probe = document.createElement("div");
  probe.style.color = raw;
  document.body.appendChild(probe);
  const m = getComputedStyle(probe).color.match(/\d+/g);
  probe.remove();
  return m ? [Number(m[0]), Number(m[1]), Number(m[2])] : fallback;
}

export function LiveBackground() {
  const ref = useRef<HTMLCanvasElement>(null);
  const reduce = useReducedMotion();
  const { resolved } = useTheme();

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const amber = readRgb("--color-keyword-400", [247, 185, 85]);
    const violet = readRgb("--color-semantic-400", [167, 139, 250]);
    // Light mode needs far less ink: the same alpha that reads as a whisper on
    // near-black reads as dirt on off-white.
    const lineAlpha = resolved === "light" ? 0.2 : 0.36;
    const dotAlpha = resolved === "light" ? 0.4 : 0.62;

    let w = 0;
    let h = 0;
    let dpr = 1;
    let points: Point[] = [];
    const pointer = { x: -9999, y: -9999 };

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas!.width = Math.floor(w * dpr);
      canvas!.height = Math.floor(h * dpr);
      canvas!.style.width = `${w}px`;
      canvas!.style.height = `${h}px`;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);

      const target = Math.round((w * h) / 1_000_000 * POINTS_PER_MPX);
      points = Array.from({ length: Math.max(28, target) }, (_, i) => ({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.22,
        vy: (Math.random() - 0.5) * 0.22,
        arm: (i % 2) as 0 | 1,
      }));
    }

    function frame() {
      ctx!.clearRect(0, 0, w, h);

      for (const p of points) {
        p.x += p.vx;
        p.y += p.vy;
        // Wrap rather than bounce: a bounce makes the edges of the viewport
        // visible as walls, which draws attention to the frame.
        if (p.x < -20) p.x = w + 20;
        if (p.x > w + 20) p.x = -20;
        if (p.y < -20) p.y = h + 20;
        if (p.y > h + 20) p.y = -20;

        // The pointer pushes points away, and they drift back on their own.
        const dx = p.x - pointer.x;
        const dy = p.y - pointer.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 26000 && d2 > 0.01) {
          const f = (1 - d2 / 26000) * 0.9;
          const d = Math.sqrt(d2);
          p.x += (dx / d) * f;
          p.y += (dy / d) * f;
        }
      }

      // Links first so dots sit on top of them.
      for (let i = 0; i < points.length; i++) {
        for (let j = i + 1; j < points.length; j++) {
          const a = points[i];
          const b = points[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const dist = Math.hypot(dx, dy);
          if (dist > LINK_DIST) continue;
          const alpha = (1 - dist / LINK_DIST) * lineAlpha;

          if (a.arm !== b.arm) {
            // A link between the two arms is drawn as the fusion gradient —
            // this is the one place the background states what the product
            // does rather than just being pleasant.
            const g = ctx!.createLinearGradient(a.x, a.y, b.x, b.y);
            const [c0, c1] = a.arm === 0 ? [amber, violet] : [violet, amber];
            g.addColorStop(0, `rgba(${c0[0]},${c0[1]},${c0[2]},${alpha})`);
            g.addColorStop(1, `rgba(${c1[0]},${c1[1]},${c1[2]},${alpha})`);
            ctx!.strokeStyle = g;
          } else {
            const c = a.arm === 0 ? amber : violet;
            ctx!.strokeStyle = `rgba(${c[0]},${c[1]},${c[2]},${alpha * 0.7})`;
          }
          ctx!.lineWidth = 1;
          ctx!.beginPath();
          ctx!.moveTo(a.x, a.y);
          ctx!.lineTo(b.x, b.y);
          ctx!.stroke();
        }
      }

      for (const p of points) {
        const c = p.arm === 0 ? amber : violet;
        ctx!.fillStyle = `rgba(${c[0]},${c[1]},${c[2]},${dotAlpha})`;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, 1.6, 0, Math.PI * 2);
        ctx!.fill();
      }

      raf = requestAnimationFrame(frame);
    }

    function onPointer(e: PointerEvent) {
      pointer.x = e.clientX;
      pointer.y = e.clientY;
    }
    function onLeave() {
      pointer.x = -9999;
      pointer.y = -9999;
    }

    resize();
    let raf = 0;
    if (reduce) {
      // One static frame: the structure still reads, nothing moves.
      frame();
      cancelAnimationFrame(raf);
    } else {
      raf = requestAnimationFrame(frame);
      window.addEventListener("pointermove", onPointer, { passive: true });
      window.addEventListener("pointerleave", onLeave);
    }
    window.addEventListener("resize", resize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("pointerleave", onLeave);
    };
  }, [reduce, resolved]);

  return (
    <canvas
      ref={ref}
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 -z-10 h-screen w-screen"
    />
  );
}
