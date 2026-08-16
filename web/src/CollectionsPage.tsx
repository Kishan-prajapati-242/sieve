// View A — collections. A collection is one literature question; the counts
// come back already aggregated from GET /api/collections, so the list is one
// request.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { createCollection, listCollections } from "./api";
import { CountBadge } from "./CountBadge";
import { DUR, EASE, rowVariants } from "./motion";

export function CollectionsPage() {
  const [name, setName] = useState("");
  const [question, setQuestion] = useState("");
  const qc = useQueryClient();

  const { data, error, isLoading } = useQuery({
    queryKey: ["collections"],
    queryFn: listCollections,
  });

  const create = useMutation({
    mutationFn: () => createCollection(name.trim(), question.trim() || undefined),
    onSuccess: () => {
      setName("");
      setQuestion("");
      qc.invalidateQueries({ queryKey: ["collections"] });
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    create.mutate();
  }

  return (
    <div className="space-y-5">
      <form
        onSubmit={onSubmit}
        className="flex flex-wrap items-end gap-2 rounded-xl hairline border bg-ink-880 p-4"
      >
        <label className="text-sm">
          <span className="block text-ink-300">Name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Clinical text simplification"
            className="mt-1 w-64 rounded-lg hairline border bg-ink-880 px-3 py-2 text-ink-50 focus:border-semantic-400 focus:outline-none"
          />
        </label>
        <label className="flex-1 text-sm">
          <span className="block text-ink-300">Question (optional)</span>
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Which methods simplify discharge summaries?"
            className="mt-1 w-full rounded-lg hairline border bg-ink-880 px-3 py-2 text-ink-50 focus:border-semantic-400 focus:outline-none"
          />
        </label>
        <button
          type="submit"
          disabled={create.isPending}
          className="rounded-lg bg-ink-50 px-4 py-2 text-sm font-medium text-ink-950
                     transition-colors hover:bg-white disabled:opacity-60"
        >
          New collection
        </button>
      </form>

      {create.error && (
        <p role="alert" className="text-sm text-danger-400">
          Could not create: {(create.error as Error).message}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger-400">
          Could not load collections: {(error as Error).message}
        </p>
      )}
      {isLoading && <p className="text-sm text-ink-400">Loading…</p>}

      {data && data.length === 0 && (
        <p className="rounded-xl border border-dashed hairline border p-8 text-center text-ink-400">
          No collections yet. A collection is one literature question.
        </p>
      )}

      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <AnimatePresence initial={false} mode="popLayout">
          {data?.map((c) => (
            <motion.li
              key={c.id}
              layout="position"
              custom={0}
              variants={rowVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={{ layout: { duration: DUR.move, ease: EASE } }}
              className="hairline group relative overflow-hidden rounded-2xl border
                         bg-ink-880/70 p-5 backdrop-blur-sm
                         transition-[background-color,border-color,box-shadow,transform]
                         duration-300 ease-[var(--ease-out-soft)] hover:-translate-y-1
                         hover:hairline-strong hover:bg-ink-850/80
                         hover:shadow-[0_18px_44px_-20px_rgba(0,0,0,0.8)]"
            >
              {/* A gradient edge that fills on hover, matching the result
                  cards — one hover language across both views. */}
              <span
                aria-hidden="true"
                className="absolute inset-x-0 top-0 h-[3px] origin-left scale-x-0 bg-fusion
                           transition-transform duration-400 ease-[var(--ease-out-soft)]
                           group-hover:scale-x-100"
              />
              <Link to={`/collections/${c.id}`} className="block">
                <h2 className="font-semibold text-ink-50 transition-colors group-hover:text-fusion">
                  {c.name}
                </h2>
                {c.question && (
                  <p className="mt-1.5 line-clamp-2 text-sm text-ink-300">{c.question}</p>
                )}
                <div className="hairline mt-4 flex flex-wrap gap-4 border-t pt-4">
                  <CountBadge value={c.screened} label="screened" />
                  <CountBadge value={c.included} label="include" />
                  <CountBadge value={c.excluded} label="exclude" />
                  <CountBadge value={c.maybe} label="maybe" />
                </div>
              </Link>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </div>
  );
}
