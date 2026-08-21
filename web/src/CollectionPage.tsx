// View B — one collection. The fuller version deliberately: a `maybe` exists
// to be revisited, and without a list there is no mechanism to revisit it.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  csvExportUrl,
  exportUrl,
  getCollection,
  screen,
  unscreen,
  type Decision,
} from "./api";
import { DecisionBar } from "./DecisionBar";
import { MembersPanel } from "./MembersPanel";
import { DUR, EASE, rowVariants } from "./motion";

const FILTERS: (Decision | "all")[] = ["all", "include", "maybe", "exclude"];

export function CollectionPage() {
  const { id } = useParams();
  const cid = Number(id);
  const [filter, setFilter] = useState<Decision | "all">("all");
  const [showTeam, setShowTeam] = useState(false);
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

  if (isLoading) return <p className="text-sm text-ink-400">Loading…</p>;
  if (error)
    return (
      <p role="alert" className="text-sm text-danger-400">
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
          <h1 className="mt-1 text-xl font-bold text-ink-50">{data.name}</h1>
          {data.question && <p className="text-ink-300">{data.question}</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          <a
            href={exportUrl(cid, filter === "all" ? "include" : filter)}
            className="hairline lift rounded-lg border px-3 py-2 text-sm font-medium
                       text-ink-200 hover:hairline-strong hover:bg-ink-850"
          >
            Export .bib
          </a>
          {/* CSV for anyone without a reference manager — and it carries the
              decisions and notes, which BibTeX cannot. */}
          <a
            href={csvExportUrl(cid)}
            className="hairline lift rounded-lg border px-3 py-2 text-sm font-medium
                       text-ink-200 hover:hairline-strong hover:bg-ink-850"
          >
            Export .csv
          </a>
          {/* A toggle rather than always-open: a solo reviewer has nobody to
              manage, and pushing the papers down the page to say so is a
              worse default than one click for the people who need it. */}
          <button
            type="button"
            onClick={() => setShowTeam((v) => !v)}
            aria-expanded={showTeam}
            className="hairline lift rounded-lg border px-3 py-2 text-sm font-medium
                       text-ink-200 hover:hairline-strong hover:bg-ink-850"
          >
            {showTeam ? "Hide team" : "Team"}
          </button>
        </div>
      </div>

      <AnimatePresence initial={false}>
        {showTeam && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3, ease: EASE }}
            className="overflow-hidden"
          >
            <div className="pb-4">
              <MembersPanel collectionId={cid} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="flex gap-2">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            aria-pressed={filter === f}
            className={`rounded-lg px-3 py-1.5 text-sm capitalize transition-colors ${
              filter === f
                ? "bg-ink-50 text-ink-950"
                : "hairline border text-ink-200 hover:bg-ink-850"
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      {data.papers.length === 0 && (
        <p className="rounded-xl border border-dashed hairline border p-8 text-center text-ink-400">
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
              className="rounded-xl hairline border bg-ink-880 p-4 
                         transition-[background-color,border-color,box-shadow,transform]
                         duration-200 hover:-translate-y-0.5 hover:hairline-strong
                         hover:bg-ink-850 hover:shadow-[0_8px_28px_-12px_rgba(0,0,0,0.7)]"
            >
              <h2 className="font-semibold text-ink-50">{p.title}</h2>
              <p className="text-sm text-ink-400">
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
              {p.note && <p className="mt-1 text-sm italic text-ink-300">{p.note}</p>}
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
                  className="text-sm text-ink-500 transition-colors hover:text-rose-600"
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
