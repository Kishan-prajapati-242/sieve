// Landing page for an invite link.
//
// The link is handed out through Slack, email, anywhere — so whoever opens it
// may be signed out, signed in as the wrong person, or already a member. Each
// of those needs a different answer, and guessing wrong burns a single-use
// token.
//
// Redemption is therefore NEVER automatic on load. The token is spent the
// instant it is POSTed, so an auto-redeem would consume it under whichever
// account happened to be in the browser — including a shared laptop still
// signed in as someone else. The user confirms who they are first.
import { useMutation } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useNavigate, useParams } from "react-router-dom";
import { acceptInvite } from "./api";
import { useAuth } from "./auth";
import { Button, Container } from "./ui";

export function InvitePage() {
  const { token } = useParams();
  const { user, isLoading } = useAuth();
  const navigate = useNavigate();

  const accept = useMutation({
    mutationFn: () => acceptInvite(token ?? ""),
    onSuccess: (r) => navigate(`/collections/${r.collection_id}`, { replace: true }),
  });

  if (isLoading) {
    return (
      <Container className="py-24">
        <div className="h-4 w-40 animate-pulse rounded bg-ink-800" />
      </Container>
    );
  }

  // RequireAuth wraps this route and sends the signed-out to /login carrying
  // this path, so they come back here rather than losing the link. `user` is
  // therefore always set by now; the guard is a type narrowing, not a branch.
  if (!user) return null;

  return (
    <Container className="flex justify-center py-24">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.2, 0.8, 0.2, 1] }}
        className="w-full max-w-md"
      >
        <h1 className="text-h2 font-semibold text-ink-50">You have been invited</h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-300">
          Joining as <span className="font-mono text-ink-100">{user.email}</span>. This link
          works once, so make sure that is the right account before accepting.
        </p>

        {accept.isError && (
          <p
            role="alert"
            className="mt-5 rounded-lg border border-danger-400/30 bg-danger-400/10 px-3 py-2
                       text-sm text-danger-400"
          >
            {(accept.error as Error).message}
          </p>
        )}

        <div className="mt-7 flex flex-wrap gap-3">
          <Button size="lg" onClick={() => accept.mutate()} disabled={accept.isPending}>
            {accept.isPending ? (
              <>
                <span className="spinner" /> Joining…
              </>
            ) : (
              "Accept invitation"
            )}
          </Button>
          <Button variant="secondary" size="lg" onClick={() => navigate("/collections")}>
            Not now
          </Button>
        </div>

        <p className="mt-6 text-xs leading-relaxed text-ink-500">
          Wrong account? Sign out first — accepting now would add{" "}
          <span className="text-ink-300">{user.email}</span> and spend the link.
        </p>
      </motion.div>
    </Container>
  );
}
