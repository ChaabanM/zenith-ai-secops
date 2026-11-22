from app.core.database import get_db
from app.main import app


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["environment"] == "test"
    assert body["version"]


def test_health_reports_degraded_when_db_unavailable(client):
    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_cors_allows_frontend_origin(client):
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_unknown_route_uses_error_envelope(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}
