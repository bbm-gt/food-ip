from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.director_core.models import (
    ContextCheckpoint,
    ExecutionStep,
    FirstResponse,
    TurnExecutionTrace,
    WorkingState,
    validate_turn_execution_trace,
    validate_working_state,
)


def uid() -> str:
    return str(uuid4())


def evidence(session_id: str | None = None) -> dict:
    return {
        "evidence_type": "owner_message",
        "target_id": uid(),
        "target_session_id": session_id or uid(),
    }


def empty_state() -> dict:
    return {
        "format_version": 1,
        "owner_facts": [],
        "ai_judgments": [],
        "unconfirmed_inferences": [],
        "rejected_items": [],
        "owner_constraints": [],
        "direction": None,
        "material_state": {"status": "UNKNOWN", "required_confirmations": []},
        "draft": None,
        "review": None,
    }


def test_working_state_is_strict_and_rejects_blank_or_extra_fields() -> None:
    WorkingState.model_validate(empty_state())
    with pytest.raises(ValidationError):
        WorkingState.model_validate({**empty_state(), "unknown": True})
    bad = empty_state()
    bad["ai_judgments"] = [{"item_id": uid(), "judgment_kind": "STRUCTURE", "statement": "　"}]
    with pytest.raises(ValidationError):
        WorkingState.model_validate(bad)


def test_required_confirmation_allows_empty_evidence() -> None:
    state = empty_state()
    state["material_state"]["required_confirmations"] = [
        {"item_id": uid(), "statement": "确认营业时间", "reason": "会影响内容", "evidence_refs": [], "inherited_from": None}
    ]
    WorkingState.model_validate(state)


def test_constraint_kind_is_closed_six_value_enum() -> None:
    state = empty_state()
    state["owner_constraints"] = [
        {"item_id": uid(), "statement": "不要拍后厨", "evidence_refs": [evidence()], "constraint_kind": "OTHER", "inherited_from": None}
    ]
    with pytest.raises(ValidationError):
        WorkingState.model_validate(state)


def test_working_state_item_ids_are_unique_across_all_item_collections() -> None:
    item_id = uid()
    state = empty_state()
    state["owner_facts"] = [{
        "item_id": item_id, "statement": "同一个事实。", "evidence_refs": [evidence()],
        "supersedes_item_ids": [], "inherited_from": None,
    }]
    state["owner_constraints"] = [{
        "item_id": item_id, "statement": "同一个约束。", "evidence_refs": [evidence()],
        "constraint_kind": "PROHIBITION", "inherited_from": None,
    }]
    with pytest.raises(ValidationError):
        WorkingState.model_validate(state)


def test_rejected_item_evidence_conditions_are_enforced() -> None:
    state = empty_state()
    state["rejected_items"] = [{
        "item_id": uid(), "item_kind": "OWNER_FACT", "statement": "旧事实",
        "rejection_code": "OWNER_CORRECTED", "evidence_refs": [evidence()],
        "rejected_by_evidence_refs": [], "superseded_by_item_id": uid(), "inherited_from": None,
    }]
    with pytest.raises(ValidationError):
        WorkingState.model_validate(state)

    state["rejected_items"][0]["rejection_code"] = "NO_LONGER_USED"
    WorkingState.model_validate(state)

    state["rejected_items"][0]["item_kind"] = "AI_JUDGMENT"
    state["rejected_items"][0]["rejection_code"] = "OWNER_REJECTED"
    with pytest.raises(ValidationError):
        WorkingState.model_validate(state)


def test_null_draft_id_only_allowed_for_revision_version_zero() -> None:
    source_id = uid()
    state = empty_state()
    state["draft"] = {
        "draft_id": None,
        "content": {"title": None, "script_text": "来源内容", "shooting_notes": []},
        "content_status": "WORKING",
        "based_on_ready_content_id": source_id,
    }
    validate_working_state(state, stage="EXPLORE", state_version=0, source_ready_content_id=source_id)
    with pytest.raises(ValueError):
        validate_working_state(state, stage="EXPLORE", state_version=1, source_ready_content_id=source_id)
    with pytest.raises(ValueError):
        validate_working_state(state, stage="EXPLORE", state_version=0)


def test_ready_cross_field_invariants() -> None:
    with pytest.raises(ValueError):
        validate_working_state(empty_state(), stage="READY", state_version=1)


def test_trace_checkpoint_and_first_response_are_strict() -> None:
    trace = {
        "format_version": 1,
        "steps": [{
            "step_no": 1, "entered_stage": "EXPLORE", "run_control": "WAIT_FOR_OWNER",
            "target_stage": "EXPLORE", "transition_reason_code": "OWNER_INPUT_REQUIRED",
            "gate": {"outcome": "BLOCKED", "gate_code": "DIRECTION_NOT_CONFIRMED", "explanation": "方向尚未确认。"},
            "review": None, "candidate_revision": 1,
        }],
    }
    TurnExecutionTrace.model_validate(trace)
    trace["steps"][0]["candidate_revision"] = 2
    with pytest.raises(ValidationError):
        TurnExecutionTrace.model_validate(trace)

    ContextCheckpoint.model_validate({
        "conversation_summary": "", "confirmed_owner_positions": [],
        "open_threads": [], "abandoned_directions": [],
    })
    with pytest.raises(ValidationError):
        ContextCheckpoint.model_validate({
            "conversation_summary": "无引用摘要", "confirmed_owner_positions": [],
            "open_threads": [], "abandoned_directions": [],
        })

    response = {
        "session_id": uid(), "turn_id": uid(), "owner_message_id": uid(),
        "director_message_id": uid(), "state_version": 1, "stage": "EXPLORE",
        "run_control": "WAIT_FOR_OWNER", "director_message": "请补充素材", "ready_content_id": None,
    }
    FirstResponse.model_validate(response)
    response["ready_content_id"] = uid()
    with pytest.raises(ValidationError):
        FirstResponse.model_validate(response)


@pytest.mark.parametrize("bad_id", ["x00000000-0000-4000-8000-000000000000", "00000000-0000-4000-8000-000000000000x", "00000000-0000-1000-8000-000000000000", "00000000-0000-4000-7000-000000000000", "00000000-0000-4000-8000-00000000000A"])
def test_uuid_fields_require_a_complete_normalized_v4_uuid(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        FirstResponse.model_validate({"session_id": bad_id, "turn_id": uid(), "owner_message_id": uid(), "director_message_id": uid(), "state_version": 1, "stage": "EXPLORE", "run_control": "WAIT_FOR_OWNER", "director_message": "reply", "ready_content_id": None})


def test_trace_closure_rejects_broken_chain_reason_and_top_level_mismatch() -> None:
    trace = {"format_version": 1, "steps": [
        {"step_no": 1, "entered_stage": "EXPLORE", "run_control": "CONTINUE", "target_stage": "DEEPEN", "transition_reason_code": "DIRECTION_CONFIRMED", "gate": None, "review": None, "candidate_revision": 1},
        {"step_no": 2, "entered_stage": "CREATE", "run_control": "WAIT_FOR_OWNER", "target_stage": "CREATE", "transition_reason_code": "OWNER_INPUT_REQUIRED", "gate": None, "review": None, "candidate_revision": 2},
    ]}
    with pytest.raises(ValueError):
        validate_turn_execution_trace(trace, pre_stage="EXPLORE", final_run_control="WAIT_FOR_OWNER", target_stage="CREATE", transition_reason_code="OWNER_INPUT_REQUIRED", gate_outcome=None, review_root_cause=None)
    trace["steps"][1].update(entered_stage="DEEPEN", target_stage="DEEPEN", transition_reason_code="DRAFT_CREATED")
    with pytest.raises(ValueError):
        validate_turn_execution_trace(trace, pre_stage="EXPLORE", final_run_control="WAIT_FOR_OWNER", target_stage="DEEPEN", transition_reason_code="DRAFT_CREATED", gate_outcome=None, review_root_cause=None)
    trace["steps"][1]["transition_reason_code"] = "OWNER_INPUT_REQUIRED"
    with pytest.raises(ValueError):
        validate_turn_execution_trace(trace, pre_stage="EXPLORE", final_run_control="WAIT_FOR_OWNER", target_stage="CREATE", transition_reason_code="OWNER_INPUT_REQUIRED", gate_outcome=None, review_root_cause=None)


def test_execution_step_rejects_create_wait_for_owner() -> None:
    with pytest.raises(ValidationError):
        ExecutionStep.model_validate({
            "step_no": 1,
            "entered_stage": "CREATE",
            "run_control": "WAIT_FOR_OWNER",
            "target_stage": "CREATE",
            "transition_reason_code": "OWNER_INPUT_REQUIRED",
            "gate": None,
            "review": None,
            "candidate_revision": 1,
        })


def test_ready_stage_cannot_be_paired_with_wait_for_owner() -> None:
    with pytest.raises(ValidationError):
        FirstResponse.model_validate({"session_id": uid(), "turn_id": uid(), "owner_message_id": uid(), "director_message_id": uid(), "state_version": 1, "stage": "READY", "run_control": "WAIT_FOR_OWNER", "director_message": "reply", "ready_content_id": None})


@pytest.mark.parametrize("entered_stage", ["DEEPEN", "CREATE"])
def test_review_is_forbidden_outside_review_stage(entered_stage: str) -> None:
    with pytest.raises(ValidationError):
        ExecutionStep.model_validate({
            "step_no": 1, "entered_stage": entered_stage, "run_control": "CONTINUE",
            "target_stage": "CREATE" if entered_stage == "DEEPEN" else "REVIEW",
            "transition_reason_code": "MATERIAL_SUFFICIENT" if entered_stage == "DEEPEN" else "DRAFT_CREATED",
            "gate": None, "review": {"outcome": "BLOCKED", "root_cause": "WRITING_PROBLEM"},
            "candidate_revision": 1,
        })


def test_review_blocked_route_and_review_passed_gate_are_closed() -> None:
    blocked = {
        "step_no": 1, "entered_stage": "REVIEW", "run_control": "CONTINUE", "target_stage": "DEEPEN",
        "transition_reason_code": "MATERIAL_GAP", "gate": None,
        "review": {"outcome": "BLOCKED", "root_cause": "WRITING_PROBLEM"}, "candidate_revision": 1,
    }
    with pytest.raises(ValidationError):
        ExecutionStep.model_validate(blocked)
    passed = {
        "step_no": 1, "entered_stage": "REVIEW", "run_control": "READY", "target_stage": "READY",
        "transition_reason_code": "REVIEW_PASSED", "gate": None,
        "review": {"outcome": "PASSED", "root_cause": None}, "candidate_revision": 1,
    }
    with pytest.raises(ValidationError):
        ExecutionStep.model_validate(passed)


def test_historical_review_passed_rejects_wrong_gate_and_accepts_readiness_gate() -> None:
    step = {
        "step_no": 1, "entered_stage": "REVIEW", "run_control": "READY",
        "target_stage": "READY", "transition_reason_code": "REVIEW_PASSED",
        "gate": {
            "outcome": "BLOCKED", "gate_code": "CONTENT_INCOMPLETE",
            "explanation": "错误 Gate。",
        },
        "review": {"outcome": "PASSED", "root_cause": None}, "candidate_revision": 1,
    }
    with pytest.raises(ValidationError):
        ExecutionStep.model_validate(step)

    step["gate"] = {
        "outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": "内容可拍。",
    }
    validated = ExecutionStep.model_validate(step)
    assert validated.gate is not None
    assert validated.gate.gate_code == "READINESS_PASSED"


@pytest.mark.parametrize(("entered_stage", "target_stage"), [
    ("EXPLORE", "EXPLORE"),
    ("DEEPEN", "DEEPEN"),
])
def test_historical_trace_v1_accepts_legal_wait_null_gate_without_version_change(
    entered_stage: str, target_stage: str
) -> None:
    trace = {"format_version": 1, "steps": [{
        "step_no": 1,
        "entered_stage": entered_stage,
        "run_control": "WAIT_FOR_OWNER",
        "target_stage": target_stage,
        "transition_reason_code": "OWNER_INPUT_REQUIRED",
        "gate": None,
        "review": None,
        "candidate_revision": 1,
    }]}
    validated = validate_turn_execution_trace(
        trace,
        pre_stage=entered_stage,
        final_run_control="WAIT_FOR_OWNER",
        target_stage=target_stage,
        transition_reason_code="OWNER_INPUT_REQUIRED",
        gate_outcome=None,
        review_root_cause=None,
    )
    assert validated.format_version == 1
    assert validated.steps[0].gate is None
