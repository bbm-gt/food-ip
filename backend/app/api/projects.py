"""Project CRUD API routes."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from ..core.store import (
    create_project,
    get_project,
    list_projects,
    load_ip_profile,
    load_research,
    save_ip_profile,
    save_research,
    update_project,
)
from ..scriptgen.ip_profile import generate_ip_profile
from ..scriptgen.models import BossInfo, IPProfile, ResearchProfile


router = APIRouter(tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project_route(body: CreateProjectRequest) -> dict:
    return create_project(body.name)


@router.get("/projects")
def list_projects_route() -> list[dict]:
    return list_projects()


@router.get("/projects/{project_id}")
def get_project_route(project_id: str) -> dict:
    return get_project(project_id)


@router.patch("/projects/{project_id}")
def patch_project_route(project_id: str, body: BossInfo) -> dict:
    project = get_project(project_id)
    current = BossInfo.model_validate(project.get("boss_info") or {})
    updated = current.model_copy(update=body.model_dump(exclude_unset=True))
    return update_project(project_id, boss_info=updated.model_dump(mode="json"))


@router.get("/projects/{project_id}/research", response_model=ResearchProfile)
def get_research_route(project_id: str) -> ResearchProfile:
    return load_research(project_id)


@router.put("/projects/{project_id}/research", response_model=ResearchProfile)
def put_research_route(
    project_id: str, body: ResearchProfile
) -> ResearchProfile:
    return save_research(project_id, body)


@router.get("/projects/{project_id}/ip-profile", response_model=IPProfile)
def get_ip_profile_route(project_id: str) -> IPProfile:
    return load_ip_profile(project_id)


@router.put("/projects/{project_id}/ip-profile", response_model=IPProfile)
def put_ip_profile_route(project_id: str, body: IPProfile) -> IPProfile:
    current = load_ip_profile(project_id)
    if current.confirmed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "已确认的 IP 定位不能直接覆盖"},
        )
    return save_ip_profile(
        project_id,
        body.model_copy(update={"confirmed": False, "confirmed_at": None}),
    )


@router.post("/projects/{project_id}/ip-profile/draft", response_model=IPProfile)
def generate_ip_profile_draft_route(project_id: str) -> IPProfile:
    current = load_ip_profile(project_id)
    if current.confirmed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "IP 定位已经确认"},
        )
    return save_ip_profile(project_id, generate_ip_profile(load_research(project_id)))


@router.post("/projects/{project_id}/ip-profile/confirm", response_model=IPProfile)
def confirm_ip_profile_route(project_id: str) -> IPProfile:
    current = load_ip_profile(project_id)
    if current.confirmed:
        return current
    confirmed = current.model_copy(
        update={"confirmed": True, "confirmed_at": datetime.now(UTC).isoformat()}
    )
    return save_ip_profile(project_id, confirmed)
