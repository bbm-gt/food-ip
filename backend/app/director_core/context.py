"""Provider-neutral Model Context Assembly for Director Core Phase 1.

This module deliberately stops at a structured, immutable input object.  It
does not know about prompts, providers, tokenizers, model responses, or
database writes.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
import json
from types import MappingProxyType
from typing import Any, Protocol

from .canonical import is_blank_text
from .models import EvidenceReference, Stage
from .stage_contract import STAGE_EXECUTION_COMBINATIONS, stage_execution_contract
from .repository import (
    AuthorizationScope,
    DirectorIntegrityError,
    DirectorRepository,
)


class ContextAssemblyError(RuntimeError):
    """The structured model input could not be assembled safely."""


class ContextBudgetExceededError(ContextAssemblyError):
    """Irreplaceable context exceeds the budget."""


class CheckpointRebuildRequiredError(ContextAssemblyError):
    """History needs a valid checkpoint before a model call can proceed."""


class EvidenceReferenceError(ContextAssemblyError):
    """An Evidence Reference cannot be resolved within the current scope."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _thaw(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_thaw(item) for item in value]
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return deepcopy(value)


class ContextSizeCounter(Protocol):
    """Provider-independent estimator for one structured context section."""

    def estimate(self, value: Any) -> int:
        ...


class DeterministicContextSizeCounter:
    """A deterministic JSON-size counter suitable for local tests."""

    def estimate(self, value: Any) -> int:
        return len(json.dumps(_thaw(value), ensure_ascii=False, separators=(",", ":")))


@dataclass(frozen=True)
class ContextBudget:
    """An injected, model-independent budget for structured context sections."""

    max_units: int
    counter: ContextSizeCounter = field(default_factory=DeterministicContextSizeCounter)

    def __post_init__(self) -> None:
        if isinstance(self.max_units, bool) or not isinstance(self.max_units, int) or self.max_units <= 0:
            raise ValueError("max_units must be a positive integer")
        if not callable(getattr(self.counter, "estimate", None)):
            raise TypeError("counter must provide estimate(value)")

    def estimate(self, value: Any) -> int:
        amount = self.counter.estimate(value)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError("context size counter must return a non-negative integer")
        return amount


@dataclass(frozen=True)
class ContextMessage:
    id: str
    role: str
    content: str
    message_seq: int
    turn_id: str


@dataclass(frozen=True)
class ContextTurn:
    owner: ContextMessage
    director: ContextMessage


@dataclass(frozen=True)
class ModelContext:
    """Immutable provider-neutral structured input for one stage handler."""

    rules: Mapping[str, Any]
    stage_contract: Mapping[str, Any]
    working_state: Mapping[str, Any]
    current_owner_message: ContextMessage
    source_ready_content: Mapping[str, Any] | None
    checkpoint: Mapping[str, Any] | None
    history_turns: tuple[ContextTurn, ...]
    evidence_messages: tuple[ContextMessage, ...]
    owner_evidence_references: tuple[Mapping[str, str], ...]
    estimated_units: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", _freeze(self.rules))
        object.__setattr__(self, "stage_contract", _freeze(self.stage_contract))
        object.__setattr__(self, "working_state", _freeze(self.working_state))
        object.__setattr__(self, "source_ready_content", _freeze(self.source_ready_content))
        object.__setattr__(self, "checkpoint", _freeze(self.checkpoint))
        object.__setattr__(self, "history_turns", _freeze(self.history_turns))
        object.__setattr__(self, "evidence_messages", _freeze(self.evidence_messages))
        object.__setattr__(self, "owner_evidence_references", _freeze(self.owner_evidence_references))

    @property
    def owner_message(self) -> str:
        return self.current_owner_message.content

    @property
    def current_owner(self) -> ContextMessage:
        return self.current_owner_message

    @property
    def history(self) -> tuple[ContextTurn, ...]:
        return self.history_turns

    @property
    def evidence(self) -> tuple[ContextMessage, ...]:
        return self.evidence_messages

    def to_dict(self) -> dict[str, Any]:
        """Return a detached mutable view for diagnostics or adapter code."""

        return _thaw({
            "rules": self.rules,
            "stage_contract": self.stage_contract,
            "working_state": self.working_state,
            "current_owner_message": self.current_owner_message,
            "source_ready_content": self.source_ready_content,
            "checkpoint": self.checkpoint,
            "history_turns": self.history_turns,
            "evidence_messages": self.evidence_messages,
            "owner_evidence_references": self.owner_evidence_references,
            "estimated_units": self.estimated_units,
        })


_RULES = {
    "workflow": ["EXPLORE", "DEEPEN", "CREATE", "REVIEW", "READY"],
    "owner_fact_boundary": "Only confirmed OWNER Message evidence establishes Owner Facts.",
    "checkpoint_boundary": "Checkpoint is a disposable history cache, never current state or evidence.",
    "review_routing": {
        "WRITING_PROBLEM": "CREATE",
        "MATERIAL_PROBLEM": "DEEPEN",
        "DIRECTION_PROBLEM": "EXPLORE",
    },
}

def _message(row: Mapping[str, Any]) -> ContextMessage:
    return ContextMessage(
        id=row["id"],
        role=row["visible_role"],
        content=row["content"],
        message_seq=row["message_seq"],
        turn_id=row["turn_id"],
    )


def _references(value: Any) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    found: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

    def visit(item: Any, inherited_from: dict[str, Any] | None = None) -> None:
        if isinstance(item, Mapping):
            current_inherited_from = inherited_from
            if "inherited_from" in item and item["inherited_from"] is not None:
                if not isinstance(item["inherited_from"], Mapping):
                    raise EvidenceReferenceError("invalid inherited Evidence boundary")
                current_inherited_from = dict(item["inherited_from"])
            if "evidence_type" in item:
                try:
                    reference = EvidenceReference.model_validate(dict(item)).model_dump(mode="json")
                except (TypeError, ValueError) as exc:
                    raise EvidenceReferenceError("invalid Evidence Reference in Working State") from exc
                found.append((reference, current_inherited_from))
            for child in item.values():
                visit(child, current_inherited_from)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, inherited_from)

    visit(value)
    return found


@dataclass(frozen=True)
class ModelContextAssembler:
    """Read and assemble only the context required by one internal step."""

    repository: DirectorRepository
    scope: AuthorizationScope
    budget: ContextBudget

    def assemble(
        self,
        context: Any | None = None,
        *,
        session_id: str | None = None,
        stage: Stage | None = None,
        working_state: dict[str, Any] | None = None,
        owner_message_id: str | None = None,
        owner_text: str | None = None,
        include_source_ready_content: bool = False,
    ) -> ModelContext:
        """Assemble from either a StageExecutionContext-like object or fields.

        Accepting a small structural object keeps this module independent from
        the Orchestrator while making the Orchestrator-to-assembler boundary
        explicit and easy to fake in tests.
        """

        if context is not None:
            session_id = context.session_id
            stage = context.stage
            working_state = deepcopy(context.working_state)
            owner_message_id = context.owner_message_id
            owner_text = context.owner_text
        if not isinstance(session_id, str) or not session_id:
            raise ContextAssemblyError("session_id is required for Context Assembly")
        if stage not in STAGE_EXECUTION_COMBINATIONS:
            raise ContextAssemblyError("stage is invalid for Context Assembly")
        if not isinstance(working_state, dict):
            raise ContextAssemblyError("working_state must be an object")
        if not isinstance(owner_message_id, str) or not owner_message_id:
            raise ContextAssemblyError("owner_message_id is required for Context Assembly")
        if not isinstance(owner_text, str) or is_blank_text(owner_text):
            raise ContextAssemblyError("current OWNER Message must be non-blank")

        session = self.repository.get_session(self.scope, session_id)
        checkpoint = self.repository.get_latest_valid_checkpoint(self.scope, session_id)
        covered_through_seq = 0 if checkpoint is None else checkpoint["covered_through_seq"]
        history_rows = self.repository.get_complete_message_turns_after_seq(
            self.scope, session_id, after_seq=covered_through_seq
        )

        source = None
        if include_source_ready_content and session.source_ready_content_id is not None:
            ready = self.repository.get_ready_content(self.scope, session.source_ready_content_id)
            source = {
                "id": ready["id"],
                "session_id": ready["session_id"],
                "final_content": deepcopy(ready["final_content_json"]),
            }

        current_owner = ContextMessage(
            id=owner_message_id,
            role="OWNER",
            content=owner_text,
            message_seq=2 * (self.repository.get_working_state(self.scope, session_id).state_version + 1) - 1,
            turn_id="CURRENT_TURN",
        )
        evidence_messages, loaded_evidence_references = self._resolve_evidence(
            session_id,
            working_state,
            current_owner=current_owner,
        )
        history = tuple(
            ContextTurn(owner=_message(pair["owner"]), director=_message(pair["director"]))
            for pair in history_rows
        )
        owner_evidence_references: list[dict[str, str]] = [{
            "evidence_type": "owner_message",
            "target_id": current_owner.id,
            "target_session_id": session_id,
        }]
        owner_evidence_references.extend({
            "evidence_type": "owner_message",
            "target_id": turn.owner.id,
            "target_session_id": session_id,
        } for turn in history)
        owner_evidence_references.extend(loaded_evidence_references)
        owner_evidence_references = list({
            (reference["evidence_type"], reference["target_id"], reference["target_session_id"]): reference
            for reference in owner_evidence_references
        }.values())

        rules = deepcopy(_RULES)
        stage_contract = stage_execution_contract(stage)
        checkpoint_payload = None if checkpoint is None else {
            "covered_through_seq": checkpoint["covered_through_seq"],
            "format_version": checkpoint["format_version"],
            "checkpoint": deepcopy(checkpoint["checkpoint_json"]),
        }

        irreducible_sections = {
            "rules": rules,
            "stage_contract": stage_contract,
            "working_state": working_state,
            "current_owner_message": current_owner,
            "source_ready_content": source,
            "evidence": {
                "messages": evidence_messages,
                "owner_references": owner_evidence_references,
            },
        }
        irreducible_units = sum(
            self.budget.estimate(value) for value in irreducible_sections.values()
        )
        if irreducible_units > self.budget.max_units:
            raise ContextBudgetExceededError(
                "irreducible Context Assembly content exceeds budget: "
                f"{irreducible_units} > {self.budget.max_units}"
            )

        checkpoint_units = (
            0 if checkpoint_payload is None else self.budget.estimate(checkpoint_payload)
        )
        history_values = list(history)
        available = self.budget.max_units - irreducible_units
        all_history_units = sum(self.budget.estimate(turn) for turn in history_values)
        optional_units = checkpoint_units + all_history_units
        if optional_units > available:
            raise CheckpointRebuildRequiredError(
                "Checkpoint and all complete history after its boundary cannot fit "
                "the remaining Context Assembly budget"
            )

        total_units = irreducible_units + optional_units
        return ModelContext(
            rules=rules,
            stage_contract=stage_contract,
            working_state=working_state,
            current_owner_message=current_owner,
            source_ready_content=source,
            checkpoint=checkpoint_payload,
            history_turns=tuple(history_values),
            evidence_messages=tuple(evidence_messages),
            owner_evidence_references=tuple(owner_evidence_references),
            estimated_units=total_units,
        )

    def _resolve_evidence(
        self,
        session_id: str,
        working_state: dict[str, Any],
        *,
        current_owner: ContextMessage,
    ) -> tuple[list[ContextMessage], list[dict[str, Any]]]:
        resolved: list[ContextMessage] = []
        resolved_references: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        session = self.repository.get_session(self.scope, session_id)
        try:
            self.repository.validate_context_evidence_closure(
                self.scope,
                session_id,
                deepcopy(working_state),
                inflight_owner_message_id=current_owner.id,
            )
        except (DirectorIntegrityError, LookupError, TypeError, ValueError) as exc:
            raise EvidenceReferenceError(
                f"Working State Evidence closure is invalid before Stage Handler execution: {exc}"
            ) from exc
        for raw_reference, inherited_from in _references(working_state):
            target_id = raw_reference["target_id"]
            target_session_id = raw_reference["target_session_id"]
            reference_key = (
                raw_reference["evidence_type"], target_id, target_session_id
            )
            if reference_key in seen:
                continue
            seen.add(reference_key)
            if target_id == current_owner.id:
                if target_session_id != session_id:
                    raise EvidenceReferenceError("current OWNER Evidence has the wrong Session")
                continue
            if target_session_id != session_id:
                try:
                    if session.source_ready_content_id is None:
                        raise EvidenceReferenceError(
                            "Evidence Reference crosses the current Session or authorization scope"
                        )
                    source_ready = self.repository.get_ready_content(
                        self.scope, session.source_ready_content_id
                    )
                    row = self.repository.get_owner_message_for_context(
                        self.scope, target_session_id, target_id
                    )
                except (DirectorIntegrityError, LookupError) as exc:
                    raise EvidenceReferenceError(
                        "inherited Evidence Reference is not valid in this authorization scope"
                    ) from exc
                if source_ready["session_id"] != target_session_id:
                    raise EvidenceReferenceError("inherited Evidence source Session does not match")
                resolved.append(_message(row))
                resolved_references.append(raw_reference)
                continue
            try:
                row = self.repository.get_owner_message_for_context(
                    self.scope, session_id, target_id
                )
            except (DirectorIntegrityError, LookupError) as exc:
                raise EvidenceReferenceError(
                    f"Evidence Reference does not resolve to a committed OWNER Message: {target_id}"
                ) from exc
            resolved.append(_message(row))
            resolved_references.append(raw_reference)
        return resolved, resolved_references


# Short discoverable aliases preserve the single assembler/budget boundary.
ContextAssembler = ModelContextAssembler
ContextSizeEstimator = ContextSizeCounter


__all__ = [
    "CheckpointRebuildRequiredError",
    "ContextAssemblyError",
    "ContextBudget",
    "ContextBudgetExceededError",
    "ContextAssembler",
    "ContextMessage",
    "ContextSizeCounter",
    "ContextSizeEstimator",
    "ContextTurn",
    "DeterministicContextSizeCounter",
    "EvidenceReferenceError",
    "ModelContext",
    "ModelContextAssembler",
]
