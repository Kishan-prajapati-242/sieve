// Shared primitives. Everything visual in the app composes from these, so a
// change to the system happens in one place rather than in forty className
// strings.
//
// Deliberately small: Container, Section, Eyebrow, Button, Card, Stat, Reveal.
// The app does not need a component library, it needs the six shapes it
// actually repeats. Adding a seventh should require noticing it three times
// first.
import { motion, useReducedMotion } from "motion/react";
import type { ComponentProps, ReactNode } from "react";
import { Link } from "react-router-dom";

export function Container({ className = "", children }: { className?: string; children: ReactNode }) {
  return <div className={`mx-auto w-full max-w-6xl px-6 ${className}`}>{children}</div>;
}

/** A page section with the hairline rule that separates every band.
 *  Structure on near-black comes from 1px lines, not from shadows. */
export function Section({
  className = "",
  children,
  bordered = true,
}: {
  className?: string;
  children: ReactNode;
  bordered?: boolean;
}) {
  return (
    <section className={`${bordered ? "hairline border-t" : ""} py-20 sm:py-28 ${className}`}>
      {children}
    </section>
  );
}

/** The monospace ALL-CAPS pill that labels each band. Borrowed structure
 *  from the reference set, but it earns its place here for a specific
 *  reason: it marks the boundary between prose and measured values, which
 *  is the same boundary the mono/sans split enforces everywhere else. */
export function Eyebrow({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={`hairline inline-flex items-center gap-2 rounded-full border px-3 py-1.5
                  font-mono text-eyebrow uppercase text-ink-300 ${className}`}
    >
      <span className="size-1.5 rounded-full bg-fusion" aria-hidden="true" />
      {children}
    </span>
  );
}

type ButtonVariant = "primary" | "secondary" | "ghost";

const BUTTON_BASE =
  "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium " +
  "transition-all duration-150 ease-[var(--ease-out-soft)] disabled:opacity-50 " +
  "disabled:pointer-events-none active:scale-[0.98]";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  // White-on-black primary, like every reference: on a near-black ground the
  // highest-contrast element should be the action, and any brand color used
  // at button size would compete with the arm chips.
  primary: "bg-ink-50 text-ink-950 hover:bg-white shadow-[0_0_0_1px_rgba(255,255,255,0.1)]",
  secondary: "hairline border bg-ink-850/60 text-ink-100 hover:bg-ink-800 hover:hairline-strong",
  ghost: "text-ink-300 hover:text-ink-50 hover:bg-ink-850",
};

const BUTTON_SIZES = { sm: "h-8 px-3", md: "h-10 px-4", lg: "h-12 px-6 text-[15px]" };

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: ComponentProps<"button"> & { variant?: ButtonVariant; size?: keyof typeof BUTTON_SIZES }) {
  return (
    <button
      className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`}
      {...props}
    />
  );
}

export function ButtonLink({
  variant = "primary",
  size = "md",
  className = "",
  to,
  children,
}: {
  variant?: ButtonVariant;
  size?: keyof typeof BUTTON_SIZES;
  className?: string;
  to: string;
  children: ReactNode;
}) {
  return (
    <Link
      to={to}
      className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`}
    >
      {children}
    </Link>
  );
}

export function Card({
  className = "",
  children,
  interactive = false,
}: {
  className?: string;
  children: ReactNode;
  interactive?: boolean;
}) {
  return (
    <div
      className={`hairline rounded-card border bg-ink-880 ${
        interactive
          ? "transition-colors duration-200 ease-[var(--ease-out-soft)] hover:bg-ink-850 hover:hairline-strong"
          : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}

/** A measured number, presented as one. The label carries its provenance
 *  because this project does not print a figure without saying where it came
 *  from — that rule applies on the landing page too, not only in bench/. */
export function Stat({
  value,
  label,
  note,
}: {
  value: ReactNode;
  label: string;
  note?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="font-mono text-h2 text-ink-50">{value}</span>
      <span className="text-sm text-ink-300">{label}</span>
      {note && <span className="font-mono text-[11px] uppercase tracking-wider text-ink-500">{note}</span>}
    </div>
  );
}

/** Scroll-triggered entrance. The density the reference sites have comes
 *  mostly from this one effect applied consistently, not from many effects.
 *  `once` so nothing re-animates on scroll-up, which reads as instability. */
export function Reveal({
  children,
  delay = 0,
  y = 14,
  className = "",
}: {
  children: ReactNode;
  delay?: number;
  y?: number;
  className?: string;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.5, delay, ease: [0.2, 0.8, 0.2, 1] }}
    >
      {children}
    </motion.div>
  );
}
