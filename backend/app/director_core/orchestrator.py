"""Application-layer orchestration for one complete Director Core Turn."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from .canonical import SQLITE_INT_MAX
from .execution import (
    CommitSuccessfulTurnInput,
    DirectorExecutionValidationError,
    PreparedIdempotencyRequest,
    StaleStateVersionError,
    SuccessfulTurnResult,
    prepare_idempotency_request,
    prepare_successful_turn,
)
from .repository import (
    AuthorizationScope,
    DirectorIntegrityError,
    DirectorRepository,
    SessionRecord,
    WorkingStateRecord,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class DirectorTurnRequest:
    """The small application request envelope for one owner message."""

    session_id: str
    client_message_id: str
    expected_state_version: int
    owner_text: str
    request_format_version: int
    parameters: dict[str, Any]


@dataclass(frozen=True)
class TurnOrchestrationContext:
    """The latest lock-owned authority snapshot visible to a candidate builder."""

    scope: AuthorizationScope
    request: DirectorTurnRequest
    prepared_request: PreparedIdempotencyRequest
    session: SessionRecord
    working_state: WorkingStateRecord
    turn_id: str
    owner_message_id: str
    director_message_id: str
    ready_content_id: str

    def __post_init__(self) -> None:
        # Candidate construction is application code.  Give it a detached
        # authority snapshot so it cannot mutate the repository-facing values
        # while still seeing exactly what was read after acquiring the lock.
        object.__setattr__(self, "request", replace(
            self.request, parameters=deepcopy(self.request.parameters)
        ))
        object.__setattr__(self, "working_state", replace(
            self.working_state, state_json=deepcopy(self.working_state.state_json)
        ))


@dataclass(frozen=True)
class TurnCandidate:
    """Business-only output from the injected candidate construction boundary."""

    director_message: str
    execution_trace: dict[str, Any]
    post_state: dict[str, Any]
    final_run_control: str
    target_stage: str
    transition_reason_code: str
    gate_outcome: str | None
    review_root_cause: str | None
    ready_content: dict[str, Any] | None

    def __post_init__(self) -> None:
        # Keep the frozen envelope detached from mutable builder-owned payloads.
        object.__setattr__(self, "execution_trace", deepcopy(self.execution_trace))
        object.__setattr__(self, "post_state", deepcopy(self.post_state))
        object.__setattr__(self, "ready_content", deepcopy(self.ready_content))


class TurnCandidateBuilder(Protocol):
    """Injected test/model boundary for producing business candidates only."""

    def __call__(self, context: TurnOrchestrationContext) -> TurnCandidate:
        ...


@dataclass(frozen=True)
class DirectorOrchestrator:
    """Run one complete Turn while retaining the Session lock throughout."""

    repository: DirectorRepository
    scope: AuthorizationScope
    candidate_builder: TurnCandidateBuilder

    def run(self, request: DirectorTurnRequest) -> SuccessfulTurnResult:
        if not isinstance(request, DirectorTurnRequest):
            raise DirectorExecutionValidationError("request must be DirectorTurnRequest")
        # This is the outermost lock.  Repository methods may re-enter it for
        # their own short reads/commit, but the application lock remains held
        # through candidate construction, commit, and COMMIT disambiguation.
        with self.repository.session_lock(request.session_id):
            return self._run_locked(request)

    def _run_locked(self, request: DirectorTurnRequest) -> SuccessfulTurnResult:
        prepared_request = prepare_idempotency_request(
            request.session_id,
            request.client_message_id,
            request.request_format_version,
            request.owner_text,
            deepcopy(request.parameters),
        )
        self._validate_expected_state_version(request.expected_state_version)

        # The first repository operation after request normalization is the
        # idempotency precheck.  In particular, it runs before any state read,
        # context assembly, ID generation, or candidate construction.
        replay = self.repository.precheck_successful_turn(self.scope, prepared_request)
        if replay is not None:
            return replay

        session = self.repository.get_session(self.scope, request.session_id)
        if session.lifecycle_status != "ACTIVE":
            raise DirectorExecutionValidationError(
                "READY Session cannot accept a new successful Turn"
            )
        working_state = self._read_or_recover_working_state(request.session_id)
        if working_state.state_version != request.expected_state_version:
            raise StaleStateVersionError(
                "expected_state_version does not match current Working State"
            )

        turn_id = str(uuid4())
        owner_message_id = str(uuid4())
        director_message_id = str(uuid4())
        ready_content_id = str(uuid4())
        context = TurnOrchestrationContext(
            scope=self.scope,
            request=request,
            prepared_request=prepared_request,
            session=session,
            working_state=working_state,
            turn_id=turn_id,
            owner_message_id=owner_message_id,
            director_message_id=director_message_id,
            ready_content_id=ready_content_id,
        )

        # Any builder exception or invalid candidate exits before the write
        # path.  There is intentionally no model retry or candidate retry here.
        candidate = self.candidate_builder(context)
        command = self._bind_request_boundary(candidate, context)
        prepared = prepare_successful_turn(
            command,
            current_state_version=working_state.state_version,
            current_max_message_seq=2 * working_state.state_version,
            current_stage=working_state.stage,
            source_ready_content_id=session.source_ready_content_id,
        )
        # Repository owns the authoritative transaction and all commit outcome
        # classification.  The outer lock is still held while it resolves an
        # uncertain COMMIT, so a same-Session waiter cannot enter construction.
        return self.repository.commit_successful_turn(self.scope, prepared)

    def _read_or_recover_working_state(self, session_id: str) -> WorkingStateRecord:
        try:
            return self.repository.get_working_state(self.scope, session_id)
        except DirectorIntegrityError:
            # Recovery is deterministic and model-free.  The repository's
            # recovery path is re-entrant under the outer Session lock.
            return self.repository.recover_working_state(self.scope, session_id)

    @staticmethod
    def _validate_expected_state_version(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= SQLITE_INT_MAX:
            raise DirectorExecutionValidationError(
                "expected_state_version must be a non-negative SQLite signed 64-bit integer"
            )
        return value

    @staticmethod
    def _bind_request_boundary(
        candidate: TurnCandidate,
        context: TurnOrchestrationContext,
    ) -> CommitSuccessfulTurnInput:
        if not isinstance(candidate, TurnCandidate):
            raise DirectorExecutionValidationError(
                "candidate builder must return TurnCandidate"
            )
        # Infrastructure identity comes from the lock-owned orchestrator, not
        # from the injected builder.  The builder supplies only business
        # output: visible reply, trace, post-state, and optional ReadyContent.
        return CommitSuccessfulTurnInput(
            session_id=context.request.session_id,
            client_message_id=context.request.client_message_id,
            expected_state_version=context.request.expected_state_version,
            request_format_version=context.request.request_format_version,
            turn_id=context.turn_id,
            owner_message_id=context.owner_message_id,
            director_message_id=context.director_message_id,
            owner_message=context.request.owner_text,
            normalized_parameters=deepcopy(context.request.parameters),
            director_message=candidate.director_message,
            execution_trace=deepcopy(candidate.execution_trace),
            post_state=deepcopy(candidate.post_state),
            final_run_control=candidate.final_run_control,
            target_stage=candidate.target_stage,
            transition_reason_code=candidate.transition_reason_code,
            gate_outcome=candidate.gate_outcome,
            review_root_cause=candidate.review_root_cause,
            ready_content_id=(context.ready_content_id if candidate.final_run_control == "READY" else None),
            ready_content=deepcopy(candidate.ready_content),
            created_at=_utc_now(),
        )


__all__ = [
    "DirectorOrchestrator",
    "DirectorTurnRequest",
    "TurnCandidate",
    "TurnCandidateBuilder",
    "TurnOrchestrationContext",
]
