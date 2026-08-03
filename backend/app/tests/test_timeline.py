import pytest

from ..engine.timeline import compute_timeline, normalize_edits


def materials(*durations: float) -> list[dict]:
    return [
        {"shot_index": index, "duration": duration}
        for index, duration in enumerate(durations)
    ]


def test_hard_cuts_use_cumulative_used_duration() -> None:
    timeline = compute_timeline(
        materials(6, 6, 6),
        {
            "shots": [{}, {}, {}],
            "junctions": [
                {"transition": "hard", "fade_seconds": 0.5},
                {"transition": "hard", "fade_seconds": 1.0},
            ],
        },
    )

    assert timeline["total_duration"] == pytest.approx(18)
    assert [segment["start"] for segment in timeline["segments"]] == [0, 6, 12]
    assert [junction["offset"] for junction in timeline["junctions"]] == [None, None]


def test_trim_changes_used_and_total_duration() -> None:
    timeline = compute_timeline(
        materials(6, 6, 6),
        {
            "shots": [
                {"trim_tail": 0.5},
                {"trim_head": 1.0},
                {},
            ],
            "junctions": [
                {"transition": "hard"},
                {"transition": "hard"},
            ],
        },
    )

    assert [segment["used_duration"] for segment in timeline["segments"]] == [
        5.5,
        5,
        6,
    ]
    assert timeline["total_duration"] == pytest.approx(16.5)


def test_trim_is_clamped_to_leave_half_a_second() -> None:
    edits = normalize_edits(
        materials(6),
        {"shots": [{"trim_head": 4.0, "trim_tail": 4.0}]},
    )
    timeline = compute_timeline(materials(6), edits)

    assert edits["shots"] == [{"trim_head": 4.0, "trim_tail": 1.5}]
    assert timeline["segments"][0]["used_duration"] == pytest.approx(0.5)


def test_negative_trim_is_clamped_to_zero() -> None:
    edits = normalize_edits(
        materials(6),
        {"shots": [{"trim_head": -2.0, "trim_tail": -1.0}]},
    )

    assert edits["shots"] == [{"trim_head": 0.0, "trim_tail": 0.0}]


def test_fade_does_not_change_total_or_segment_starts() -> None:
    timeline = compute_timeline(
        materials(6, 6, 6),
        {
            "shots": [{}, {}, {}],
            "junctions": [
                {"transition": "fade", "fade_seconds": 0.5},
                {"transition": "fade", "fade_seconds": 0.5},
            ],
        },
    )

    assert timeline["total_duration"] == pytest.approx(18)
    assert [segment["start"] for segment in timeline["segments"]] == [0, 6, 12]


def test_crossfade_uses_cumulative_formula() -> None:
    timeline = compute_timeline(
        materials(6, 6, 6),
        {
            "shots": [{}, {}, {}],
            "junctions": [
                {"transition": "crossfade", "fade_seconds": 0.5},
                {"transition": "crossfade", "fade_seconds": 1.0},
            ],
        },
    )

    assert [junction["offset"] for junction in timeline["junctions"]] == [5.5, 10.5]
    assert [segment["start"] for segment in timeline["segments"]] == [0, 5.5, 10.5]
    assert timeline["total_duration"] == pytest.approx(16.5)


def test_crossfade_is_clamped_by_adjacent_used_duration_and_one_second() -> None:
    timeline = compute_timeline(
        materials(6, 6),
        {
            "shots": [
                {"trim_head": 5.5},
                {"trim_tail": 5.25},
            ],
            "junctions": [
                {"transition": "crossfade", "fade_seconds": 10.0}
            ],
        },
    )

    assert timeline["junctions"][0]["fade_seconds"] == pytest.approx(0.5)
    assert timeline["total_duration"] == pytest.approx(0.75)


def test_empty_materials_has_zero_duration() -> None:
    assert compute_timeline([], {}) == {
        "segments": [],
        "junctions": [],
        "total_duration": 0.0,
    }

