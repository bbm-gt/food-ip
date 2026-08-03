"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

import imageio_ffmpeg


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPOSITORY_ROOT / ".env"
DEFAULT_CODEX_BIN = (
    r"C:\Users\HP\AppData\Local\OpenAI\Codex\bin"
    r"\d7e8094cfb76a267\codex.exe"
)
DEFAULT_PROJECTS_ROOT = REPOSITORY_ROOT / "runtime" / "projects"
DEFAULT_CORS_ORIGINS = "http://localhost:5173"


def _load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding the process environment."""
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")
        if key:
            os.environ.setdefault(key, value)


def _probe_binary(probe: str) -> str | None:
    try:
        getter = getattr(imageio_ffmpeg, f"get_{probe}_exe")
        path = getter()
        return str(path) if path else None
    except Exception:
        return None


_load_env_file(ENV_FILE)

CODEX_BIN = os.environ.get("CODEX_BIN", DEFAULT_CODEX_BIN)
PROJECTS_ROOT = os.environ.get("PROJECTS_ROOT", str(DEFAULT_PROJECTS_ROOT))
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]
FFMPEG_PATH = _probe_binary("ffmpeg")
FFPROBE_PATH = _probe_binary("ffprobe")
