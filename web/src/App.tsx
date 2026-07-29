// Search page: form -> POST /api/search -> result list. The form edits draft
// state and only submit copies it into `params`; useQuery keys on `params`,
// so typing never fires requests and TanStack Query caches by exact
// (query, year_from, year_to) — hitting Back to a previous search is free.
import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { search, type SearchParams } from "./api";
import { ResultCard } from "./ResultCard";

export function App() {
  const [draftQuery, setDraftQuery] = useState("");
  const [draftYearFrom, setDraftYearFrom] = useState("");
  const [draftYearTo, setDraftYearTo] = useState("");
  const [params, setParams] = useState<SearchParams | null>(null);

  const { data, error, isFetching } = useQuery({
    queryKey: ["search", params],
    queryFn: () => search(params!),
    enabled: params !== null,
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!draftQuery.trim()) return;
    setParams({
      query: draftQuery.trim(),
      // Empty inputs mean "no bound" — the API's param-is-NULL filter.
      ...(draftYearFrom && { year_from: Number(draftYearFrom) }),
      ...(draftYearTo && { year_to: Number(draftYearTo) }),
    });
  }

  return (
    <main className="max-w-3xl mx-auto p-4 space-y-4">
      <h1 className="text-xl font-bold">Sieve</h1>
      <form onSubmit={onSubmit} className="flex flex-wrap gap-2 items-end">
        <label className="flex-1 min-w-64">
          <span className="block text-sm">Query</span>
          <input
            type="text"
            value={draftQuery}
            onChange={(e) => setDraftQuery(e.target.value)}
            className="w-full border border-gray-400 rounded px-2 py-1"
            maxLength={500}
          />
        </label>
        <label>
          <span className="block text-sm">Year from</span>
          <input
            type="number"
            value={draftYearFrom}
            onChange={(e) => setDraftYearFrom(e.target.value)}
            className="w-24 border border-gray-400 rounded px-2 py-1"
            min={1800}
            max={2100}
          />
        </label>
        <label>
          <span className="block text-sm">Year to</span>
          <input
            type="number"
            value={draftYearTo}
            onChange={(e) => setDraftYearTo(e.target.value)}
            className="w-24 border border-gray-400 rounded px-2 py-1"
            min={1800}
            max={2100}
          />
        </label>
        <button type="submit" className="border border-gray-600 rounded px-3 py-1">
          Search
        </button>
      </form>

      {isFetching && <p>Searching…</p>}
      {error && <p role="alert">Search failed: {(error as Error).message}</p>}
      {data && !isFetching && (
        <>
          <p className="text-sm text-gray-600">
            {data.results.length} results · {data.took_ms} ms
          </p>
          {data.results.length === 0 && <p>No papers matched.</p>}
          <ul className="space-y-2">
            {data.results.map((r) => (
              <ResultCard key={r.id} result={r} />
            ))}
          </ul>
        </>
      )}
    </main>
  );
}

