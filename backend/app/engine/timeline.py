"""Authoritative, side-effect-free timeline calculations."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


TRANSITIONS = {"hard", "fade", "crossfade"}
MIN_USED_DURATION = 0.5
MAX_FADE_SECONDS = 1.0


def _finite_number(value: object, default: float = 0.0) -> float:
    """Return a finite float, treating booleans and invalid data as default."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _mapping_at(values: object, index: int) -> Mapping[str, Any]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return {}
    if index >= len(values) or not isinstance(values[index], Mapping):
        return {}
    return values[index]


def _source_durations(materials: Sequence[Mapping[str, Any]]) -> list[float]:
    return [max(0.0, _finite_number(material.get("duration"))) for material in materials]


def normalize_edits(
    materials: Sequence[Mapping[str, Any]], edits: Mapping[str, Any] | None
) -> dict[str, list[dict[str, float | str]]]:
    """Clamp edits to the exact limits used by :func:`compute_timeline`.

    ``trim_head`` is clamped first and ``trim_tail`` is then clamped to the
    remaining allowance.  This deterministic ordering guarantees both values
    are non-negative and their sum never exceeds ``D[i] - 0.5``.
    """
    durations = _source_durations(materials)
    edit_values: Mapping[str, Any] = edits if isinstance(edits, Mapping) else {}
    requested_shots = edit_values.get("shots", [])
    shots: list[dict[str, float]] = []
    used_durations: list[float] = []

    for index, duration in enumerate(durations):
        requested = _mapping_at(requested_shots, index)
        trim_limit = max(0.0, duration - MIN_USED_DURATION)
        trim_head = min(max(0.0, _finite_number(requested.get("trim_head"))), trim_limit)
        trim_tail = min(
            max(0.0, _finite_number(requested.get("trim_tail"))),
            trim_limit - trim_head,
        )
        shots.append({"trim_head": trim_head, "trim_tail": trim_tail})
        used_durations.append(duration - trim_head - trim_tail)

    requested_junctions = edit_values.get("junctions", [])
    junctions: list[dict[str, float | str]] = []
    for index in range(max(0, len(materials) - 1)):
        requested = _mapping_at(requested_junctions, index)
        transition = requested.get("transition", "fade")
        if transition not in TRANSITIONS:
            transition = "hard"
        fade_limit = min(used_durations[index], used_durations[index + 1], MAX_FADE_SECONDS)
        fade_seconds = min(
            max(0.0, _finite_number(requested.get("fade_seconds"), 0.5)),
            fade_limit,
        )
        junctions.append(
            {"transition": str(transition), "fade_seconds": fade_seconds}
        )

    return {"shots": shots, "junctions": junctions}


def compute_timeline(
    materials: Sequence[Mapping[str, Any]], edits: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Compute segments, junction offsets and total duration without IO.

    Hard cuts and fades do not overlap segments.  A crossfade junction overlaps
    the adjacent segments by its clamped ``fade_seconds`` value, so each later
    start and the final total subtract the cumulative crossfade overlap.
    """
    if not materials:
        return {"segments": [], "junctions": [], "total_duration": 0.0}

    durations = _source_durations(materials)
    effective_edits = normalize_edits(materials, edits)
    shots = effective_edits["shots"]
    effective_junctions = effective_edits["junctions"]

    segments: list[dict[str, float | int]] = []
    start = 0.0
    for index, (material, duration, shot_edit) in enumerate(
        zip(materials, durations, shots, strict=True)
    ):
        trim_head = float(shot_edit["trim_head"])
        trim_tail = float(shot_edit["trim_tail"])
        used_duration = duration - trim_head - trim_tail
        end = start + used_duration
        shot_index_value = material.get("shot_index", index)
        shot_index = shot_index_value if isinstance(shot_index_value, int) else index
        segments.append(
            {
                "shot_index": shot_index,
                "source_duration": duration,
                "trim_head": trim_head,
                "trim_tail": trim_tail,
                "used_duration": used_duration,
                "start": start,
                "end": end,
            }
        )
        if index < len(effective_junctions):
            junction = effective_junctions[index]
            overlap = (
                float(junction["fade_seconds"])
                if junction["transition"] == "crossfade"
                else 0.0
            )
            start = end - overlap

    junctions: list[dict[str, float | int | str | None]] = []
    cumulative_duration = 0.0
    cumulative_overlap = 0.0
    for index, junction in enumerate(effective_junctions):
        cumulative_duration = math.fsum(
            (cumulative_duration, float(segments[index]["used_duration"]))
        )
        is_crossfade = junction["transition"] == "crossfade"
        if is_crossfade:
            cumulative_overlap = math.fsum(
                (cumulative_overlap, float(junction["fade_seconds"]))
            )
        junctions.append(
            {
                "index": index,
                "transition": str(junction["transition"]),
                "fade_seconds": float(junction["fade_seconds"]),
                "offset": (
                    cumulative_duration - cumulative_overlap
                    if is_crossfade
                    else None
                ),
            }
        )

    total_duration = math.fsum(float(segment["used_duration"]) for segment in segments)
    total_duration -= math.fsum(
        float(junction["fade_seconds"])
        for junction in effective_junctions
        if junction["transition"] == "crossfade"
    )
    return {
        "segments": segments,
        "junctions": junctions,
        "total_duration": total_duration,
    }
