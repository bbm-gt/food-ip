"""Static, shot-level subtitle generation for final renders."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from ..scriptgen.models import ScriptModel


def _ass_time(seconds: float) -> str:
    total = max(0, int(math.floor(seconds * 100)))
    centiseconds = total % 100
    total_seconds = total // 100
    return f"{total_seconds // 3600}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}.{centiseconds:02d}"


def _ass_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text.replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}")


def build_subtitle_file(
    work_dir: Path,
    timeline: dict[str, Any],
    script: ScriptModel | None,
) -> Path | None:
    """Create an ASS file using one static subtitle event per timeline shot."""
    if script is None:
        return None

    shots = {shot.shot_index: shot for shot in script.shots}
    events: list[str] = []
    for segment in timeline.get("segments", []):
        shot = shots.get(int(segment["shot_index"]))
        if shot is None:
            continue
        text = _ass_text(shot.subtitle or shot.lines)
        if not text:
            continue
        start = float(segment["start"])
        end = float(segment["end"])
        if end <= start:
            continue
        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=_ass_time(start),
                end=_ass_time(end),
                text=text,
            )
        )

    if not events:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    output = work_dir / "subtitles.ass"
    output.write_text(
        """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,1,2,80,80,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        + "\n".join(events)
        + "\n",
        encoding="utf-8",
    )
    return output
