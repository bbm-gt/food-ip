"""Edit persistence and authoritative timeline routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

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


@router.get("/projects/{project_id}/timeline")
def get_timeline_route(project_id: str) -> dict:
    materials = store.list_materials(project_id)
    return compute_timeline(materials, _effective_edits(project_id, materials))

