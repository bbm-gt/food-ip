"""Project CRUD API routes."""

from fastapi import APIRouter, status
from pydantic import BaseModel

from ..core.store import (
    create_project,
    get_project,
    list_projects,
    load_research,
    save_research,
    update_project,
)
from ..scriptgen.models import BossInfo, ResearchProfile


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
