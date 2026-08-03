"""Low-resolution previews around a timeline junction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import config
from ..core import store
from .build import RenderError, _number, prepare_material_paths, run_ffmpeg
from .timeline import compute_timeline


def _timeline_for_project(project_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    materials = store.list_materials(project_id)
    return materials, compute_timeline(materials, store.get_edits(project_id))


def render_junction_preview(
    project_id: str,
    junction_index: int,
    before: float = 1.5,
    after: float = 1.5,
    width: int = 360,
) -> Path:
    """Render (or reuse) the two clips surrounding one junction."""
    project_dir = Path(config.PROJECTS_ROOT) / project_id
    store.get_project(project_id)
    output = project_dir / "work" / f"preview_j{junction_index}.mp4"
    if output.is_file():
        return output

    materials, timeline = _timeline_for_project(project_id)
    segments = timeline["segments"]
    junctions = timeline["junctions"]
    if junction_index < 0 or junction_index >= len(junctions):
        raise RenderError("接缝序号超出范围")
    left_segment = segments[junction_index]
    right_segment = segments[junction_index + 1]
    left_duration = min(max(0.0, float(before)), float(left_segment["used_duration"]))
    right_duration = min(max(0.0, float(after)), float(right_segment["used_duration"]))
    if left_duration <= 0 or right_duration <= 0:
        raise RenderError("接缝预览时长必须大于 0")

    material_paths = prepare_material_paths(project_id, timeline, materials)
    left_start = (
        float(left_segment["trim_head"])
        + float(left_segment["used_duration"])
        - left_duration
    )
    left_end = left_start + left_duration
    right_start = float(right_segment["trim_head"])
    right_end = right_start + right_duration
    junction = junctions[junction_index]
    is_fade = junction["transition"] in {"fade", "crossfade"}
    fade_seconds = min(
        float(junction["fade_seconds"]), left_duration, right_duration
    )

    left_video = [
        f"trim=start={_number(left_start)}:end={_number(left_end)}",
        "setpts=PTS-STARTPTS",
        f"scale={width}:-2",
        "fps=10",
        "format=yuv420p",
    ]
    right_video = [
        f"trim=start={_number(right_start)}:end={_number(right_end)}",
        "setpts=PTS-STARTPTS",
        f"scale={width}:-2",
        "fps=10",
        "format=yuv420p",
    ]
    if is_fade and fade_seconds > 0:
        left_video.append(
            f"fade=t=out:st={_number(left_duration - fade_seconds)}"
            f":d={_number(fade_seconds)}"
        )
        right_video.append(f"fade=t=in:st=0:d={_number(fade_seconds)}")

    chains = [
        f"[0:v]{','.join(left_video)}[v0]",
        (
            f"[0:a]atrim=start={_number(left_start)}:end={_number(left_end)},"
            "asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={_number(left_duration)},"
            "aformat=sample_rates=44100:channel_layouts=stereo[a0]"
        ),
        f"[1:v]{','.join(right_video)}[v1]",
        (
            f"[1:a]atrim=start={_number(right_start)}:end={_number(right_end)},"
            "asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={_number(right_duration)},"
            "aformat=sample_rates=44100:channel_layouts=stereo[a1]"
        ),
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vout][aout]",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-y",
            "-i",
            material_paths[junction_index],
            "-i",
            material_paths[junction_index + 1],
            "-filter_complex",
            ";".join(chains),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            output,
        ]
    )
    return output
