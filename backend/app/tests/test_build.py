import shutil
from pathlib import Path

import pytest

from .. import config
from ..core import store
from ..engine.build import build_filter_complex, build_final
from ..engine.media import probe_video
from ..engine.timeline import compute_timeline, normalize_edits


def _filter_timeline(transition: str) -> dict:
    is_crossfade = transition == "crossfade"
    return {
        "segments": [
            {
                "shot_index": 0,
                "source_duration": 6.0,
                "trim_head": 0.2,
                "trim_tail": 0.3,
                "used_duration": 5.5,
                "start": 0.0,
                "end": 5.5,
            },
            {
                "shot_index": 1,
                "source_duration": 6.0,
                "trim_head": 0.4,
                "trim_tail": 0.1,
                "used_duration": 5.5,
                "start": 5.0 if is_crossfade else 5.5,
                "end": 10.5 if is_crossfade else 11.0,
            },
        ],
        "junctions": [
            {
                "index": 0,
                "transition": transition,
                "fade_seconds": 0.5,
                "offset": 5.0 if is_crossfade else None,
            }
        ],
        "total_duration": 10.5 if is_crossfade else 11.0,
    }


def test_build_filter_complex_contains_required_fade_graph() -> None:
    graph = build_filter_complex(
        _filter_timeline("fade"), [Path("shot_0.mp4"), Path("shot_1.mp4")]
    )

    assert "trim=start=0.2:end=5.7" in graph
    assert "scale=1080:1920" in graph
    assert "fade=t=out" in graph
    assert "fade=t=in" in graph
    assert "concat=n=2:v=1:a=1" in graph


def test_build_filter_complex_hard_cut_has_no_fade() -> None:
    graph = build_filter_complex(
        _filter_timeline("hard"), [Path("shot_0.mp4"), Path("shot_1.mp4")]
    )

    assert "fade=t=" not in graph


def test_build_filter_complex_crossfade_uses_real_overlap() -> None:
    graph = build_filter_complex(
        _filter_timeline("crossfade"), [Path("shot_0.mp4"), Path("shot_1.mp4")]
    )

    assert "xfade=transition=fade:duration=0.5:offset=5" in graph
    assert "acrossfade=d=0.5" in graph
    assert "fade=t=" not in graph


@pytest.mark.parametrize("transition", ["fade", "crossfade"])
def test_build_final_real_render_matches_authoritative_timeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_shots: list[Path],
    transition: str,
) -> None:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    project_id = store.create_project("真实渲染测试")["id"]
    materials = []
    for index, sample in enumerate(sample_shots[:2]):
        destination = store.material_path(project_id, index)
        shutil.copy2(sample, destination)
        info = probe_video(destination)
        if index == 1:
            info["has_audio"] = False
        material = {"shot_index": index, "filename": destination.name, **info}
        store.save_material(project_id, material)
        materials.append(material)

    edits = normalize_edits(
        materials,
        {
            "shots": [
                {"trim_head": 0.2, "trim_tail": 0.3},
                {"trim_head": 0.4, "trim_tail": 0.1},
            ],
            "junctions": [{"transition": transition, "fade_seconds": 0.5}],
        },
    )
    store.save_edits(project_id, edits)
    timeline = compute_timeline(materials, edits)

    output = build_final(project_id, timeline)
    info = probe_video(output)

    assert info["duration"] == pytest.approx(timeline["total_duration"], abs=0.2)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert (tmp_path / "projects" / project_id / "work" / "silent_1.mp4").is_file()
