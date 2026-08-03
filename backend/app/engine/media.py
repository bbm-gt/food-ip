"""ffprobe/ffmpeg-backed media inspection helpers."""

from __future__ import annotations

import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path

from .. import config


class MediaCommandError(RuntimeError):
    """Raised when a required media command cannot produce valid output."""


def _run(command: list[str], *, allow_nonzero: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise MediaCommandError(f"无法启动媒体命令：{command[0]}") from exc
    if result.returncode != 0 and not allow_nonzero:
        detail = result.stderr.strip() or result.stdout.strip()
        raise MediaCommandError(detail or f"媒体命令失败：{result.returncode}")
    return result


def _probe_with_ffprobe(path: Path, executable: str) -> dict[str, float | int | bool]:
    video_result = _run(
        [
            executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    audio_result = _run(
        [
            executable,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        video_payload = json.loads(video_result.stdout)
        audio_payload = json.loads(audio_result.stdout)
        video_stream = video_payload["streams"][0]
        duration = float(video_payload["format"]["duration"])
        fps = float(Fraction(video_stream["r_frame_rate"]))
        return {
            "duration": duration,
            "width": int(video_stream["width"]),
            "height": int(video_stream["height"]),
            "fps": fps,
            "has_audio": bool(audio_payload.get("streams")),
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaCommandError("ffprobe 返回了无法解析的视频元信息") from exc


def _probe_with_ffmpeg(path: Path, executable: str) -> dict[str, float | int | bool]:
    """Offline fallback for imageio-ffmpeg distributions without ffprobe."""
    result = _run([executable, "-hide_banner", "-i", str(path)], allow_nonzero=True)
    output = result.stderr
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    video_match = re.search(
        r"Stream[^\r\n]*Video:[^\r\n]*?\b(\d{2,5})x(\d{2,5})\b[^\r\n]*?"
        r"(?:,\s*)?(\d+(?:\.\d+)?)\s+fps\b",
        output,
    )
    if duration_match is None or video_match is None:
        raise MediaCommandError("ffmpeg 返回了无法解析的视频元信息")
    hours, minutes, seconds = duration_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return {
        "duration": duration,
        "width": int(video_match.group(1)),
        "height": int(video_match.group(2)),
        "fps": float(video_match.group(3)),
        "has_audio": re.search(r"Stream[^\r\n]*Audio:", output) is not None,
    }


def probe_video(path: str | Path) -> dict[str, float | int | bool]:
    """Return duration, dimensions, frame rate and audio presence for a video."""
    source = Path(path)
    if not source.is_file():
        raise MediaCommandError(f"视频文件不存在：{source}")
    if config.FFPROBE_PATH:
        return _probe_with_ffprobe(source, config.FFPROBE_PATH)
    if config.FFMPEG_PATH:
        return _probe_with_ffmpeg(source, config.FFMPEG_PATH)
    raise MediaCommandError("未找到 ffprobe 或 ffmpeg")


def make_thumbnail(
    src: str | Path, dst: str | Path, at_seconds: float = 1.0, width: int = 320
) -> Path:
    """Extract one JPEG frame from ``src`` into ``dst``."""
    if not config.FFMPEG_PATH:
        raise MediaCommandError("未找到 ffmpeg")
    source = Path(src)
    destination = Path(dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            config.FFMPEG_PATH,
            "-y",
            "-ss",
            str(max(0.0, float(at_seconds))),
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale={max(1, int(width))}:-1",
            str(destination),
        ]
    )
    if not destination.is_file():
        raise MediaCommandError("ffmpeg 未生成缩略图")
    return destination

