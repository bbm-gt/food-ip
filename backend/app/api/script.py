"""Script generation and editing API routes."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse

from ..core.store import (
    get_project,
    load_ip_profile,
    load_script,
    load_script_bundle,
    load_script_versions,
    save_research,
    save_script,
    save_script_bundle,
    update_project,
)
from ..scriptgen.bundles import generate_script_bundle
from ..scriptgen.ai import (
    AIConfigurationError,
    AIResponseError,
    AIScriptError,
    generate_ai_script_bundle,
)
from ..scriptgen.generators import get
from ..scriptgen.quality import annotate_script_quality
from ..scriptgen.models import (
    BossInfo,
    ResearchProfile,
    ScriptBundle,
    ScriptModel,
    ScriptVersion,
)


router = APIRouter(tags=["script"])


class GenerateBundleRequest(BaseModel):
    research: ResearchProfile
    candidate_count: int = Field(default=3, ge=2, le=5)


@router.post("/projects/{project_id}/script/template", response_model=ScriptModel)
def generate_template_route(project_id: str, body: BossInfo) -> ScriptModel:
    get_project(project_id)
    update_project(project_id, boss_info=body.model_dump(mode="json"))
    scan_profile = ResearchProfile.from_boss_info(
        body.model_copy(
            update={
                "target_duration_seconds": min(
                    180, max(15, body.target_duration_seconds)
                )
            }
        )
    )
    script = annotate_script_quality(get("template").generate(body), scan_profile)
    save_script(project_id, script, source="template_generation")
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
    save_script(project_id, body, source="manual_save")
    return body


@router.get(
    "/projects/{project_id}/script/versions",
    response_model=list[ScriptVersion],
)
def get_script_versions_route(project_id: str) -> list[ScriptVersion]:
    return list(reversed(load_script_versions(project_id)))


@router.post(
    "/projects/{project_id}/script-bundles/template",
    response_model=ScriptBundle,
)
def generate_script_bundle_route(
    project_id: str, body: GenerateBundleRequest
) -> ScriptBundle:
    get_project(project_id)
    save_research(project_id, body.research)
    bundle = generate_script_bundle(body.research, body.candidate_count)
    return save_script_bundle(project_id, bundle)


@router.post(
    "/projects/{project_id}/script-bundles/ai",
    response_model=ScriptBundle,
    responses={
        status.HTTP_502_BAD_GATEWAY: {"description": "AI 输出或服务异常"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "AI 尚未配置"},
    },
)
def generate_ai_script_bundle_route(
    project_id: str, body: GenerateBundleRequest
) -> ScriptBundle:
    get_project(project_id)
    save_research(project_id, body.research)
    try:
        bundle = generate_ai_script_bundle(body.research, body.candidate_count)
    except AIConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"message": str(exc)}
        ) from exc
    except AIResponseError as exc:
        # AI 返回结构错误 / schema 校验失败 / 质量检查失败 → 规则模板兜底，
        # 保证用户始终有可用方案；不吞掉配置缺失(503)与服务异常(502)。
        fallback = generate_script_bundle(body.research, body.candidate_count)
        fallback = fallback.model_copy(
            update={
                "generator": "template_fallback",
                "model_name": "",
                "warnings": [
                    *fallback.warnings,
                    f"AI 生成未通过质检，已用规则模板兜底：{exc}",
                ],
            }
        )
        return save_script_bundle(project_id, fallback)
    except AIScriptError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": f"AI 脚本生成失败：{exc}"},
        ) from exc
    return save_script_bundle(project_id, bundle)


@router.post(
    "/projects/{project_id}/script-bundles/ip-ai",
    response_model=ScriptBundle,
    responses={
        status.HTTP_409_CONFLICT: {"description": "IP 定位尚未确认"},
        status.HTTP_502_BAD_GATEWAY: {"description": "AI 输出或服务异常"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "AI 尚未配置"},
    },
)
def generate_ip_script_bundle_route(
    project_id: str, body: GenerateBundleRequest
) -> ScriptBundle:
    profile = load_ip_profile(project_id)
    if not profile.confirmed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "请先确认 IP 定位，再开始脚本共创"},
        )
    save_research(project_id, body.research)
    try:
        bundle = generate_ai_script_bundle(
            body.research,
            body.candidate_count,
            creative_context={"ip_profile": profile.model_dump(mode="json")},
        )
    except AIConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": str(exc)},
        ) from exc
    except AIScriptError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": f"AI 脚本生成失败：{exc}"},
        ) from exc
    return save_script_bundle(project_id, bundle)


@router.get(
    "/projects/{project_id}/script-bundles/latest",
    response_model=ScriptBundle,
    responses={status.HTTP_404_NOT_FOUND: {"description": "脚本方案不存在"}},
)
def get_script_bundle_route(project_id: str) -> ScriptBundle:
    bundle = load_script_bundle(project_id)
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "该项目还没有多套脚本方案"},
        )
    return bundle


@router.post(
    "/projects/{project_id}/script-bundles/{bundle_id}/select/{script_id}",
    response_model=ScriptModel,
)
def select_script_candidate_route(
    project_id: str, bundle_id: str, script_id: str
) -> ScriptModel:
    bundle = load_script_bundle(project_id)
    if bundle is None or bundle.id != bundle_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "脚本方案不存在或已过期"},
        )
    candidate = next(
        (item for item in bundle.candidates if item.id == script_id), None
    )
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "候选脚本不存在"},
        )
    selected = bundle.model_copy(update={"selected_script_id": candidate.id})
    save_script_bundle(project_id, selected)
    save_script(project_id, candidate.script, source="candidate_selection")
    return candidate.script
