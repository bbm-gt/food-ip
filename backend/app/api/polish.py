"""Placeholder HTTP API for the second-phase AI polish capability."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ..core import store
from ..polish import PolishRequest, PolishResult, REGISTRY, SegmentRef, get


router = APIRouter(tags=["polish"])


class PolishJunctionBody(BaseModel):
    goal: Literal[
        "harmonize_junction", "stabilize", "relight", "fix_audio"
    ] = "harmonize_junction"
    params: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/projects/{project_id}/polish/junctions/{junction_index}",
    response_model=PolishResult,
)
async def polish_junction_route(
    project_id: str, junction_index: int, body: PolishJunctionBody
) -> PolishResult:
    store.get_project(project_id)
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "接缝不存在：相邻素材未补齐"},
        )

    request = PolishRequest(
        segment=SegmentRef(project_id=project_id, junction_id=junction_index),
        goal=body.goal,
        params=body.params,
    )
    return await get("null").polish(request)


@router.get("/projects/{project_id}/polish/providers", response_model=list[str])
def list_polish_providers_route(project_id: str) -> list[str]:
    store.get_project(project_id)
    return sorted(REGISTRY)
