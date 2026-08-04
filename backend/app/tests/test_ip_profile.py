from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    return TestClient(app)


def test_legacy_project_gets_persistable_rule_draft_and_can_confirm(client: TestClient) -> None:
    project_id = client.post("/api/projects", json={"name": "老项目"}).json()["id"]
    legacy = client.patch(f"/api/projects/{project_id}", json={"restaurant_name": "阿芳家常菜", "owner_persona": "实在老板"})
    assert legacy.status_code == 200
    draft = client.get(f"/api/projects/{project_id}/ip-profile")
    assert draft.status_code == 200
    assert draft.json()["confirmed"] is False
    editable = draft.json()
    editable.update({"memory_points": ["现切现串"], "content_pillars": ["食材真相"], "avoided_topics": ["夸大功效"], "speaking_style": "实在直说", "conversion_path": ["认识", "到店"]})
    updated = client.put(f"/api/projects/{project_id}/ip-profile", json=editable)
    assert updated.status_code == 200
    assert client.get(f"/api/projects/{project_id}/ip-profile").json()["memory_points"] == ["现切现串"]
    saved = client.post(f"/api/projects/{project_id}/ip-profile/draft")
    assert saved.status_code == 200
    confirmed = client.post(f"/api/projects/{project_id}/ip-profile/confirm")
    assert confirmed.json()["confirmed"] is True
    assert client.put(f"/api/projects/{project_id}/ip-profile", json=draft.json()).status_code == 409


def test_new_co_creation_requires_confirmed_ip_profile(client: TestClient) -> None:
    project_id = client.post("/api/projects", json={"name": "定位测试"}).json()["id"]
    response = client.post(f"/api/projects/{project_id}/script-bundles/ip-ai", json={"research": {}, "candidate_count": 3})
    assert response.status_code == 409
