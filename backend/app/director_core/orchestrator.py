"""Application-layer orchestration for one complete Director Core Turn."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from .canonical import SQLITE_INT_MAX, is_blank_text
from .execution import (
    CommitSuccessfulTurnInput,
    DirectorExecutionValidationError,
    PreparedIdempotencyRequest,
    StaleStateVersionError,
    SuccessfulTurnResult,
    prepare_idempotency_request,
    prepare_successful_turn,
)
from .models import (
    ExecutionStep,
    Stage,
    validate_turn_execution_trace,
    validate_working_state,
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
    """The lock-owned persistence bindings for the completed internal loop."""

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
    """Internal final candidate assembled after the stage loop completes."""

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
        # Keep the frozen envelope detached from mutable loop-owned payloads.
        object.__setattr__(self, "execution_trace", deepcopy(self.execution_trace))
        object.__setattr__(self, "post_state", deepcopy(self.post_state))
        object.__setattr__(self, "ready_content", deepcopy(self.ready_content))


@dataclass(frozen=True)
class StageExecutionContext:
    """Business-only input for one provider-independent stage execution.

    The context contains only the current business snapshot plus the
    pre-allocated owner-evidence binding needed by the strict Phase 1 JSON
    contract.  It contains no Turn ID, timestamp, or persistence result.  The
    ``working_state`` is the latest in-memory candidate state at call time.
    """

    stage: Stage
    working_state: dict[str, Any]
    owner_text: str
    parameters: dict[str, Any]
    candidate_revision: int
    # The current owner message is pre-allocated by the Orchestrator so a
    # business executor can attach formal Owner Evidence without inventing an
    # identity.  These are input bindings only; no infrastructure fields are
    # accepted in StageExecutionResult.
    session_id: str
    owner_message_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "working_state", deepcopy(self.working_state))
        object.__setattr__(self, "parameters", deepcopy(self.parameters))


@dataclass(frozen=True)
class StageExecutionResult:
    """Business result for exactly one stage.

    The Orchestrator adds step number, candidate revision, and execution trace
    ordering.  The executor cannot provide infrastructure fields or a
    pre-built whole-Turn trace.
    """

    director_message: str | None
    post_state: dict[str, Any]
    run_control: str
    target_stage: str
    transition_reason_code: str
    gate: dict[str, Any] | None
    review: dict[str, Any] | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "post_state", deepcopy(self.post_state))
        object.__setattr__(self, "gate", deepcopy(self.gate))
        object.__setattr__(self, "review", deepcopy(self.review))

    @property
    def candidate_state(self) -> dict[str, Any]:
        """Readable alias for callers that use the workflow vocabulary."""

        return deepcopy(self.post_state)


class SingleStageExecutor(Protocol):
    """Provider-neutral boundary for one current-stage business decision."""

    def __call__(self, context: StageExecutionContext) -> StageExecutionResult:
        ...


# Short aliases keep the public boundary discoverable without introducing a
# second abstraction or a provider-specific name.
StageExecutor = SingleStageExecutor


@dataclass(frozen=True)
class DirectorOrchestrator:
    """Run one complete Turn while retaining the Session lock throughout."""

    repository: DirectorRepository
    scope: AuthorizationScope
    stage_executor: SingleStageExecutor
    max_internal_steps: int

    def __post_init__(self) -> None:
        if self.stage_executor is None or not callable(self.stage_executor):
            raise DirectorExecutionValidationError(
                "stage_executor must be provided"
            )
        self._validate_max_internal_steps(self.max_internal_steps)

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

        # Any executor exception or invalid candidate exits before the write
        # path.  There is intentionally no model retry here.
        candidate = self._run_internal_loop(
            request=request,
            working_state=working_state,
            session=session,
            owner_message_id=owner_message_id,
        )
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

    def _run_internal_loop(
        self,
        *,
        request: DirectorTurnRequest,
        working_state: WorkingStateRecord,
        session: SessionRecord,
        owner_message_id: str,
    ) -> TurnCandidate:
        executor = self.stage_executor

        candidate_state = deepcopy(working_state.state_json)
        current_stage: Stage = working_state.stage  # type: ignore[assignment]
        trace_steps: list[dict[str, Any]] = []
        final_result: StageExecutionResult | None = None

        for step_no in range(1, self.max_internal_steps + 1):
            step_context = StageExecutionContext(
                stage=current_stage,
                working_state=candidate_state,
                owner_text=request.owner_text,
                parameters=deepcopy(request.parameters),
                candidate_revision=step_no - 1,
                session_id=session.id,
                owner_message_id=owner_message_id,
            )
            result = executor(step_context)
            if not isinstance(result, StageExecutionResult):
                raise DirectorExecutionValidationError(
                    "single-stage executor must return StageExecutionResult"
                )
            if result.run_control == "CONTINUE":
                if result.director_message is not None:
                    raise DirectorExecutionValidationError(
                        "CONTINUE stage execution must not include director_message"
                    )
            elif result.run_control in {"WAIT_FOR_OWNER", "READY"}:
                if not isinstance(result.director_message, str) or is_blank_text(result.director_message):
                    raise DirectorExecutionValidationError(
                        "terminal stage execution director_message must not be blank"
                    )
            if not isinstance(result.post_state, dict):
                raise DirectorExecutionValidationError(
                    "stage execution post_state must be an object"
                )

            try:
                state_model = validate_working_state(
                    deepcopy(result.post_state),
                    stage=result.target_stage,
                    state_version=working_state.state_version,
                    source_ready_content_id=session.source_ready_content_id,
                )
                step = ExecutionStep.model_validate(
                    {
                        "step_no": step_no,
                        "entered_stage": current_stage,
                        "run_control": result.run_control,
                        "target_stage": result.target_stage,
                        "transition_reason_code": result.transition_reason_code,
                        "gate": deepcopy(result.gate),
                        "review": deepcopy(result.review),
                        "candidate_revision": step_no,
                    }
                )
            except (TypeError, ValueError) as exc:
                raise DirectorExecutionValidationError(
                    "single-stage execution result is invalid"
                ) from exc

            candidate_state = state_model.model_dump(mode="json")
            trace_steps.append(step.model_dump(mode="json"))
            current_stage = step.target_stage
            final_result = result
            if step.run_control != "CONTINUE":
                break
        else:
            raise DirectorExecutionValidationError(
                "max_internal_steps exceeded before the loop reached a terminal control"
            )

        if final_result is None:  # pragma: no cover - range is positive
            raise DirectorExecutionValidationError("internal loop produced no result")
        final_step = trace_steps[-1]
        trace = {"format_version": 1, "steps": trace_steps}
        try:
            validate_turn_execution_trace(
                deepcopy(trace),
                pre_stage=working_state.stage,
                final_run_control=final_step["run_control"],
                target_stage=final_step["target_stage"],
                transition_reason_code=final_step["transition_reason_code"],
                gate_outcome=(final_step["gate"]["outcome"] if final_step["gate"] else None),
                review_root_cause=(
                    final_step["review"]["root_cause"] if final_step["review"] else None
                ),
            )
        except (TypeError, ValueError) as exc:
            raise DirectorExecutionValidationError(
                "Orchestrator-generated execution trace is invalid"
            ) from exc

        ready_content = None
        if final_step["run_control"] == "READY":
            draft = candidate_state.get("draft")
            if not isinstance(draft, dict) or not isinstance(draft.get("content"), dict):
                raise DirectorExecutionValidationError(
                    "READY requires a draft content object"
                )
            ready_content = deepcopy(draft["content"])
        if not isinstance(final_result.director_message, str):  # pragma: no cover - terminal validation above
            raise DirectorExecutionValidationError(
                "terminal stage execution must provide director_message"
            )
        return TurnCandidate(
            director_message=final_result.director_message,
            execution_trace=trace,
            post_state=candidate_state,
            final_run_control=final_step["run_control"],
            target_stage=final_step["target_stage"],
            transition_reason_code=final_step["transition_reason_code"],
            gate_outcome=(final_step["gate"]["outcome"] if final_step["gate"] else None),
            review_root_cause=(
                final_step["review"]["root_cause"] if final_step["review"] else None
            ),
            ready_content=ready_content,
        )

    @staticmethod
    def _validate_max_internal_steps(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise DirectorExecutionValidationError(
                "max_internal_steps must be a positive integer"
            )
        return value

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
                "internal loop must produce TurnCandidate"
            )
        # Infrastructure identity comes from the lock-owned orchestrator.  The
        # internal loop supplies only business output: visible reply, trace,
        # post-state, and optional ReadyContent.
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
    "TurnOrchestrationContext",
    "SingleStageExecutor",
    "StageExecutor",
    "StageExecutionContext",
    "StageExecutionResult",
]
