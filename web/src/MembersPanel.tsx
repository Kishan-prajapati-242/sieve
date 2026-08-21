// Who is on this review, and how to add someone.
//
// Shows two things that look similar and are not: the ROSTER (who, what role)
// and PROGRESS (how many papers each has screened). Progress is deliberately
// volume-only — it says how much work has been done and never what anyone
// concluded, which is the line the read audit draws. A per-member
// include/exclude breakdown here would leak judgement in exactly the way the
// collection cards used to.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import {
  createInvite,
  getMembers,
  inviteLink,
  removeMember,
  type Role,
} from "./api";
import { Button } from "./ui";

const ROLE_BLURB: Record<Exclude<Role, "owner">, string> = {
  resolver: "Screens, and settles disagreements. Cannot add or remove people.",
  screener: "Screens papers. Sees nobody else's calls until they have made their own.",
  viewer: "Reads the outcome. Cannot screen.",
};

const ROLE_TONE: Record<Role, string> = {
  owner: "text-keyword-ink",
  resolver: "text-semantic-ink",
  screener: "text-ink-200",
  viewer: "text-ink-400",
};

export function MembersPanel({ collectionId }: { collectionId: number }) {
  const qc = useQueryClient();
  const [inviteRole, setInviteRole] = useState<Exclude<Role, "owner">>("screener");
  const [minted, setMinted] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["members", collectionId],
    queryFn: () => getMembers(collectionId),
  });

  const invite = useMutation({
    mutationFn: () => createInvite(collectionId, inviteRole),
    onSuccess: (r) => {
      setMinted(r.token);
      setCopied(false);
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: (memberId: number) => removeMember(collectionId, memberId),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["members", collectionId] });
      void qc.invalidateQueries({ queryKey: ["collections"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  if (isLoading || !data) {
    return <div className="h-24 animate-pulse rounded-xl bg-ink-880" />;
  }

  const isOwner = data.your_role === "owner";
  const screenedBy = new Map(data.progress.map((p) => [p.user_id, p.screened]));

  return (
    <div className="hairline rounded-2xl border bg-ink-880/70 p-5 backdrop-blur-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-eyebrow uppercase text-ink-400">Review team</h2>
        <span className="font-mono text-[11px] text-ink-500">
          {data.members.length} {data.members.length === 1 ? "member" : "members"}
        </span>
      </div>

      <ul className="mt-4 flex flex-col gap-2">
        {data.members.map((m, i) => (
          <motion.li
            key={m.user_id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: i * 0.04, ease: [0.2, 0.8, 0.2, 1] }}
            className="hairline group flex items-center justify-between gap-3 rounded-lg
                       border bg-ink-900/60 px-3 py-2.5"
          >
            <div className="min-w-0">
              <p className="truncate text-sm text-ink-100">{m.email}</p>
              <p className="mt-0.5 flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider">
                <span className={ROLE_TONE[m.role]}>{m.role}</span>
                <span className="text-ink-600">·</span>
                {/* Volume, never a breakdown. */}
                <span className="text-ink-500">{screenedBy.get(m.user_id) ?? 0} screened</span>
              </p>
            </div>
            {isOwner && m.role !== "owner" && (
              <button
                type="button"
                onClick={() => remove.mutate(m.user_id)}
                className="shrink-0 rounded-md px-2 py-1 font-mono text-[10px] uppercase
                           tracking-wider text-ink-500 opacity-0 transition-all
                           hover:bg-danger-400/10 hover:text-danger-400
                           focus-visible:opacity-100 group-hover:opacity-100"
              >
                Remove
              </button>
            )}
          </motion.li>
        ))}
      </ul>

      {isOwner && (
        <div className="hairline mt-5 border-t pt-5">
          <div className="flex flex-wrap items-center gap-2">
            {(["screener", "resolver", "viewer"] as const).map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setInviteRole(r)}
                aria-pressed={inviteRole === r}
                className={`hairline rounded-lg border px-3 py-1.5 font-mono text-[10px]
                            uppercase tracking-wider transition-colors ${
                              inviteRole === r
                                ? "bg-ink-50 text-ink-950"
                                : "text-ink-400 hover:text-ink-100"
                            }`}
              >
                {r}
              </button>
            ))}
            <Button
              size="sm"
              onClick={() => invite.mutate()}
              disabled={invite.isPending}
              className="ml-auto"
            >
              {invite.isPending ? <span className="spinner" /> : "Create invite link"}
            </Button>
          </div>
          <p className="mt-2.5 text-xs leading-relaxed text-ink-500">
            {ROLE_BLURB[inviteRole]}
          </p>

          <AnimatePresence>
            {minted && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="overflow-hidden"
              >
                <div className="hairline mt-4 rounded-lg border bg-ink-900 p-3">
                  <p className="font-mono text-[10px] uppercase tracking-wider text-ink-500">
                    Single use · expires in 14 days
                  </p>
                  <div className="mt-2 flex items-center gap-2">
                    <code className="min-w-0 flex-1 truncate font-mono text-xs text-ink-200">
                      {inviteLink(minted)}
                    </code>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => {
                        void navigator.clipboard.writeText(inviteLink(minted));
                        setCopied(true);
                      }}
                    >
                      {copied ? "Copied" : "Copy"}
                    </Button>
                  </div>
                  {/* Said once, plainly: the server keeps only a hash, so this
                      is the only time the link exists in readable form. */}
                  <p className="mt-2 text-xs text-ink-500">
                    Shown once — we store only a hash of it. Send it however you like.
                  </p>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      <AnimatePresence>
        {error && (
          <motion.p
            role="alert"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mt-4 rounded-lg border border-danger-400/30 bg-danger-400/10 px-3 py-2
                       text-sm text-danger-400"
          >
            {error}
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}
