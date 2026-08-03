"""Material upload, listing, thumbnail and deletion routes."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse

from ..core import store
from ..engine.media import MediaCommandError, make_thumbnail, probe_video


router = APIRouter(tags=["materials"])
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}


@router.post("/projects/{project_id}/materials")
def upload_material_route(
    project_id: str,
    shot_index: int = Form(...),
    file: UploadFile = File(...),
) -> dict:
    store.get_project(project_id)
    if shot_index < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "拍摄序号必须是非负整数"},
        )
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "仅支持 MP4、MOV、MKV、AVI 视频文件"},
        )
    if any(item["shot_index"] == shot_index for item in store.list_materials(project_id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": f"拍摄序号 {shot_index} 已存在"},
        )

    destination = store.material_path(project_id, shot_index)
    thumbnail = store.thumbnail_path(project_id, shot_index)
    try:
        with destination.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        media_info = probe_video(destination)
        make_thumbnail(destination, thumbnail)
    except (OSError, MediaCommandError) as exc:
        destination.unlink(missing_ok=True)
        thumbnail.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": f"视频处理失败：{exc}"},
        ) from exc

    material = {
        "shot_index": shot_index,
        "filename": destination.name,
        **media_info,
    }
    return store.save_material(project_id, material)


@router.get("/projects/{project_id}/materials")
def list_materials_route(project_id: str) -> list[dict]:
    return store.list_materials(project_id)


@router.delete(
    "/projects/{project_id}/materials/{shot_index}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_material_route(project_id: str, shot_index: int) -> Response:
    if shot_index < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "拍摄序号必须是非负整数"},
        )
    store.get_project(project_id)
    if not store.delete_material(project_id, shot_index):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "素材不存在"},
        )
    store.material_path(project_id, shot_index).unlink(missing_ok=True)
    store.thumbnail_path(project_id, shot_index).unlink(missing_ok=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/materials/{shot_index}/thumbnail")
def material_thumbnail_route(project_id: str, shot_index: int) -> FileResponse:
    if shot_index < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "拍摄序号必须是非负整数"},
        )
    thumbnail = store.thumbnail_path(project_id, shot_index)
    if not thumbnail.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "缩略图不存在"},
        )
    return FileResponse(thumbnail, media_type="image/jpeg")
