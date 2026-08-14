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
import { useEffect, useRef, useState, type FormEvent } from "react";
import { search, type SearchMode, type SearchParams, type SearchResponse } from "./api";
import { ResultCard } from "./ResultCard";
import { arrivalDelay } from "./motion";

const MODES: SearchMode[] = ["bm25", "vector", "hybrid"];

/** One label per meaning. A bare "of N" would be the unlabeled-number
 *  failure on the most visible surface in the app. */
function totalLabel(kind: SearchResponse["total"]["kind"]): string {
  if (kind === "matches") return "keyword matches";
  if (kind === "ranked") return "papers ranked";
  return "fused candidates";
}

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
    // Keep the previous mode's rows mounted while the next mode loads.
    //
    // Without this the list unmounts on every mode change (the old render
    // gated on `!isFetching`), React creates fresh <li> nodes, and `layout`
    // has nothing to move — measured 2026-08-14: 0 of 20 nodes survived a
    // toggle, so every motion touch was invisible on the marquee
    // interaction. Layout animation moves EXISTING DOM; it cannot move a
    // node that was just created.
    placeholderData: (previous) => previous,
  });

  // Which rows are ARRIVING versus surviving, so arrivals can be gated on
  // the survivors finishing their move. Held in a ref because this is a
  // comparison against the previously RENDERED list, not against state.
  const prevIds = useRef<Set<number>>(new Set());
  const shown = data?.results ?? [];
  const arriving = shown.filter((r) => !prevIds.current.has(r.id)).map((r) => r.id);
  const hasSurvivors = shown.some((r) => prevIds.current.has(r.id));
  const arrivalIndex = new Map(arriving.map((id, i) => [id, i]));
  useEffect(() => {
    if (data) prevIds.current = new Set(data.results.map((r) => r.id));
  }, [data]);

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

      {isFetching && !data && <p className="text-sm text-slate-500">Searching…</p>}
      {error && (
        <p role="alert" className="text-sm text-rose-700">
          Search failed: {(error as Error).message}
        </p>
      )}
      {data && (
        <>
          <div className="flex flex-wrap items-baseline justify-between gap-2 py-1 text-sm">
            <p className="text-slate-600">
              <span className="font-medium text-slate-900">{data.results.length}</span> shown of{" "}
              <span className="font-medium text-slate-900">
                {data.total.value.toLocaleString()}
              </span>{" "}
              {totalLabel(data.total.kind)}
            </p>
            {/* The measurement work, in the product rather than only in bench/. */}
            <p className="tabular-nums text-xs text-slate-400" title="server-side timings">
              {data.took_ms} ms
              {data.timings.embed_ms !== null && <> · embed {data.timings.embed_ms}</>}
              {" · retrieve "}
              {data.timings.retrieve_ms}
              {" · serialize "}
              {data.timings.serialize_ms}
              {data.ef_search !== null && <> · ef {data.ef_search}</>}
            </p>
          </div>
          {data.results.length === 0 && <p className="text-slate-500">No papers matched.</p>}
          <ul className="divide-y divide-slate-100 border-t border-slate-100">
            <AnimatePresence initial={false} mode="popLayout">
              {data.results.map((r) => (
                <ResultCard
                  key={r.id}
                  result={r}
                  delay={
                    arrivalIndex.has(r.id)
                      ? arrivalDelay(arrivalIndex.get(r.id)!, arriving.length, hasSurvivors)
                      : 0
                  }
                />
              ))}
            </AnimatePresence>
          </ul>
        </>
      )}
    </div>
  );
}
