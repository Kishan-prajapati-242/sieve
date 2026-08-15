// Sign in and sign up. One component, two modes — the forms differ only in
// which endpoint they call and what the copy says, and two near-identical
// files would drift.
//
// The error is rendered verbatim from the server rather than reinterpreted,
// because the server deliberately returns the SAME message for "no such
// account" and "wrong password"; a client that tried to be helpful here would
// undo the anti-enumeration work.
import { motion } from "motion/react";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import { Button, Container } from "./ui";

const COPY = {
  login: {
    title: "Sign in",
    sub: "Your collections are waiting.",
    action: "Sign in",
    swapText: "Don't have an account?",
    swapLink: "Create one",
    swapTo: "/signup",
  },
  signup: {
    title: "Create an account",
    sub: "Collections and screening decisions, saved to you.",
    action: "Create account",
    swapText: "Already have an account?",
    swapLink: "Sign in",
    swapTo: "/login",
  },
} as const;

export function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const copy = COPY[mode];
  const { user, login, signup, isLoading, config } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // The OAuth callback cannot render an error, so it redirects here with a
  // reason. Mapped to plain language rather than shown raw.
  const oauthError = new URLSearchParams(location.search).get("error");
  const [error, setError] = useState<string | null>(
    oauthError === "state"
      ? "That sign-in link expired or did not match. Please try again."
      : oauthError === "denied"
        ? "Google sign-in was cancelled."
        : oauthError === "exchange"
          ? "Google sign-in failed. Please try again."
          : null,
  );
  const [busy, setBusy] = useState(false);

  // Where the user was headed before being bounced here.
  const next = (location.state as { from?: string } | null)?.from ?? "/collections";

  if (!isLoading && user) return <Navigate to={next} replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await (mode === "login" ? login(email, password) : signup(email, password));
      navigate(next, { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="field-glow relative min-h-[70vh] overflow-hidden">
      <Container className="relative z-10 flex justify-center py-24">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: [0.2, 0.8, 0.2, 1] }}
          className="w-full max-w-sm"
        >
          <h1 className="text-h2 font-semibold text-ink-50">{copy.title}</h1>
          <p className="mt-2 text-sm text-ink-400">{copy.sub}</p>

          {config?.google && (
            <>
              <a
                href="/api/auth/google/start"
                className="hairline mt-8 flex h-11 items-center justify-center gap-3 rounded-lg
                           border bg-ink-850/60 text-sm font-medium text-ink-100
                           transition-all duration-150 hover:bg-ink-800 active:scale-[0.98]"
              >
                <GoogleMark />
                Continue with Google
              </a>
              <div className="my-6 flex items-center gap-4">
                <span className="hairline h-px flex-1 border-t" />
                <span className="font-mono text-[10px] uppercase tracking-widest text-ink-600">
                  or
                </span>
                <span className="hairline h-px flex-1 border-t" />
              </div>
            </>
          )}

          <form onSubmit={onSubmit} className={`flex flex-col gap-4 ${config?.google ? "" : "mt-8"}`}>
            <label className="flex flex-col gap-2">
              <span className="font-mono text-eyebrow uppercase text-ink-400">Email</span>
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="hairline h-11 rounded-lg border bg-ink-880 px-3 text-ink-50
                           transition-colors placeholder:text-ink-600 focus:border-semantic-400
                           focus:outline-none"
                placeholder="you@university.edu"
              />
            </label>

            <label className="flex flex-col gap-2">
              <span className="font-mono text-eyebrow uppercase text-ink-400">Password</span>
              <input
                type="password"
                required
                // Tells the password manager which flow this is, so it offers
                // to save on signup and to fill on login.
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                minLength={mode === "signup" ? 10 : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="hairline h-11 rounded-lg border bg-ink-880 px-3 text-ink-50
                           transition-colors placeholder:text-ink-600 focus:border-semantic-400
                           focus:outline-none"
                placeholder={mode === "signup" ? "at least 10 characters" : ""}
              />
            </label>

            {error && (
              <motion.p
                role="alert"
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                className="rounded-lg border border-danger-400/30 bg-danger-400/10 px-3 py-2
                           text-sm text-danger-400"
              >
                {error}
              </motion.p>
            )}

            <Button type="submit" size="lg" disabled={busy} className="mt-2">
              {busy ? "…" : copy.action}
            </Button>
          </form>

          <p className="mt-6 text-sm text-ink-400">
            {copy.swapText}{" "}
            <Link to={copy.swapTo} className="text-ink-100 underline underline-offset-4">
              {copy.swapLink}
            </Link>
          </p>
        </motion.div>
      </Container>
    </div>
  );
}

/** Wraps routes that need a session. Sends the signed-out to /login and
 *  remembers where they were going, so the redirect after signing in lands
 *  them where they meant to be rather than on a generic home page. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();
  const location = useLocation();
  if (isLoading) {
    return (
      <Container className="py-24">
        <div className="h-4 w-32 animate-pulse rounded bg-ink-800" />
      </Container>
    );
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}


function GoogleMark() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 0 1-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8Z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.2 0 5.9-1.1 7.9-2.9l-3.9-3c-1.1.7-2.4 1.2-4 1.2-3.1 0-5.7-2.1-6.6-4.9H1.4v3.1A12 12 0 0 0 12 24Z"
      />
      <path fill="#FBBC05" d="M5.4 14.4a7.2 7.2 0 0 1 0-4.6V6.7H1.4a12 12 0 0 0 0 10.8l4-3.1Z" />
      <path
        fill="#EA4335"
        d="M12 4.8c1.8 0 3.3.6 4.5 1.8l3.4-3.4A12 12 0 0 0 1.4 6.7l4 3.1C6.3 6.9 8.9 4.8 12 4.8Z"
      />
    </svg>
  );
}

