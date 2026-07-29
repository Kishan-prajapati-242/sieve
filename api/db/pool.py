"""Process-wide psycopg connection pool.

One pool per process, created lazily on first use rather than at import time:
importing this module must be side-effect free so tests and scripts can set
DATABASE_URL before the first connection is opened.

Alternative rejected: opening a connection per request. TCP + auth on every
query would dominate search latency, and the pool is the single place to cap
total connections — Postgres max_connections becomes the real shared resource
once the worker processes exist in Phase 3.
"""

import os

from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Return the process-wide pool, creating and opening it on first call."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=os.environ["DATABASE_URL"],
            min_size=1,
            max_size=10,
            open=True,
        )
    return _pool


def close_pool() -> None:
    """Close the pool if open. Safe to call twice; tests rely on that."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
