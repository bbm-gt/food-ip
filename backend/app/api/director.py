"""Minimal public HTTP boundary for the independent Director Core."""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, UUID4

from ..core.store import get_project
from ..director_core.execution import (
    DirectorExecutionValidationError,
    IdempotencyConflictError,
    SessionReadyError,
    StaleStateVersionError,
)
from ..director_core.orchestrator import DirectorTurnRequest
from ..director_core.providers.deepseek import (
    DeepSeekConfigurationError,
    DeepSeekHTTPStatusError,
    DeepSeekProviderError,
    DeepSeekTransportError,
)
from ..director_core.repository import (
    CommitOutcomeIndeterminateError,
    CommitRolledBackError,
    DirectorIntegrityError,
    DirectorNotFoundError,
    SQLiteBusyError,
    is_sqlite_lock_error,
)
from ..director_core.stage_handler import StageModelOutputError
from ..director_runtime import (
    create_director_orchestrator,
    director_repository,
    director_scope,
)


router = APIRouter(tags=["director"])


class CreateDirectorSessionRequest(BaseModel):
    source_ready_content_id: UUID4 | None = None


class DirectorSessionResponse(BaseModel):
    session_id: str
    lifecycle_status: Literal["ACTIVE"]
    state_version: Literal[0]
    source_ready_content_id: str | None


class SubmitOwnerMessageRequest(BaseModel):
    client_message_id: UUID4
    expected_state_version: int = Field(ge=0, le=9_223_372_036_854_775_807)
    content: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class DirectorMessageResponse(BaseModel):
    id: str
    role: Literal["DIRECTOR"]
    content: str


class DirectorTurnResponse(BaseModel):
    session_id: str
    turn_id: str
    state_version: int
    message: DirectorMessageResponse
    status: Literal["WAITING_FOR_OWNER", "READY"]
    ready_content: dict[str, Any] | None
    replayed: bool


def _http_error(status_code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"message": message})


def _raise_mapped_error(error: Exception) -> None:
    """Translate only known boundary failures and never expose provider details."""

    if isinstance(error, DirectorNotFoundError):
        raise _http_error(status.HTTP_404_NOT_FOUND, "Director 资源不存在") from error
    if isinstance(error, (IdempotencyConflictError, StaleStateVersionError)):
        raise _http_error(status.HTTP_409_CONFLICT, "请求与当前 Director 状态冲突") from error
    if isinstance(error, SessionReadyError):
        raise _http_error(status.HTTP_409_CONFLICT, "session_ready") from error
    if isinstance(error, (SQLiteBusyError, CommitRolledBackError, CommitOutcomeIndeterminateError)):
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "Director 服务暂时不可用，请使用同一消息 ID 重试") from error
    if isinstance(error, DeepSeekConfigurationError):
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "Director 模型服务暂不可用") from error
    if isinstance(error, DeepSeekTransportError):
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "Director 模型服务暂时不可用") from error
    if isinstance(error, DeepSeekHTTPStatusError):
        code = status.HTTP_503_SERVICE_UNAVAILABLE if error.status_code == 429 or error.status_code >= 500 else status.HTTP_502_BAD_GATEWAY
        raise _http_error(code, "Director 模型服务返回异常") from error
    if isinstance(error, (DeepSeekProviderError, StageModelOutputError)):
        raise _http_error(status.HTTP_502_BAD_GATEWAY, "Director 模型输出不可用") from error
    if isinstance(error, DirectorIntegrityError):
        raise _http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "Director 持久化完整性错误") from error
    if isinstance(error, sqlite3.DatabaseError) and is_sqlite_lock_error(error):
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "Director 服务暂时不可用，请使用同一消息 ID 重试") from error
    if isinstance(error, (DirectorExecutionValidationError, sqlite3.DatabaseError)):
        raise _http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "Director 内部执行错误") from error
    raise error


@router.post(
    "/projects/{project_id}/director-sessions",
    response_model=DirectorSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_director_session_route(
    project_id: str, body: CreateDirectorSessionRequest
) -> DirectorSessionResponse:
    # The legacy project remains only the authorization/existence boundary;
    # no legacy creative state is read into the Director Core.
    get_project(project_id)
    scope = director_scope(project_id)
    try:
        with director_repository() as repository:
            if body.source_ready_content_id is None:
                session = repository.create_session(scope)
            else:
                session = repository.create_revision_session(
                    scope, str(body.source_ready_content_id)
                )
            return DirectorSessionResponse(
                session_id=session.id,
                lifecycle_status="ACTIVE",
                state_version=0,
                source_ready_content_id=session.source_ready_content_id,
            )
    except Exception as error:
        _raise_mapped_error(error)
        raise AssertionError("unreachable")


@router.post(
    "/projects/{project_id}/director-sessions/{session_id}/messages",
    response_model=DirectorTurnResponse,
)
def submit_owner_message_route(
    project_id: str,
    session_id: UUID4,
    body: SubmitOwnerMessageRequest,
) -> DirectorTurnResponse:
    get_project(project_id)
    scoped_session_id = str(session_id)
    scope = director_scope(project_id)
    try:
        with director_repository() as repository:
            # Existence must be established before building the provider so an
            # unknown Session remains a 404 even when model configuration is
            # unavailable. READY handling stays inside the Orchestrator so a
            # duplicate successful Turn can still replay first.
            repository.get_session(scope, scoped_session_id)
            result = create_director_orchestrator(repository, scope).run(
                DirectorTurnRequest(
                    session_id=scoped_session_id,
                    client_message_id=str(body.client_message_id),
                    expected_state_version=body.expected_state_version,
                    owner_text=body.content,
                    request_format_version=1,
                    parameters=body.parameters,
                )
            )
            response = result.response
            ready_content = None
            if response["run_control"] == "READY":
                ready = repository.get_ready_content(scope, response["ready_content_id"])
                ready_content = {"id": ready["id"], **ready["final_content_json"]}
            return DirectorTurnResponse(
                session_id=response["session_id"],
                turn_id=response["turn_id"],
                state_version=response["state_version"],
                message=DirectorMessageResponse(
                    id=response["director_message_id"],
                    role="DIRECTOR",
                    content=response["director_message"],
                ),
                status=("READY" if response["run_control"] == "READY" else "WAITING_FOR_OWNER"),
                ready_content=ready_content,
                replayed=result.replayed,
            )
    except Exception as error:
        _raise_mapped_error(error)
        raise AssertionError("unreachable")
