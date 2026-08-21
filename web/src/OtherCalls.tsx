// What other screeners said about one paper — under the blinding rule.
//
// Three states, and the component's job is to make which one you are in
// obvious rather than showing an empty space that could mean either "nobody
// else has looked" or "you are not allowed to know yet":
//
//   blinded    you have not decided. Says so explicitly, because silence here
//              is ambiguous and ambiguity invites people to assume the paper
//              is untouched.
//   decided    their decisions, no notes. The absence of notes is stated, not
//              implied, so nobody wonders whether the note field is broken.
//   agreement  everyone matched — worth saying plainly, since a review's
//              interesting rows are the ones that did not.
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { getPaperScreening, type Decision } from "./api";

const TONE: Record<Decision, string> = {
  include: "text-signal-400 border-signal-400/30 bg-signal-400/10",
  exclude: "text-danger-400 border-danger-400/30 bg-danger-400/10",
  maybe: "text-keyword-ink border-keyword-500/30 bg-keyword-950/60",
};

export function OtherCalls({
  collectionId,
  paperId,
}: {
  collectionId: number;
  paperId: number;
}) {
  const { data } = useQuery({
    queryKey: ["paper-screening", collectionId, paperId],
    queryFn: () => getPaperScreening(collectionId, paperId),
  });

  if (!data) return null;

  if (data.blinded) {
    return (
      <p className="mt-2 font-mono text-[10px] uppercase tracking-wider text-ink-500">
        · blind — decide first to see the others
      </p>
    );
  }

  if (data.others.length === 0) return null;

  const decisions = new Set(data.others.map((o) => o.decision));
  const unanimous =
    data.mine != null && decisions.size === 1 && decisions.has(data.mine.decision);

  return (
    <motion.div
      initial={{ opacity: 0, y: -3 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="mt-2 flex flex-wrap items-center gap-1.5"
    >
      {data.others.map((o) => (
        <span
          key={o.user_id}
          title={`${o.email} — ${o.decision}`}
          className={`hairline inline-flex items-center gap-1.5 rounded-md border px-1.5 py-0.5
                      font-mono text-[10px] uppercase tracking-wider ${TONE[o.decision]}`}
        >
          {o.email.split("@")[0].slice(0, 12)}
          <span className="opacity-70">{o.decision}</span>
        </span>
      ))}
      {unanimous ? (
        <span className="font-mono text-[10px] uppercase tracking-wider text-ink-500">
          · agreed
        </span>
      ) : (
        <span className="font-mono text-[10px] uppercase tracking-wider text-keyword-ink">
          · disagreement
        </span>
      )}
      {/* Stated, not implied: an absent note field here is the rule working,
          not a bug. */}
      {!data.notes_visible && (
        <span className="font-mono text-[10px] uppercase tracking-wider text-ink-600">
          · reasons sealed until review
        </span>
      )}
    </motion.div>
  );
}
