"""FastAPI entrypoint.

Holds only app wiring: lifespan (pool open/close) and health. Search, ingest,
and stats routers are added by their own modules in later phases, so this file
stays readable as a map of the API surface.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.db.pool import close_pool, get_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # wait() blocks until min_size connections are live, so a misconfigured
    # DATABASE_URL fails the process at startup instead of on the first
    # request. The finally is load-bearing: wait() closes the pool before
    # raising on timeout, and close_pool() resets the module global so the
    # next startup builds a fresh pool instead of reusing the closed one.
    try:
        get_pool().wait(timeout=10.0)
        yield
    finally:
        close_pool()


app = FastAPI(title="sieve", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness plus a DB round-trip: proves pool -> Postgres actually works."""
    with get_pool().connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
