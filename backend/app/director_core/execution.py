"""Pure transaction-outside successful-Turn preparation for Director Core."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from .canonical import (
    SQLITE_INT_MAX,
    canonical_sha256,
    canonical_text,
    is_blank_text,
    normalized_request,
    parse_canonical_object,
    state_sha256,
    validate_normalized_request,
)
from .models import (
    FirstResponse,
    ReadyContent,
    Stage,
    TurnPostStateSnapshot,
    validate_turn_execution_trace,
    validate_utc_millis,
    validate_uuid4,
    validate_working_state,
)


class DirectorExecutionError(ValueError):
    """Base error for Director Core execution preparation."""


class DirectorExecutionValidationError(DirectorExecutionError):
    """A pure input, trace, state, or successful-Turn closure violation."""


class StaleStateVersionError(DirectorExecutionError):
    """The request's expected state version is not the current authority."""


class IdempotencyConflictError(DirectorExecutionError):
    """A client message ID was previously committed with another request."""


@dataclass(frozen=True)
class PreparedIdempotencyRequest:
    """Immutable canonical identity of a client request, excluding state version."""

    session_id: str
    client_message_id: str
    request_format_version: int
    normalized_request_json: str
    request_sha256: str

    @property
    def normalized_request(self) -> dict[str, Any]:
        return parse_canonical_object(self.normalized_request_json)


def validate_prepared_idempotency_request(
    request: PreparedIdempotencyRequest,
) -> PreparedIdempotencyRequest:
    """Re-validate an idempotency value supplied across the repository boundary.

    ``PreparedIdempotencyRequest`` is intentionally a frozen value object, but
    callers can still construct one directly (or use ``dataclasses.replace``).
    Never trust its persisted identity fields until the canonical request and
    digest have been closed again here.
    """
    if not isinstance(request, PreparedIdempotencyRequest):
        raise DirectorExecutionValidationError("request must be PreparedIdempotencyRequest")
    try:
        session_id = _require_uuid(request.session_id, "session_id")
        client_message_id = _require_nonblank_text(request.client_message_id, "client_message_id")
        if (
            isinstance(request.request_format_version, bool)
            or not isinstance(request.request_format_version, int)
            or request.request_format_version != 1
        ):
            raise DirectorExecutionValidationError("only request_format_version 1 is supported")
        if not isinstance(request.normalized_request_json, str):
            raise DirectorExecutionValidationError("normalized_request_json must be canonical JSON text")
        normalized = parse_canonical_object(request.normalized_request_json)
        validate_normalized_request(normalized)
        canonical_json = canonical_text(normalized)
        if canonical_json != request.normalized_request_json:
            raise DirectorExecutionValidationError("normalized_request_json is not canonical")
        if not isinstance(request.request_sha256, str):
            raise DirectorExecutionValidationError("request_sha256 must be a hexadecimal digest")
        digest = canonical_sha256(normalized)
        if digest != request.request_sha256:
            raise DirectorExecutionValidationError("request_sha256 does not close normalized_request_json")
        return PreparedIdempotencyRequest(
            session_id=session_id,
            client_message_id=client_message_id,
            request_format_version=1,
            normalized_request_json=canonical_json,
            request_sha256=digest,
        )
    except DirectorExecutionError:
        raise
    except (TypeError, ValueError) as exc:
        raise DirectorExecutionValidationError("prepared idempotency request is invalid") from exc


@dataclass(frozen=True)
class CommitSuccessfulTurnInput:
    """Raw successful-Turn facts; all persistence values are derived internally."""

    session_id: str
    client_message_id: str
    expected_state_version: int
    request_format_version: int
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
    """A validated FirstResponse snapshot with no shared mutable response."""

    first_response_json: str
    replayed: bool

    def __init__(
        self,
        first_response_json: str | None = None,
        replayed: bool = False,
        *,
        response: dict[str, Any] | None = None,
    ) -> None:
        if type(replayed) is not bool:
            raise DirectorExecutionValidationError("replayed must be a bool")
        if first_response_json is None:
            if response is None:
                raise DirectorExecutionValidationError("first_response_json is required")
            try:
                first_response_json = canonical_text(response)
            except (TypeError, ValueError) as exc:
                raise DirectorExecutionValidationError("response is not Canonical JSON v1") from exc
        elif response is not None:
            raise DirectorExecutionValidationError("provide first_response_json or response, not both")
        if not isinstance(first_response_json, str):
            raise DirectorExecutionValidationError("first_response_json must be canonical JSON text")
        try:
            parsed = parse_canonical_object(first_response_json)
            validated = FirstResponse.model_validate(parsed).model_dump(mode="json")
            if canonical_text(validated) != first_response_json:
                raise ValueError("FirstResponse JSON is not canonical")
        except (TypeError, ValueError) as exc:
            raise DirectorExecutionValidationError("first_response_json is not a valid FirstResponse") from exc
        object.__setattr__(self, "first_response_json", first_response_json)
        object.__setattr__(self, "replayed", replayed)

    @property
    def response(self) -> dict[str, Any]:
        return parse_canonical_object(self.first_response_json)

    @property
    def first_response(self) -> dict[str, Any]:
        return self.response


@dataclass(frozen=True)
class PreparedSuccessfulTurn:
    """Complete, validated, canonical data needed by the Phase 1B-2 write path."""

    session_id: str
    client_message_id: str
    request_format_version: int
    normalized_request_json: str
    request_sha256: str
    turn_id: str
    owner_message_id: str
    director_message_id: str
    owner_message: str
    director_message: str
    pre_state_version: int
    post_state_version: int
    owner_message_seq: int
    director_message_seq: int
    final_run_control: str
    target_stage: str
    transition_reason_code: str
    gate_outcome: str | None
    review_root_cause: str | None
    execution_format_version: int
    execution_trace_json: str
    response_format_version: int
    first_response_json: str
    snapshot_format_version: int
    post_state_json: str
    post_state_snapshot_json: str
    post_state_sha256: str
    ready_content_id: str | None
    content_format_version: int | None
    final_content_json: str | None
    created_at: str

    @staticmethod
    def _object_view(value: str) -> dict[str, Any]:
        return parse_canonical_object(value)

    @property
    def normalized_request(self) -> dict[str, Any]:
        return self._object_view(self.normalized_request_json)

    @property
    def execution_trace(self) -> dict[str, Any]:
        return self._object_view(self.execution_trace_json)

    @property
    def first_response(self) -> dict[str, Any]:
        return self._object_view(self.first_response_json)

    @property
    def post_state(self) -> dict[str, Any]:
        return self._object_view(self.post_state_json)

    @property
    def post_state_snapshot(self) -> dict[str, Any]:
        return self._object_view(self.post_state_snapshot_json)

    @property
    def ready_content(self) -> dict[str, Any] | None:
        return None if self.final_content_json is None else self._object_view(self.final_content_json)

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


_STAGE_ADAPTER = TypeAdapter(Stage)


def _require_uuid(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise DirectorExecutionValidationError(f"{field_name} must be a UUIDv4 string")
    try:
        return validate_uuid4(value)
    except (TypeError, ValueError) as exc:
        raise DirectorExecutionValidationError(f"{field_name} is not a UUIDv4") from exc


def _require_stage(value: Any, field_name: str) -> str:
    try:
        return _STAGE_ADAPTER.validate_python(value)
    except (TypeError, ValueError) as exc:
        raise DirectorExecutionValidationError(f"{field_name} is not a Director Core stage") from exc


def _require_nonblank_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or is_blank_text(value):
        raise DirectorExecutionValidationError(f"{field_name} must not be blank")
    return value


def _require_nonnegative_sqlite_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= SQLITE_INT_MAX:
        raise DirectorExecutionValidationError(
            f"{field_name} must be a non-negative SQLite signed 64-bit integer"
        )
    return value


def _canonical_payload(value: dict[str, Any], field_name: str) -> tuple[dict[str, Any], str]:
    try:
        canonical_json = canonical_text(value)
    except (TypeError, ValueError) as exc:
        raise DirectorExecutionValidationError(f"{field_name} is not Canonical JSON v1") from exc
    return deepcopy(value), canonical_json


def prepare_idempotency_request(
    session_id: str,
    client_message_id: str,
    request_format_version: int,
    owner_message: str,
    parameters: dict[str, Any],
) -> PreparedIdempotencyRequest:
    """Canonicalize the complete v1 request identity without database access."""

    try:
        prepared_session_id = _require_uuid(session_id, "session_id")
        prepared_client_message_id = _require_nonblank_text(client_message_id, "client_message_id")
        if (
            isinstance(request_format_version, bool)
            or not isinstance(request_format_version, int)
            or request_format_version != 1
        ):
            raise DirectorExecutionValidationError("only request_format_version 1 is supported")
        if not isinstance(parameters, dict):
            raise DirectorExecutionValidationError("parameters must be an object")
        request = normalized_request(owner_message, deepcopy(parameters))
        validate_normalized_request(request)
        _, request_json = _canonical_payload(request, "normalized_request")
        return PreparedIdempotencyRequest(
            session_id=prepared_session_id,
            client_message_id=prepared_client_message_id,
            request_format_version=request_format_version,
            normalized_request_json=request_json,
            request_sha256=canonical_sha256(request),
        )
    except DirectorExecutionError:
        raise
    except (TypeError, ValueError) as exc:
        raise DirectorExecutionValidationError("idempotency request is invalid") from exc


def _validate_ready_closure(
    command: CommitSuccessfulTurnInput,
    *,
    post_state: dict[str, Any],
    ready_content: dict[str, Any] | None,
) -> None:
    if command.final_run_control == "READY":
        if command.target_stage != "READY":
            raise DirectorExecutionValidationError("READY run control requires READY target stage")
        if command.transition_reason_code != "REVIEW_PASSED":
            raise DirectorExecutionValidationError("READY requires REVIEW_PASSED")
        if command.ready_content_id is None or ready_content is None:
            raise DirectorExecutionValidationError("READY requires ReadyContent")
        if command.gate_outcome != "PASSED" or command.review_root_cause is not None:
            raise DirectorExecutionValidationError("READY requires a passed gate and no review root cause")
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
            raise DirectorExecutionValidationError("READY Working State is incomplete")
        if ready_content != draft.get("content"):
            raise DirectorExecutionValidationError("ReadyContent must equal the READY draft content")
        return
    if command.final_run_control != "WAIT_FOR_OWNER":
        raise DirectorExecutionValidationError("a successful non-READY Turn must wait for the owner")
    if command.target_stage == "READY":
        raise DirectorExecutionValidationError("non-READY Turn cannot target READY")
    if command.ready_content_id is not None or ready_content is not None:
        raise DirectorExecutionValidationError("non-READY Turn cannot carry ReadyContent")


def prepare_successful_turn(
    command: CommitSuccessfulTurnInput,
    *,
    current_state_version: int,
    current_max_message_seq: int,
    current_stage: str,
    source_ready_content_id: str | None,
) -> PreparedSuccessfulTurn:
    """Validate and canonicalize a candidate without a database read or write."""

    try:
        if not isinstance(command, CommitSuccessfulTurnInput):
            raise DirectorExecutionValidationError("command must be CommitSuccessfulTurnInput")
        session_id = _require_uuid(command.session_id, "session_id")
        client_message_id = _require_nonblank_text(command.client_message_id, "client_message_id")
        expected_state_version = _require_nonnegative_sqlite_int(
            command.expected_state_version, "expected_state_version"
        )
        pre_state_version = _require_nonnegative_sqlite_int(
            current_state_version, "current_state_version"
        )
        max_message_seq = _require_nonnegative_sqlite_int(
            current_max_message_seq, "current_max_message_seq"
        )
        if expected_state_version != pre_state_version:
            raise StaleStateVersionError("expected_state_version does not match current_state_version")
        if pre_state_version >= SQLITE_INT_MAX:
            raise DirectorExecutionValidationError("post_state_version exceeds SQLite signed 64-bit range")
        if pre_state_version > SQLITE_INT_MAX // 2:
            raise DirectorExecutionValidationError("current message sequence multiplication overflows SQLite range")
        expected_current_max_message_seq = 2 * pre_state_version
        if max_message_seq != expected_current_max_message_seq:
            raise DirectorExecutionValidationError(
                "current_max_message_seq does not match current_state_version"
            )
        post_state_version = pre_state_version + 1
        if post_state_version > SQLITE_INT_MAX // 2:
            raise DirectorExecutionValidationError("derived message sequence exceeds SQLite range")
        if max_message_seq > SQLITE_INT_MAX - 2:
            raise DirectorExecutionValidationError("message sequence exceeds SQLite signed 64-bit range")
        turn_id = _require_uuid(command.turn_id, "turn_id")
        owner_message_id = _require_uuid(command.owner_message_id, "owner_message_id")
        director_message_id = _require_uuid(command.director_message_id, "director_message_id")
        if source_ready_content_id is not None:
            _require_uuid(source_ready_content_id, "source_ready_content_id")
        ready_content_id = _require_uuid(command.ready_content_id, "ready_content_id") if command.ready_content_id is not None else None
        owner_message = _require_nonblank_text(command.owner_message, "owner_message")
        director_message = _require_nonblank_text(command.director_message, "director_message")
        current_stage = _require_stage(current_stage, "current_stage")
        if current_stage == "READY":
            raise DirectorExecutionValidationError("READY Session cannot accept a new successful Turn")
        target_stage = _require_stage(command.target_stage, "target_stage")
        validate_utc_millis(command.created_at)
        if not isinstance(command.execution_trace, dict):
            raise DirectorExecutionValidationError("execution_trace must be an object")
        if not isinstance(command.post_state, dict):
            raise DirectorExecutionValidationError("post_state must be an object")
        request_identity = prepare_idempotency_request(
            session_id, client_message_id, command.request_format_version,
            owner_message, command.normalized_parameters,
        )
        normalized_request_json = request_identity.normalized_request_json
        request_hash = request_identity.request_sha256
        owner_message_seq = 2 * post_state_version - 1
        director_message_seq = 2 * post_state_version
        trace_model = validate_turn_execution_trace(
            deepcopy(command.execution_trace),
            pre_stage=current_stage,
            final_run_control=command.final_run_control,
            target_stage=target_stage,
            transition_reason_code=command.transition_reason_code,
            gate_outcome=command.gate_outcome,
            review_root_cause=command.review_root_cause,
        )
        trace, execution_trace_json = _canonical_payload(trace_model.model_dump(mode="json"), "execution_trace")
        state_model = validate_working_state(
            deepcopy(command.post_state),
            stage=target_stage,
            state_version=post_state_version,
            source_ready_content_id=source_ready_content_id,
        )
        post_state, post_state_json = _canonical_payload(state_model.model_dump(mode="json"), "post_state")
        ready_content: dict[str, Any] | None = None
        final_content_json: str | None = None
        content_format_version: int | None = None
        if command.ready_content is not None:
            if not isinstance(command.ready_content, dict):
                raise DirectorExecutionValidationError("ready_content must be an object or null")
            ready_model = ReadyContent.model_validate(deepcopy(command.ready_content))
            ready_content, final_content_json = _canonical_payload(ready_model.model_dump(mode="json"), "ready_content")
            content_format_version = 1
        _validate_ready_closure(command, post_state=post_state, ready_content=ready_content)
        snapshot_model = TurnPostStateSnapshot.model_validate(
            {
                "snapshot_format_version": 1,
                "state_version": post_state_version,
                "stage": target_stage,
                "state_json": post_state,
            }
        )
        snapshot, post_state_snapshot_json = _canonical_payload(snapshot_model.model_dump(mode="json"), "post_state_snapshot")
        post_hash = state_sha256(post_state_version, target_stage, post_state)
        response_model = FirstResponse.model_validate(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "owner_message_id": owner_message_id,
                "director_message_id": director_message_id,
                "state_version": post_state_version,
                "stage": target_stage,
                "run_control": command.final_run_control,
                "director_message": director_message,
                "ready_content_id": ready_content_id,
            }
        )
        response, first_response_json = _canonical_payload(response_model.model_dump(mode="json"), "first_response")
        if response["director_message"] != director_message:
            raise DirectorExecutionValidationError("FirstResponse DIRECTOR text mismatch")
        return PreparedSuccessfulTurn(
            session_id=session_id, client_message_id=client_message_id,
            request_format_version=command.request_format_version,
            normalized_request_json=normalized_request_json,
            request_sha256=request_hash, turn_id=turn_id,
            owner_message_id=owner_message_id, director_message_id=director_message_id,
            owner_message=owner_message, director_message=director_message,
            pre_state_version=pre_state_version, post_state_version=post_state_version,
            owner_message_seq=owner_message_seq, director_message_seq=director_message_seq,
            final_run_control=command.final_run_control, target_stage=target_stage,
            transition_reason_code=command.transition_reason_code,
            gate_outcome=command.gate_outcome, review_root_cause=command.review_root_cause,
            execution_format_version=trace["format_version"],
            execution_trace_json=execution_trace_json, response_format_version=1,
            first_response_json=first_response_json,
            snapshot_format_version=snapshot["snapshot_format_version"],
            post_state_json=post_state_json,
            post_state_snapshot_json=post_state_snapshot_json,
            post_state_sha256=post_hash, ready_content_id=ready_content_id,
            content_format_version=content_format_version,
            final_content_json=final_content_json, created_at=command.created_at,
        )
    except DirectorExecutionError:
        raise
    except (TypeError, ValueError) as exc:
        raise DirectorExecutionValidationError("successful Turn candidate is invalid") from exc


__all__ = [
    "CommitSuccessfulTurnInput",
    "DirectorExecutionError",
    "DirectorExecutionValidationError",
    "IdempotencyConflictError",
    "PreparedIdempotencyRequest",
    "PreparedSuccessfulTurn",
    "StaleStateVersionError",
    "SuccessfulTurnResult",
    "prepare_successful_turn",
    "prepare_idempotency_request",
    "validate_prepared_idempotency_request",
]
