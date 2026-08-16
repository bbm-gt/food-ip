"""Single authoritative Director Core Stage outcome contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .canonical import is_blank_text


STAGE_OUTCOME_CONTRACT: dict[str, tuple[dict[str, Any], ...]] = {
    "EXPLORE": (
        {"run_control": "WAIT_FOR_OWNER", "target_stage": "EXPLORE", "transition_reason_code": "OWNER_INPUT_REQUIRED", "director_message": "REQUIRED", "gate_outcome": "BLOCKED", "gate_codes": ("DIRECTION_NOT_CONFIRMED", "FACT_BOUNDARY_UNCLEAR"), "review_outcome": None, "review_root_cause": None},
        {"run_control": "CONTINUE", "target_stage": "DEEPEN", "transition_reason_code": "DIRECTION_CONFIRMED", "director_message": "FORBIDDEN", "gate_outcome": None, "gate_codes": (), "review_outcome": None, "review_root_cause": None},
    ),
    "DEEPEN": (
        {"run_control": "CONTINUE", "target_stage": "DEEPEN", "transition_reason_code": "MATERIAL_GAP", "director_message": "FORBIDDEN", "gate_outcome": "BLOCKED", "gate_codes": ("MATERIAL_INSUFFICIENT",), "review_outcome": None, "review_root_cause": None},
        {"run_control": "WAIT_FOR_OWNER", "target_stage": "DEEPEN", "transition_reason_code": "OWNER_INPUT_REQUIRED", "director_message": "REQUIRED", "gate_outcome": "BLOCKED", "gate_codes": ("MATERIAL_INSUFFICIENT",), "review_outcome": None, "review_root_cause": None},
        {"run_control": "CONTINUE", "target_stage": "CREATE", "transition_reason_code": "MATERIAL_SUFFICIENT", "director_message": "FORBIDDEN", "gate_outcome": None, "gate_codes": (), "review_outcome": None, "review_root_cause": None},
    ),
    "CREATE": (
        {"run_control": "CONTINUE", "target_stage": "REVIEW", "transition_reason_code": "DRAFT_CREATED", "director_message": "FORBIDDEN", "gate_outcome": None, "gate_codes": (), "review_outcome": None, "review_root_cause": None},
    ),
    "REVIEW": (
        {"run_control": "CONTINUE", "target_stage": "CREATE", "transition_reason_code": "WRITING_REPAIR", "director_message": "FORBIDDEN", "gate_outcome": "BLOCKED", "gate_codes": ("CONTENT_INCOMPLETE", "NOT_SHOOTABLE", "OWNER_VOICE_MISMATCH"), "review_outcome": "BLOCKED", "review_root_cause": "WRITING_PROBLEM"},
        {"run_control": "CONTINUE", "target_stage": "DEEPEN", "transition_reason_code": "MATERIAL_GAP", "director_message": "FORBIDDEN", "gate_outcome": "BLOCKED", "gate_codes": ("MATERIAL_INSUFFICIENT",), "review_outcome": "BLOCKED", "review_root_cause": "MATERIAL_PROBLEM"},
        {"run_control": "CONTINUE", "target_stage": "EXPLORE", "transition_reason_code": "DIRECTION_INVALID", "director_message": "FORBIDDEN", "gate_outcome": "BLOCKED", "gate_codes": ("DIRECTION_NOT_CONFIRMED", "FACT_BOUNDARY_UNCLEAR"), "review_outcome": "BLOCKED", "review_root_cause": "DIRECTION_PROBLEM"},
        {"run_control": "READY", "target_stage": "READY", "transition_reason_code": "REVIEW_PASSED", "director_message": "REQUIRED", "gate_outcome": "PASSED", "gate_codes": ("READINESS_PASSED",), "review_outcome": "PASSED", "review_root_cause": None},
    ),
    "READY": (),
}


STAGE_EXECUTION_COMBINATIONS = {
    stage: tuple((item["run_control"], item["target_stage"]) for item in outcomes)
    for stage, outcomes in STAGE_OUTCOME_CONTRACT.items()
}


def stage_execution_contract(stage: str) -> dict[str, Any]:
    """Generate the handler-visible contract from the validation authority."""

    outcomes = STAGE_OUTCOME_CONTRACT[stage]
    return {
        "stage": stage,
        "outcomes": [deepcopy(item) for item in outcomes],
        "allowed_combinations": [
            {"run_control": item["run_control"], "target_stage": item["target_stage"]}
            for item in outcomes
        ],
        "run_controls": list(dict.fromkeys(item["run_control"] for item in outcomes)),
        "legal_target_stages": list(dict.fromkeys(item["target_stage"] for item in outcomes)),
    }


def outcome_spec(
    entered_stage: str,
    run_control: str,
    target_stage: str,
    transition_reason_code: str,
) -> dict[str, Any]:
    for item in STAGE_OUTCOME_CONTRACT.get(entered_stage, ()):
        if (
            item["run_control"] == run_control
            and item["target_stage"] == target_stage
            and item["transition_reason_code"] == transition_reason_code
        ):
            return item
    raise ValueError("illegal Stage outcome control/target/reason combination")


def validate_outcome_envelope(
    *,
    entered_stage: str,
    run_control: str,
    target_stage: str,
    transition_reason_code: str,
    director_message: str | None,
    gate: Any,
    review: Any,
) -> None:
    """Validate the fields shared by model output and persisted trace."""

    spec = outcome_spec(entered_stage, run_control, target_stage, transition_reason_code)
    if spec["director_message"] == "FORBIDDEN" and director_message is not None:
        raise ValueError("CONTINUE outcomes forbid director_message")
    if spec["director_message"] == "REQUIRED" and (
        not isinstance(director_message, str) or is_blank_text(director_message)
    ):
        raise ValueError("terminal outcomes require a non-blank director_message")
    if spec["gate_outcome"] is None:
        if gate is not None:
            raise ValueError("this Stage outcome forbids gate")
    elif (
        gate is None
        or getattr(gate, "outcome", None) != spec["gate_outcome"]
        or getattr(gate, "gate_code", None) not in spec["gate_codes"]
    ):
        raise ValueError("gate does not match the Stage outcome contract")
    if spec["review_outcome"] is None:
        if review is not None:
            raise ValueError("non-REVIEW outcomes forbid review")
    elif (
        review is None
        or getattr(review, "outcome", None) != spec["review_outcome"]
        or getattr(review, "root_cause", None) != spec["review_root_cause"]
    ):
        raise ValueError("review does not match the Stage outcome contract")


__all__ = [
    "STAGE_EXECUTION_COMBINATIONS",
    "STAGE_OUTCOME_CONTRACT",
    "outcome_spec",
    "stage_execution_contract",
    "validate_outcome_envelope",
]
