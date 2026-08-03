"""FastAPI entry point for food-ip."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import CODEX_BIN, CORS_ORIGINS, FFMPEG_PATH, FFPROBE_PATH, PROJECTS_ROOT


app = FastAPI(title="food-ip")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "food-ip API"}


@app.get("/api/health")
def health() -> dict[str, str | bool | None]:
    return {
        "ok": True,
        "ffmpeg": FFMPEG_PATH,
        "ffprobe": FFPROBE_PATH,
        "codex_bin": CODEX_BIN,
        "projects_root": PROJECTS_ROOT,
    }
