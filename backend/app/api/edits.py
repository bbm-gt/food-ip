"""Edit persistence and authoritative timeline routes."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from .. import config
from ..core import store
from ..engine.timeline import compute_timeline, normalize_edits


router = APIRouter(tags=["edits"])


class ShotEdit(BaseModel):
    trim_head: float = Field(default=0.0)
    trim_tail: float = Field(default=0.0)


class JunctionEdit(BaseModel):
    transition: Literal["hard", "fade", "crossfade"] = "fade"
    fade_seconds: float = 0.5


class EditsPayload(BaseModel):
    shots: list[ShotEdit] = Field(default_factory=list)
    junctions: list[JunctionEdit] = Field(default_factory=list)


class JunctionPayload(BaseModel):
    trim_tail: float
    trim_head: float
    transition: Literal["hard", "fade", "crossfade"]
    fade_seconds: float


def _default_edits(material_count: int) -> dict:
    return {
        "shots": [
            {"trim_head": 0.0, "trim_tail": 0.0}
            for _ in range(material_count)
        ],
        "junctions": [
            {"transition": "fade", "fade_seconds": 0.5}
            for _ in range(max(0, material_count - 1))
        ],
    }


def _effective_edits(project_id: str, materials: list[dict]) -> dict:
    saved = store.get_edits(project_id)
    requested = saved if saved is not None else _default_edits(len(materials))
    return normalize_edits(materials, requested)


@router.get("/projects/{project_id}/edits")
def get_edits_route(project_id: str) -> dict:
    materials = store.list_materials(project_id)
    return _effective_edits(project_id, materials)


@router.put("/projects/{project_id}/edits")
def put_edits_route(project_id: str, body: EditsPayload) -> dict:
    materials = store.list_materials(project_id)
    effective = normalize_edits(materials, body.model_dump(mode="json"))
    store.save_edits(project_id, effective)
    return {
        "edits": effective,
        "timeline": compute_timeline(materials, effective),
    }


@router.put("/projects/{project_id}/junctions/{junction_index}")
def put_junction_route(
    project_id: str, junction_index: int, body: JunctionPayload
) -> dict:
    materials = store.list_materials(project_id)
    if junction_index < 0 or junction_index >= max(0, len(materials) - 1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "接缝序号超出范围"},
        )
    left = materials[junction_index]
    right = materials[junction_index + 1]
    left_shot_index = left.get("shot_index")
    right_shot_index = right.get("shot_index")
    if (
        not isinstance(left_shot_index, int)
        or not isinstance(right_shot_index, int)
        or right_shot_index != left_shot_index + 1
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "接缝相邻素材缺失，请先补齐镜头"},
        )

    requested = _effective_edits(project_id, materials)
    requested["shots"][junction_index]["trim_tail"] = body.trim_tail
    requested["shots"][junction_index + 1]["trim_head"] = body.trim_head
    requested["junctions"][junction_index] = {
        "transition": body.transition,
        "fade_seconds": body.fade_seconds,
    }
    effective = normalize_edits(materials, requested)
    store.save_edits(project_id, effective)
    preview = (
        Path(config.PROJECTS_ROOT)
        / project_id
        / "work"
        / f"preview_j{junction_index}.mp4"
    )
    preview.unlink(missing_ok=True)
    return {
        "edits": effective,
        "timeline": compute_timeline(materials, effective),
    }


@router.get("/projects/{project_id}/timeline")
def get_timeline_route(project_id: str) -> dict:
    materials = store.list_materials(project_id)
    return compute_timeline(materials, _effective_edits(project_id, materials))
