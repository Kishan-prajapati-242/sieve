// One search hit.
//
// Anatomy rebuilt 2026-08-14 against what real products do
// (.design-review/visual-sheet.html). Three findings drove it:
//
//   TITLE LEADS. Semantic Scholar, OpenAlex, Europe PMC and GitHub all put
//   the title first and demote provenance to a metadata line. We had
//   `#rank · score · keyword #n · semantic #n` ABOVE the title — our most
//   distinctive feature parked exactly where the eye lands while scanning.
//
//   NO CARD. None of the four academic products puts a border around a
//   result row; separation is whitespace and a hairline. Twenty bordered,
//   shadowed, lifting cards was the single biggest reason this read as a
//   template rather than a tool.
//
//   THREE TYPE LEVELS, not two: title 15-16px coloured, authors 13px grey,
//   metadata 12px lighter grey. Ours ran authors and metadata at the same
//   size, so the eye could not triage.
//
// Hover is CSS-only — a background tint, no lift, no shadow. A spotlight
// effect needs a mousemove listener per row and this list runs long.
import { motion, useReducedMotion } from "motion/react";
import { forwardRef } from "react";
import { AddToCollection } from "./AddToCollection";
import type { SearchResult } from "./api";
import { DUR, EASE, rowMotionProps, rowVariants } from "./motion";
import { ProvenanceChips } from "./ProvenanceChips";

const MAX_AUTHORS = 10;

function authorLine(authors: string[] | null): string {
  if (!authors || authors.length === 0) return "";
  if (authors.length <= MAX_AUTHORS) return authors.join(", ");
  return `${authors.slice(0, MAX_AUTHORS).join(", ")}, +${authors.length - MAX_AUTHORS} more`;
}

export const ResultCard = forwardRef<
  HTMLLIElement,
  { result: SearchResult; delay?: number; showAddTo?: boolean; dimmed?: boolean }
>(function ResultCard({ result, delay = 0, showAddTo = true, dimmed = false }, ref) {
  const reduce = useReducedMotion();
  return (
    <motion.li
      ref={ref}
      {...rowMotionProps(!!reduce, delay)}
      // Faded, not hidden: the row keeps its space so the list does not
      // reflow under the pointer, and the reader can see WHICH results the
      // other arm contributed rather than just how many.
      style={{ opacity: dimmed ? 0.22 : 1 }}
      variants={rowVariants}
      animate="animate"
      exit="exit"
      transition={{ layout: { duration: DUR.move, ease: EASE } }}
      className="group relative mb-2.5 overflow-hidden rounded-xl border bg-ink-880/70
                 px-4 py-4 backdrop-blur-sm transition-[transform,border-color,background-color,box-shadow]
                 duration-300 ease-[var(--ease-out-soft)] hairline
                 hover:-translate-y-0.5 hover:hairline-strong hover:bg-ink-850/80
                 hover:shadow-[0_14px_38px_-18px_rgba(0,0,0,0.75)]"
    >
      {/* A fusion-colored rail that grows from the row's centre on hover.
          Scale rather than width so it is a compositor-only animation and
          cannot cause layout work on a 20-row list. */}
      <span
        aria-hidden="true"
        className="absolute inset-y-0 left-0 w-[3px] origin-center scale-y-0 bg-fusion
                   transition-transform duration-400 ease-[var(--ease-out-soft)]
                   group-hover:scale-y-100"
      />
      <div className="flex items-start gap-3">
        <span className="w-6 shrink-0 pt-0.5 text-right text-xs tabular-nums text-ink-500">
          {result.rank}
        </span>

        <div className="min-w-0 flex-1">
          {/* Title leads. The only coloured, link-weight text on the row. */}
          <h2 className="text-[15px] font-medium leading-snug text-ink-50">
            {result.doi ? (
              <a
                href={`https://doi.org/${result.doi}`}
                target="_blank"
                rel="noreferrer"
                className="hover:underline"
              >
                {result.title}
              </a>
            ) : (
              result.title
            )}
            {result.is_retracted && (
              <span
                role="alert"
                className="ml-2 align-middle rounded bg-rose-100 px-1.5 py-0.5 text-[11px]
                           font-semibold text-danger-400"
              >
                RETRACTED
              </span>
            )}
          </h2>

          {result.authors && (
            <p className="mt-0.5 truncate text-[13px] text-ink-400">
              {authorLine(result.authors)}
            </p>
          )}

          <p className="mt-0.5 font-mono text-[11px] text-ink-500">
            {result.year ?? "year unknown"}
            {result.venue && <> · {result.venue}</>}
            {" · "}
            {result.citation_count} citations
            {result.sources && <> · {result.sources.join(", ")}</>}
          </p>

          {/* Provenance below the title, not above it — and inline on every
              row rather than on hover, because a screenshot has no hover. */}
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <ProvenanceChips bm25Rank={result.bm25_rank} vectorRank={result.vector_rank} />
            <span className="text-[11px] tabular-nums text-ink-500">
              score {result.score.toFixed(4)}
            </span>
          </div>

          {result.abstract && (
            <details className="mt-1.5 text-[13px] text-ink-300">
              <summary className="cursor-pointer font-mono text-[11px] text-ink-500 hover:text-ink-300">
                Abstract
              </summary>
              <p className="mt-1 leading-relaxed">{result.abstract}</p>
            </details>
          )}
        </div>

        {showAddTo && (
          <div className="shrink-0 opacity-0 transition-opacity focus-within:opacity-100
                          group-hover:opacity-100">
            <AddToCollection paperId={result.id} />
          </div>
        )}
      </div>
    </motion.li>
  );
});
