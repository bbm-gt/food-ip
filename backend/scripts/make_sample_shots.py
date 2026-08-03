"""Generate three deterministic vertical sample clips with one black second."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app import config  # noqa: E402


SAMPLE_SETTINGS = (
    (440, 0),
    (554, 70),
    (659, 140),
)


def make_sample_shots(output_dir: str | Path | None = None) -> list[Path]:
    """Create three six-second 720x1280 H.264/AAC sample videos."""
    if not config.FFMPEG_PATH:
        raise RuntimeError("未找到 ffmpeg")
    destination = (
        Path(output_dir)
        if output_dir is not None
        else Path(__file__).resolve().parent / "samples"
    )
    destination.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    for index, (frequency, hue) in enumerate(SAMPLE_SETTINGS):
        output = destination / f"sample_{index}.mp4"
        command = [
            config.FFMPEG_PATH,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=720x1280:d=1:r=30",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=720x1280:d=5:r=30",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration=6",
            "-filter_complex",
            f"[1:v]hue=h={hue}[content];[0:v][content]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "生成测试素材失败")
        outputs.append(output)
    return outputs


if __name__ == "__main__":
    for sample in make_sample_shots():
        print(sample)

