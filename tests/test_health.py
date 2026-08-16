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
    body = resp.json()
    assert body["status"] == "ok"
    # Readiness of the embedding model travels with liveness, so a deploy
    # missing the downloaded artifact is visible without a 500.
    assert "embed_model_present" in body
    assert "bm25" in body["modes_available"]

    # Lifespan exit must reset the module global: a pool left behind after
    # close would be handed out, already closed, to every later caller.
    assert pool_module._pool is None
    # And close_pool is documented as safe to call on an already-closed pool.
    close_pool()
