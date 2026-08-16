"""Strict provider-neutral Stage model output boundary for Director Core."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import ConfigDict, StrictInt, StrictStr, ValidationError, field_validator

from .models import (
    GateResult,
    RunControl,
    Stage,
    StrictModel,
    TraceReview,
    WorkingState,
)
from .stage_contract import outcome_spec, validate_outcome_envelope


class StageModelOutputError(ValueError):
    """Base error for rejected model output before authoritative writes."""


class StageModelOutputTypeError(StageModelOutputError):
    """The provider result was not one structured object."""


class StageModelOutputSchemaError(StageModelOutputError):
    """The structured object failed strict StageModelOutputV1 validation."""


class StageContractViolationError(StageModelOutputError):
    """A schema-valid output violated the current Stage business contract."""


class StageModelOutputV1(StrictModel):
    """The complete and only accepted Phase 1E model result shape."""

    model_config = ConfigDict(extra="forbid", strict=True)

    output_format_version: StrictInt
    run_control: RunControl
    target_stage: Stage
    transition_reason_code: Literal[
        "OWNER_INPUT_REQUIRED", "DIRECTION_CONFIRMED", "DIRECTION_INVALID",
        "MATERIAL_GAP", "MATERIAL_SUFFICIENT", "DRAFT_CREATED",
        "WRITING_REPAIR", "REVIEW_PASSED",
    ]
    director_message: StrictStr | None
    gate: GateResult | None
    review: TraceReview | None
    post_state: WorkingState

    @field_validator("output_format_version")
    @classmethod
    def version_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only output_format_version 1 is supported")
        return value


def _legal_owner_evidence_references(context: Any) -> set[tuple[str, str, str]]:
    references = getattr(context, "owner_evidence_references", ())
    return {
        (reference["evidence_type"], reference["target_id"], reference["target_session_id"])
        for reference in references
    }


def _require_confirmed_direction(state: WorkingState, context: Any) -> None:
    direction = state.direction
    if direction is None or direction.owner_confirmed is not True or not direction.evidence_refs:
        raise StageContractViolationError("progression requires an owner-confirmed Direction")


def _require_authorized_evidence(state: WorkingState, context: Any) -> None:
    legal_references = _legal_owner_evidence_references(context)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "evidence_type" in value:
                marker = (
                    value.get("evidence_type"),
                    value.get("target_id"),
                    value.get("target_session_id"),
                )
                if marker not in legal_references:
                    raise StageContractViolationError(
                        "Evidence must exactly match an authorized OWNER Evidence Reference in Model Context"
                    )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(state.model_dump(mode="json"))


def _require_review_matches_state(state: WorkingState, trace_review: TraceReview | None) -> None:
    state_review = state.review
    if state_review is None or trace_review is None:
        raise StageContractViolationError("REVIEW output requires a current Working State review")
    if (
        state_review.outcome != trace_review.outcome
        or state_review.root_cause != trace_review.root_cause
        or state.draft is None
        or state_review.against_draft_id != state.draft.draft_id
    ):
        raise StageContractViolationError(
            "trace review must match Working State review and current Draft"
        )


def _normalized_context_state(context: Any) -> WorkingState:
    to_dict = getattr(context, "to_dict", None)
    raw_state = to_dict()["working_state"] if callable(to_dict) else deepcopy(context.working_state)
    return WorkingState.model_validate(raw_state)


def _validate_state_requirements(
    requirements: dict[str, str],
    *,
    state: WorkingState,
    pre_state: WorkingState,
    trace_review: TraceReview | None,
    context: Any,
) -> None:
    if requirements["active_direction"] == "REQUIRED" and state.direction is None:
        raise StageContractViolationError("outcome requires an active Direction")
    if requirements["active_direction"] == "ABSENT" and state.direction is not None:
        raise StageContractViolationError("outcome requires the active Direction to be cleared")
    if requirements["confirmed_direction"] == "REQUIRED":
        _require_confirmed_direction(state, context)
    if requirements["material_status"] != "ANY" and (
        state.material_state.status != requirements["material_status"]
    ):
        raise StageContractViolationError(
            f"outcome requires {requirements['material_status']} material"
        )
    confirmations = state.material_state.required_confirmations
    if requirements["required_confirmations"] == "NON_EMPTY" and not confirmations:
        raise StageContractViolationError("outcome requires material confirmations")
    if requirements["required_confirmations"] == "EMPTY" and confirmations:
        raise StageContractViolationError("outcome requires no pending material confirmations")
    if requirements["draft"] == "REQUIRED" and state.draft is None:
        raise StageContractViolationError("outcome requires a Draft")
    if requirements["draft"] == "FINAL_CANDIDATE" and (
        state.draft is None
        or state.draft.draft_id is None
        or state.draft.content_status != "FINAL_CANDIDATE"
    ):
        raise StageContractViolationError("outcome requires one UUIDv4 FINAL_CANDIDATE Draft")
    if requirements["review"] == "ABSENT" and state.review is not None:
        raise StageContractViolationError("outcome requires review to be absent")
    if requirements["review"] == "MATCH_TRACE_AND_DRAFT":
        _require_review_matches_state(state, trace_review)
    if requirements["state_change"] == "REQUIRED" and (
        state.model_dump(mode="json") == pre_state.model_dump(mode="json")
    ):
        raise StageContractViolationError("outcome requires a meaningful Working State change")


def validate_stage_model_output(
    raw_output: Any,
    *,
    context: Any,
) -> StageModelOutputV1:
    """Reject loose provider results, then enforce the current Stage contract."""

    if not isinstance(raw_output, (dict, StageModelOutputV1)):
        raise StageModelOutputTypeError(
            "Stage model output must be one structured object; text, Markdown, and arrays are forbidden"
        )
    candidate = (
        raw_output.model_dump(mode="python", warnings=False)
        if isinstance(raw_output, StageModelOutputV1)
        else deepcopy(raw_output)
    )
    try:
        output = StageModelOutputV1.model_validate(candidate)
    except ValidationError as exc:
        raise StageModelOutputSchemaError("Stage model output failed strict v1 schema") from exc

    entered_stage = getattr(context, "stage", None)
    if entered_stage is None:
        entered_stage = context.stage_contract["stage"]
    try:
        validate_outcome_envelope(
            entered_stage=entered_stage,
            run_control=output.run_control,
            target_stage=output.target_stage,
            transition_reason_code=output.transition_reason_code,
            director_message=output.director_message,
            gate=output.gate,
            review=output.review,
        )
        state = output.post_state
        _require_authorized_evidence(state, context)
        spec = outcome_spec(
            entered_stage,
            output.run_control,
            output.target_stage,
            output.transition_reason_code,
        )
        _validate_state_requirements(
            spec["state_requirements"],
            state=state,
            pre_state=_normalized_context_state(context),
            trace_review=output.review,
            context=context,
        )
    except StageContractViolationError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise StageContractViolationError("Stage output violates the business contract") from exc
    return output


__all__ = [
    "StageContractViolationError",
    "StageModelOutputError",
    "StageModelOutputSchemaError",
    "StageModelOutputTypeError",
    "StageModelOutputV1",
    "validate_stage_model_output",
]
