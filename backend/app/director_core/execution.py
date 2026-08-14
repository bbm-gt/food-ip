"""Pure transaction-outside execution contracts for Director Core Phase 1B-1.

This module deliberately does not import the repository or open a database
connection.  It turns a caller-provided successful-turn candidate into the
deterministic values that a later persistence transaction may write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical import (
    canonical_sha256,
    canonical_text,
    is_blank_text,
    normalized_request,
    state_sha256,
    validate_normalized_request,
)
from .models import (
    FirstResponse,
    ReadyContent,
    TurnPostStateSnapshot,
    validate_turn_execution_trace,
    validate_utc_millis,
    validate_uuid4,
    validate_working_state,
)


class DirectorExecutionError(ValueError):
    """Base error for invalid or unusable Director execution data."""


class IdempotencyConflictError(DirectorExecutionError):
    """The same client id is associated with a different request."""


class StaleStateVersionError(DirectorExecutionError):
    """The caller's expected Working State version is no longer current."""


class SessionAlreadyReadyError(DirectorExecutionError):
    """A new successful turn was attempted for a terminal Session."""


class StateConflictError(DirectorExecutionError):
    """The candidate does not agree with the expected state transition."""


class DirectorBusyError(DirectorExecutionError):
    """Reserved for the later persistence executor's busy/lock outcome."""


class CommitOutcomeCorruptError(DirectorExecutionError):
    """The candidate cannot form a valid, closed successful Turn."""


@dataclass(frozen=True)
class CommitSuccessfulTurnInput:
    """Minimum input owned by the transaction-outside execution layer.

    Request hashes, versions after the pre-state, message sequences, the
    snapshot, and the first response are intentionally absent: they are
    derived by :func:`prepare_successful_turn`.
    """

    session_id: str
    client_message_id: str
    expected_state_version: int

    turn_id: str
    owner_message_id: str
    director_message_id: str

    owner_message: str
    director_message: str

    normalized_parameters: dict[str, Any]
    execution_trace: dict[str, Any]
    post_state: dict[str, Any]

    final_run_control: str
    target_stage: str
    transition_reason_code: str
    gate_outcome: str | None
    review_root_cause: str | None

    ready_content_id: str | None
    ready_content: dict[str, Any] | None

    created_at: str


@dataclass(frozen=True)
class SuccessfulTurnResult:
    """Shape reserved for a future persistence executor response."""

    response: dict[str, Any]
    replayed: bool


@dataclass(frozen=True)
class PreparedSuccessfulTurn:
    """Deterministic, validated output for a future write transaction."""

    normalized_request: dict[str, Any]
    request_sha256: str
    pre_state_version: int
    post_state_version: int
    owner_message_seq: int
    director_message_seq: int
    execution_trace: dict[str, Any]
    post_state: dict[str, Any]
    post_state_snapshot: dict[str, Any]
    post_state_sha256: str
    first_response: dict[str, Any]
    ready_content: dict[str, Any] | None

    @property
    def validated_execution_trace(self) -> dict[str, Any]:
        return self.execution_trace

    @property
    def validated_post_state(self) -> dict[str, Any]:
        return self.post_state

    @property
    def canonical_post_state_snapshot(self) -> dict[str, Any]:
        return self.post_state_snapshot

    @property
    def canonical_first_response(self) -> dict[str, Any]:
        return self.first_response

    @property
    def validated_ready_content(self) -> dict[str, Any] | None:
        return self.ready_content


_STAGES = {"EXPLORE", "DEEPEN", "CREATE", "REVIEW", "READY"}
_FINAL_RUN_CONTROLS = {"WAIT_FOR_OWNER", "READY"}


def _require_uuid(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CommitOutcomeCorruptError(f"{field_name} must be a UUIDv4 string")
    try:
        return validate_uuid4(value)
    except (TypeError, ValueError) as exc:
        raise CommitOutcomeCorruptError(f"{field_name} is not a UUIDv4") from exc


def _require_stage(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or value not in _STAGES:
        raise CommitOutcomeCorruptError(f"{field_name} is not a Director Core stage")
    return value


def _require_nonblank_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or is_blank_text(value):
        raise CommitOutcomeCorruptError(f"{field_name} must not be blank")
    return value


def _require_nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CommitOutcomeCorruptError(f"{field_name} must be a non-negative integer")
    return value


def _canonical_model_payload(value: dict[str, Any], field_name: str) -> dict[str, Any]:
    try:
        canonical_text(value)
    except (TypeError, ValueError) as exc:
        raise CommitOutcomeCorruptError(f"{field_name} is not Canonical JSON v1") from exc
    return value


def _validate_ready_closure(
    command: CommitSuccessfulTurnInput,
    *,
    post_state: dict[str, Any],
    ready_content: dict[str, Any] | None,
) -> None:
    if command.final_run_control == "READY":
        if command.target_stage != "READY":
            raise CommitOutcomeCorruptError("READY run control requires READY target stage")
        if command.ready_content_id is None or ready_content is None:
            raise CommitOutcomeCorruptError("READY requires ReadyContent")
        if command.gate_outcome != "PASSED" or command.review_root_cause is not None:
            raise CommitOutcomeCorruptError("READY requires a passed gate and no review root cause")

        review = post_state.get("review")
        draft = post_state.get("draft")
        material_state = post_state.get("material_state")
        if (
            not isinstance(review, dict)
            or review.get("outcome") != "PASSED"
            or review.get("root_cause") is not None
            or not isinstance(draft, dict)
            or draft.get("draft_id") is None
            or not isinstance(material_state, dict)
            or material_state.get("status") != "SUFFICIENT"
        ):
            raise CommitOutcomeCorruptError("READY Working State is incomplete")
        if ready_content != draft.get("content"):
            raise CommitOutcomeCorruptError("ReadyContent must equal the READY draft content")
    else:
        if command.final_run_control != "WAIT_FOR_OWNER":
            raise CommitOutcomeCorruptError("a successful non-READY Turn must wait for the owner")
        if command.target_stage == "READY":
            raise CommitOutcomeCorruptError("non-READY Turn cannot target READY")
        if command.ready_content_id is not None or ready_content is not None:
            raise CommitOutcomeCorruptError("non-READY Turn cannot carry ReadyContent")


def prepare_successful_turn(
    command: CommitSuccessfulTurnInput,
    *,
    current_stage: str,
    source_ready_content_id: str | None,
) -> PreparedSuccessfulTurn:
    """Validate and close a successful Turn candidate without database writes."""

    try:
        if not isinstance(command, CommitSuccessfulTurnInput):
            raise CommitOutcomeCorruptError("command must be CommitSuccessfulTurnInput")

        session_id = _require_uuid(command.session_id, "session_id")
        _require_nonblank_text(command.client_message_id, "client_message_id")
        pre_state_version = _require_nonnegative_int(
            command.expected_state_version, "expected_state_version"
        )
        turn_id = _require_uuid(command.turn_id, "turn_id")
        owner_message_id = _require_uuid(command.owner_message_id, "owner_message_id")
        director_message_id = _require_uuid(command.director_message_id, "director_message_id")
        if source_ready_content_id is not None:
            _require_uuid(source_ready_content_id, "source_ready_content_id")
        if command.ready_content_id is not None:
            _require_uuid(command.ready_content_id, "ready_content_id")

        owner_message = _require_nonblank_text(command.owner_message, "owner_message")
        director_message = _require_nonblank_text(command.director_message, "director_message")
        if not isinstance(command.normalized_parameters, dict):
            raise CommitOutcomeCorruptError("normalized_parameters must be an object")
        if not isinstance(command.execution_trace, dict):
            raise CommitOutcomeCorruptError("execution_trace must be an object")
        if not isinstance(command.post_state, dict):
            raise CommitOutcomeCorruptError("post_state must be an object")

        _require_stage(current_stage, "current_stage")
        if current_stage == "READY":
            raise SessionAlreadyReadyError("READY Session cannot accept a new successful Turn")
        target_stage = _require_stage(command.target_stage, "target_stage")
        if not isinstance(command.final_run_control, str) or command.final_run_control not in _FINAL_RUN_CONTROLS:
            raise CommitOutcomeCorruptError("invalid final_run_control")
        validate_utc_millis(command.created_at)

        request = normalized_request(owner_message, command.normalized_parameters)
        validate_normalized_request(request)
        request = _canonical_model_payload(request, "normalized_request")
        request_hash = canonical_sha256(request)

        post_state_version = pre_state_version + 1
        owner_message_seq = 2 * post_state_version - 1
        director_message_seq = 2 * post_state_version
        if owner_message_seq < 1 or director_message_seq < 1:
            raise StateConflictError("message sequence is outside the valid range")

        trace = validate_turn_execution_trace(
            command.execution_trace,
            pre_stage=current_stage,
            final_run_control=command.final_run_control,
            target_stage=target_stage,
            transition_reason_code=command.transition_reason_code,
            gate_outcome=command.gate_outcome,
            review_root_cause=command.review_root_cause,
        ).model_dump(mode="json")
        trace = _canonical_model_payload(trace, "execution_trace")

        validated_state = validate_working_state(
            command.post_state,
            stage=target_stage,
            state_version=post_state_version,
            source_ready_content_id=source_ready_content_id,
        ).model_dump(mode="json")
        validated_state = _canonical_model_payload(validated_state, "post_state")

        ready_content: dict[str, Any] | None = None
        if command.ready_content is not None:
            if not isinstance(command.ready_content, dict):
                raise CommitOutcomeCorruptError("ready_content must be an object or null")
            ready_content = ReadyContent.model_validate(command.ready_content).model_dump(mode="json")
            ready_content = _canonical_model_payload(ready_content, "ready_content")
        _validate_ready_closure(command, post_state=validated_state, ready_content=ready_content)

        snapshot = TurnPostStateSnapshot.model_validate(
            {
                "snapshot_format_version": 1,
                "state_version": post_state_version,
                "stage": target_stage,
                "state_json": validated_state,
            }
        ).model_dump(mode="json")
        snapshot = _canonical_model_payload(snapshot, "post_state_snapshot")
        post_hash = state_sha256(post_state_version, target_stage, validated_state)

        response = FirstResponse.model_validate(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "owner_message_id": owner_message_id,
                "director_message_id": director_message_id,
                "state_version": post_state_version,
                "stage": target_stage,
                "run_control": command.final_run_control,
                "director_message": director_message,
                "ready_content_id": command.ready_content_id,
            }
        ).model_dump(mode="json")
        response = _canonical_model_payload(response, "first_response")
        if response["director_message"] != director_message:
            raise CommitOutcomeCorruptError("FirstResponse DIRECTOR text mismatch")

        return PreparedSuccessfulTurn(
            normalized_request=request,
            request_sha256=request_hash,
            pre_state_version=pre_state_version,
            post_state_version=post_state_version,
            owner_message_seq=owner_message_seq,
            director_message_seq=director_message_seq,
            execution_trace=trace,
            post_state=validated_state,
            post_state_snapshot=snapshot,
            post_state_sha256=post_hash,
            first_response=response,
            ready_content=ready_content,
        )
    except DirectorExecutionError:
        raise
    except (TypeError, ValueError) as exc:
        raise CommitOutcomeCorruptError("successful Turn candidate is invalid") from exc


__all__ = [
    "CommitOutcomeCorruptError",
    "CommitSuccessfulTurnInput",
    "DirectorBusyError",
    "DirectorExecutionError",
    "IdempotencyConflictError",
    "PreparedSuccessfulTurn",
    "SessionAlreadyReadyError",
    "StaleStateVersionError",
    "StateConflictError",
    "SuccessfulTurnResult",
    "prepare_successful_turn",
]
