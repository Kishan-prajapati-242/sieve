"""POST /api/search: request/response models and wiring around bm25.py.

Thin by design — validation lives in the Pydantic models, ranking lives in
the SQL. mode is a Literal with one value for now; Phase 2 widens it to
vector and hybrid, and clients written against this contract keep working.
rank is assigned here (enumerate over an already-ordered result set)
rather than in SQL: a window function would re-state the ORDER BY just to
number rows Python can number for free. Phase 2's fusion query computes
real per-ranker ranks in SQL, where they participate in scoring.
"""

import logging
import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.db.pool import get_pool
from api.embed.runtime import embed_query
from api.search.bm25 import search_bm25
from api.search.fusion import HYBRID_DEFAULT_EF_SEARCH, search_hybrid
from api.search.vector import DEFAULT_EF_SEARCH, search_vector

logger = logging.getLogger("sieve.search")

router = APIRouter(prefix="/api")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["bm25", "vector", "hybrid"] = "bm25"
    k: int = Field(default=20, ge=1, le=100)
    year_from: int | None = Field(default=None, ge=1800, le=2100)
    year_to: int | None = Field(default=None, ge=1800, le=2100)
    # hnsw.ef_search (vector/hybrid): candidate-list size, the
    # recall/latency dial. Bounds are pgvector's own. None means the mode
    # default (vector 40, hybrid 600 — DECISION-2e); an explicit value is
    # honored but hybrid still auto-raises to >= depth, because ef < depth
    # silently truncates the vector candidate list.
    ef_search: int | None = Field(default=None, ge=1, le=1000)
    # Hybrid candidate depth: top-N fetched from EACH ranker before RRF
    # (DECISION-2e, from the joint + fixed-depth sweeps).
    depth: int = Field(default=200, ge=10, le=1000)


class SearchResult(BaseModel):
    rank: int
    score: float
    id: int
    doi: str | None
    title: str
    authors: list[str] | None
    abstract: str | None
    year: int | None
    venue: str | None
    citation_count: int
    # Surfaced, never filtered (DECISION-1c): the UI shows a retraction
    # warning; a screening tool must let reviewers exclude these on purpose.
    is_retracted: bool
    # Hybrid-mode score breakdown (null in other modes): each ranker's rank
    # for this paper (null = that ranker missed it at this depth), and which
    # rankers retrieved it. Product feature: fusion is checkable by eye.
    bm25_rank: int | None = None
    vector_rank: int | None = None
    sources: list[str] | None = None


class SearchTimings(BaseModel):
    """took_ms decomposed, so a latency number states what it includes
    (2026-07-31, after the baseline's window ambiguity — findings.md).

    embed_ms is null for bm25 (no query embedding); Phase 2's vector mode
    fills it, and it is the fixed floor no index can reduce. serialize_ms
    covers response-model construction; the framework's JSON encoding runs
    after the handler returns and is outside took_ms entirely.
    """

    embed_ms: float | None
    retrieve_ms: float
    serialize_ms: float


class SearchResponse(BaseModel):
    query: str
    mode: str
    took_ms: float
    timings: SearchTimings
    # The ef_search actually used — recorded per query so a latency or
    # recall observation is reproducible. Null for bm25.
    ef_search: int | None
    results: list[SearchResult]


@router.post("/search")
def search(req: SearchRequest) -> SearchResponse:
    start = time.perf_counter()
    embed_ms: float | None = None
    ef_search: int | None = None

    if req.mode in ("vector", "hybrid"):
        # embed_query applies the bge instruction prefix itself (the
        # contract is enforced at the choke point, not per call site).
        query_vec = embed_query(req.query)
        embed_done = time.perf_counter()
        embed_ms = round((embed_done - start) * 1000, 1)
        with get_pool().connection() as conn:
            if req.mode == "hybrid":
                # ef < depth silently truncates the vector candidate list.
                base_ef = HYBRID_DEFAULT_EF_SEARCH if req.ef_search is None else req.ef_search
                ef_search = max(base_ef, req.depth)
                rows = search_hybrid(
                    conn,
                    query=req.query,
                    query_vec=query_vec,
                    k=req.k,
                    depth=req.depth,
                    year_from=req.year_from,
                    year_to=req.year_to,
                    ef_search=ef_search,
                )
                for row in rows:
                    rank_pairs = (("bm25", row["bm25_rank"]), ("vector", row["vector_rank"]))
                    row["sources"] = [name for name, rank in rank_pairs if rank is not None]
            else:
                ef_search = DEFAULT_EF_SEARCH if req.ef_search is None else req.ef_search
                rows = search_vector(
                    conn,
                    query_vec=query_vec,
                    k=req.k,
                    year_from=req.year_from,
                    year_to=req.year_to,
                    ef_search=ef_search,
                )
        retrieve_ms = round((time.perf_counter() - embed_done) * 1000, 1)
    else:
        with get_pool().connection() as conn:
            rows = search_bm25(
                conn, query=req.query, k=req.k, year_from=req.year_from, year_to=req.year_to
            )
        retrieve_ms = round((time.perf_counter() - start) * 1000, 1)

    retrieve_done = time.perf_counter()
    results = [SearchResult(rank=i, **row) for i, row in enumerate(rows, start=1)]
    serialize_done = time.perf_counter()

    timings = SearchTimings(
        embed_ms=embed_ms,
        retrieve_ms=retrieve_ms,
        serialize_ms=round((serialize_done - retrieve_done) * 1000, 1),
    )
    took_ms = round((serialize_done - start) * 1000, 1)
    logger.info(
        "search",
        extra={
            "extra_fields": {
                "mode": req.mode,
                "query": req.query,
                "k": req.k,
                "results": len(rows),
                "took_ms": took_ms,
                "embed_ms": timings.embed_ms,
                "retrieve_ms": timings.retrieve_ms,
                "serialize_ms": timings.serialize_ms,
                "ef_search": ef_search,
                "depth": req.depth if req.mode == "hybrid" else None,
            }
        },
    )
    return SearchResponse(
        query=req.query,
        mode=req.mode,
        took_ms=took_ms,
        timings=timings,
        ef_search=ef_search,
        results=results,
    )
