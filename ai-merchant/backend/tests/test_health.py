"""Phase 1 tests - health endpoint."""

from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_health_endpoint():
    """Health endpoint must return 200 with expected structure."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok at v1"


def test_root_endpoint():
    """Root endpoint must return service info."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Vend.ai"
    assert data["version"] == "0.1.0"