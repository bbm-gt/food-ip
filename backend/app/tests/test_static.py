from fastapi.testclient import TestClient

from ..main import app


def test_root_and_health_remain_available() -> None:
    client = TestClient(app)

    root = client.get("/")
    assert root.status_code == 200
    content_type = root.headers.get("content-type", "")
    if "text/html" in content_type:
        assert "<html" in root.text.lower() or "<!doctype html" in root.text.lower()
    else:
        assert "前端未构建" in root.json()["message"]

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
