"""Script generation and editing API routes."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from ..core.store import get_project, load_script, save_script, update_project
from ..scriptgen.generators import get
from ..scriptgen.models import BossInfo, ScriptModel


router = APIRouter(tags=["script"])


@router.post("/projects/{project_id}/script/template", response_model=ScriptModel)
def generate_template_route(project_id: str, body: BossInfo) -> ScriptModel:
    get_project(project_id)
    update_project(project_id, boss_info=body.model_dump(mode="json"))
    script = get("template").generate(body)
    save_script(project_id, script)
    return script


@router.get(
    "/projects/{project_id}/script",
    response_model=ScriptModel,
    responses={status.HTTP_404_NOT_FOUND: {"description": "项目或脚本不存在"}},
)
def get_script_route(project_id: str) -> ScriptModel | JSONResponse:
    script = load_script(project_id)
    if script is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"message": "该项目还没有脚本"},
        )
    return script


@router.put("/projects/{project_id}/script", response_model=ScriptModel)
def put_script_route(project_id: str, body: ScriptModel) -> ScriptModel:
    save_script(project_id, body)
    return body
