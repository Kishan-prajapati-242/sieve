// The one place the frontend knows the API's shape. These types mirror the
// Pydantic models in api/search/routes.py and api/collections/routes.py by
// hand — duplicating the shapes was chosen over an OpenAPI codegen step,
// which would drag a generator into the toolchain. The surface is now two
// routers; if it grows a third, revisit.

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
  bm25_rank: number | null;
  vector_rank: number | null;
  sources: string[] | null;
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

export type SearchMode = "bm25" | "vector" | "hybrid";

export interface SearchParams {
  query: string;
  mode: SearchMode;
  year_from?: number;
  year_to?: number;
}

export async function search(params: SearchParams): Promise<SearchResponse> {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...params, k: 20 }),
  });
  if (!res.ok) {
    throw new Error(`search failed: HTTP ${res.status}`);
  }
  return res.json() as Promise<SearchResponse>;
}


// ---------------------------------------------------------------- collections
//
// Screening endpoints. PUT is an upsert on (collection_id, paper_id), so
// changing a decision is the same request as making it — the UI never has to
// distinguish "decide" from "re-decide", and clicking include then exclude
// leaves one row rather than two and a tiebreak.

export type Decision = "include" | "exclude" | "maybe";

export interface CollectionSummary {
  id: number;
  name: string;
  question: string | null;
  created_at: string;
  screened: number;
  included: number;
  excluded: number;
  maybe: number;
}

/** A paper as it appears inside a collection: the paper fields plus the
 *  decision made about it. Mirrors PAPERS_SQL in api/collections/routes.py. */
export interface ScreenedPaper {
  id: number;
  doi: string | null;
  title: string;
  abstract: string | null;
  year: number | null;
  venue: string | null;
  citation_count: number;
  is_retracted: boolean;
  authors: string[] | null;
  arxiv_id: string | null;
  pubmed_id: string | null;
  decision: Decision;
  note: string | null;
  decided_at: string;
}

export interface CollectionDetail {
  id: number;
  name: string;
  question: string | null;
  created_at: string;
  papers: ScreenedPaper[];
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    // FastAPI puts the reason in `detail`; surface it rather than a bare status.
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export async function listCollections(): Promise<CollectionSummary[]> {
  return json(await fetch("/api/collections"));
}

export async function createCollection(
  name: string,
  question?: string,
): Promise<CollectionSummary> {
  return json(
    await fetch("/api/collections", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, question: question || null }),
    }),
  );
}

export async function getCollection(
  id: number,
  decision?: Decision,
): Promise<CollectionDetail> {
  const qs = decision ? `?decision=${decision}` : "";
  return json(await fetch(`/api/collections/${id}${qs}`));
}

export async function screen(
  collectionId: number,
  paperId: number,
  decision: Decision,
  note?: string,
): Promise<void> {
  await json(
    await fetch(`/api/collections/${collectionId}/screenings/${paperId}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ decision, note: note || null }),
    }),
  );
}

export async function unscreen(collectionId: number, paperId: number): Promise<void> {
  const res = await fetch(`/api/collections/${collectionId}/screenings/${paperId}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 404) throw new Error(`${res.status} ${res.statusText}`);
}

/** A plain href, not a fetch: the browser should download the file, and
 *  content-disposition already names it. */
export function exportUrl(collectionId: number, decision: Decision = "include"): string {
  return `/api/collections/${collectionId}/export.bib?decision=${decision}`;
}
