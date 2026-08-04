import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..core import store
from ..engine.build import build_final, low_resolution_warnings, run_ffmpeg
from ..engine.captions import build_subtitle_file
from ..engine.media import probe_video
from ..engine.timeline import compute_timeline
from ..main import app
from ..scriptgen.models import ScriptModel, Shot


def _script(*indexes: int) -> ScriptModel:
    return ScriptModel(
        title="T108 test script",
        target_duration_seconds=60,
        style="vertical",
        opening_hook="hook",
        cta="cta",
        shots=[
            Shot(
                shot_index=index,
                lines=f"台词 {index}",
                subtitle=f"字幕 {index}" if index == indexes[0] else "",
                shooting_tips="tips",
                duration_hint_seconds=6,
            )
            for index in indexes
        ],
    )


def _save_materials(project_id: str, samples: list[Path], indexes: list[int]) -> list[dict]:
    materials = []
    for index, sample in zip(indexes, samples, strict=True):
        destination = store.material_path(project_id, index)
        shutil.copy2(sample, destination)
        info = probe_video(destination)
        material = {"shot_index": index, "filename": destination.name, **info}
        store.save_material(project_id, material)
        materials.append(material)
    return materials


def test_export_blocks_when_script_material_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_shots: list[Path]
) -> None:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    project_id = store.create_project("missing material")["id"]
    store.save_script(project_id, _script(1, 2), source="manual_save")
    _save_materials(project_id, sample_shots[:1], [1])

    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project_id}/render/export")

    assert response.status_code == 409
    assert "2" in response.json()["message"]


def test_subtitle_file_prefers_subtitle_and_falls_back_to_lines(tmp_path: Path) -> None:
    script = _script(1, 2)
    timeline = {
        "segments": [
            {"shot_index": 1, "start": 0.0, "end": 2.0},
            {"shot_index": 2, "start": 2.0, "end": 4.0},
        ]
    }

    output = build_subtitle_file(tmp_path, timeline, script)

    assert output is not None
    content = output.read_text(encoding="utf-8")
    assert "字幕 1" in content
    assert "台词 2" in content
    assert "Dialogue:" in content


def test_build_final_burns_subtitles_and_mixes_bgm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_shots: list[Path],
) -> None:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    project_id = store.create_project("T108 render")["id"]
    script = _script(1, 2)
    store.save_script(project_id, script, source="manual_save")
    materials = _save_materials(project_id, sample_shots[:2], [1, 2])

    bgm_path = store.bgm_path(project_id, "bgm.m4a")
    run_ffmpeg(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "aac",
            bgm_path,
        ]
    )
    store.save_bgm(
        project_id,
        {"filename": bgm_path.name, "original_filename": "bgm.m4a", "duration": 1.0, "has_audio": True},
    )
    timeline = compute_timeline(materials, store.get_edits(project_id))

    output = build_final(project_id, timeline)
    metadata = probe_video(output)

    assert metadata["has_audio"] is True
    assert (metadata["width"], metadata["height"]) == (1080, 1920)
    assert metadata["duration"] == pytest.approx(timeline["total_duration"], abs=0.25)
    assert (tmp_path / "projects" / project_id / "work" / "subtitles.ass").is_file()


def test_low_resolution_is_a_non_blocking_warning() -> None:
    warnings = low_resolution_warnings([{"width": 720, "height": 1280}])

    assert warnings == ["当前素材分辨率较低，导出1080P不会提升原始画质。"]


def test_bgm_can_be_uploaded_and_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    project_id = store.create_project("BGM API")["id"]
    source = tmp_path / "source.m4a"
    run_ffmpeg(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "aac",
            source,
        ]
    )

    with TestClient(app) as client:
        uploaded = client.post(
            f"/api/projects/{project_id}/bgm",
            files={"file": ("music.m4a", source.read_bytes(), "audio/mp4")},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["filename"] == "bgm.m4a"
        assert client.get(f"/api/projects/{project_id}/bgm").json()["has_audio"] is True
        assert client.delete(f"/api/projects/{project_id}/bgm").status_code == 204
        assert client.get(f"/api/projects/{project_id}/bgm").json() is None
