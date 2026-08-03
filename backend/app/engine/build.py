"""Final-video filter construction and ffmpeg execution."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Thread
from typing import Any

from .. import config
from ..core import store


ProgressCallback = Callable[[float], None]


class RenderError(RuntimeError):
    """Raised when ffmpeg cannot complete a render."""


def _number(value: float | int) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def _transition_is_fade(value: object) -> bool:
    return value == "fade"


def build_filter_complex(
    timeline: dict[str, Any], material_paths: Sequence[str | Path]
) -> str:
    """Build the deterministic main-render filter graph from a timeline."""
    segments = timeline.get("segments", [])
    junctions = timeline.get("junctions", [])
    if not segments:
        raise RenderError("时间轴中没有可渲染素材")
    if len(material_paths) != len(segments):
        raise RenderError("素材文件数量与时间轴不一致")

    chains: list[str] = []
    for index, segment in enumerate(segments):
        trim_head = float(segment["trim_head"])
        trim_tail = float(segment["trim_tail"])
        source_duration = float(segment["source_duration"])
        used_duration = float(segment["used_duration"])
        video_filters = [
            f"trim=start={_number(trim_head)}:end={_number(source_duration - trim_tail)}",
            "setpts=PTS-STARTPTS",
            "settb=AVTB",
            "scale=1080:1920:force_original_aspect_ratio=decrease",
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            "fps=30",
            "format=yuv420p",
        ]
        if index > 0 and _transition_is_fade(junctions[index - 1]["transition"]):
            fade_seconds = float(junctions[index - 1]["fade_seconds"])
            video_filters.append(f"fade=t=in:st=0:d={_number(fade_seconds)}")
        if index < len(junctions) and _transition_is_fade(
            junctions[index]["transition"]
        ):
            fade_seconds = float(junctions[index]["fade_seconds"])
            video_filters.append(
                f"fade=t=out:st={_number(max(0.0, used_duration - fade_seconds))}"
                f":d={_number(fade_seconds)}"
            )
        chains.append(f"[{index}:v]{','.join(video_filters)}[v{index}]")

        audio_filters = [
            f"atrim=start={_number(trim_head)}:end={_number(source_duration - trim_tail)}",
            "asetpts=PTS-STARTPTS",
            f"apad=whole_dur={_number(used_duration)}",
            "aformat=sample_rates=44100:channel_layouts=stereo",
        ]
        chains.append(f"[{index}:a]{','.join(audio_filters)}[a{index}]")

    if len(segments) == 1:
        chains.extend(("[v0]null[vout]", "[a0]anull[aout]"))
        return ";".join(chains)

    current_video = "v0"
    current_audio = "a0"
    cumulative_overlap = 0.0
    cumulative_duration = float(segments[0]["used_duration"])
    for index, junction in enumerate(junctions):
        transition = junction.get("transition")
        fade_seconds = float(junction.get("fade_seconds", 0.0))
        is_last = index == len(junctions) - 1
        output_video = "vout" if is_last else f"vjoin{index}"
        output_audio = "aout" if is_last else f"ajoin{index}"
        next_video = f"v{index + 1}"
        next_audio = f"a{index + 1}"

        if transition == "crossfade" and fade_seconds > 0:
            cumulative_overlap += fade_seconds
            offset = cumulative_duration - cumulative_overlap
            chains.append(
                f"[{current_video}][{next_video}]"
                f"xfade=transition=fade:duration={_number(fade_seconds)}:"
                f"offset={_number(offset)}[{output_video}]"
            )
            chains.append(
                f"[{current_audio}][{next_audio}]"
                f"acrossfade=d={_number(fade_seconds)}:c1=tri:c2=tri[{output_audio}]"
            )
        else:
            chains.append(
                f"[{current_video}][{current_audio}]"
                f"[{next_video}][{next_audio}]"
                f"concat=n=2:v=1:a=1[{output_video}][{output_audio}]"
            )

        cumulative_duration += float(segments[index + 1]["used_duration"])
        current_video = output_video
        current_audio = output_audio
    return ";".join(chains)


def run_ffmpeg(
    args: Sequence[str | Path], on_progress: ProgressCallback | None = None
) -> None:
    """Run ffmpeg, parse machine-readable progress, and raise on failure."""
    if not config.FFMPEG_PATH:
        raise RenderError("未找到 ffmpeg")
    command = [config.FFMPEG_PATH, *(str(value) for value in args)]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise RenderError("无法启动 ffmpeg") from exc

    stderr_lines: list[str] = []

    def collect_stderr() -> None:
        if process.stderr is not None:
            stderr_lines.extend(process.stderr)

    stderr_thread = Thread(target=collect_stderr, daemon=True)
    stderr_thread.start()
    if process.stdout is not None:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if on_progress is not None and line.startswith("out_time_us="):
                try:
                    on_progress(max(0.0, float(line.split("=", 1)[1]) / 1_000_000))
                except ValueError:
                    continue
    return_code = process.wait()
    stderr_thread.join()
    if return_code != 0:
        detail = "".join(stderr_lines).strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        raise RenderError(detail or f"ffmpeg 渲染失败（退出码 {return_code}）")


def _project_dir(project_id: str) -> Path:
    store.get_project(project_id)
    return Path(config.PROJECTS_ROOT) / project_id


def prepare_material_paths(
    project_id: str, timeline: dict[str, Any], materials: Sequence[dict[str, Any]]
) -> list[Path]:
    """Return input paths aligned to the timeline, adding silent audio as needed."""
    project_dir = _project_dir(project_id)
    work_dir = project_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    materials_by_shot = {int(item["shot_index"]): item for item in materials}
    prepared: list[Path] = []

    for segment in timeline.get("segments", []):
        shot_index = int(segment["shot_index"])
        material = materials_by_shot.get(shot_index)
        source = project_dir / "shots" / f"shot_{shot_index}.mp4"
        if material is None or not source.is_file():
            raise RenderError(f"镜头 {shot_index} 素材缺失")
        if bool(material.get("has_audio", False)):
            prepared.append(source)
            continue

        silent = work_dir / f"silent_{shot_index}.mp4"
        if not silent.is_file() or silent.stat().st_mtime < source.stat().st_mtime:
            silent.unlink(missing_ok=True)
            try:
                run_ffmpeg(
                    [
                        "-y",
                        "-i",
                        source,
                        "-f",
                        "lavfi",
                        "-i",
                        "anullsrc=r=44100:cl=stereo",
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-shortest",
                        silent,
                    ]
                )
            except RenderError:
                silent.unlink(missing_ok=True)
                raise
        prepared.append(silent)
    return prepared


def build_final(
    project_id: str,
    timeline: dict[str, Any],
    *,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Render a project's authoritative timeline to ``work/final.mp4``."""
    materials = store.list_materials(project_id)
    material_paths = prepare_material_paths(project_id, timeline, materials)
    output = _project_dir(project_id) / "work" / "final.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = build_filter_complex(timeline, material_paths)
    args: list[str | Path] = ["-y"]
    for path in material_paths:
        args.extend(["-i", path])
    args.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-t",
            _number(float(timeline["total_duration"])),
        ]
    )
    if on_progress is not None:
        args.extend(["-progress", "pipe:1", "-nostats"])
    args.append(output)
    run_ffmpeg(args, on_progress=on_progress)
    return output
