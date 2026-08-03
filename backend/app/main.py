"""FastAPI entry point for food-ip."""

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import projects_router, script_router
from .config import CODEX_BIN, CORS_ORIGINS, FFMPEG_PATH, FFPROBE_PATH, PROJECTS_ROOT
from .core.store import InvalidProjectIdError, ProjectNotFoundError


app = FastAPI(title="food-ip")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(projects_router, prefix="/api")
app.include_router(script_router, prefix="/api")


@app.exception_handler(ProjectNotFoundError)
def project_not_found_handler(
    request: Request, exc: ProjectNotFoundError
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"message": f"项目不存在：{exc.project_id}"},
    )


@app.exception_handler(InvalidProjectIdError)
def invalid_project_id_handler(
    request: Request, exc: InvalidProjectIdError
) -> JSONResponse:
    del request, exc
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "项目 ID 格式不合法"},
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
