"""Health endpoint round-trips the database through the pool."""

from fastapi.testclient import TestClient

from api.main import app


def test_healthz_ok() -> None:
    # Context manager runs the lifespan, so this also covers pool startup.
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
