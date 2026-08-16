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
from .stage_contract import validate_outcome_envelope


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


def _legal_owner_message_ids(context: Any) -> set[str]:
    ids = {context.current_owner_message.id}
    ids.update(message.id for message in context.evidence_messages if message.role == "OWNER")
    ids.update(turn.owner.id for turn in context.history_turns)
    return ids


def _require_confirmed_direction(state: WorkingState, context: Any) -> None:
    direction = state.direction
    if direction is None or direction.owner_confirmed is not True or not direction.evidence_refs:
        raise StageContractViolationError("progression requires an owner-confirmed Direction")
    legal_ids = _legal_owner_message_ids(context)
    if any(reference.target_id not in legal_ids for reference in direction.evidence_refs):
        raise StageContractViolationError(
            "Direction Evidence must close to a legal OWNER Message in Model Context"
        )


def _require_material_gap(state: WorkingState) -> None:
    if state.material_state.status != "INSUFFICIENT":
        raise StageContractViolationError("MATERIAL_GAP requires INSUFFICIENT material")
    if not state.material_state.required_confirmations:
        raise StageContractViolationError("MATERIAL_GAP requires confirmations")


def _require_review_matches_state(output: StageModelOutputV1) -> None:
    state_review = output.post_state.review
    if state_review is None or output.review is None:
        raise StageContractViolationError("REVIEW output requires a current Working State review")
    if (
        state_review.outcome != output.review.outcome
        or state_review.root_cause != output.review.root_cause
        or output.post_state.draft is None
        or state_review.against_draft_id != output.post_state.draft.draft_id
    ):
        raise StageContractViolationError(
            "trace review must match Working State review and current Draft"
        )


def validate_stage_model_output(
    raw_output: Any,
    *,
    context: Any,
) -> StageModelOutputV1:
    """Reject loose provider results, then enforce the current Stage contract."""

    if isinstance(raw_output, StageModelOutputV1):
        output = raw_output
    else:
        if not isinstance(raw_output, dict):
            raise StageModelOutputTypeError(
                "Stage model output must be one structured object; text, Markdown, and arrays are forbidden"
            )
        try:
            output = StageModelOutputV1.model_validate(deepcopy(raw_output))
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
        if entered_stage == "EXPLORE" and output.target_stage == "DEEPEN":
            _require_confirmed_direction(state, context)

        if entered_stage == "DEEPEN":
            if output.transition_reason_code in {"MATERIAL_GAP", "OWNER_INPUT_REQUIRED"}:
                _require_material_gap(state)
                if (
                    output.transition_reason_code == "MATERIAL_GAP"
                    and state.model_dump(mode="json") == dict(context.working_state)
                ):
                    raise StageContractViolationError(
                        "internal MATERIAL_GAP must make a meaningful candidate-state change"
                    )
            elif output.target_stage == "CREATE":
                _require_confirmed_direction(state, context)
                if state.material_state.status != "SUFFICIENT":
                    raise StageContractViolationError("CREATE requires SUFFICIENT material")
                if state.material_state.required_confirmations:
                    raise StageContractViolationError(
                        "CREATE requires an empty required_confirmations list"
                    )

        if entered_stage == "CREATE":
            _require_confirmed_direction(state, context)
            if state.material_state.status != "SUFFICIENT":
                raise StageContractViolationError("CREATE requires SUFFICIENT material")
            if state.review is not None:
                raise StageContractViolationError("CREATE must clear the current review")
            draft = state.draft
            if (
                draft is None
                or draft.draft_id is None
                or draft.content_status != "FINAL_CANDIDATE"
            ):
                raise StageContractViolationError(
                    "CREATE must produce one UUIDv4 FINAL_CANDIDATE Draft"
                )

        if entered_stage == "REVIEW":
            _require_review_matches_state(output)
            if output.transition_reason_code == "MATERIAL_GAP":
                _require_material_gap(state)
            elif output.transition_reason_code == "DIRECTION_INVALID":
                if state.direction is not None:
                    raise StageContractViolationError(
                        "an invalid Direction cannot remain the active Direction"
                    )
            elif output.transition_reason_code == "REVIEW_PASSED":
                _require_confirmed_direction(state, context)
                if state.material_state.status != "SUFFICIENT":
                    raise StageContractViolationError("READY requires SUFFICIENT material")
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
