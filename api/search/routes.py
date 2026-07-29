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
from api.search.bm25 import search_bm25

logger = logging.getLogger("sieve.search")

router = APIRouter(prefix="/api")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["bm25"] = "bm25"
    k: int = Field(default=20, ge=1, le=100)
    year_from: int | None = Field(default=None, ge=1800, le=2100)
    year_to: int | None = Field(default=None, ge=1800, le=2100)


class SearchResult(BaseModel):
    rank: int
    score: float
    id: int
    doi: str | None
    title: str
    abstract: str | None
    year: int | None
    venue: str | None
    citation_count: int


class SearchResponse(BaseModel):
    query: str
    mode: str
    took_ms: float
    results: list[SearchResult]


@router.post("/search")
def search(req: SearchRequest) -> SearchResponse:
    start = time.perf_counter()
    with get_pool().connection() as conn:
        rows = search_bm25(
            conn, query=req.query, k=req.k, year_from=req.year_from, year_to=req.year_to
        )
    took_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        "search",
        extra={
            "extra_fields": {
                "mode": req.mode,
                "query": req.query,
                "k": req.k,
                "results": len(rows),
                "took_ms": took_ms,
            }
        },
    )
    return SearchResponse(
        query=req.query,
        mode=req.mode,
        took_ms=took_ms,
        results=[SearchResult(rank=i, **row) for i, row in enumerate(rows, start=1)],
    )
