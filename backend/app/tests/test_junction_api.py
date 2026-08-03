import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..core import store
from ..engine.media import probe_video
from ..main import app


def _project_with_samples(root: Path, sample_shots: list[Path], count: int = 2) -> str:
    project_id = store.create_project("接缝测试")["id"]
    for index, sample in enumerate(sample_shots[:count], start=1):
        destination = store.material_path(project_id, index)
        shutil.copy2(sample, destination)
        store.save_material(
            project_id,
            {"shot_index": index, "filename": destination.name, **probe_video(destination)},
        )
    return project_id


@pytest.mark.parametrize(
    ("transition", "expected_timeline_duration", "expected_preview_duration"),
    [("fade", 11.5, 3.0), ("crossfade", 11.1, 2.6)],
)
def test_put_junction_and_render_real_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_shots: list[Path],
    transition: str,
    expected_timeline_duration: float,
    expected_preview_duration: float,
) -> None:
    projects_root = tmp_path / "projects"
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(projects_root))
    project_id = _project_with_samples(projects_root, sample_shots)

    with TestClient(app) as client:
        response = client.put(
            f"/api/projects/{project_id}/junctions/0",
            json={
                "trim_tail": 0.2,
                "trim_head": 0.3,
                "transition": transition,
                "fade_seconds": 0.4,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["edits"]["shots"][0]["trim_tail"] == pytest.approx(0.2)
        assert payload["edits"]["shots"][1]["trim_head"] == pytest.approx(0.3)
        assert payload["timeline"]["total_duration"] == pytest.approx(
            expected_timeline_duration
        )

        preview = client.get(
            f"/api/projects/{project_id}/preview/junction/0?before=1.5&after=1.5&w=360"
        )
        assert preview.status_code == 200
        assert preview.headers["content-type"].startswith("video/mp4")

    info = probe_video(projects_root / project_id / "work" / "preview_j0.mp4")
    assert info["duration"] == pytest.approx(expected_preview_duration, abs=0.2)
    assert info["width"] == 360
