// The one place the frontend knows the API's shape. These types mirror the
// Pydantic models in api/search/routes.py by hand — duplicating ~20 lines was
// chosen over an OpenAPI codegen step, which would drag a generator into the
// toolchain for a single endpoint. Revisit if Phase 2 grows the surface.

export interface SearchResult {
  rank: number;
  score: number;
  id: number;
  doi: string | null;
  title: string;
  authors: string[] | null;
  abstract: string | null;
  year: number | null;
  venue: string | null;
  citation_count: number;
  is_retracted: boolean;
}

export interface SearchTimings {
  embed_ms: number | null;
  retrieve_ms: number;
  serialize_ms: number;
}

export interface SearchResponse {
  query: string;
  mode: string;
  took_ms: number;
  timings: SearchTimings;
  ef_search: number | null;
  results: SearchResult[];
}

export interface SearchParams {
  query: string;
  year_from?: number;
  year_to?: number;
}

export async function search(params: SearchParams): Promise<SearchResponse> {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...params, mode: "bm25", k: 20 }),
  });
  if (!res.ok) {
    throw new Error(`search failed: HTTP ${res.status}`);
  }
  return res.json() as Promise<SearchResponse>;
}
