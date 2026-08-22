/** Where the API lives.
 *
 *  Empty in dev (Vite proxies same-origin). In production the frontend is on
 *  Vercel and the API on Render, so calls need the absolute origin — and
 *  every one of them already sends credentials:"include", which is what makes
 *  the cross-site session cookie travel.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

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

/** How many papers the query reached, and what that number MEANS. The kind
 *  is not optional decoration: bm25's value is a match count, vector's is
 *  the whole embedded corpus, hybrid's is the fused candidate pool at the
 *  configured depth. Rendering the integer without its label would report
 *  three different quantities under one word. */
export interface Total {
  value: number;
  kind: "matches" | "ranked" | "candidates";
}

export interface SearchResponse {
  query: string;
  mode: string;
  took_ms: number;
  timings: SearchTimings;
  ef_search: number | null;
  total: Total;
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
  const res = await fetch(`${API_BASE}/api/search`, {
    credentials: "include",
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
  /** YOUR counts, not the team's. Aggregating every screener's decisions here
   *  leaked judgement in bulk — a card reading "12 included" when you had
   *  decided 5 told you what colleagues concluded about 7 papers you had not
   *  opened. See docs/plans/screening-read-audit.md. */
  screened: number;
  included: number;
  excluded: number;
  maybe: number;
  /** Volume, not judgement — safe to show under blind screening, and needed
   *  for coordination. */
  team_screened: number;
  screener_count: number;
  screening_mode: "solo" | "blind";
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
  return json(await fetch(`${API_BASE}/api/collections`, { credentials: "include" }));
}

export async function createCollection(
  name: string,
  question?: string,
): Promise<CollectionSummary> {
  return json(
    await fetch(`${API_BASE}/api/collections`, {
    credentials: "include",
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
  return json(await fetch(`${API_BASE}/api/collections/${id}${qs}`, { credentials: "include" }));
}

export async function screen(
  collectionId: number,
  paperId: number,
  decision: Decision,
  note?: string,
): Promise<void> {
  await json(
    await fetch(`${API_BASE}/api/collections/${collectionId}/screenings/${paperId}`, {
    credentials: "include",
    method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ decision, note: note || null }),
    }),
  );
}

export async function unscreen(collectionId: number, paperId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/collections/${collectionId}/screenings/${paperId}`, {
    credentials: "include",
    method: "DELETE",
  });
  if (!res.ok && res.status !== 404) throw new Error(`${res.status} ${res.statusText}`);
}

/** A plain href, not a fetch: the browser should download the file, and
 *  content-disposition already names it. */
export function exportUrl(collectionId: number, decision: Decision = "include"): string {
  return `${API_BASE}/api/collections/${collectionId}/export.bib?decision=${decision}`;
}

/** CSV of the whole screening, decisions included.
 *
 *  No `decision` filter by default, unlike BibTeX: the two exports answer
 *  different questions. BibTeX is "the citations that made the cut"; CSV is
 *  "here is the screening", and one with the exclusions stripped out is not a
 *  screening record.
 */
export function csvExportUrl(collectionId: number): string {
  return `${API_BASE}/api/collections/${collectionId}/export.csv`;
}


export interface CorpusStats {
  papers: number;
  retracted_papers: number;
  source_records: number;
}

/** The live corpus size.
 *
 *  Typed into the hero, "183,167" becomes wrong the moment the PubMed pull
 *  lands and takes it to ~200,000 — a number in a headline describing a
 *  corpus that no longer exists is the same defect this project has spent
 *  weeks chasing, on its most visible surface. So the page asks the API.
 */
export async function fetchStats(): Promise<CorpusStats> {
  const res = await fetch(`${API_BASE}/api/stats`, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ============================================================ COLLABORATION ==
//
// Every function here maps to one audited endpoint. What comes back is already
// scoped by role on the server (docs/plans/screening-read-audit.md) — the
// client never filters for privacy, because a filter in the browser is a
// suggestion, not a boundary.

export type Role = "owner" | "resolver" | "screener" | "viewer";

export interface Member {
  user_id: number;
  email: string;
  role: Role;
  joined_at: string;
}

export interface MemberProgress {
  user_id: number;
  email: string;
  role: Role;
  /** Volume only. Never a decision breakdown — see the audit. */
  screened: number;
}

export interface MembersResponse {
  members: Member[];
  your_role: Role;
  progress: MemberProgress[];
}

export async function getMembers(collectionId: number): Promise<MembersResponse> {
  return json(
    await fetch(`${API_BASE}/api/collections/${collectionId}/members`, {
      credentials: "include",
    }),
  );
}

export async function createInvite(
  collectionId: number,
  role: Exclude<Role, "owner">,
): Promise<{ token: string; role: Role }> {
  return json(
    await fetch(`${API_BASE}/api/collections/${collectionId}/invites`, {
      credentials: "include",
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ role }),
    }),
  );
}

export async function acceptInvite(token: string): Promise<{ collection_id: number }> {
  return json(
    await fetch(`${API_BASE}/api/collections/invites/${token}/accept`, {
      credentials: "include",
      method: "POST",
    }),
  );
}

export async function removeMember(collectionId: number, memberId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/collections/${collectionId}/members/${memberId}`, {
    credentials: "include",
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `${res.status}`);
  }
}

/** An invite link the owner can paste anywhere.
 *
 *  Built against the FRONTEND origin, not the API's: the recipient needs a page
 *  that can sign them in first, and /api/... would hand them raw JSON.
 */
export function inviteLink(token: string): string {
  return `${window.location.origin}/invite/${token}`;
}

export interface OtherCall {
  user_id: number;
  email: string;
  decision: Decision;
  /** Present ONLY at reconciliation. The server omits the column entirely
   *  otherwise, so this being undefined is a fact about the response, not a
   *  client-side redaction. */
  note?: string;
  decided_at: string;
}

export interface PaperScreening {
  mine: { decision: Decision; note: string | null; decided_at: string } | null;
  others: OtherCall[];
  notes_visible: boolean;
  /** True when the caller has not decided yet in a blind collection: no
   *  decisions, no notes, and no count — a count is itself a signal. */
  blinded?: boolean;
}

export async function getPaperScreening(
  collectionId: number,
  paperId: number,
): Promise<PaperScreening> {
  return json(
    await fetch(
      `${API_BASE}/api/collections/${collectionId}/papers/${paperId}/screening`,
      { credentials: "include" },
    ),
  );
}

export interface Conflict {
  paper_id: number;
  title: string;
  screener_count: number;
  distinct_decisions: number;
  decisions: Decision[];
}

export async function getConflicts(
  collectionId: number,
): Promise<{ conflicts: Conflict[]; scoped: boolean }> {
  return json(
    await fetch(`${API_BASE}/api/collections/${collectionId}/conflicts`, {
      credentials: "include",
    }),
  );
}

export async function getConflictDetail(
  collectionId: number,
  paperId: number,
): Promise<PaperScreening> {
  return json(
    await fetch(`${API_BASE}/api/collections/${collectionId}/conflicts/${paperId}`, {
      credentials: "include",
    }),
  );
}

export async function resolveConflict(
  collectionId: number,
  paperId: number,
  decision: Decision,
  note?: string,
): Promise<{ decision: Decision; self_resolved: boolean }> {
  return json(
    await fetch(`${API_BASE}/api/collections/${collectionId}/conflicts/${paperId}`, {
      credentials: "include",
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ decision, note: note || null }),
    }),
  );
}

export interface PairwiseKappa {
  user_a: number;
  user_b: number;
  kappa: number | null;
  n: number;
  observed_agreement?: number;
  undefined: string | null;
}

export interface AgreementReport {
  screened_papers: number;
  multiply_screened: number;
  raters: number;
  alpha: { alpha: number | null; n_items: number; undefined: string | null };
  pairwise_cohen: PairwiseKappa[];
  method: Record<string, unknown>;
}

export async function getAgreement(collectionId: number): Promise<AgreementReport> {
  return json(
    await fetch(`${API_BASE}/api/collections/${collectionId}/agreement`, {
      credentials: "include",
    }),
  );
}

export type Phase = "screening" | "review" | "closed";

export interface PhaseInfo {
  phase: Phase;
  screening_mode: "solo" | "blind";
  can_change: boolean;
  reveal_preview: { papers: number; decisions: number; screeners: number; conflicts: number };
  history: {
    from_phase: Phase;
    to_phase: Phase;
    changed_by: string;
    changed_at: string;
    papers_revealed: number;
    decisions_revealed: number;
  }[];
}

export async function getPhase(collectionId: number): Promise<PhaseInfo> {
  return json(
    await fetch(`${API_BASE}/api/collections/${collectionId}/phase`, {
      credentials: "include",
    }),
  );
}

export async function setPhase(collectionId: number, phase: Phase): Promise<unknown> {
  return json(
    await fetch(`${API_BASE}/api/collections/${collectionId}/phase`, {
      credentials: "include",
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ phase }),
    }),
  );
}
