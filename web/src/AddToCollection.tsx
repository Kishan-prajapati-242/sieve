// The control that closes the loop: screen a paper straight from search
// results into a collection. This is what makes Phase 3's "screening
// workflow usable end to end" true rather than API-only.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { listCollections, screen, type Decision } from "./api";
import { DUR, EASE } from "./motion";

const DECISIONS: Decision[] = ["include", "maybe", "exclude"];

export function AddToCollection({ paperId }: { paperId: number }) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const qc = useQueryClient();

  const { data: collections } = useQuery({
    queryKey: ["collections"],
    queryFn: listCollections,
    enabled: open,
  });

  const mut = useMutation({
    mutationFn: ({ cid, decision }: { cid: number; decision: Decision }) =>
      screen(cid, paperId, decision),
    onSuccess: (_d, vars) => {
      const name = collections?.find((c) => c.id === vars.cid)?.name ?? "collection";
      setDone(`${vars.decision} → ${name}`);
      setOpen(false);
      // The counts on View A and the rows in View B both move.
      qc.invalidateQueries({ queryKey: ["collections"] });
      qc.invalidateQueries({ queryKey: ["collection"] });
    },
  });

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => { setOpen((o) => !o); setDone(null); }}
        className="rounded-md hairline border px-2.5 py-1 text-xs font-medium
                   text-ink-200 transition-colors hover:border-slate-400 hover:bg-ink-850"
      >
        {done ?? "Add to…"}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ duration: DUR.enter, ease: EASE }}
            className="absolute right-0 z-20 mt-1 w-64 rounded-lg hairline border
                       bg-ink-880 p-2 -lg"
          >
            {collections === undefined && <p className="p-2 text-xs text-ink-400">Loading…</p>}
            {collections?.length === 0 && (
              <p className="p-2 text-xs text-ink-400">
                No collections yet — make one first.
              </p>
            )}
            {collections?.map((c) => (
              <div key={c.id} className="flex items-center justify-between gap-2 p-1">
                <span className="truncate text-xs text-ink-200">{c.name}</span>
                <span className="flex gap-1">
                  {DECISIONS.map((d) => (
                    <button
                      key={d}
                      type="button"
                      disabled={mut.isPending}
                      onClick={() => mut.mutate({ cid: c.id, decision: d })}
                      className="rounded hairline border px-1.5 py-0.5 text-[11px]
                                 capitalize text-ink-300 hover:bg-ink-800 disabled:opacity-50"
                    >
                      {d}
                    </button>
                  ))}
                </span>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
