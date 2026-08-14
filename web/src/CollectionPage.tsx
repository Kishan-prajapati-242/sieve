// View B — one collection. The fuller version deliberately: a `maybe` exists
// to be revisited, and without a list there is no mechanism to revisit it.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  exportUrl,
  getCollection,
  screen,
  unscreen,
  type Decision,
} from "./api";
import { DecisionBar } from "./DecisionBar";
import { DUR, EASE, rowVariants } from "./motion";

const FILTERS: (Decision | "all")[] = ["all", "include", "maybe", "exclude"];

export function CollectionPage() {
  const { id } = useParams();
  const cid = Number(id);
  const [filter, setFilter] = useState<Decision | "all">("all");
  const qc = useQueryClient();

  const { data, error, isLoading } = useQuery({
    queryKey: ["collection", cid, filter],
    queryFn: () => getCollection(cid, filter === "all" ? undefined : filter),
    enabled: Number.isFinite(cid),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["collection"] });
    qc.invalidateQueries({ queryKey: ["collections"] });
  };
  const decide = useMutation({
    mutationFn: (v: { paperId: number; decision: Decision }) =>
      screen(cid, v.paperId, v.decision),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (paperId: number) => unscreen(cid, paperId),
    onSuccess: invalidate,
  });

  if (isLoading) return <p className="text-sm text-slate-500">Loading…</p>;
  if (error)
    return (
      <p role="alert" className="text-sm text-rose-700">
        Could not load: {(error as Error).message}
      </p>
    );
  if (!data) return null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/collections" className="text-sm text-blue-600 hover:underline">
            ← All collections
          </Link>
          <h1 className="mt-1 text-xl font-bold text-slate-900">{data.name}</h1>
          {data.question && <p className="text-slate-600">{data.question}</p>}
        </div>
        <a
          href={exportUrl(cid, filter === "all" ? "include" : filter)}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium
                     text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-50"
        >
          Export .bib
        </a>
      </div>

      <div className="flex gap-2">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            aria-pressed={filter === f}
            className={`rounded-lg px-3 py-1.5 text-sm capitalize transition-colors ${
              filter === f
                ? "bg-slate-900 text-white"
                : "border border-slate-300 text-slate-700 hover:bg-slate-50"
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      {data.papers.length === 0 && (
        <p className="rounded-xl border border-dashed border-slate-300 p-8 text-center text-slate-500">
          Nothing screened into this collection yet. Search, then add papers from the results.
        </p>
      )}

      <ul className="space-y-3">
        <AnimatePresence initial={false} mode="popLayout">
          {data.papers.map((p) => (
            <motion.li
              key={p.id}
              layout="position"
              variants={rowVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={{ layout: { duration: DUR.move, ease: EASE } }}
              className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm
                         transition-[background-color,border-color,box-shadow,transform]
                         duration-200 hover:-translate-y-0.5 hover:border-slate-300
                         hover:bg-slate-50 hover:shadow-md"
            >
              <h2 className="font-semibold text-slate-900">{p.title}</h2>
              <p className="text-sm text-slate-500">
                {p.year ?? "year unknown"}
                {p.venue && <> · {p.venue}</>}
                {p.doi && (
                  <>
                    {" · "}
                    <a
                      className="text-blue-600 underline underline-offset-2"
                      href={`https://doi.org/${p.doi}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      DOI
                    </a>
                  </>
                )}
              </p>
              {p.note && <p className="mt-1 text-sm italic text-slate-600">{p.note}</p>}
              <div className="mt-3 flex items-center gap-3">
                <DecisionBar
                  value={p.decision}
                  groupId={String(p.id)}
                  busy={decide.isPending}
                  onChange={(d) => decide.mutate({ paperId: p.id, decision: d })}
                />
                <button
                  type="button"
                  onClick={() => remove.mutate(p.id)}
                  aria-label={`Remove ${p.title}`}
                  className="text-sm text-slate-400 transition-colors hover:text-rose-600"
                >
                  ✕
                </button>
              </div>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </div>
  );
}
