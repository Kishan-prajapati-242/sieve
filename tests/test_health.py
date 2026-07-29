"""Health endpoint round-trips the database through the pool."""

from fastapi.testclient import TestClient

import api.db.pool as pool_module
from api.db.pool import close_pool
from api.main import app


def test_healthz_ok_and_pool_lifecycle() -> None:
    # Context manager runs the lifespan, so this also covers pool startup.
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # Lifespan exit must reset the module global: a pool left behind after
    # close would be handed out, already closed, to every later caller.
    assert pool_module._pool is None
    # And close_pool is documented as safe to call on an already-closed pool.
    close_pool()
