from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..core import store
from ..main import app


@pytest.fixture
def edits_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    return TestClient(app)


def test_timeline_without_materials_is_empty(edits_client: TestClient) -> None:
    project_id = edits_client.post("/api/projects", json={"name": "空项目"}).json()["id"]

    response = edits_client.get(f"/api/projects/{project_id}/timeline")

    assert response.status_code == 200
    assert response.json() == {
        "segments": [],
        "junctions": [],
        "total_duration": 0.0,
    }


def test_put_edits_echoes_clamped_values_and_timeline(
    edits_client: TestClient,
) -> None:
    project_id = edits_client.post("/api/projects", json={"name": "剪辑测试"}).json()["id"]
    for index in range(3):
        store.save_material(
            project_id,
            {
                "shot_index": index,
                "filename": f"shot_{index}.mp4",
                "duration": 6.0,
                "width": 720,
                "height": 1280,
                "fps": 30.0,
                "has_audio": True,
            },
        )

    response = edits_client.put(
        f"/api/projects/{project_id}/edits",
        json={
            "shots": [
                {"trim_head": 4.0, "trim_tail": 4.0},
                {"trim_head": 1.0, "trim_tail": -2.0},
                {"trim_head": 0.0, "trim_tail": 0.0},
            ],
            "junctions": [
                {"transition": "crossfade", "fade_seconds": 4.0},
                {"transition": "fade", "fade_seconds": 4.0},
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["edits"]["shots"][0] == {
        "trim_head": 4.0,
        "trim_tail": 1.5,
    }
    assert payload["edits"]["shots"][1] == {
        "trim_head": 1.0,
        "trim_tail": 0.0,
    }
    assert payload["edits"]["junctions"][0]["fade_seconds"] == pytest.approx(0.5)
    assert payload["edits"]["junctions"][1]["fade_seconds"] == pytest.approx(1.0)
    assert payload["timeline"]["total_duration"] == pytest.approx(11.0)
    assert edits_client.get(f"/api/projects/{project_id}/edits").json() == payload["edits"]
    assert edits_client.get(f"/api/projects/{project_id}/timeline").json() == payload["timeline"]

