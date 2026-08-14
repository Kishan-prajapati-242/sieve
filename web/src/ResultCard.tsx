// One search hit. Restyled 2026-08-13; the score breakdown stays the strongest
// thing on the card, because keyword-rank vs semantic-rank beside a fused
// score is what makes the mode toggle legible.
//
// Hover is CSS-only — border, shadow, background, a 2px lift. A spotlight
// effect needs a mousemove listener per card, and this list runs to hundreds
// of rows (docs/plans/ui-assembly-plan.md).
//
// forwardRef because AnimatePresence mode="popLayout" measures the exiting
// child to lift it out of layout flow while survivors move; it needs a ref on
// the direct child, and a plain function component cannot take one.
import { motion, useReducedMotion } from "motion/react";
import { forwardRef } from "react";
import { AddToCollection } from "./AddToCollection";
import type { SearchResult } from "./api";
import { DUR, EASE, rowVariants } from "./motion";

const MAX_AUTHORS = 10;

function authorLine(authors: string[] | null): string {
  if (!authors || authors.length === 0) return "";
  if (authors.length <= MAX_AUTHORS) return authors.join(", ");
  return `${authors.slice(0, MAX_AUTHORS).join(", ")}, +${authors.length - MAX_AUTHORS} more`;
}

export const ResultCard = forwardRef<
  HTMLLIElement,
  { result: SearchResult; stagger?: number; showAddTo?: boolean }
>(function ResultCard({ result, stagger = 0, showAddTo = true }, ref) {
  const reduce = useReducedMotion();
  return (
    <motion.li
      ref={ref}
      // `layout` animates the re-rank when the mode toggle changes the order.
      // Simultaneous by decision: survivors move while arrivals land.
      layout={reduce ? false : "position"}
      custom={stagger}
      variants={rowVariants}
      initial="initial"
      animate="animate"
      exit="exit"
      transition={{ layout: { duration: DUR.move, ease: EASE } }}
      className="group rounded-xl border border-slate-200 bg-white p-4 shadow-sm
                 transition-[background-color,border-color,box-shadow,transform] duration-200
                 hover:-translate-y-0.5 hover:border-slate-300 hover:bg-slate-50 hover:shadow-md"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="text-xs text-slate-500">
          #{result.rank} · score {result.score.toFixed(4)}
          {result.sources && <>{" · "}{result.sources.join(", ")}</>}
          {(result.bm25_rank !== null || result.vector_rank !== null) && (
            <span className="ml-2 inline-flex gap-2">
              <span className="rounded bg-slate-100 px-1.5 py-0.5">
                keyword {result.bm25_rank !== null ? `#${result.bm25_rank}` : "—"}
              </span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5">
                semantic {result.vector_rank !== null ? `#${result.vector_rank}` : "—"}
              </span>
            </span>
          )}
        </div>
        {showAddTo && <AddToCollection paperId={result.id} />}
      </div>

      <h2 className="mt-1 font-semibold text-slate-900">{result.title}</h2>
      {result.authors && <p className="text-sm text-slate-600">{authorLine(result.authors)}</p>}
      <p className="text-sm text-slate-500">
        {result.year ?? "year unknown"}
        {result.venue && <> · {result.venue}</>}
        {" · "}
        {result.citation_count} citations
        {result.doi && (
          <>
            {" · "}
            <a
              className="text-blue-600 underline underline-offset-2 hover:text-blue-800"
              href={`https://doi.org/${result.doi}`}
              target="_blank"
              rel="noreferrer"
            >
              DOI
            </a>
          </>
        )}
        {result.is_retracted && (
          <span
            role="alert"
            className="ml-2 rounded bg-rose-100 px-1.5 py-0.5 text-xs font-medium text-rose-700"
          >
            RETRACTED
          </span>
        )}
      </p>
      {result.abstract && (
        <details className="mt-1 text-sm text-slate-700">
          <summary className="cursor-pointer text-slate-500">Abstract</summary>
          <p className="mt-1">{result.abstract}</p>
        </details>
      )}
    </motion.li>
  );
});
