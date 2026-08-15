// The hero visual: what the product does, drawn.
//
// Two ranked columns — amber keyword, violet semantic — converging into one
// fused column. Chose a diagram over an abstract graphic because the thing
// being sold IS this operation, and a reader who understands the picture has
// understood the product. The reference sites all show real product surface
// rather than illustration for the same reason.
//
// Pure SVG + CSS, no canvas and no image: it has to cost nothing on a free
// tier, stay sharp at any density, and be legible with motion off.
import { motion, useReducedMotion } from "motion/react";

const ROWS = 7;
const ROW_H = 26;
const BAR_H = 14;
const COL_W = 108;
const GAP = 74;
const H = ROWS * ROW_H;
const W = COL_W * 3 + GAP * 2;

// Which source column each fused row draws from. Not decorative — this is
// the real shape of a fused list: the top rows tend to carry BOTH ranks,
// the tail is single-armed. Row 3 is the one neither arm ranked first and
// fusion lifts, which is the demo query's actual behaviour.
const FUSED: Array<"both" | "keyword" | "semantic"> = [
  "both",
  "both",
  "both",
  "semantic",
  "keyword",
  "semantic",
  "keyword",
];

function Column({
  x,
  color,
  widths,
  label,
}: {
  x: number;
  color: string;
  widths: number[];
  label: string;
}) {
  return (
    <g>
      <text
        x={x}
        y={-14}
        className="fill-ink-500 font-mono"
        style={{ fontSize: 9, letterSpacing: "0.14em" }}
      >
        {label}
      </text>
      {widths.map((w, i) => (
        <rect
          key={i}
          x={x}
          y={i * ROW_H}
          width={COL_W * w}
          height={BAR_H}
          rx={3}
          fill={color}
          opacity={0.28 + (1 - i / ROWS) * 0.5}
        />
      ))}
    </g>
  );
}

export function FusionDiagram({ className = "" }: { className?: string }) {
  const reduce = useReducedMotion();
  const kw = [1, 0.86, 0.74, 0.66, 0.58, 0.52, 0.45];
  const sm = [0.95, 0.88, 0.8, 0.7, 0.62, 0.55, 0.48];
  const fx = COL_W + GAP;
  const sx = (COL_W + GAP) * 2;

  return (
    <svg
      viewBox={`0 -28 ${W} ${H + 40}`}
      className={className}
      role="img"
      aria-label="Two ranked lists, keyword and semantic, fusing into one ranked list"
    >
      <defs>
        <linearGradient id="fusionBar" x1="0" x2="1">
          <stop offset="0%" stopColor="var(--color-fusion-from)" />
          <stop offset="100%" stopColor="var(--color-fusion-to)" />
        </linearGradient>
      </defs>

      {/* Connectors first so bars sit on top of them. */}
      <g fill="none" strokeWidth={1}>
        {FUSED.map((src, i) => {
          const targets =
            src === "both" ? ["keyword", "semantic"] : [src as "keyword" | "semantic"];
          return targets.map((t) => {
            const fromX = t === "keyword" ? COL_W : sx;
            const fromRow = t === "keyword" ? i : i;
            const y0 = fromRow * ROW_H + BAR_H / 2;
            const y1 = i * ROW_H + BAR_H / 2;
            const toX = t === "keyword" ? fx : fx + COL_W;
            const mid = (fromX + toX) / 2;
            return (
              <motion.path
                key={`${i}-${t}`}
                d={`M${fromX},${y0} C${mid},${y0} ${mid},${y1} ${toX},${y1}`}
                stroke={
                  t === "keyword" ? "var(--color-keyword-400)" : "var(--color-semantic-400)"
                }
                strokeOpacity={0.3}
                initial={reduce ? false : { pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: 1 }}
                transition={{ duration: 0.9, delay: 0.25 + i * 0.06, ease: [0.2, 0.8, 0.2, 1] }}
              />
            );
          });
        })}
      </g>

      <Column x={0} color="var(--color-keyword-400)" widths={kw} label="KEYWORD" />
      <Column x={sx} color="var(--color-semantic-400)" widths={sm} label="SEMANTIC" />

      <g>
        <text
          x={fx}
          y={-14}
          className="fill-ink-300 font-mono"
          style={{ fontSize: 9, letterSpacing: "0.14em" }}
        >
          FUSED
        </text>
        {FUSED.map((src, i) => (
          <motion.rect
            key={i}
            x={fx}
            y={i * ROW_H}
            width={COL_W * (1 - i * 0.07)}
            height={BAR_H}
            rx={3}
            fill={
              src === "both"
                ? "url(#fusionBar)"
                : src === "keyword"
                  ? "var(--color-keyword-400)"
                  : "var(--color-semantic-400)"
            }
            opacity={src === "both" ? 1 : 0.55}
            initial={reduce ? false : { scaleX: 0, opacity: 0 }}
            animate={{ scaleX: 1, opacity: src === "both" ? 1 : 0.55 }}
            style={{ originX: 0 }}
            transition={{ duration: 0.5, delay: 0.5 + i * 0.07, ease: [0.2, 0.8, 0.2, 1] }}
          />
        ))}
      </g>
    </svg>
  );
}
