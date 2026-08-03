from pathlib import Path

import pytest

from ..engine.media import make_thumbnail, probe_video


def test_probe_video_reads_sample_metadata(sample_shots: list[Path]) -> None:
    metadata = probe_video(sample_shots[0])

    assert metadata["duration"] == pytest.approx(6.0, abs=0.15)
    assert metadata["width"] == 720
    assert metadata["height"] == 1280
    assert metadata["fps"] == pytest.approx(30.0)
    assert metadata["has_audio"] is True


def test_make_thumbnail_creates_jpeg(
    sample_shots: list[Path], tmp_path: Path
) -> None:
    destination = tmp_path / "work" / "thumb.jpg"

    result = make_thumbnail(sample_shots[0], destination)

    assert result == destination
    assert destination.is_file()
    assert destination.read_bytes().startswith(b"\xff\xd8")

