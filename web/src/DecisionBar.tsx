// include / exclude / maybe. The selected pill is a shared-layout element,
// so switching decisions slides one highlight rather than swapping three
// backgrounds — the morph that makes it read as one control changing state.
import { motion } from "motion/react";
import type { Decision } from "./api";
import { DUR, EASE } from "./motion";

const OPTIONS: { value: Decision; label: string; tone: string }[] = [
  { value: "include", label: "Include", tone: "bg-signal-400/100" },
  { value: "maybe", label: "Maybe", tone: "bg-amber-500" },
  { value: "exclude", label: "Exclude", tone: "bg-danger-400/100" },
];

export function DecisionBar({
  value,
  onChange,
  groupId,
  busy,
}: {
  value: Decision | null;
  onChange: (d: Decision) => void;
  groupId: string;
  busy?: boolean;
}) {
  return (
    <div className="inline-flex rounded-lg bg-ink-800 p-1" role="group" aria-label="Decision">
      {OPTIONS.map((o) => {
        const active = value === o.value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={active}
            disabled={busy}
            onClick={() => onChange(o.value)}
            className="relative rounded-md px-3 py-1.5 text-sm font-medium transition-colors
                       disabled:opacity-60"
          >
            {active && (
              <motion.span
                // layoutId is what makes the highlight travel between buttons
                // instead of cross-fading in place.
                layoutId={`decision-${groupId}`}
                className={`absolute inset-0 rounded-md ${o.tone}`}
                transition={{ duration: DUR.move * 0.6, ease: EASE }}
              />
            )}
            <span className={`relative ${active ? "text-ink-950" : "text-ink-300"}`}>
              {o.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}
