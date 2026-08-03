from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    return TestClient(app)


def test_project_script_end_to_end(client: TestClient) -> None:
    created_response = client.post("/api/projects", json={"name": "测试项目"})
    assert created_response.status_code == 201
    project_id = created_response.json()["id"]

    patch_response = client.patch(
        f"/api/projects/{project_id}",
        json={
            "restaurant_name": "阿芳家常菜",
            "signature_dishes": ["红烧肉"],
            "owner_persona": "热情的老板娘",
        },
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["boss_info"]["restaurant_name"] == "阿芳家常菜"

    boss_info = {
        **patch_response.json()["boss_info"],
        "cuisine_type": "家常菜",
        "target_duration_seconds": 60,
    }
    generated_response = client.post(
        f"/api/projects/{project_id}/script/template", json=boss_info
    )
    assert generated_response.status_code == 200
    generated = generated_response.json()
    assert len(generated["shots"]) == 6

    get_response = client.get(f"/api/projects/{project_id}/script")
    assert get_response.status_code == 200
    assert get_response.json() == generated

    generated["title"] = "手工修改后的标题"
    generated["shots"][0]["lines"] = "这是手工改过的开场。"
    put_response = client.put(
        f"/api/projects/{project_id}/script", json=generated
    )
    assert put_response.status_code == 200
    assert put_response.json() == generated
    assert client.get(f"/api/projects/{project_id}/script").json() == generated
    assert client.get(f"/api/projects/{project_id}").json()["script"] == generated


def test_missing_project_returns_chinese_404(client: TestClient) -> None:
    response = client.get("/api/projects/does-not-exist")

    assert response.status_code == 404
    assert "项目不存在" in response.json()["message"]


def test_project_id_rejects_directory_traversal(client: TestClient) -> None:
    response = client.get("/api/projects/%2E%2E%2Fsecret")

    assert response.status_code in {400, 404}
