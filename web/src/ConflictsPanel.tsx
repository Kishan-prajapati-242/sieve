// The conflicts queue and the reconciliation view.
//
// This is the one screen where notes cross the blind, and the component makes
// that boundary visible rather than quietly showing more than usual: the
// reconciliation panel says the reasons are open, because a reviewer who does
// not notice the rules changed cannot reason about what they are reading.
//
// The queue itself is server-scoped. A screener sees only conflicts on papers
// they have already decided — "this paper is contested" is a signal about the
// paper, and arguably a stronger one than a single decision because it says
// the paper is hard. `scoped` tells us which case we are in so the UI can say
// so instead of implying the queue is empty.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import {
  getConflictDetail,
  getConflicts,
  resolveConflict,
  type Decision,
} from "./api";
import { Button } from "./ui";

const DECISIONS: Decision[] = ["include", "exclude", "maybe"];

const TONE: Record<Decision, string> = {
  include: "text-signal-400",
  exclude: "text-danger-400",
  maybe: "text-keyword-ink",
};

function Reconcile({
  collectionId,
  paperId,
  title,
  onDone,
}: {
  collectionId: number;
  paperId: number;
  title: string;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [choice, setChoice] = useState<Decision | null>(null);
  const [note, setNote] = useState("");

  const { data } = useQuery({
    queryKey: ["conflict", collectionId, paperId],
    queryFn: () => getConflictDetail(collectionId, paperId),
  });

  const resolve = useMutation({
    mutationFn: () => resolveConflict(collectionId, paperId, choice!, note),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["conflicts", collectionId] });
      void qc.invalidateQueries({ queryKey: ["collection"] });
      onDone();
    },
  });

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      className="overflow-hidden"
    >
      <div className="hairline mt-3 rounded-xl border bg-ink-900/70 p-4">
        <p className="font-mono text-[10px] uppercase tracking-wider text-semantic-ink">
          Reasons open for review
        </p>
        <p className="mt-1 text-xs leading-relaxed text-ink-500">
          Notes are sealed while screening and visible here, because resolving a
          disagreement means understanding why it happened.
        </p>

        <ul className="mt-4 flex flex-col gap-2">
          {(data?.others ?? []).map((o) => (
            <li key={o.user_id} className="hairline rounded-lg border bg-ink-880 px-3 py-2">
              <p className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider">
                <span className="text-ink-400">{o.email.split("@")[0]}</span>
                <span className={TONE[o.decision]}>{o.decision}</span>
              </p>
              {o.note ? (
                <p className="mt-1.5 text-sm leading-relaxed text-ink-200">{o.note}</p>
              ) : (
                <p className="mt-1.5 text-sm italic text-ink-500">no reason given</p>
              )}
            </li>
          ))}
        </ul>

        <div className="hairline mt-4 border-t pt-4">
          <p className="font-mono text-[10px] uppercase tracking-wider text-ink-400">
            Your ruling
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {DECISIONS.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setChoice(d)}
                aria-pressed={choice === d}
                className={`hairline rounded-lg border px-3 py-1.5 font-mono text-[10px]
                            uppercase tracking-wider transition-colors ${
                              choice === d
                                ? "bg-ink-50 text-ink-950"
                                : `${TONE[d]} hover:bg-ink-850`
                            }`}
              >
                {d}
              </button>
            ))}
          </div>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why this ruling (kept with the record)"
            rows={2}
            className="hairline mt-3 w-full rounded-lg border bg-ink-880 px-3 py-2 text-sm
                       text-ink-100 placeholder:text-ink-600 focus:border-semantic-400
                       focus:outline-none"
          />
          <div className="mt-3 flex items-center gap-3">
            <Button
              size="sm"
              disabled={!choice || resolve.isPending}
              onClick={() => resolve.mutate()}
            >
              {resolve.isPending ? <span className="spinner" /> : "Record resolution"}
            </Button>
            <Button variant="ghost" size="sm" onClick={onDone}>
              Cancel
            </Button>
            <span className="ml-auto truncate font-mono text-[10px] text-ink-600">
              {title.slice(0, 48)}
            </span>
          </div>
          {/* The individual calls are never overwritten — say so, because the
              word "resolve" implies replacement. */}
          <p className="mt-2 text-[11px] leading-relaxed text-ink-600">
            Both calls above stay on the record. A resolution is added, not a
            correction.
          </p>
        </div>
      </div>
    </motion.div>
  );
}

export function ConflictsPanel({ collectionId }: { collectionId: number }) {
  const [open, setOpen] = useState<number | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["conflicts", collectionId],
    queryFn: () => getConflicts(collectionId),
  });

  if (isLoading || !data) {
    return <div className="h-16 animate-pulse rounded-xl bg-ink-880" />;
  }

  return (
    <div className="hairline rounded-2xl border bg-ink-880/70 p-5 backdrop-blur-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-eyebrow uppercase text-ink-400">Disagreements</h2>
        <span className="font-mono text-[11px] text-ink-500">
          {data.conflicts.length} unresolved
        </span>
      </div>

      {data.conflicts.length === 0 ? (
        <p className="mt-3 text-sm text-ink-400">
          {data.scoped
            ? // Not "there are none" — we genuinely cannot say that to a
              // screener, and claiming it would be a lie of omission.
              "No disagreements among the papers you have screened."
            : "No disagreements outstanding."}
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-2">
          {data.conflicts.map((c, i) => (
            <motion.li
              key={c.paper_id}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: i * 0.04 }}
              className="hairline rounded-lg border bg-ink-900/60 p-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm leading-snug text-ink-100">{c.title}</p>
                  <p className="mt-1 flex flex-wrap items-center gap-1.5">
                    {c.decisions.map((d, j) => (
                      <span
                        key={j}
                        className={`font-mono text-[10px] uppercase tracking-wider ${TONE[d]}`}
                      >
                        {d}
                      </span>
                    ))}
                    <span className="font-mono text-[10px] text-ink-600">
                      · {c.screener_count} screeners
                    </span>
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  className="shrink-0"
                  onClick={() => setOpen(open === c.paper_id ? null : c.paper_id)}
                >
                  {open === c.paper_id ? "Close" : "Review"}
                </Button>
              </div>
              <AnimatePresence initial={false}>
                {open === c.paper_id && (
                  <Reconcile
                    collectionId={collectionId}
                    paperId={c.paper_id}
                    title={c.title}
                    onDone={() => setOpen(null)}
                  />
                )}
              </AnimatePresence>
            </motion.li>
          ))}
        </ul>
      )}
    </div>
  );
}
