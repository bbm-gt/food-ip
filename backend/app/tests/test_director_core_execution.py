from dataclasses import replace
from uuid import uuid4

import pytest

from backend.app.director_core.canonical import canonical_text, normalize_text
from backend.app.director_core.execution import (
    CommitOutcomeCorruptError,
    CommitSuccessfulTurnInput,
    PreparedSuccessfulTurn,
    SessionAlreadyReadyError,
    prepare_successful_turn,
)


def uid() -> str:
    return str(uuid4())


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


def valid_non_ready_command() -> CommitSuccessfulTurnInput:
    return CommitSuccessfulTurnInput(
        session_id=uid(),
        client_message_id="client-001",
        expected_state_version=4,
        turn_id=uid(),
        owner_message_id=uid(),
        director_message_id=uid(),
        owner_message="老板说：今天先聊聊这道菜\r\n的来历。",
        director_message="请再补充一个最关键的真实细节。",
        normalized_parameters={},
        execution_trace={
            "format_version": 1,
            "steps": [{
                "step_no": 1,
                "entered_stage": "EXPLORE",
                "run_control": "WAIT_FOR_OWNER",
                "target_stage": "EXPLORE",
                "transition_reason_code": "OWNER_INPUT_REQUIRED",
                "gate": None,
                "review": None,
                "candidate_revision": 1,
            }],
        },
        post_state=empty_state(),
        final_run_control="WAIT_FOR_OWNER",
        target_stage="EXPLORE",
        transition_reason_code="OWNER_INPUT_REQUIRED",
        gate_outcome=None,
        review_root_cause=None,
        ready_content_id=None,
        ready_content=None,
        created_at="2026-08-14T10:20:30.123Z",
    )


def valid_ready_command() -> CommitSuccessfulTurnInput:
    session_id = uid()
    owner_message_id = uid()
    direction_id = uid()
    draft_id = uid()
    review_id = uid()
    ready_content_id = uid()
    content = {
        "title": "一碗汤的来历",
        "script_text": "这道汤不是为了复杂，而是为了让客人喝到我们真正熟悉的味道。",
        "shooting_notes": ["从出锅画面开始"] ,
    }
    state = empty_state()
    state.update(
        direction={
            "item_id": direction_id,
            "statement": "讲清这道汤为什么值得被记住",
            "owner_confirmed": True,
            "evidence_refs": [{
                "evidence_type": "owner_message",
                "target_id": owner_message_id,
                "target_session_id": session_id,
            }],
            "inherited_from": None,
        },
        material_state={"status": "SUFFICIENT", "required_confirmations": []},
        draft={
            "draft_id": draft_id,
            "content": content,
            "content_status": "FINAL_CANDIDATE",
            "based_on_ready_content_id": None,
        },
        review={
            "review_id": review_id,
            "outcome": "PASSED",
            "root_cause": None,
            "against_draft_id": draft_id,
            "against_content": content,
        },
    )
    return CommitSuccessfulTurnInput(
        session_id=session_id,
        client_message_id="client-ready-001",
        expected_state_version=0,
        turn_id=uid(),
        owner_message_id=owner_message_id,
        director_message_id=uid(),
        owner_message="老板确认，就按这个方向拍。",
        director_message="这版已经可以拍了。",
        normalized_parameters={},
        execution_trace={
            "format_version": 1,
            "steps": [{
                "step_no": 1,
                "entered_stage": "REVIEW",
                "run_control": "READY",
                "target_stage": "READY",
                "transition_reason_code": "REVIEW_PASSED",
                "gate": {
                    "outcome": "PASSED",
                    "gate_code": "READINESS_PASSED",
                    "explanation": "内容完整、真实且可拍。",
                },
                "review": {"outcome": "PASSED", "root_cause": None},
                "candidate_revision": 1,
            }],
        },
        post_state=state,
        final_run_control="READY",
        target_stage="READY",
        transition_reason_code="REVIEW_PASSED",
        gate_outcome="PASSED",
        review_root_cause=None,
        ready_content_id=ready_content_id,
        ready_content=content,
        created_at="2026-08-14T10:20:30.123Z",
    )


def prepare(command: CommitSuccessfulTurnInput, *, current_stage: str = "EXPLORE") -> PreparedSuccessfulTurn:
    return prepare_successful_turn(command, current_stage=current_stage, source_ready_content_id=None)


def test_prepare_valid_non_ready_candidate_is_pure_and_closed() -> None:
    command = valid_non_ready_command()
    prepared = prepare(command)

    assert prepared.pre_state_version == 4
    assert prepared.post_state_version == 5
    assert prepared.owner_message_seq == 9
    assert prepared.director_message_seq == 10
    assert prepared.normalized_request["owner_text"] == normalize_text(command.owner_message)
    assert prepared.ready_content is None
    assert prepared.first_response["ready_content_id"] is None
    assert prepared.first_response["director_message"] == command.director_message
    assert prepared.post_state_snapshot["state_version"] == 5
    assert prepared.post_state_snapshot["stage"] == "EXPLORE"


def test_prepare_valid_ready_candidate_closes_ready_content_and_response() -> None:
    command = valid_ready_command()
    prepared = prepare(command, current_stage="REVIEW")

    assert prepared.first_response["run_control"] == "READY"
    assert prepared.first_response["stage"] == "READY"
    assert prepared.first_response["ready_content_id"] == command.ready_content_id
    assert prepared.validated_ready_content == command.ready_content
    assert prepared.post_state["draft"]["content"] == prepared.validated_ready_content


def test_request_and_state_hashes_are_stable() -> None:
    command = valid_non_ready_command()
    first = prepare(command)
    second = prepare(command)

    assert first.request_sha256 == second.request_sha256
    assert first.post_state_sha256 == second.post_state_sha256
    assert canonical_text(first.normalized_request) == canonical_text(second.normalized_request)
    assert canonical_text(first.post_state_snapshot) == canonical_text(second.post_state_snapshot)


@pytest.mark.parametrize("field", ["session_id", "turn_id", "owner_message_id", "director_message_id"])
def test_internal_ids_must_be_uuid4(field: str) -> None:
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(valid_non_ready_command(), **{field: "not-a-uuid"}))


def test_invalid_time_is_rejected() -> None:
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(valid_non_ready_command(), created_at="2026-08-14T18:20:30+08:00"))


def test_empty_owner_message_is_rejected() -> None:
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(valid_non_ready_command(), owner_message=" \u3000\r\n"))


def test_bool_expected_state_version_is_not_an_integer() -> None:
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(valid_non_ready_command(), expected_state_version=True))


def test_trace_first_stage_must_match_current_stage() -> None:
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(valid_non_ready_command(), current_stage="DEEPEN")


def test_trace_final_fields_must_match_top_level_fields() -> None:
    command = valid_non_ready_command()
    trace = {**command.execution_trace, "steps": [dict(command.execution_trace["steps"][0])]}
    trace["steps"][0]["target_stage"] = "DEEPEN"
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(command, execution_trace=trace))


def test_target_stage_must_match_working_state_stage() -> None:
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(valid_non_ready_command(), target_stage="DEEPEN"))


def test_ready_requires_passed_review_and_sufficient_material() -> None:
    command = valid_ready_command()
    bad_review = {**command.post_state, "review": {**command.post_state["review"], "outcome": "BLOCKED", "root_cause": "WRITING_PROBLEM"}}
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(command, post_state=bad_review))

    bad_material = {**command.post_state, "material_state": {"status": "INSUFFICIENT", "required_confirmations": []}}
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(command, post_state=bad_material))


def test_ready_requires_ready_content_and_exact_draft_content() -> None:
    command = valid_ready_command()
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(command, ready_content_id=None, ready_content=None))
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(command, ready_content={**command.ready_content, "script_text": "另一版"}))


def test_non_ready_cannot_carry_ready_content() -> None:
    command = valid_non_ready_command()
    with pytest.raises(CommitOutcomeCorruptError):
        prepare(replace(command, ready_content_id=uid(), ready_content={"title": None, "script_text": "内容", "shooting_notes": []}))


def test_first_response_director_text_is_closed_over_input() -> None:
    prepared = prepare(valid_non_ready_command())
    assert prepared.first_response["director_message"] == "请再补充一个最关键的真实细节。"


def test_ready_session_is_rejected_before_candidate_preparation() -> None:
    with pytest.raises(SessionAlreadyReadyError):
        prepare(valid_non_ready_command(), current_stage="READY")
