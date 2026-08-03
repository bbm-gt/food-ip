from fastapi.testclient import TestClient

from ..main import app


def test_health() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["ffmpeg"]
