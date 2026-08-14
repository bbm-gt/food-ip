from dataclasses import replace
from uuid import uuid4

import pytest

from backend.app.director_core.canonical import SQLITE_INT_MAX, canonical_sha256, canonical_text, normalize_text, state_sha256
from backend.app.director_core.execution import (
    CommitSuccessfulTurnInput,
    DirectorExecutionError,
    DirectorExecutionValidationError,
    PreparedSuccessfulTurn,
    StaleStateVersionError,
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
        session_id=uid(), client_message_id="client-001", expected_state_version=4,
        request_format_version=1, turn_id=uid(), owner_message_id=uid(),
        director_message_id=uid(), owner_message="老板说：今天先聊聊这道菜\r\n的来历。",
        director_message="请再补充一个最关键的真实细节。", normalized_parameters={},
        execution_trace={"format_version": 1, "steps": [{
            "step_no": 1, "entered_stage": "EXPLORE", "run_control": "WAIT_FOR_OWNER",
            "target_stage": "EXPLORE", "transition_reason_code": "OWNER_INPUT_REQUIRED",
            "gate": None, "review": None, "candidate_revision": 1,
        }]},
        post_state=empty_state(), final_run_control="WAIT_FOR_OWNER", target_stage="EXPLORE",
        transition_reason_code="OWNER_INPUT_REQUIRED", gate_outcome=None, review_root_cause=None,
        ready_content_id=None, ready_content=None, created_at="2026-08-14T10:20:30.123Z",
    )


def valid_ready_command() -> CommitSuccessfulTurnInput:
    session_id, owner_message_id = uid(), uid()
    draft_id, review_id, ready_content_id = uid(), uid(), uid()
    content = {
        "title": "一碗汤的来历",
        "script_text": "这道汤不是为了复杂，而是为了让客人喝到我们真正熟悉的味道。",
        "shooting_notes": ["从出锅画面开始"],
    }
    state = empty_state()
    state.update(
        direction={
            "item_id": uid(), "statement": "讲清这道汤为什么值得被记住", "owner_confirmed": True,
            "evidence_refs": [{"evidence_type": "owner_message", "target_id": owner_message_id,
                               "target_session_id": session_id}],
            "inherited_from": None,
        },
        material_state={"status": "SUFFICIENT", "required_confirmations": []},
        draft={"draft_id": draft_id, "content": content, "content_status": "FINAL_CANDIDATE",
               "based_on_ready_content_id": None},
        review={"review_id": review_id, "outcome": "PASSED", "root_cause": None,
                "against_draft_id": draft_id, "against_content": content},
    )
    return replace(
        valid_non_ready_command(), session_id=session_id, client_message_id="client-ready-001",
        expected_state_version=0, turn_id=uid(), owner_message_id=owner_message_id,
        director_message_id=uid(), owner_message="老板确认，就按这个方向拍。",
        director_message="这版已经可以拍了。",
        execution_trace={"format_version": 1, "steps": [{
            "step_no": 1, "entered_stage": "REVIEW", "run_control": "READY",
            "target_stage": "READY", "transition_reason_code": "REVIEW_PASSED",
            "gate": {"outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": "内容完整、真实且可拍。"},
            "review": {"outcome": "PASSED", "root_cause": None}, "candidate_revision": 1,
        }]},
        post_state=state, final_run_control="READY", target_stage="READY",
        transition_reason_code="REVIEW_PASSED", gate_outcome="PASSED",
        ready_content_id=ready_content_id, ready_content=content,
    )


def prepare(
    command: CommitSuccessfulTurnInput,
    *,
    current_state_version: int | None = None,
    current_max_message_seq: int | None = None,
    current_stage: str = "EXPLORE",
) -> PreparedSuccessfulTurn:
    current_state_version = command.expected_state_version if current_state_version is None else current_state_version
    current_max_message_seq = 2 * current_state_version if current_max_message_seq is None else current_max_message_seq
    return prepare_successful_turn(
        command,
        current_state_version=current_state_version,
        current_max_message_seq=current_max_message_seq,
        current_stage=current_stage,
        source_ready_content_id=None,
    )


def test_prepare_valid_non_ready_candidate_is_pure_and_closed() -> None:
    command = valid_non_ready_command()
    prepared = prepare(command)
    assert (prepared.pre_state_version, prepared.post_state_version) == (4, 5)
    assert (prepared.owner_message_seq, prepared.director_message_seq) == (9, 10)
    assert prepared.normalized_request["owner_text"] == normalize_text(command.owner_message)
    assert prepared.owner_message == command.owner_message
    assert prepared.ready_content is prepared.final_content_json is None
    assert prepared.first_response["ready_content_id"] is None
    assert prepared.first_response["director_message"] == command.director_message


def test_prepare_valid_ready_candidate_closes_ready_content_and_response() -> None:
    command = valid_ready_command()
    prepared = prepare(command, current_stage="REVIEW", current_max_message_seq=0)
    assert prepared.first_response["ready_content_id"] == command.ready_content_id
    assert prepared.ready_content == command.ready_content
    assert prepared.post_state["draft"]["content"] == prepared.ready_content
    assert prepared.content_format_version == 1
    assert prepared.final_content_json == canonical_text(prepared.ready_content)


def test_stale_expected_state_version_is_rejected() -> None:
    with pytest.raises(StaleStateVersionError):
        prepare(valid_non_ready_command(), current_state_version=5)


def test_pre_state_version_comes_from_current_authority() -> None:
    command = replace(valid_non_ready_command(), expected_state_version=7)
    prepared = prepare(command, current_state_version=7)
    assert (prepared.pre_state_version, prepared.post_state_version) == (7, 8)


def test_message_sequences_come_from_current_maximum() -> None:
    prepared = prepare(valid_non_ready_command(), current_max_message_seq=8)
    assert (prepared.owner_message_seq, prepared.director_message_seq) == (9, 10)


def test_inconsistent_current_max_message_seq_is_rejected() -> None:
    with pytest.raises(DirectorExecutionValidationError):
        prepare(valid_non_ready_command(), current_max_message_seq=41)


@pytest.mark.parametrize("current_state_version, current_max_message_seq", [(0, 0), (4, 8)])
def test_message_sequence_formula_is_version_derived(current_state_version: int, current_max_message_seq: int) -> None:
    command = replace(valid_non_ready_command(), expected_state_version=current_state_version)
    prepared = prepare(command, current_state_version=current_state_version, current_max_message_seq=current_max_message_seq)
    assert prepared.post_state_version == current_state_version + 1
    assert prepared.owner_message_seq == 2 * prepared.post_state_version - 1
    assert prepared.director_message_seq == 2 * prepared.post_state_version


@pytest.mark.parametrize("kwargs", [
    {"current_state_version": SQLITE_INT_MAX},
    {"current_max_message_seq": SQLITE_INT_MAX - 1},
    {"current_state_version": True},
    {"current_max_message_seq": True},
])
def test_authority_version_and_sequence_bounds_are_rejected(kwargs: dict) -> None:
    command = valid_non_ready_command()
    if kwargs.get("current_state_version") == SQLITE_INT_MAX:
        command = replace(command, expected_state_version=SQLITE_INT_MAX)
    with pytest.raises(DirectorExecutionValidationError):
        prepare(command, **kwargs)


def test_request_format_version_must_be_v1() -> None:
    for value in (2, 1.0, True):
        with pytest.raises(DirectorExecutionValidationError):
            prepare(replace(valid_non_ready_command(), request_format_version=value))


@pytest.mark.parametrize("field", ["session_id", "turn_id", "owner_message_id", "director_message_id"])
def test_internal_ids_must_be_uuid4(field: str) -> None:
    with pytest.raises(DirectorExecutionValidationError):
        prepare(replace(valid_non_ready_command(), **{field: "not-a-uuid"}))


def test_invalid_timestamp_and_bool_expected_version_are_rejected() -> None:
    with pytest.raises(DirectorExecutionValidationError):
        prepare(replace(valid_non_ready_command(), created_at="2026-08-14T18:20:30+08:00"))
    with pytest.raises(DirectorExecutionValidationError):
        prepare(replace(valid_non_ready_command(), expected_state_version=True))


def test_ready_session_is_a_validation_error() -> None:
    with pytest.raises(DirectorExecutionValidationError):
        prepare(valid_non_ready_command(), current_stage="READY")


def test_crlf_and_lone_cr_normalize_only_the_request() -> None:
    crlf = replace(valid_non_ready_command(), owner_message="A\r\nB")
    lone_cr = replace(valid_non_ready_command(), owner_message="A\rB")
    assert prepare(crlf).normalized_request["owner_text"] == "A\nB"
    assert prepare(lone_cr).normalized_request["owner_text"] == "A\nB"
    assert prepare(crlf).owner_message == "A\r\nB"


def test_unicode_nfc_equivalents_have_identical_normalized_request_and_hash() -> None:
    composed = replace(valid_non_ready_command(), owner_message="é")
    decomposed = replace(valid_non_ready_command(), owner_message="e\u0301")
    assert prepare(composed).normalized_request == prepare(decomposed).normalized_request
    assert prepare(composed).request_sha256 == prepare(decomposed).request_sha256


def test_nonempty_parameters_and_blank_owner_text_are_rejected() -> None:
    with pytest.raises(DirectorExecutionValidationError):
        prepare(replace(valid_non_ready_command(), normalized_parameters={"x": 1}))
    with pytest.raises(DirectorExecutionValidationError):
        prepare(replace(valid_non_ready_command(), owner_message="\u3000\r\n"))


@pytest.mark.parametrize("state", [
    {key: value for key, value in empty_state().items() if key != "review"},
    {**empty_state(), "unknown": None},
])
def test_working_state_missing_or_extra_fields_are_rejected(state: dict) -> None:
    with pytest.raises(DirectorExecutionValidationError):
        prepare(replace(valid_non_ready_command(), post_state=state))


def test_trace_start_and_top_level_fields_must_close() -> None:
    with pytest.raises(DirectorExecutionValidationError):
        prepare(valid_non_ready_command(), current_stage="DEEPEN")
    command = valid_non_ready_command()
    trace = {**command.execution_trace, "steps": [dict(command.execution_trace["steps"][0])]}
    trace["steps"][0]["target_stage"] = "DEEPEN"
    with pytest.raises(DirectorExecutionValidationError):
        prepare(replace(command, execution_trace=trace))


def test_illegal_stage_transition_and_target_state_mismatch_are_rejected() -> None:
    command = valid_non_ready_command()
    trace = {**command.execution_trace, "steps": [dict(command.execution_trace["steps"][0])]}
    trace["steps"][0].update(entered_stage="CREATE", target_stage="EXPLORE")
    with pytest.raises(DirectorExecutionValidationError):
        prepare(replace(command, execution_trace=trace), current_stage="CREATE")
    with pytest.raises(DirectorExecutionValidationError):
        prepare(replace(command, target_stage="DEEPEN"))


@pytest.mark.parametrize("mutate", [
    lambda command: replace(command, post_state={**command.post_state, "draft": None}),
    lambda command: replace(command, post_state={**command.post_state, "review": None}),
    lambda command: replace(command, post_state={**command.post_state, "material_state": {"status": "INSUFFICIENT", "required_confirmations": []}}),
    lambda command: replace(command, ready_content={**command.ready_content, "script_text": "另一版"}),
])
def test_ready_closure_requires_complete_matching_content(mutate) -> None:
    with pytest.raises(DirectorExecutionValidationError):
        prepare(mutate(valid_ready_command()), current_stage="REVIEW")


def test_non_ready_cannot_carry_ready_content() -> None:
    with pytest.raises(DirectorExecutionValidationError):
        prepare(replace(valid_non_ready_command(), ready_content_id=uid(), ready_content={
            "title": None, "script_text": "内容", "shooting_notes": [],
        }))


def test_prepared_contains_complete_canonical_json_and_matching_hashes() -> None:
    prepared = prepare(valid_non_ready_command())
    assert prepared.normalized_request_json == canonical_text(prepared.normalized_request)
    assert prepared.execution_trace_json == canonical_text(prepared.execution_trace)
    assert prepared.first_response_json == canonical_text(prepared.first_response)
    assert prepared.post_state_json == canonical_text(prepared.post_state)
    assert prepared.post_state_snapshot_json == canonical_text(prepared.post_state_snapshot)
    assert prepared.request_sha256 == canonical_sha256(prepared.normalized_request)
    assert prepared.post_state_sha256 == state_sha256(
        prepared.post_state_version, prepared.target_stage, prepared.post_state
    )


def test_prepared_does_not_share_mutable_input_references() -> None:
    command = valid_ready_command()
    prepared = prepare(command, current_stage="REVIEW")
    before = (prepared.normalized_request_json, prepared.execution_trace_json,
              prepared.post_state_snapshot_json, prepared.final_content_json)
    command.normalized_parameters["later"] = "mutation"
    command.execution_trace["steps"][0]["step_no"] = 99
    command.post_state["draft"]["content"]["script_text"] = "mutation"
    command.ready_content["script_text"] = "mutation"
    assert before == (prepared.normalized_request_json, prepared.execution_trace_json,
                      prepared.post_state_snapshot_json, prepared.final_content_json)
    assert prepared.ready_content["script_text"] != "mutation"
    assert prepared.execution_trace["steps"][0]["step_no"] == 1
    assert prepared.post_state["draft"]["content"]["script_text"] != "mutation"


def test_prepared_property_views_cannot_mutate_canonical_state() -> None:
    prepared = prepare(valid_ready_command(), current_stage="REVIEW", current_max_message_seq=0)
    request = prepared.normalized_request
    trace = prepared.execution_trace
    response = prepared.first_response
    state = prepared.post_state
    snapshot = prepared.post_state_snapshot
    content = prepared.ready_content
    request["owner_text"] = "mutation"
    trace["steps"].clear()
    response["stage"] = "READY"
    state["draft"] = None
    snapshot["state_version"] = 999
    content["script_text"] = "mutation"
    assert prepared.normalized_request["owner_text"] != "mutation"
    assert len(prepared.execution_trace["steps"]) == 1
    assert prepared.first_response["stage"] == "READY"
    assert prepared.post_state["draft"] is not None
    assert prepared.post_state_snapshot["state_version"] == 1
    assert prepared.ready_content["script_text"] != "mutation"
    assert prepared.post_state_json == canonical_text(prepared.post_state)
    assert prepared.post_state_sha256 == state_sha256(
        prepared.post_state_version, prepared.target_stage, prepared.post_state
    )


def test_same_input_produces_the_same_prepared_object() -> None:
    command = valid_non_ready_command()
    assert prepare(command) == prepare(command)


def test_execution_exception_types_are_exported() -> None:
    import backend.app.director_core.execution as execution

    exported_errors = {name for name in execution.__all__ if name.endswith("Error")}
    assert exported_errors == {
        "DirectorExecutionError", "DirectorExecutionValidationError", "IdempotencyConflictError",
        "StaleStateVersionError",
    }
    assert issubclass(StaleStateVersionError, DirectorExecutionError)


def test_successful_turn_result_validates_first_response_and_isolated_views() -> None:
    from backend.app.director_core.execution import SuccessfulTurnResult

    prepared = prepare(valid_non_ready_command())
    result = SuccessfulTurnResult(first_response_json=prepared.first_response_json, replayed=False)
    assert result.response == prepared.first_response
    result.response["stage"] = "READY"
    assert result.response["stage"] == "EXPLORE"
    with pytest.raises(DirectorExecutionValidationError):
        SuccessfulTurnResult(response={}, replayed=False)
    with pytest.raises(DirectorExecutionValidationError):
        SuccessfulTurnResult(first_response_json="{}", replayed=0)
    with pytest.raises(DirectorExecutionValidationError):
        SuccessfulTurnResult(first_response_json=prepared.first_response_json[:-1], replayed=False)
