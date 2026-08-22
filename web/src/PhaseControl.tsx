// The phase switch, and the confirmation that makes it deliberate.
//
// Advancing to `review` lifts blinding for the WHOLE collection in one click,
// including on papers the reader never screened. That is the point of the
// phase, and it is also irreversible in the only sense that matters: people
// cannot un-see a colleague's call. So the owner is shown the numbers first —
// a confirmation dialog with nothing in it is not informed consent.
//
// Everyone sees the current phase and the history, not just owners. A screener
// is entitled to know their calls became visible, and when, and who did it.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { getPhase, setPhase, type Phase } from "./api";
import { Button } from "./ui";

const COPY: Record<Phase, { label: string; blurb: string; tone: string }> = {
  screening: {
    label: "Screening",
    blurb: "Blind. Nobody sees another call until they have made their own.",
    tone: "text-semantic-ink",
  },
  review: {
    label: "Review",
    blurb: "Decisions visible to the whole team. Conflicts open to everyone.",
    tone: "text-keyword-ink",
  },
  closed: {
    label: "Closed",
    blurb: "Finished. No further decisions or resolutions until reopened.",
    tone: "text-ink-300",
  },
};

const ORDER: Phase[] = ["screening", "review", "closed"];

export function PhaseControl({ collectionId }: { collectionId: number }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<Phase | null>(null);

  const { data } = useQuery({
    queryKey: ["phase", collectionId],
    queryFn: () => getPhase(collectionId),
  });

  const change = useMutation({
    mutationFn: (p: Phase) => setPhase(collectionId, p),
    onSuccess: () => {
      setPending(null);
      // Every visibility answer keys on phase, so the whole collection's
      // cached state is now suspect — invalidate broadly rather than guessing
      // which queries moved.
      void qc.invalidateQueries();
    },
  });

  if (!data) return <div className="h-14 animate-pulse rounded-xl bg-ink-880" />;
  if (data.screening_mode === "solo") return null; // nothing to coordinate

  const current = COPY[data.phase];
  const reopening = pending !== null && ORDER.indexOf(pending) < ORDER.indexOf(data.phase);

  return (
    <div className="hairline rounded-2xl border bg-ink-880/70 p-5 backdrop-blur-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-eyebrow uppercase text-ink-400">Phase</h2>
        <span className={`font-mono text-[11px] uppercase tracking-wider ${current.tone}`}>
          {current.label}
        </span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-ink-300">{current.blurb}</p>

      {data.can_change && (
        <div className="mt-4 flex flex-wrap gap-2">
          {ORDER.filter((p) => p !== data.phase).map((p) => (
            <Button key={p} variant="secondary" size="sm" onClick={() => setPending(p)}>
              {ORDER.indexOf(p) < ORDER.indexOf(data.phase) ? "Reopen to " : "Move to "}
              {COPY[p].label.toLowerCase()}
            </Button>
          ))}
        </div>
      )}

      <AnimatePresence>
        {pending && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="hairline mt-4 rounded-xl border bg-ink-900 p-4">
              <p className="text-sm font-medium text-ink-100">
                {pending === "review"
                  ? "This reveals every decision to every member."
                  : reopening
                    ? "Reopening lets people change decisions again."
                    : `Move this review to ${COPY[pending].label.toLowerCase()}?`}
              </p>

              {pending === "review" && (
                <ul className="mt-3 flex flex-col gap-1 font-mono text-[11px] text-ink-400">
                  <li>{data.reveal_preview.decisions} decisions become visible</li>
                  <li>
                    across {data.reveal_preview.papers} papers, by{" "}
                    {data.reveal_preview.screeners} screeners
                  </li>
                  <li className="text-keyword-ink">
                    {data.reveal_preview.conflicts} disagreements open to the team
                  </li>
                </ul>
              )}

              {reopening && (
                <p className="mt-3 text-xs leading-relaxed text-ink-400">
                  Existing rulings stay on the record. Any whose underlying calls
                  change afterwards get flagged as stale rather than deleted —
                  nothing is rewritten.
                </p>
              )}

              <p className="mt-3 text-xs text-ink-500">
                Recorded against your account, with the time.
              </p>

              <div className="mt-4 flex gap-2">
                <Button
                  size="sm"
                  onClick={() => change.mutate(pending)}
                  disabled={change.isPending}
                >
                  {change.isPending ? <span className="spinner" /> : "Confirm"}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setPending(null)}>
                  Cancel
                </Button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {data.history.length > 0 && (
        <ul className="hairline mt-4 flex flex-col gap-1 border-t pt-3">
          {data.history.slice(0, 3).map((h, i) => (
            <li key={i} className="font-mono text-[10px] uppercase tracking-wider text-ink-500">
              {h.from_phase} → {h.to_phase} · {h.changed_by.split("@")[0]} ·{" "}
              {new Date(h.changed_at).toLocaleDateString()}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
