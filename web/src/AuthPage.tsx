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
  const { user, login, signup, isLoading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
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

          <form onSubmit={onSubmit} className="mt-8 flex flex-col gap-4">
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
