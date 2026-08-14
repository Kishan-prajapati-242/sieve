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
import { useReducedMotion } from "motion/react";
import { search, type SearchMode, type SearchParams, type SearchResponse } from "./api";
import { ResultCard } from "./ResultCard";
import { arrivalDelay } from "./motion";
import { headerCount, presentationSteps } from "./presentation";
import { useDelayedFlag } from "./useDelayedFlag";

// Long enough that a warm query (~30ms) never flashes, short enough that a
// cold one (1,611ms measured) does not read as a frozen page.
const STALE_AFTER_MS = 200;

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

  const { data, error, isFetching, isPlaceholderData } = useQuery({
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
  // The previous RENDERED result set, snapshotted during render rather than
  // in an effect. An effect that mutates this ref races the effect that
  // reads it — under StrictMode the scheduler re-ran after the ref was
  // already updated, computed zero arrivals, and jumped the header straight
  // to the final count (measured: header hit 20 at 109ms against a schedule
  // that should have taken ~400ms). Deriving it here makes the diff a
  // function of `data`, so both consumers see the same one.
  const snap = useRef<{ key: SearchResponse | null; prev: number[] }>({ key: null, prev: [] });
  const shown = data?.results ?? [];
  if (data && snap.current.key !== data) {
    snap.current = { key: data, prev: snap.current.key?.results.map((r) => r.id) ?? [] };
  }
  const prevSet = new Set(snap.current.prev);
  const arriving = shown.filter((r) => !prevSet.has(r.id)).map((r) => r.id);
  const survivors = shown.filter((r) => prevSet.has(r.id)).map((r) => r.id);
  const hasSurvivors = survivors.length > 0;
  const arrivalIndex = new Map(arriving.map((id, i) => [id, i]));
  const finalOrder = shown.map((r) => r.id);

  // ONE schedule drives both the rows and the header: each row's delay comes
  // from arrivalDelay(), and the header steps through presentationSteps() on
  // those same delays. The count therefore cannot describe rows that have
  // not begun appearing — the defect frames caught and arithmetic could not,
  // because a true count of the response contradicted no other number, only
  // the moment (findings.md 2026-08-14).
  const reduce = useReducedMotion();
  const [presentedCount, setPresentedCount] = useState(0);
  useEffect(() => {
    if (!data) return;
    const steps = presentationSteps(survivors, arriving, finalOrder);
    if (reduce || arriving.length === 0) {
      setPresentedCount(finalOrder.length);
      return;
    }
    setPresentedCount(steps[0] ? headerCount(steps[0]) : 0);
    const timers = arriving.map((_id, i) =>
      setTimeout(
        () => setPresentedCount((n) => Math.min(n + 1, finalOrder.length)),
        arrivalDelay(i, arriving.length, hasSurvivors) * 1000,
      ),
    );
    return () => timers.forEach(clearTimeout);
    // Keyed on `data` alone: the derived arrays are recomputed from it.
  }, [data, reduce]);

  // isPlaceholderData is the precise signal: true exactly while the previous
  // mode's rows stand in for a result set still in flight. isFetching would
  // also fire on a background refetch of data already on screen.
  const stale = useDelayedFlag(isPlaceholderData, STALE_AFTER_MS);

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
      {/* Indeterminate: the server reports its timings only once it answers,
          so there is no honest progress fraction to show. */}
      <div className="h-0.5 overflow-hidden" aria-hidden="true">
        {stale && <div className="h-full w-full animate-pulse bg-blue-500/60" />}
      </div>
      {error && (
        <p role="alert" className="text-sm text-rose-700">
          Search failed: {(error as Error).message}
        </p>
      )}
      {data && (
        <>
          <div className="flex flex-wrap items-baseline justify-between gap-2 py-1 text-sm">
            <p className="text-slate-600">
              <span className="font-medium text-slate-900">
                {Math.min(presentedCount, data.results.length)}
              </span>{" "}
              shown of{" "}
              <span className="font-medium text-slate-900">
                {data.total.value.toLocaleString()}
              </span>{" "}
              {totalLabel(data.total.kind)}
              {stale && <span className="ml-2 text-slate-400">· searching…</span>}
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
          <ul
            aria-busy={stale}
            className={
              "divide-y divide-slate-100 border-t border-slate-100 transition-opacity" +
              // The rows stay MOUNTED while dimmed — unmounting them is what
              // broke the layout animation in the first place. This only
              // says "these are the previous results".
              (stale ? " opacity-40" : " opacity-100")
            }
          >
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
