"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import imageio_ffmpeg


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPOSITORY_ROOT / ".env"
DEFAULT_CODEX_BIN = shutil.which("codex") or "codex"
DEFAULT_PROJECTS_ROOT = REPOSITORY_ROOT / "runtime" / "projects"
DEFAULT_DIRECTOR_DB_PATH = REPOSITORY_ROOT / "runtime" / "director" / "director.sqlite3"
DEFAULT_FRONTEND_DIST = REPOSITORY_ROOT / "frontend" / "dist"
DEFAULT_CORS_ORIGINS = "http://localhost:5173"
DEFAULT_AI_SCRIPT_BASE_URL = "https://api.deepseek.com"
DEFAULT_AI_SCRIPT_MODEL = "deepseek-v4-flash"
DEFAULT_DIRECTOR_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DIRECTOR_DEEPSEEK_MODEL = "deepseek-v4-flash"


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


def _path_setting(name: str, default: Path) -> Path:
    value = Path(os.environ.get(name, str(default))).expanduser()
    return value if value.is_absolute() else REPOSITORY_ROOT / value


_load_env_file(ENV_FILE)

CODEX_BIN = os.environ.get("CODEX_BIN", DEFAULT_CODEX_BIN)
PROJECTS_ROOT = str(_path_setting("PROJECTS_ROOT", DEFAULT_PROJECTS_ROOT))
DIRECTOR_DB_PATH = _path_setting("DIRECTOR_DB_PATH", DEFAULT_DIRECTOR_DB_PATH)
DIRECTOR_CONTEXT_MAX_UNITS = int(
    os.environ.get("DIRECTOR_CONTEXT_MAX_UNITS", "100000")
)
DIRECTOR_MAX_INTERNAL_STEPS = int(
    os.environ.get("DIRECTOR_MAX_INTERNAL_STEPS", "8")
)
FRONTEND_DIST = _path_setting("FRONTEND_DIST", DEFAULT_FRONTEND_DIST)
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]
AI_SCRIPT_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
AI_SCRIPT_BASE_URL = os.environ.get(
    "AI_SCRIPT_BASE_URL", DEFAULT_AI_SCRIPT_BASE_URL
).rstrip("/")
AI_SCRIPT_MODEL = os.environ.get("AI_SCRIPT_MODEL", DEFAULT_AI_SCRIPT_MODEL).strip()
AI_SCRIPT_THINKING = os.environ.get("AI_SCRIPT_THINKING", "disabled").strip().lower()
if AI_SCRIPT_THINKING not in {"enabled", "disabled"}:
    AI_SCRIPT_THINKING = "disabled"
AI_SCRIPT_TIMEOUT_SECONDS = float(os.environ.get("AI_SCRIPT_TIMEOUT_SECONDS", "90"))
DIRECTOR_DEEPSEEK_API_KEY = os.environ.get("DIRECTOR_DEEPSEEK_API_KEY", "").strip()
DIRECTOR_DEEPSEEK_BASE_URL = os.environ.get(
    "DIRECTOR_DEEPSEEK_BASE_URL", DEFAULT_DIRECTOR_DEEPSEEK_BASE_URL
).rstrip("/")
DIRECTOR_DEEPSEEK_MODEL = os.environ.get(
    "DIRECTOR_DEEPSEEK_MODEL", DEFAULT_DIRECTOR_DEEPSEEK_MODEL
).strip()
DIRECTOR_DEEPSEEK_TIMEOUT_SECONDS = float(
    os.environ.get("DIRECTOR_DEEPSEEK_TIMEOUT_SECONDS", "90")
)
DIRECTOR_DEEPSEEK_MAX_OUTPUT_TOKENS = int(
    os.environ.get("DIRECTOR_DEEPSEEK_MAX_OUTPUT_TOKENS", "8000")
)
DIRECTOR_DEEPSEEK_THINKING_MODE = os.environ.get(
    "DIRECTOR_DEEPSEEK_THINKING_MODE", "disabled"
).strip().lower()
FFMPEG_PATH = _probe_binary("ffmpeg")
FFPROBE_PATH = _probe_binary("ffprobe")
