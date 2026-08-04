"""Junction preview, export and job status routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from .. import config
from ..core import jobs, store
from ..engine.build import RenderError
from ..engine.export import start_export
from ..engine.junction import render_junction_preview


render_router = APIRouter(tags=["render"])
jobs_router = APIRouter(tags=["jobs"])


@render_router.get("/projects/{project_id}/preview/junction/{junction_index}")
def preview_junction_route(
    project_id: str,
    junction_index: int,
    before: float = Query(default=1.5, gt=0),
    after: float = Query(default=1.5, gt=0),
    width: int = Query(default=360, alias="w", ge=120, le=1080),
) -> FileResponse:
    try:
        output = render_junction_preview(
            project_id, junction_index, before=before, after=after, width=width
        )
    except (RenderError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": f"接缝预览渲染失败：{exc}"},
        ) from exc
    return FileResponse(output, media_type="video/mp4")


@render_router.post("/projects/{project_id}/render/export")
def start_export_route(project_id: str) -> dict[str, str]:
    try:
        return {"job_id": start_export(project_id)}
    except (RenderError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": f"无法开始导出: {exc}"},
        ) from exc


@jobs_router.get("/jobs/{job_id}")
def get_job_route(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "导出任务不存在"},
        )
    return job


@render_router.get("/projects/{project_id}/exports")
def list_exports_route(project_id: str) -> list[str]:
    store.get_project(project_id)
    exports_dir = Path(config.PROJECTS_ROOT) / project_id / "exports"
    if not exports_dir.is_dir():
        return []
    return sorted(path.name for path in exports_dir.iterdir() if path.is_file())


@render_router.get("/projects/{project_id}/exports/final.mp4")
def download_export_route(project_id: str) -> FileResponse:
    store.get_project(project_id)
    output = Path(config.PROJECTS_ROOT) / project_id / "exports" / "final.mp4"
    if not output.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "导出文件不存在"},
        )
    return FileResponse(
        output,
        media_type="video/mp4",
        filename="final.mp4",
    )
