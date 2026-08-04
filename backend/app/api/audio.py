"""Project-level audio asset routes."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status

from ..core import store
from ..engine.media import MediaCommandError, probe_audio


router = APIRouter(tags=["audio"])
ALLOWED_BGM_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}


@router.get("/projects/{project_id}/bgm")
def get_bgm_route(project_id: str) -> dict | None:
    store.get_project(project_id)
    return store.get_bgm(project_id)


@router.post("/projects/{project_id}/bgm")
def upload_bgm_route(project_id: str, file: UploadFile = File(...)) -> dict:
    store.get_project(project_id)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_BGM_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "BGM 仅支持 AAC、FLAC、M4A、MP3、OGG、WAV"},
        )

    audio_dir = store.bgm_path(project_id, f"bgm{extension}").parent
    temporary = audio_dir / f".bgm.upload{extension}"
    old_metadata = store.get_bgm(project_id)
    destination = store.bgm_path(project_id, f"bgm{extension}")
    try:
        temporary.unlink(missing_ok=True)
        with temporary.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        media_info = probe_audio(temporary)
        temporary.replace(destination)
        metadata = {
            "filename": destination.name,
            "original_filename": file.filename or destination.name,
            **media_info,
        }
        store.save_bgm(project_id, metadata)
        if isinstance(old_metadata, dict):
            old_filename = old_metadata.get("filename")
            if isinstance(old_filename, str) and old_filename != destination.name:
                store.bgm_path(project_id, old_filename).unlink(missing_ok=True)
        return metadata
    except (OSError, MediaCommandError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": f"BGM 处理失败: {exc}"},
        ) from exc


@router.delete("/projects/{project_id}/bgm", status_code=status.HTTP_204_NO_CONTENT)
def delete_bgm_route(project_id: str) -> Response:
    store.get_project(project_id)
    store.delete_bgm(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
