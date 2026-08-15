// A count that animates its CHANGE, not its mount (Kishan, 2026-08-13).
// A count-up on load is decoration — nothing happened, the page just
// appeared. A tick when a decision lands is feedback: the reviewer did
// something and the number noticed.
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { DUR, EASE } from "./motion";

export function CountBadge({ value, label }: { value: number; label: string }) {
  const prev = useRef(value);
  const [bumped, setBumped] = useState(false);
  const reduce = useReducedMotion();

  useEffect(() => {
    if (prev.current !== value) {
      prev.current = value;
      setBumped(true);
      const t = setTimeout(() => setBumped(false), DUR.badge * 1000);
      return () => clearTimeout(t);
    }
  }, [value]);

  return (
    <span className="inline-flex items-baseline gap-1 text-sm text-ink-300">
      <motion.span
        // key on the value so a change mounts a new node: that is what makes
        // the tick a transition rather than a re-render.
        key={value}
        initial={reduce || !bumped ? false : { y: -6, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: DUR.badge, ease: EASE }}
        className="font-semibold tabular-nums text-ink-50"
      >
        {value}
      </motion.span>
      {label}
    </span>
  );
}
