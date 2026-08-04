import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    return TestClient(app)


def _create_project(client: TestClient) -> str:
    response = client.post("/api/projects", json={"name": "脚本版本测试"})
    assert response.status_code == 201
    return response.json()["id"]


def test_all_current_script_writes_create_readable_versions(client: TestClient) -> None:
    project_id = _create_project(client)
    empty = client.get(f"/api/projects/{project_id}/script/versions")
    assert empty.status_code == 200
    assert empty.json() == []

    generated = client.post(
        f"/api/projects/{project_id}/script/template",
        json={"restaurant_name": "版本小馆"},
    )
    assert generated.status_code == 200

    bundle = client.post(
        f"/api/projects/{project_id}/script-bundles/template",
        json={"research": {}, "candidate_count": 3},
    ).json()
    candidate = bundle["candidates"][0]
    selected = client.post(
        f"/api/projects/{project_id}/script-bundles/{bundle['id']}"
        f"/select/{candidate['id']}"
    )
    assert selected.status_code == 200

    edited = selected.json()
    edited["title"] = "手工调整后的标题"
    saved = client.put(f"/api/projects/{project_id}/script", json=edited)
    assert saved.status_code == 200

    versions = client.get(f"/api/projects/{project_id}/script/versions")
    assert versions.status_code == 200
    payload = versions.json()
    assert [item["version_number"] for item in payload] == [3, 2, 1]
    assert [item["source"] for item in payload] == [
        "manual_save",
        "candidate_selection",
        "template_generation",
    ]
    assert payload[0]["script"] == edited
    assert client.get(f"/api/projects/{project_id}/script").json() == edited

    duplicate = client.put(f"/api/projects/{project_id}/script", json=edited)
    assert duplicate.status_code == 200
    assert len(client.get(f"/api/projects/{project_id}/script/versions").json()) == 3

    versions_file = Path(config.PROJECTS_ROOT) / project_id / "script_versions.json"
    stored = json.loads(versions_file.read_text(encoding="utf-8"))
    assert [item["version_number"] for item in stored] == [1, 2, 3]


def test_existing_script_is_materialized_as_legacy_baseline(client: TestClient) -> None:
    project_id = _create_project(client)
    generated = client.post(
        f"/api/projects/{project_id}/script/template",
        json={"restaurant_name": "旧项目小馆"},
    ).json()
    versions_file = Path(config.PROJECTS_ROOT) / project_id / "script_versions.json"
    versions_file.unlink()

    versions = client.get(f"/api/projects/{project_id}/script/versions")
    assert versions.status_code == 200
    assert versions.json()[0]["version_number"] == 1
    assert versions.json()[0]["source"] == "legacy_import"
    assert versions.json()[0]["script"] == generated
    assert versions_file.is_file()
