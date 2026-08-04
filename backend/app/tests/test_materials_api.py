from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..main import app


@pytest.fixture
def materials_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    return TestClient(app)


def _create_project(client: TestClient) -> str:
    response = client.post("/api/projects", json={"name": "素材测试"})
    assert response.status_code == 201
    return response.json()["id"]


def test_material_upload_list_thumbnail_duplicate_and_delete(
    materials_client: TestClient, sample_shots: list[Path]
) -> None:
    project_id = _create_project(materials_client)
    for shot_index in (2, 0, 1):
        with sample_shots[shot_index].open("rb") as source:
            response = materials_client.post(
                f"/api/projects/{project_id}/materials",
                data={"shot_index": str(shot_index)},
                files={"file": (f"SAMPLE_{shot_index}.MP4", source, "video/mp4")},
            )
        assert response.status_code == 200
        assert response.json()["duration"] == pytest.approx(6.0, abs=0.15)

    listed = materials_client.get(f"/api/projects/{project_id}/materials")
    assert listed.status_code == 200
    assert [item["shot_index"] for item in listed.json()] == [0, 1, 2]
    assert materials_client.get(
        f"/api/projects/{project_id}/materials/1/thumbnail"
    ).status_code == 200

    with sample_shots[0].open("rb") as source:
        duplicate = materials_client.post(
            f"/api/projects/{project_id}/materials",
            data={"shot_index": "1"},
            files={"file": ("again.mp4", source, "video/mp4")},
        )
    assert duplicate.status_code == 409

    with sample_shots[1].open("rb") as source:
        replaced = materials_client.put(
            f"/api/projects/{project_id}/materials/1",
            files={"file": ("replacement.mp4", source, "video/mp4")},
        )
    assert replaced.status_code == 200
    assert replaced.json()["shot_index"] == 1
    assert materials_client.get(
        f"/api/projects/{project_id}/materials/1/thumbnail"
    ).status_code == 200

    failed_replace = materials_client.put(
        f"/api/projects/{project_id}/materials/1",
        files={"file": ("broken.mp4", b"not a video", "video/mp4")},
    )
    assert failed_replace.status_code == 400
    assert materials_client.get(
        f"/api/projects/{project_id}/materials/1/thumbnail"
    ).status_code == 200

    invalid = materials_client.post(
        f"/api/projects/{project_id}/materials",
        data={"shot_index": "3"},
        files={"file": ("notes.txt", b"not a video", "text/plain")},
    )
    assert invalid.status_code == 400
    assert "仅支持" in str(invalid.json())

    deleted = materials_client.delete(
        f"/api/projects/{project_id}/materials/1"
    )
    assert deleted.status_code == 204
    remaining = materials_client.get(f"/api/projects/{project_id}/materials").json()
    assert [item["shot_index"] for item in remaining] == [0, 2]
    assert materials_client.get(
        f"/api/projects/{project_id}/materials/1/thumbnail"
    ).status_code == 404
