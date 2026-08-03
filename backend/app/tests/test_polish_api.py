from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..core import store
from ..main import app


@pytest.fixture
def polish_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    return TestClient(app)


def _project_with_junction(client: TestClient) -> str:
    project_id = client.post("/api/projects", json={"name": "润色测试"}).json()["id"]
    for index in range(2):
        store.save_material(
            project_id,
            {
                "shot_index": index,
                "filename": f"shot_{index}.mp4",
                "duration": 3.0,
                "width": 720,
                "height": 1280,
                "fps": 30.0,
                "has_audio": True,
            },
        )
    return project_id


def test_polish_junction_returns_not_configured(polish_client: TestClient) -> None:
    project_id = _project_with_junction(polish_client)

    response = polish_client.post(
        f"/api/projects/{project_id}/polish/junctions/0",
        json={"goal": "harmonize_junction", "params": {"strength": 0.5}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_configured"
    assert "尚未接入" in payload["message"]
    assert payload["segment_id"] == f"{project_id}:junction:0"


def test_polish_missing_project_returns_404(polish_client: TestClient) -> None:
    response = polish_client.post(
        "/api/projects/does-not-exist/polish/junctions/0", json={}
    )

    assert response.status_code == 404
    assert "项目不存在" in response.json()["message"]


def test_polish_missing_junction_returns_400(polish_client: TestClient) -> None:
    project_id = polish_client.post(
        "/api/projects", json={"name": "空项目"}
    ).json()["id"]

    response = polish_client.post(
        f"/api/projects/{project_id}/polish/junctions/0", json={}
    )

    assert response.status_code == 400
    assert "接缝" in response.json()["message"]


def test_polish_providers_lists_null(polish_client: TestClient) -> None:
    project_id = polish_client.post(
        "/api/projects", json={"name": "能力检测"}
    ).json()["id"]

    response = polish_client.get(f"/api/projects/{project_id}/polish/providers")

    assert response.status_code == 200
    assert response.json() == ["null"]
