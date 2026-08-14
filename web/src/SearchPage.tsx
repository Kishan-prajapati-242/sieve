// Search: form -> POST /api/search -> ranked list. The form edits draft state
// and only submit copies it into `params`; useQuery keys on `params`, so
// typing never fires requests and TanStack Query caches by exact
// (query, mode, year_from, year_to).
//
// The mode toggle is the marquee interaction: switching bm25 -> vector ->
// hybrid re-ranks the list, and animating that shows fusion happening rather
// than describing it. On the de-identification query, the paper neither arm
// ranks first rises to #1 under hybrid.
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence } from "motion/react";
import { useRef, useState, type FormEvent } from "react";
import { search, type SearchMode, type SearchParams } from "./api";
import { ResultCard } from "./ResultCard";
import { staggerFor } from "./motion";

const MODES: SearchMode[] = ["bm25", "vector", "hybrid"];

export function SearchPage() {
  const [draftQuery, setDraftQuery] = useState("");
  const [draftYearFrom, setDraftYearFrom] = useState("");
  const [draftYearTo, setDraftYearTo] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [params, setParams] = useState<SearchParams | null>(null);

  const { data, error, isFetching } = useQuery({
    queryKey: ["search", params],
    queryFn: () => search(params!),
    enabled: params !== null,
  });

  // Stagger applies ONLY when the previous list was empty. Everything else
  // reorders simultaneously (see motion.ts).
  const prevCount = useRef(0);
  const fromEmpty = prevCount.current === 0;
  if (data && !isFetching) prevCount.current = data.results.length;

  function submit(next: Partial<SearchParams>) {
    if (!draftQuery.trim()) return;
    setParams({
      query: draftQuery.trim(),
      mode,
      ...(draftYearFrom && { year_from: Number(draftYearFrom) }),
      ...(draftYearTo && { year_to: Number(draftYearTo) }),
      ...next,
    });
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit({});
  }

  function pickMode(m: SearchMode) {
    setMode(m);
    // Re-run immediately so the toggle re-ranks in place — that IS the demo.
    if (params) setParams({ ...params, mode: m });
  }

  return (
    <div className="space-y-4">
      <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-2">
        <label className="flex-1 text-sm">
          <span className="block text-slate-600">Query</span>
          <input
            type="text"
            value={draftQuery}
            onChange={(e) => setDraftQuery(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2
                       focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
        </label>
        <label className="text-sm">
          <span className="block text-slate-600">Year from</span>
          <input
            type="text"
            value={draftYearFrom}
            onChange={(e) => setDraftYearFrom(e.target.value)}
            className="mt-1 w-24 rounded-lg border border-slate-300 px-3 py-2"
          />
        </label>
        <label className="text-sm">
          <span className="block text-slate-600">Year to</span>
          <input
            type="text"
            value={draftYearTo}
            onChange={(e) => setDraftYearTo(e.target.value)}
            className="mt-1 w-24 rounded-lg border border-slate-300 px-3 py-2"
          />
        </label>
        <div className="flex items-center gap-3">
          {MODES.map((m) => (
            <label key={m} className="flex items-center gap-1 text-sm text-slate-700">
              <input
                type="radio"
                name="mode"
                checked={mode === m}
                onChange={() => pickMode(m)}
              />
              {m}
            </label>
          ))}
        </div>
        <button
          type="submit"
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white
                     transition-colors hover:bg-slate-700"
        >
          Search
        </button>
      </form>

      {isFetching && <p className="text-sm text-slate-500">Searching…</p>}
      {error && (
        <p role="alert" className="text-sm text-rose-700">
          Search failed: {(error as Error).message}
        </p>
      )}
      {data && !isFetching && (
        <>
          <p className="text-sm text-slate-600">
            {data.results.length} results · {data.took_ms} ms
          </p>
          {data.results.length === 0 && <p className="text-slate-500">No papers matched.</p>}
          <ul className="space-y-3">
            <AnimatePresence initial={false} mode="popLayout">
              {data.results.map((r, i) => (
                <ResultCard key={r.id} result={r} stagger={staggerFor(i, fromEmpty)} />
              ))}
            </AnimatePresence>
          </ul>
        </>
      )}
    </div>
  );
}
