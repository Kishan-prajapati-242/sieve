// The inline verification step.
//
// Lives on the auth page rather than as a route or a banner: the moment
// after signing up IS the moment to verify, and sending someone to
// /collections first made it read as an unrelated interruption.
//
// Reports delivery honestly. Resend refuses recipients outside the account
// owner's address until a sending domain is verified, so "check your inbox"
// would be false for almost everyone right now — the component says which
// address can actually be reached and offers a way past.
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { API_BASE } from "./api";
import { useAuth } from "./auth";
import { Button } from "./ui";

const LEN = 6;

export function CodeInput({
  email,
  onVerified,
}: {
  email: string;
  onVerified: () => void;
}) {
  const { verify, config } = useAuth();
  const [digits, setDigits] = useState<string[]>(Array(LEN).fill(""));
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const refs = useRef<Array<HTMLInputElement | null>>([]);

  useEffect(() => {
    refs.current[0]?.focus();
  }, []);

  async function submit(code: string) {
    setBusy(true);
    setError(null);
    try {
      await verify(code);
      onVerified();
    } catch (e) {
      setError((e as Error).message);
      setDigits(Array(LEN).fill(""));
      refs.current[0]?.focus();
    } finally {
      setBusy(false);
    }
  }

  function setAt(i: number, v: string) {
    const next = [...digits];
    next[i] = v.slice(-1);
    setDigits(next);
    if (v && i < LEN - 1) refs.current[i + 1]?.focus();
    const joined = next.join("");
    if (joined.length === LEN && !joined.includes("")) void submit(joined);
  }

  async function resend() {
    setResending(true);
    setError(null);
    try {
      const r = await fetch(`${API_BASE}/api/auth/verify/resend`, {
        method: "POST",
        credentials: "include",
      });
      const body = await r.json().catch(() => ({}));
      // The server reports what actually happened to the message, so a
      // provider rejection surfaces here instead of looking like success.
      setStatus(
        body.status === "sent"
          ? "Code sent."
          : String(body.status ?? "").startsWith("failed")
            ? "The mail provider rejected that address."
            : "Code issued.",
      );
    } finally {
      setResending(false);
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.4, ease: [0.2, 0.8, 0.2, 1] }}
    >
      <h1 className="text-h2 font-semibold text-ink-50">Check your email</h1>
      <p className="mt-2 text-sm text-ink-400">
        Six-digit code sent to <span className="font-mono text-ink-100">{email}</span>
      </p>

      <div className="mt-8 flex gap-2">
        {digits.map((d, i) => (
          <motion.input
            key={i}
            ref={(el) => {
              refs.current[i] = el;
            }}
            value={d}
            inputMode="numeric"
            maxLength={1}
            disabled={busy}
            aria-label={`Digit ${i + 1}`}
            whileFocus={{ scale: 1.06 }}
            onChange={(e) => setAt(i, e.target.value.replace(/\D/g, ""))}
            onKeyDown={(e) => {
              if (e.key === "Backspace" && !digits[i] && i > 0) refs.current[i - 1]?.focus();
            }}
            onPaste={(e) => {
              const text = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, LEN);
              if (!text) return;
              e.preventDefault();
              const next = Array(LEN)
                .fill("")
                .map((_, j) => text[j] ?? "");
              setDigits(next);
              if (text.length === LEN) void submit(text);
              else refs.current[text.length]?.focus();
            }}
            className="hairline h-14 w-full rounded-xl border bg-ink-880 text-center font-mono
                       text-xl text-ink-50 transition-colors focus:border-semantic-400
                       focus:outline-none disabled:opacity-50"
          />
        ))}
      </div>

      <AnimatePresence>
        {error && (
          <motion.p
            role="alert"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mt-4 rounded-lg border border-danger-400/30 bg-danger-400/10 px-3 py-2 text-sm text-danger-400"
          >
            {error}
          </motion.p>
        )}
        {status && (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="mt-4 text-sm text-signal-400"
          >
            {status}
          </motion.p>
        )}
      </AnimatePresence>

      <div className="mt-6 flex flex-wrap items-center gap-4">
        <Button variant="secondary" size="sm" onClick={() => void resend()} disabled={resending}>
          {resending ? (
            <>
              <span className="spinner" /> Sending…
            </>
          ) : (
            "Resend code"
          )}
        </Button>
        <Link
          to="/login"
          className="text-sm text-ink-400 underline underline-offset-4 hover:text-ink-100"
        >
          Use a different email
        </Link>
      </div>

      {config?.email_transport === "console" ? (
        <p className="hairline mt-6 border-t pt-4 font-mono text-[11px] uppercase tracking-wider text-ink-500">
          Demo · no mail provider configured · code is in the server log
        </p>
      ) : (
        <p className="hairline mt-6 border-t pt-4 text-xs leading-relaxed text-ink-500">
          Mail is sent through Resend. Until a sending domain is verified, the provider only
          delivers to the account owner&apos;s own address — other addresses are rejected
          outright, and the button above will say so rather than pretending it worked.
        </p>
      )}
    </motion.div>
  );
}
