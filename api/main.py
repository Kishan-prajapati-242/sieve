"""FastAPI entrypoint.

Holds only app wiring: lifespan (logging + pool), the request-ID
middleware, and router registration. Endpoint logic lives with its module
(api/search/routes.py), so this file stays readable as a map of the API
surface.
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from api.db.pool import close_pool, get_pool
from api.logs import request_id_var, setup_logging
from api.search.routes import router as search_router
from api.stats import router as stats_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
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
app.include_router(search_router)
app.include_router(stats_router)


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Honor a caller-supplied ID (so a browser trace and our logs can meet),
    # otherwise mint one. The ContextVar makes it visible to every log line
    # emitted while this request is in flight.
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness plus a DB round-trip: proves pool -> Postgres actually works."""
    with get_pool().connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
