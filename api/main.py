"""FastAPI entrypoint.

Holds only app wiring: lifespan (logging + pool), the request-ID
middleware, and router registration. Endpoint logic lives with its module
(api/search/routes.py), so this file stays readable as a map of the API
surface.
"""

import logging
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth.routes import router as auth_router
from api.collections.routes import router as collections_router
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

# CORS with credentials. In production the frontend is on Cloudflare Pages and
# the API on Render — different origins — so the session cookie only travels
# if both allow_credentials and an EXPLICIT origin list are set. The wildcard
# "*" is not merely discouraged here, it is refused by browsers whenever
# credentials are included, so an origin list is mandatory rather than
# cautious. Read from the environment so no deployment URL is hardcoded.
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "x-request-id"],
    )
app.include_router(auth_router)
app.include_router(search_router)
app.include_router(stats_router)
app.include_router(collections_router)


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


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """Return a JSON 500 THROUGH the middleware stack.

    An exception that escapes to Starlette's ServerErrorMiddleware produces a
    bare text/plain 500 generated ABOVE the CORS middleware, so the response
    carries no Access-Control-Allow-Origin header. The browser then reports a
    CORS error, and the actual server fault is invisible — which is exactly
    what happened on the first deploy: a missing embedding model produced a
    500 that reached the console as "blocked by CORS policy".

    Handling it here keeps the response inside the stack, so CORS headers are
    applied and the client sees the real status and a request id to grep for.
    """
    log = logging.getLogger("sieve.error")
    log.exception("unhandled_exception", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id_var.get("")},
    )


@app.get("/healthz")
def healthz() -> dict[str, object]:
    """Liveness plus a DB round-trip: proves pool -> Postgres actually works."""
    with get_pool().connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
