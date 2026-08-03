import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..core import store
from ..engine.media import probe_video
from ..engine.timeline import compute_timeline
from ..main import app


def test_export_job_reaches_done_and_downloads_real_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_shots: list[Path],
) -> None:
    projects_root = tmp_path / "projects"
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(projects_root))
    project_id = store.create_project("导出测试")["id"]
    for index, sample in enumerate(sample_shots[:2]):
        destination = store.material_path(project_id, index)
        shutil.copy2(sample, destination)
        store.save_material(
            project_id,
            {"shot_index": index, "filename": destination.name, **probe_video(destination)},
        )
    materials = store.list_materials(project_id)
    timeline = compute_timeline(materials, store.get_edits(project_id))

    with TestClient(app) as client:
        started = client.post(f"/api/projects/{project_id}/render/export")
        assert started.status_code == 200
        job_id = started.json()["job_id"]

        job = None
        for _ in range(600):
            response = client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            job = response.json()
            if job["status"] in {"done", "failed"}:
                break
            time.sleep(0.1)

        assert job is not None
        assert job["status"] == "done", job["message"]
        assert job["progress"] == 100
        assert job["result"]["total_duration"] == pytest.approx(
            timeline["total_duration"]
        )

        listed = client.get(f"/api/projects/{project_id}/exports")
        assert listed.status_code == 200
        assert "final.mp4" in listed.json()
        downloaded = client.get(f"/api/projects/{project_id}/exports/final.mp4")
        assert downloaded.status_code == 200

    output = projects_root / project_id / "exports" / "final.mp4"
    assert output.is_file()
    assert probe_video(output)["duration"] == pytest.approx(
        timeline["total_duration"], abs=0.2
    )
