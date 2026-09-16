from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_reports_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "neon-agent"


def test_cors_allows_the_static_site():
    r = client.get("/api/health", headers={"Origin": "https://example.vercel.app"})
    assert r.headers["access-control-allow-origin"] in ("*", "https://example.vercel.app")
