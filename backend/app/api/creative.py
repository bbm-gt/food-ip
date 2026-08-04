"""Creative conversation and confirmed-brief generation APIs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ..core.store import (
    list_creative_conversations,
    load_creative_conversation,
    load_ip_profile,
    load_research,
    load_script,
    save_creative_conversation,
    save_script_bundle,
)
from ..scriptgen.ai import (
    AIConfigurationError,
    AIScriptError,
    generate_ai_script_bundle,
)
from ..scriptgen.creative import (
    generate_creative_turn,
    generate_topic_cards,
    missing_brief_fields,
    normalize_brief,
)
from ..scriptgen.models import (
    ConversationStage,
    CreativeBrief,
    CreativeConversation,
    CreativeMessage,
    CreativeMode,
    FactScope,
    ScriptBundle,
    TopicCard,
    TopicCardSet,
)


router = APIRouter(tags=["creative"])


class CreateConversationRequest(BaseModel):
    mode: CreativeMode


class AddOwnerMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    fact_scope: FactScope = FactScope.EPISODE_ONLY
    client_message_id: str = Field(default_factory=lambda: uuid4().hex[:12])


class GenerateFromBriefRequest(BaseModel):
    candidate_count: int = Field(default=3, ge=2, le=5)
    topic_card_id: str | None = None


class GenerateTopicCardsRequest(BaseModel):
    card_count: int = Field(default=4, ge=3, le=6)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _save_updated(conversation: CreativeConversation) -> CreativeConversation:
    updated = conversation.model_copy(update={"updated_at": _now()})
    return save_creative_conversation(updated.project_id, updated)


def _raise_ai_error(exc: AIScriptError, action: str) -> NoReturn:
    if isinstance(exc, AIConfigurationError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": str(exc)},
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"message": f"AI{action}失败：{exc}"},
    ) from exc


def _require_confirmed_brief(
    conversation: CreativeConversation,
) -> CreativeConversation:
    if (
        conversation.stage != ConversationStage.CONFIRMED
        or conversation.brief is None
        or not conversation.brief.confirmed
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "请先确认 CreativeBrief，再生成选题或脚本"},
        )
    return conversation


@router.post(
    "/projects/{project_id}/creative-conversations",
    response_model=CreativeConversation,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation_route(
    project_id: str, body: CreateConversationRequest
) -> CreativeConversation:
    research = load_research(project_id)
    ip_profile = load_ip_profile(project_id)
    if not ip_profile.confirmed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "请先确认IP定位，再开始脚本共创"},
        )
    source_script = (
        load_script(project_id)
        if body.mode == CreativeMode.REVISE_SCRIPT
        else None
    )
    if body.mode == CreativeMode.REVISE_SCRIPT and source_script is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "修改现有脚本模式需要项目中已有脚本"},
        )
    conversation = CreativeConversation(
        project_id=project_id,
        mode=body.mode,
        research_snapshot=research,
        ip_profile_snapshot=ip_profile,
        source_script=source_script,
    )
    return save_creative_conversation(project_id, conversation)


@router.get(
    "/projects/{project_id}/creative-conversations",
    response_model=list[CreativeConversation],
)
def list_conversations_route(project_id: str) -> list[CreativeConversation]:
    return list_creative_conversations(project_id)


@router.get(
    "/projects/{project_id}/creative-conversations/{conversation_id}",
    response_model=CreativeConversation,
)
def get_conversation_route(
    project_id: str, conversation_id: str
) -> CreativeConversation:
    return load_creative_conversation(project_id, conversation_id)


@router.post(
    "/projects/{project_id}/creative-conversations/{conversation_id}/messages",
    response_model=CreativeConversation,
)
def add_owner_message_route(
    project_id: str,
    conversation_id: str,
    body: AddOwnerMessageRequest,
) -> CreativeConversation:
    conversation = load_creative_conversation(project_id, conversation_id)
    if conversation.stage == ConversationStage.CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "CreativeBrief 已确认；如需调整，请新建一轮共创对话"},
        )

    existing = next(
        (message for message in conversation.messages if message.id == body.client_message_id),
        None,
    )
    if existing is not None:
        if (
            existing.role != "owner"
            or existing.content != body.content
            or existing.fact_scope != body.fact_scope
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "client_message_id 已被其他消息使用"},
            )
        if any(
            message.reply_to_message_id == existing.id
            for message in conversation.messages
            if message.role == "ai"
        ):
            return conversation
    else:
        owner_message = CreativeMessage(
            id=body.client_message_id,
            role="owner",
            content=body.content,
            fact_scope=body.fact_scope,
            trust_status="untrusted",
        )
        conversation = conversation.model_copy(
            update={
                "messages": [*conversation.messages, owner_message],
                "last_error": None,
            }
        )
        conversation = _save_updated(conversation)
        existing = owner_message

    try:
        result = generate_creative_turn(conversation)
    except AIScriptError as exc:
        failed = conversation.model_copy(update={"last_error": str(exc)})
        _save_updated(failed)
        _raise_ai_error(exc, "共创")

    assistant_message = CreativeMessage(
        role="ai",
        content=result.reply,
        questions=result.questions,
        trust_status="assistant_synthesis",
        reply_to_message_id=existing.id,
    )
    brief = result.brief if result.brief is not None else conversation.brief
    stage = (
        ConversationStage.BRIEF_READY
        if brief is not None
        and not result.questions
        and not missing_brief_fields(brief)
        else ConversationStage.COLLECTING
    )
    completed = conversation.model_copy(
        update={
            "messages": [*conversation.messages, assistant_message],
            "brief": brief,
            "stage": stage,
            "last_error": None,
        }
    )
    return _save_updated(completed)


@router.put(
    "/projects/{project_id}/creative-conversations/{conversation_id}/brief",
    response_model=CreativeConversation,
)
def update_brief_route(
    project_id: str, conversation_id: str, body: CreativeBrief
) -> CreativeConversation:
    conversation = load_creative_conversation(project_id, conversation_id)
    if conversation.stage == ConversationStage.CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "已确认的 CreativeBrief 不能直接覆盖"},
        )
    brief = normalize_brief(
        body.model_copy(update={"confirmed": False, "confirmed_at": None}),
        conversation,
    )
    assert brief is not None
    stage = (
        ConversationStage.BRIEF_READY
        if not missing_brief_fields(brief)
        else ConversationStage.COLLECTING
    )
    return _save_updated(
        conversation.model_copy(update={"brief": brief, "stage": stage})
    )


@router.post(
    "/projects/{project_id}/creative-conversations/{conversation_id}/brief/confirm",
    response_model=CreativeConversation,
)
def confirm_brief_route(
    project_id: str, conversation_id: str
) -> CreativeConversation:
    conversation = load_creative_conversation(project_id, conversation_id)
    if conversation.brief is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "还没有可确认的 CreativeBrief"},
        )
    if conversation.stage != ConversationStage.BRIEF_READY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "CreativeBrief 仍在收集中，请先回答关键问题或完成编辑"},
        )
    normalized_brief = normalize_brief(conversation.brief, conversation)
    assert normalized_brief is not None
    missing = missing_brief_fields(normalized_brief)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": f"CreativeBrief 信息不完整：{', '.join(missing)}"},
        )
    brief = normalized_brief.model_copy(
        update={"confirmed": True, "confirmed_at": _now()}
    )
    return _save_updated(
        conversation.model_copy(
            update={"brief": brief, "stage": ConversationStage.CONFIRMED}
        )
    )


@router.post(
    "/projects/{project_id}/creative-conversations/{conversation_id}/topic-cards/ai",
    response_model=TopicCardSet,
    responses={
        status.HTTP_409_CONFLICT: {"description": "CreativeBrief 尚未确认"},
        status.HTTP_502_BAD_GATEWAY: {"description": "AI 输出或服务异常"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "AI 尚未配置"},
    },
)
def generate_topic_cards_route(
    project_id: str,
    conversation_id: str,
    body: GenerateTopicCardsRequest,
) -> TopicCardSet:
    conversation = _require_confirmed_brief(
        load_creative_conversation(project_id, conversation_id)
    )
    try:
        card_set = generate_topic_cards(conversation, body.card_count)
    except AIScriptError as exc:
        _raise_ai_error(exc, "选题卡生成")
    updated = conversation.model_copy(
        update={"topic_card_set": card_set, "last_error": None}
    )
    _save_updated(updated)
    return card_set


@router.post(
    "/projects/{project_id}/creative-conversations/{conversation_id}"
    "/topic-cards/{topic_card_id}/select",
    response_model=TopicCardSet,
)
def select_topic_card_route(
    project_id: str,
    conversation_id: str,
    topic_card_id: str,
) -> TopicCardSet:
    conversation = _require_confirmed_brief(
        load_creative_conversation(project_id, conversation_id)
    )
    card_set = conversation.topic_card_set
    if card_set is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "请先生成选题卡"},
        )
    if not any(card.id == topic_card_id for card in card_set.cards):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "选题卡不存在或已过期"},
        )
    selected = card_set.model_copy(
        update={"selected_topic_card_id": topic_card_id}
    )
    _save_updated(conversation.model_copy(update={"topic_card_set": selected}))
    return selected


@router.post(
    "/projects/{project_id}/creative-conversations/{conversation_id}/script-bundles/ai",
    response_model=ScriptBundle,
    responses={
        status.HTTP_409_CONFLICT: {"description": "CreativeBrief 尚未确认"},
        status.HTTP_502_BAD_GATEWAY: {"description": "AI 输出或服务异常"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "AI 尚未配置"},
    },
)
def generate_from_brief_route(
    project_id: str,
    conversation_id: str,
    body: GenerateFromBriefRequest,
) -> ScriptBundle:
    conversation = _require_confirmed_brief(
        load_creative_conversation(project_id, conversation_id)
    )
    selected_topic: TopicCard | None = None
    if body.topic_card_id is not None:
        card_set = conversation.topic_card_set
        if card_set is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "请先生成并选择一张选题卡"},
            )
        selected_topic = next(
            (card for card in card_set.cards if card.id == body.topic_card_id),
            None,
        )
        if selected_topic is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"message": "选题卡不存在或已过期"},
            )
        if card_set.selected_topic_card_id != selected_topic.id:
            selected_set = card_set.model_copy(
                update={"selected_topic_card_id": selected_topic.id}
            )
            conversation = _save_updated(
                conversation.model_copy(update={"topic_card_set": selected_set})
            )
    creative_context: dict[str, object] = {
        "mode": conversation.mode.value,
        "brief": conversation.brief.model_dump(mode="json"),
        "selected_topic_card": (
            selected_topic.model_dump(mode="json")
            if selected_topic is not None
            else None
        ),
        "existing_script": (
            conversation.source_script.model_dump(mode="json")
            if conversation.source_script is not None
            else None
        ),
    }
    try:
        bundle = generate_ai_script_bundle(
            conversation.research_snapshot,
            body.candidate_count,
            creative_context=creative_context,
        )
    except AIScriptError as exc:
        _raise_ai_error(exc, "脚本生成")
    return save_script_bundle(project_id, bundle)
