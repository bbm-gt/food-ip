"""FastAPI entry point for food-ip."""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import (
    edits_router,
    materials_router,
    polish_router,
    projects_router,
    script_router,
)
from .api.render import jobs_router, render_router
from .config import (
    AI_SCRIPT_API_KEY,
    AI_SCRIPT_MODEL,
    CODEX_BIN,
    CORS_ORIGINS,
    FFMPEG_PATH,
    FFPROBE_PATH,
    FRONTEND_DIST,
    PROJECTS_ROOT,
)
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
app.include_router(materials_router, prefix="/api")
app.include_router(edits_router, prefix="/api")
app.include_router(render_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(polish_router, prefix="/api")


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    del request
    detail = exc.detail
    if isinstance(detail, dict) and "message" in detail:
        detail = detail["message"]
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": str(detail)},
        headers=exc.headers,
    )


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


@app.get("/api/health")
def health() -> dict[str, str | bool | None]:
    return {
        "ok": True,
        "ffmpeg": FFMPEG_PATH,
        "ffprobe": FFPROBE_PATH,
        "codex_bin": CODEX_BIN,
        "projects_root": PROJECTS_ROOT,
        "ai_script_configured": bool(AI_SCRIPT_API_KEY),
        "ai_script_model": AI_SCRIPT_MODEL,
    }


if (FRONTEND_DIST / "index.html").is_file():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "message": "前端未构建，请先运行 npm run build",
            "docs": "/docs",
        }
