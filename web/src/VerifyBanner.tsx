// The "confirm your email" strip.
//
// Shown to signed-in but unverified users, above whatever they were doing,
// rather than as a blocking wall. The account works; the address is simply
// not yet proven, and blocking a reviewer who wanted to look around would
// cost more than it protects.
//
// The code input is six single-character boxes rather than one text field
// because that is the shape of the thing being entered, and pasting a code
// from an email should fill all six — handled explicitly, since paste into a
// per-character input otherwise drops five of them.
import { AnimatePresence, motion } from "motion/react";
import { useRef, useState } from "react";
import { useAuth } from "./auth";

const LEN = 6;

export function VerifyBanner() {
  const { user, verify, resend, config } = useAuth();
  const [digits, setDigits] = useState<string[]>(Array(LEN).fill(""));
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const refs = useRef<Array<HTMLInputElement | null>>([]);

  if (!user || user.email_verified) return null;

  async function submit(code: string) {
    setBusy(true);
    setError(null);
    try {
      await verify(code);
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

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      transition={{ duration: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
      className="hairline overflow-hidden border-b bg-keyword-950/40"
    >
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-6 py-3">
        <div className="flex items-center gap-2.5">
          <span className="size-1.5 animate-pulse rounded-full bg-keyword-400" aria-hidden="true" />
          <p className="text-sm text-ink-200">
            Confirm <span className="font-mono text-ink-50">{user.email}</span>
          </p>
        </div>

        <div className="flex items-center gap-1.5">
          {digits.map((d, i) => (
            <input
              key={i}
              ref={(el) => {
                refs.current[i] = el;
              }}
              value={d}
              inputMode="numeric"
              maxLength={1}
              aria-label={`Digit ${i + 1}`}
              disabled={busy}
              onChange={(e) => setAt(i, e.target.value.replace(/\D/g, ""))}
              onKeyDown={(e) => {
                // Backspace on an empty box steps back, which is what every
                // code input does and what fingers expect.
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
              className="hairline size-9 rounded-md border bg-ink-900 text-center font-mono
                         text-sm text-ink-50 transition-colors focus:border-keyword-400
                         focus:outline-none disabled:opacity-50"
            />
          ))}
        </div>

        <div className="flex items-center gap-4">
          <button
            type="button"
            onClick={async () => {
              await resend();
              setSent(true);
              setTimeout(() => setSent(false), 4000);
            }}
            className="text-xs text-ink-400 underline underline-offset-4 transition-colors hover:text-ink-100"
          >
            Resend code
          </button>
          {config?.email_transport === "console" && (
            // Honest about the demo deployment: no mail service is wired, so
            // say where the code actually goes instead of implying an inbox.
            <span className="font-mono text-[10px] uppercase tracking-wider text-ink-600">
              demo · code is in the server log
            </span>
          )}
        </div>

        <AnimatePresence>
          {error && (
            <motion.span
              role="alert"
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              className="text-xs text-danger-400"
            >
              {error}
            </motion.span>
          )}
          {sent && (
            <motion.span
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="text-xs text-signal-400"
            >
              New code sent.
            </motion.span>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
