from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from backend.app.director_core.context import ContextBudget, ContextMessage, ModelContextAssembler
from backend.app.director_core.database import apply_migrations, connect
from backend.app.director_core.orchestrator import (
    DirectorOrchestrator,
    DirectorStageExecutor,
    DirectorTurnRequest,
)
from backend.app.director_core.repository import AuthorizationScope, DirectorRepository
from backend.app.director_core.stage_handler import (
    ContentIdentityError,
    DuplicateItemIdentityError,
    DraftIdentityError,
    ExistingObjectMutationError,
    ForgedUUIDError,
    ReviewIdentityError,
    RejectedItemIdentityError,
    StageContractViolationError,
    StageModelOutputSchemaError,
    StageModelOutputTypeError,
    StageModelOutputV1,
    TemporaryReferenceForbiddenError,
    TemporaryReferenceNamespaceError,
    UndefinedTemporaryReferenceError,
    DuplicateTemporaryDefinitionError,
    resolve_stage_model_proposal,
    validate_stage_model_output,
)


def uid() -> str:
    return str(uuid4())


def temp(namespace: str, key: str) -> str:
    return f"new:{namespace}:{key}"


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


def context(stage: str, state: dict | None = None):
    session_id = uid()
    owner_id = uid()
    owner = ContextMessage(owner_id, "OWNER", "老板确认了这条内容。", 1, "CURRENT_TURN")
    owner_reference = {
        "evidence_type": "owner_message",
        "target_id": owner_id,
        "target_session_id": session_id,
    }
    return SimpleNamespace(
        stage=stage,
        working_state=deepcopy(empty_state() if state is None else state),
        current_owner_message=owner,
        evidence_messages=(),
        history_turns=(),
        owner_evidence_references=(owner_reference,),
        session_id=session_id,
    )


def direction(ctx) -> dict:
    return {
        "item_id": temp("item", "direction_1"),
        "statement": "讲清这道菜的真实来历",
        "owner_confirmed": True,
        "evidence_refs": [{
            "evidence_type": "owner_message",
            "target_id": ctx.current_owner_message.id,
            "target_session_id": ctx.session_id,
        }],
        "inherited_from": None,
    }


def confirmation() -> dict:
    return {
        "item_id": temp("item", "confirmation_1"), "statement": "补充关键细节", "reason": "核心表达需要",
        "evidence_refs": [], "inherited_from": None,
    }


def draft() -> dict:
    return {
        "draft_id": temp("draft", "draft_1"),
        "content": {"title": None, "script_text": "这是完整、真实、可以拍摄的内容。", "shooting_notes": []},
        "content_status": "FINAL_CANDIDATE",
        "based_on_ready_content_id": None,
    }


def output(**changes) -> dict:
    value = {
        "output_format_version": 1,
        "run_control": "WAIT_FOR_OWNER",
        "target_stage": "EXPLORE",
        "transition_reason_code": "OWNER_INPUT_REQUIRED",
        "director_message": "请确认想讲的方向。",
        "gate": {"outcome": "BLOCKED", "gate_code": "DIRECTION_NOT_CONFIRMED", "explanation": "方向尚未确认。"},
        "review": None,
        "post_state": empty_state(),
    }
    value.update(changes)
    return value


def review_state(ctx, root: str | None, *, material: str = "SUFFICIENT", keep_direction: bool = True) -> dict:
    state = empty_state()
    state["direction"] = direction(ctx) if keep_direction else None
    state["material_state"] = {
        "status": material,
        "required_confirmations": [confirmation()] if material == "INSUFFICIENT" else [],
    }
    state["draft"] = draft()
    state["review"] = {
        "review_id": temp("review", "review_1"),
        "outcome": "PASSED" if root is None else "BLOCKED",
        "root_cause": root,
        "against_draft_id": state["draft"]["draft_id"],
        "against_content": deepcopy(state["draft"]["content"]),
    }
    return state


def legal_cases():
    explore_wait = context("EXPLORE")
    explore_go = context("EXPLORE")
    explore_go_state = empty_state()
    explore_go_state["direction"] = direction(explore_go)

    deepen_gap = context("DEEPEN")
    gap_state = empty_state()
    gap_state["material_state"] = {"status": "INSUFFICIENT", "required_confirmations": [confirmation()]}

    deepen_wait = context("DEEPEN")
    wait_state = deepcopy(gap_state)

    deepen_go = context("DEEPEN")
    go_state = empty_state()
    go_state["direction"] = direction(deepen_go)
    go_state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}

    create = context("CREATE")
    create_state = empty_state()
    create_state["direction"] = direction(create)
    create_state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
    create_state["draft"] = draft()

    writing = context("REVIEW")
    writing_state = review_state(writing, "WRITING_PROBLEM")
    material = context("REVIEW")
    material_state = review_state(material, "MATERIAL_PROBLEM", material="INSUFFICIENT")
    invalid = context("REVIEW")
    invalid_state = review_state(invalid, "DIRECTION_PROBLEM", keep_direction=False)
    passed = context("REVIEW")
    passed_state = review_state(passed, None)

    return [
        (explore_wait, output()),
        (explore_go, output(run_control="CONTINUE", target_stage="DEEPEN", transition_reason_code="DIRECTION_CONFIRMED", director_message=None, gate=None, post_state=explore_go_state)),
        (deepen_gap, output(run_control="CONTINUE", target_stage="DEEPEN", transition_reason_code="MATERIAL_GAP", director_message=None, gate={"outcome": "BLOCKED", "gate_code": "MATERIAL_INSUFFICIENT", "explanation": "需要补素材。"}, post_state=gap_state)),
        (deepen_wait, output(target_stage="DEEPEN", gate={"outcome": "BLOCKED", "gate_code": "MATERIAL_INSUFFICIENT", "explanation": "需要老板补素材。"}, post_state=wait_state)),
        (deepen_go, output(run_control="CONTINUE", target_stage="CREATE", transition_reason_code="MATERIAL_SUFFICIENT", director_message=None, gate=None, post_state=go_state)),
        (create, output(run_control="CONTINUE", target_stage="REVIEW", transition_reason_code="DRAFT_CREATED", director_message=None, gate=None, post_state=create_state)),
        (writing, output(run_control="CONTINUE", target_stage="CREATE", transition_reason_code="WRITING_REPAIR", director_message=None, gate={"outcome": "BLOCKED", "gate_code": "CONTENT_INCOMPLETE", "explanation": "表达不完整。"}, review={"outcome": "BLOCKED", "root_cause": "WRITING_PROBLEM"}, post_state=writing_state)),
        (material, output(run_control="CONTINUE", target_stage="DEEPEN", transition_reason_code="MATERIAL_GAP", director_message=None, gate={"outcome": "BLOCKED", "gate_code": "MATERIAL_INSUFFICIENT", "explanation": "素材不足。"}, review={"outcome": "BLOCKED", "root_cause": "MATERIAL_PROBLEM"}, post_state=material_state)),
        (invalid, output(run_control="CONTINUE", target_stage="EXPLORE", transition_reason_code="DIRECTION_INVALID", director_message=None, gate={"outcome": "BLOCKED", "gate_code": "FACT_BOUNDARY_UNCLEAR", "explanation": "方向事实边界不清。"}, review={"outcome": "BLOCKED", "root_cause": "DIRECTION_PROBLEM"}, post_state=invalid_state)),
        (passed, output(run_control="READY", target_stage="READY", transition_reason_code="REVIEW_PASSED", director_message="这版已经可以拍了。", gate={"outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": "完整、真实、可拍。"}, review={"outcome": "PASSED", "root_cause": None}, post_state=passed_state)),
    ]


@pytest.mark.parametrize(("ctx", "payload"), legal_cases())
def test_all_legal_stage_model_outputs_close(ctx, payload) -> None:
    validated = validate_stage_model_output(payload, context=ctx)
    assert isinstance(validated, StageModelOutputV1)


@pytest.mark.parametrize(
    "bad",
    ["free text", "```json\n{}\n```", [], 1, None],
)
def test_model_output_rejects_text_markdown_arrays_and_scalars(bad) -> None:
    with pytest.raises(StageModelOutputTypeError):
        validate_stage_model_output(bad, context=context("EXPLORE"))


@pytest.mark.parametrize(
    "change",
    [
        {"extra": True},
        {"output_format_version": "1"},
        {"output_format_version": True},
        {"post_state": []},
        {"post_state": {**empty_state(), "format_version": "1"}},
        {"post_state": {**empty_state(), "format_version": True}},
        {"current_stage": "EXPLORE"},
        {"step_no": 1},
    ],
)
def test_model_output_is_strict_forbids_unknown_and_infrastructure_fields(change) -> None:
    payload = output()
    payload.update(change)
    with pytest.raises(StageModelOutputSchemaError):
        validate_stage_model_output(payload, context=context("EXPLORE"))


def test_new_stage_model_output_still_requires_gate_field() -> None:
    payload = output()
    del payload["gate"]
    with pytest.raises(StageModelOutputSchemaError):
        validate_stage_model_output(payload, context=context("EXPLORE"))


@pytest.mark.parametrize("wrong_part", ["session", "target"])
def test_direction_evidence_must_match_the_complete_authorized_reference(wrong_part: str) -> None:
    ctx = context("EXPLORE")
    state = empty_state()
    state["direction"] = direction(ctx)
    reference = state["direction"]["evidence_refs"][0]
    if wrong_part == "session":
        reference["target_session_id"] = uid()
    else:
        reference["target_id"] = uid()
    payload = output(
        run_control="CONTINUE",
        target_stage="DEEPEN",
        transition_reason_code="DIRECTION_CONFIRMED",
        director_message=None,
        gate=None,
        post_state=state,
    )
    with pytest.raises(StageContractViolationError, match="exactly match"):
        validate_stage_model_output(payload, context=ctx)


def test_mutated_stage_model_output_instance_is_revalidated_and_rejected() -> None:
    model = StageModelOutputV1.model_validate(output())
    object.__setattr__(model, "output_format_version", "1")
    with pytest.raises(StageModelOutputSchemaError):
        validate_stage_model_output(model, context=context("EXPLORE"))


@pytest.mark.parametrize(
    "change",
    [
        {"run_control": "CONTINUE"},
        {"target_stage": "DEEPEN"},
        {"transition_reason_code": "DIRECTION_CONFIRMED"},
        {"director_message": None},
        {"gate": None},
        {"review": {"outcome": "BLOCKED", "root_cause": "DIRECTION_PROBLEM"}},
    ],
)
def test_illegal_explore_control_target_reason_gate_review_and_message_fail(change) -> None:
    payload = output()
    payload.update(change)
    with pytest.raises(StageContractViolationError):
        validate_stage_model_output(payload, context=context("EXPLORE"))


@pytest.mark.parametrize("case_index", [2, 4, 5, 6, 7, 8, 9])
def test_each_non_explore_outcome_rejects_an_illegal_target(case_index: int) -> None:
    ctx, payload = legal_cases()[case_index]
    payload = deepcopy(payload)
    payload["target_stage"] = "READY" if payload["target_stage"] != "READY" else "CREATE"
    with pytest.raises(StageContractViolationError):
        validate_stage_model_output(payload, context=ctx)


def test_ai_judgment_alone_cannot_confirm_direction() -> None:
    ctx = context("EXPLORE")
    state = empty_state()
    state["ai_judgments"] = [{"item_id": temp("item", "judgment_1"), "judgment_kind": "DIRECTION_CANDIDATE", "statement": "候选方向"}]
    payload = output(run_control="CONTINUE", target_stage="DEEPEN", transition_reason_code="DIRECTION_CONFIRMED", director_message=None, gate=None, post_state=state)
    with pytest.raises(StageContractViolationError):
        validate_stage_model_output(payload, context=ctx)


def test_deepen_cannot_create_with_insufficient_material() -> None:
    ctx = context("DEEPEN")
    state = empty_state()
    state["direction"] = direction(ctx)
    state["material_state"] = {"status": "INSUFFICIENT", "required_confirmations": [confirmation()]}
    payload = output(run_control="CONTINUE", target_stage="CREATE", transition_reason_code="MATERIAL_SUFFICIENT", director_message=None, gate=None, post_state=state)
    with pytest.raises(StageContractViolationError):
        validate_stage_model_output(payload, context=ctx)


def test_create_requires_final_candidate_and_review_trace_matches_state() -> None:
    create = context("CREATE")
    state = empty_state()
    state["direction"] = direction(create)
    state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
    state["draft"] = draft()
    state["draft"]["content_status"] = "WORKING"
    payload = output(run_control="CONTINUE", target_stage="REVIEW", transition_reason_code="DRAFT_CREATED", director_message=None, gate=None, post_state=state)
    with pytest.raises(StageContractViolationError):
        validate_stage_model_output(payload, context=create)

    review = context("REVIEW")
    state = review_state(review, "WRITING_PROBLEM")
    payload = output(run_control="CONTINUE", target_stage="CREATE", transition_reason_code="WRITING_REPAIR", director_message=None, gate={"outcome": "BLOCKED", "gate_code": "CONTENT_INCOMPLETE", "explanation": "表达不完整。"}, review={"outcome": "BLOCKED", "root_cause": "DIRECTION_PROBLEM"}, post_state=state)
    with pytest.raises(StageContractViolationError):
        validate_stage_model_output(payload, context=review)


def test_model_output_failure_leaves_all_six_tables_unchanged(tmp_path) -> None:
    connection = connect(tmp_path / "stage-output.sqlite", busy_timeout_ms=100)
    apply_migrations(connection)
    repo = DirectorRepository(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = repo.create_session(scope)
    tables = (
        "director_sessions", "director_messages", "director_working_state",
        "director_turns", "director_context_checkpoints", "director_ready_content",
    )
    before = {table: tuple(map(tuple, connection.execute(f"SELECT * FROM {table}").fetchall())) for table in tables}
    executor = DirectorStageExecutor(
        ModelContextAssembler(repo, scope, ContextBudget(100_000)),
        lambda _context: {"freeform": "not the strict model output"},
    )
    request = DirectorTurnRequest(session.id, "bad-output", 0, "老板输入。", 1, {})
    with pytest.raises(StageModelOutputSchemaError):
        DirectorOrchestrator(repo, scope, executor, 2).run(request)
    after = {table: tuple(map(tuple, connection.execute(f"SELECT * FROM {table}").fetchall())) for table in tables}
    assert after == before


@pytest.mark.parametrize(
    ("failure_kind", "expected_error"),
    [
        ("wrong_session", StageContractViolationError),
        ("unauthorized_target", StageContractViolationError),
        ("mutated_instance", StageModelOutputSchemaError),
        ("missing_gate", StageModelOutputSchemaError),
    ],
)
def test_review_failures_leave_all_six_tables_unchanged(
    tmp_path, failure_kind, expected_error
) -> None:
    connection = connect(tmp_path / f"atomic-{failure_kind}.sqlite", busy_timeout_ms=100)
    apply_migrations(connection)
    repo = DirectorRepository(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = repo.create_session(scope)
    tables = (
        "director_sessions", "director_messages", "director_working_state",
        "director_turns", "director_context_checkpoints", "director_ready_content",
    )
    before = {
        table: tuple(map(tuple, connection.execute(f"SELECT * FROM {table}").fetchall()))
        for table in tables
    }

    def handler(model_context):
        if failure_kind == "mutated_instance":
            model = StageModelOutputV1.model_validate(output())
            object.__setattr__(model, "output_format_version", "1")
            return model
        if failure_kind == "missing_gate":
            payload = output()
            del payload["gate"]
            return payload
        state = model_context.to_dict()["working_state"]
        reference = deepcopy(model_context.to_dict()["owner_evidence_references"][0])
        reference[
            "target_session_id" if failure_kind == "wrong_session" else "target_id"
        ] = uid()
        state["direction"] = {
            "item_id": temp("item", "bad_direction"),
            "statement": "未经授权的方向。",
            "owner_confirmed": True,
            "evidence_refs": [reference],
            "inherited_from": None,
        }
        return output(
            run_control="CONTINUE",
            target_stage="DEEPEN",
            transition_reason_code="DIRECTION_CONFIRMED",
            director_message=None,
            gate=None,
            post_state=state,
        )

    executor = DirectorStageExecutor(
        ModelContextAssembler(repo, scope, ContextBudget(100_000)), handler
    )
    with pytest.raises(expected_error):
        DirectorOrchestrator(repo, scope, executor, 2).run(
            DirectorTurnRequest(session.id, failure_kind, 0, "老板输入。", 1, {})
        )
    after = {
        table: tuple(map(tuple, connection.execute(f"SELECT * FROM {table}").fetchall()))
        for table in tables
    }
    assert after == before


def test_real_model_context_unchanged_deepen_material_gap_is_atomic(tmp_path) -> None:
    connection = connect(tmp_path / "material-loop.sqlite", busy_timeout_ms=100)
    apply_migrations(connection)
    repo = DirectorRepository(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = repo.create_session(scope)
    tables = (
        "director_sessions", "director_messages", "director_working_state",
        "director_turns", "director_context_checkpoints", "director_ready_content",
    )
    before = {table: tuple(map(tuple, connection.execute(f"SELECT * FROM {table}").fetchall())) for table in tables}
    gap_confirmation = confirmation()

    def handler(model_context):
        state = model_context.to_dict()["working_state"]
        stage = model_context.stage_contract["stage"]
        if stage == "EXPLORE":
            state["direction"] = {
                "item_id": temp("item", "direction_1"), "statement": "老板确认方向", "owner_confirmed": True,
                "evidence_refs": [{"evidence_type": "owner_message", "target_id": model_context.current_owner_message.id, "target_session_id": session.id}],
                "inherited_from": None,
            }
            return output(run_control="CONTINUE", target_stage="DEEPEN", transition_reason_code="DIRECTION_CONFIRMED", director_message=None, gate=None, post_state=state)
        state["material_state"] = {
            "status": "INSUFFICIENT",
            "required_confirmations": [
                {
                    **deepcopy(gap_confirmation),
                    "item_id": (
                        state["material_state"]["required_confirmations"][0]["item_id"]
                        if state["material_state"]["required_confirmations"]
                        else gap_confirmation["item_id"]
                    ),
                }
            ],
        }
        return output(run_control="CONTINUE", target_stage="DEEPEN", transition_reason_code="MATERIAL_GAP", director_message=None, gate={"outcome": "BLOCKED", "gate_code": "MATERIAL_INSUFFICIENT", "explanation": "内部投影素材缺口。"}, post_state=state)

    executor = DirectorStageExecutor(
        ModelContextAssembler(repo, scope, ContextBudget(100_000)), handler
    )
    request = DirectorTurnRequest(session.id, "repeated-gap", 0, "老板输入。", 1, {})
    with pytest.raises(StageContractViolationError, match="Working State change"):
        DirectorOrchestrator(repo, scope, executor, 5).run(request)
    after = {table: tuple(map(tuple, connection.execute(f"SELECT * FROM {table}").fetchall())) for table in tables}
    assert after == before


def _owner_reference(ctx) -> dict[str, str]:
    return {
        "evidence_type": "owner_message",
        "target_id": ctx.current_owner_message.id,
        "target_session_id": ctx.session_id,
    }


def _confirmed_direction(ctx, item_id: str) -> dict:
    return {
        "item_id": item_id,
        "statement": "讲清一道菜的真实来历",
        "owner_confirmed": True,
        "evidence_refs": [_owner_reference(ctx)],
        "inherited_from": None,
    }


def _owner_fact(ctx, item_id: str, statement: str = "老板确认的一条真实事实。") -> dict:
    return {
        "item_id": item_id,
        "statement": statement,
        "evidence_refs": [_owner_reference(ctx)],
        "supersedes_item_ids": [],
        "inherited_from": None,
    }


def _rejected(source: dict, item_kind: str, *, item_id: str | None = None, **changes) -> dict:
    value = {
        "item_id": source["item_id"] if item_id is None else item_id,
        "item_kind": item_kind,
        "statement": source["statement"],
        "rejection_code": "NO_LONGER_USED",
        "evidence_refs": deepcopy(source.get("evidence_refs", [])),
        "rejected_by_evidence_refs": [],
        "superseded_by_item_id": None,
        "inherited_from": deepcopy(source.get("inherited_from")),
    }
    value.update(changes)
    return value


def _draft(item_id: str | None, text: str = "这是完整、真实、可以拍摄的内容。") -> dict:
    return {
        "draft_id": item_id,
        "content": {"title": None, "script_text": text, "shooting_notes": []},
        "content_status": "FINAL_CANDIDATE",
        "based_on_ready_content_id": None,
    }


def test_phase1e_new_item_reference_is_application_allocated_uuid4() -> None:
    ctx = context("EXPLORE")
    state = empty_state()
    state["direction"] = _confirmed_direction(ctx, "new:item:direction_1")
    resolved = resolve_stage_model_proposal(output(post_state=state), context=ctx)
    value = resolved.post_state.direction.item_id
    assert UUID(value).version == 4


def test_phase1e_same_item_reference_binds_definition_and_rejection_reference() -> None:
    old_id = uid()
    ctx = context("EXPLORE", {**empty_state(), "owner_facts": [_owner_fact(context("EXPLORE"), old_id)]})
    # Rebuild the fact with the actual Context evidence boundary.
    old_fact = _owner_fact(ctx, old_id)
    ctx.working_state["owner_facts"] = [old_fact]
    state = deepcopy(ctx.working_state)
    state["owner_facts"] = [_owner_fact(ctx, "new:item:replacement", "老板确认的新事实。")]
    state["rejected_items"] = [{
        "item_id": old_id,
        "item_kind": "OWNER_FACT",
        "statement": old_fact["statement"],
        "rejection_code": "OWNER_CORRECTED",
        "evidence_refs": deepcopy(old_fact["evidence_refs"]),
        "rejected_by_evidence_refs": [_owner_reference(ctx)],
        "superseded_by_item_id": "new:item:replacement",
        "inherited_from": None,
    }]
    resolved = resolve_stage_model_proposal(output(post_state=state), context=ctx)
    assert resolved.post_state.owner_facts[0].item_id == resolved.post_state.rejected_items[0].superseded_by_item_id


def test_phase1e_new_draft_and_review_cross_bind_to_resolved_draft_uuid() -> None:
    ctx = context("REVIEW")
    state = empty_state()
    state["direction"] = _confirmed_direction(ctx, "new:item:direction_1")
    state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
    state["draft"] = _draft("new:draft:draft_1")
    state["review"] = {
        "review_id": "new:review:review_1",
        "outcome": "PASSED",
        "root_cause": None,
        "against_draft_id": "new:draft:draft_1",
        "against_content": deepcopy(state["draft"]["content"]),
    }
    resolved = resolve_stage_model_proposal(output(post_state=state), context=ctx)
    assert resolved.post_state.review.against_draft_id == resolved.post_state.draft.draft_id
    assert UUID(resolved.post_state.draft.draft_id).version == 4
    assert UUID(resolved.post_state.review.review_id).version == 4


def test_phase1e_unknown_real_uuid_is_rejected_as_model_forgery() -> None:
    ctx = context("EXPLORE")
    state = empty_state()
    state["direction"] = _confirmed_direction(ctx, uid())
    with pytest.raises(ForgedUUIDError):
        validate_stage_model_output(
            output(
                run_control="CONTINUE", target_stage="DEEPEN",
                transition_reason_code="DIRECTION_CONFIRMED", director_message=None,
                gate=None, post_state=state,
            ),
            context=ctx,
        )


def test_phase1e_undefined_and_mixed_namespace_references_are_rejected() -> None:
    ctx = context("EXPLORE")
    undefined = empty_state()
    undefined["owner_facts"] = [{**_owner_fact(ctx, "new:item:fact_1"), "supersedes_item_ids": ["new:item:missing"]}]
    with pytest.raises(UndefinedTemporaryReferenceError):
        resolve_stage_model_proposal(output(post_state=undefined), context=ctx)

    mixed = empty_state()
    mixed["direction"] = _confirmed_direction(ctx, "new:draft:wrong_namespace")
    with pytest.raises(TemporaryReferenceNamespaceError):
        resolve_stage_model_proposal(output(post_state=mixed), context=ctx)


def test_phase1e_duplicate_definition_and_forbidden_evidence_reference_are_rejected() -> None:
    ctx = context("EXPLORE")
    duplicate = empty_state()
    duplicate["ai_judgments"] = [
        {"item_id": "new:item:same", "judgment_kind": "STRUCTURE", "statement": "结构一"},
        {"item_id": "new:item:same", "judgment_kind": "EXPRESSION", "statement": "表达二"},
    ]
    with pytest.raises(DuplicateTemporaryDefinitionError):
        resolve_stage_model_proposal(output(post_state=duplicate), context=ctx)

    forbidden = empty_state()
    forbidden["direction"] = _confirmed_direction(ctx, "new:item:direction_1")
    forbidden["direction"]["evidence_refs"][0]["target_id"] = "new:item:message_1"
    with pytest.raises(TemporaryReferenceForbiddenError):
        resolve_stage_model_proposal(output(post_state=forbidden), context=ctx)


def test_phase1e_existing_object_mutation_and_unchanged_new_id_are_rejected() -> None:
    item_id = uid()
    ctx = context("EXPLORE")
    ctx.working_state["owner_facts"] = [_owner_fact(ctx, item_id)]
    mutated = deepcopy(ctx.working_state)
    mutated["owner_facts"][0]["statement"] = "偷偷改变的语义。"
    with pytest.raises(ExistingObjectMutationError):
        resolve_stage_model_proposal(output(post_state=mutated), context=ctx)

    new_id = deepcopy(ctx.working_state)
    new_id["owner_facts"][0]["item_id"] = "new:item:fact_2"
    with pytest.raises(ContentIdentityError):
        resolve_stage_model_proposal(output(post_state=new_id), context=ctx)


def test_phase1e_draft_content_identity_and_review_reuse_are_rejected() -> None:
    draft_id = uid()
    ctx = context("REVIEW")
    state = empty_state()
    state["direction"] = _confirmed_direction(ctx, uid())
    state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
    state["draft"] = _draft(draft_id)
    review_id = uid()
    state["review"] = {
        "review_id": review_id, "outcome": "PASSED", "root_cause": None,
        "against_draft_id": draft_id, "against_content": deepcopy(state["draft"]["content"]),
    }
    ctx.working_state = deepcopy(state)

    changed_draft = deepcopy(state)
    changed_draft["draft"]["content"]["script_text"] = "改写后的内容。"
    with pytest.raises(DraftIdentityError):
        resolve_stage_model_proposal(output(post_state=changed_draft), context=ctx)

    unchanged_new_draft = deepcopy(state)
    unchanged_new_draft["draft"]["draft_id"] = "new:draft:draft_2"
    with pytest.raises(DraftIdentityError):
        resolve_stage_model_proposal(output(post_state=unchanged_new_draft), context=ctx)

    reused_review = deepcopy(state)
    with pytest.raises(ReviewIdentityError):
        resolve_stage_model_proposal(output(post_state=reused_review), context=ctx)


def test_phase1e_ai_judgment_upgrade_requires_a_new_item_id() -> None:
    judgment_id = uid()
    ctx = context("EXPLORE")
    ctx.working_state["ai_judgments"] = [{
        "item_id": judgment_id, "judgment_kind": "DIRECTION_CANDIDATE", "statement": "候选方向",
    }]
    state = deepcopy(ctx.working_state)
    state["direction"] = _confirmed_direction(ctx, judgment_id)
    with pytest.raises(ExistingObjectMutationError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def test_rejected_item_cannot_define_a_new_item_identity() -> None:
    ctx = context("EXPLORE")
    state = empty_state()
    state["rejected_items"] = [{
        "item_id": temp("item", "rejected_1"),
        "item_kind": "AI_JUDGMENT",
        "statement": "凭空制造的旧判断。",
        "rejection_code": "NO_LONGER_USED",
        "evidence_refs": [],
        "rejected_by_evidence_refs": [],
        "superseded_by_item_id": None,
        "inherited_from": None,
    }]
    with pytest.raises(RejectedItemIdentityError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def test_rejected_item_must_reference_a_known_pre_state_item() -> None:
    ctx = context("EXPLORE")
    source = _owner_fact(ctx, uid())
    state = empty_state()
    state["rejected_items"] = [_rejected(source, "OWNER_FACT")]
    with pytest.raises(ForgedUUIDError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


@pytest.mark.parametrize("change", [
    {"statement": "被篡改的旧事实。"},
    {"evidence_refs": []},
])
def test_rejected_item_closure_preserves_source_statement_and_evidence(change) -> None:
    ctx = context("EXPLORE")
    source = _owner_fact(ctx, uid())
    ctx.working_state["owner_facts"] = [source]
    state = deepcopy(ctx.working_state)
    state["owner_facts"] = []
    rejected = _rejected(source, "OWNER_FACT")
    rejected.update(change)
    state["rejected_items"] = [rejected]
    with pytest.raises(RejectedItemIdentityError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def test_rejected_item_cannot_leave_the_source_object_effective() -> None:
    ctx = context("EXPLORE")
    source = _owner_fact(ctx, uid())
    ctx.working_state["owner_facts"] = [source]
    state = deepcopy(ctx.working_state)
    state["rejected_items"] = [_rejected(source, "OWNER_FACT")]
    with pytest.raises(DuplicateItemIdentityError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def test_rejected_item_kind_must_match_the_pre_state_source_kind() -> None:
    ctx = context("EXPLORE")
    source = _owner_fact(ctx, uid())
    ctx.working_state["owner_facts"] = [source]
    state = deepcopy(ctx.working_state)
    state["owner_facts"] = []
    state["rejected_items"] = [_rejected(source, "OWNER_CONSTRAINT")]
    with pytest.raises(RejectedItemIdentityError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def test_working_state_rejects_item_ids_repeated_across_collections() -> None:
    ctx = context("EXPLORE")
    source = _owner_fact(ctx, uid())
    ctx.working_state["owner_facts"] = [source]
    state = deepcopy(ctx.working_state)
    state["rejected_items"] = [_rejected(source, "OWNER_FACT")]
    with pytest.raises(DuplicateItemIdentityError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def _revision_null_draft_context(text: str = "来源内容"):
    state = empty_state()
    state["draft"] = _draft(None, text)
    state["draft"]["based_on_ready_content_id"] = uid()
    return context("EXPLORE", state)


def test_revision_null_draft_rewrite_requires_a_new_draft_identity() -> None:
    ctx = _revision_null_draft_context()
    state = deepcopy(ctx.working_state)
    state["draft"]["content"]["script_text"] = "第一次改写后的内容。"
    with pytest.raises(DraftIdentityError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def test_revision_null_draft_rewrite_allocates_a_uuid4_draft_identity() -> None:
    ctx = _revision_null_draft_context()
    state = deepcopy(ctx.working_state)
    state["draft"]["content"]["script_text"] = "第一次改写后的内容。"
    state["draft"]["draft_id"] = temp("draft", "first_rewrite")
    resolved = resolve_stage_model_proposal(output(post_state=state), context=ctx)
    assert UUID(resolved.post_state.draft.draft_id).version == 4


def test_revision_null_draft_can_be_inherited_unchanged() -> None:
    ctx = _revision_null_draft_context()
    resolved = resolve_stage_model_proposal(output(post_state=deepcopy(ctx.working_state)), context=ctx)
    assert resolved.post_state.draft.draft_id is None


def test_item_reference_cannot_use_a_draft_uuid() -> None:
    draft_id = uid()
    ctx = context("EXPLORE")
    ctx.working_state["draft"] = _draft(draft_id)
    state = empty_state()
    state["owner_facts"] = [{**_owner_fact(ctx, temp("item", "fact_1")), "supersedes_item_ids": [draft_id]}]
    with pytest.raises(ForgedUUIDError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def test_item_reference_cannot_use_a_review_uuid() -> None:
    draft_id = uid()
    review_id = uid()
    ctx = context("EXPLORE")
    ctx.working_state["owner_facts"] = [_owner_fact(ctx, uid())]
    ctx.working_state["draft"] = _draft(draft_id)
    ctx.working_state["review"] = {
        "review_id": review_id, "outcome": "PASSED", "root_cause": None,
        "against_draft_id": draft_id, "against_content": deepcopy(ctx.working_state["draft"]["content"]),
    }
    source = ctx.working_state["owner_facts"][0]
    state = deepcopy(ctx.working_state)
    state["owner_facts"] = []
    state["rejected_items"] = [_rejected(source, "OWNER_FACT", superseded_by_item_id=review_id)]
    with pytest.raises(ForgedUUIDError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def test_draft_reference_cannot_use_an_item_uuid() -> None:
    draft_id = uid()
    item_id = uid()
    ctx = context("REVIEW")
    ctx.working_state["direction"] = _confirmed_direction(ctx, item_id)
    ctx.working_state["draft"] = _draft(draft_id)
    state = deepcopy(ctx.working_state)
    state["review"] = {
        "review_id": temp("review", "review_1"), "outcome": "PASSED", "root_cause": None,
        "against_draft_id": item_id, "against_content": deepcopy(state["draft"]["content"]),
    }
    with pytest.raises(ForgedUUIDError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


@pytest.mark.parametrize("stage", ["EXPLORE", "DEEPEN", "CREATE"])
def test_only_review_stage_can_create_a_new_review(stage: str) -> None:
    ctx = context(stage)
    state = empty_state()
    state["review"] = {
        "review_id": temp("review", "forbidden"), "outcome": "PASSED", "root_cause": None,
        "against_draft_id": temp("draft", "draft_1"),
        "against_content": _draft(None)["content"],
    }
    state["draft"] = _draft("new:draft:draft_1")
    with pytest.raises(ReviewIdentityError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def test_non_review_stage_cannot_modify_an_existing_review() -> None:
    draft_id = uid()
    ctx = context("EXPLORE")
    ctx.working_state["draft"] = _draft(draft_id)
    ctx.working_state["review"] = {
        "review_id": uid(), "outcome": "PASSED", "root_cause": None,
        "against_draft_id": draft_id, "against_content": deepcopy(ctx.working_state["draft"]["content"]),
    }
    state = deepcopy(ctx.working_state)
    state["review"]["outcome"] = "BLOCKED"
    state["review"]["root_cause"] = "WRITING_PROBLEM"
    with pytest.raises(ReviewIdentityError):
        resolve_stage_model_proposal(output(post_state=state), context=ctx)


def test_review_stage_must_generate_a_new_review_and_bind_current_draft() -> None:
    draft_id = uid()
    ctx = context("REVIEW")
    ctx.working_state["draft"] = _draft(draft_id)
    ctx.working_state["review"] = {
        "review_id": uid(), "outcome": "PASSED", "root_cause": None,
        "against_draft_id": draft_id, "against_content": deepcopy(ctx.working_state["draft"]["content"]),
    }
    state = deepcopy(ctx.working_state)
    state["review"]["review_id"] = temp("review", "replacement")
    resolved = resolve_stage_model_proposal(output(post_state=state), context=ctx)
    assert UUID(resolved.post_state.review.review_id).version == 4
    assert resolved.post_state.review.against_draft_id == draft_id


def test_review_stage_rejects_reusing_the_previous_review_id() -> None:
    draft_id = uid()
    review_id = uid()
    ctx = context("REVIEW")
    ctx.working_state["draft"] = _draft(draft_id)
    ctx.working_state["review"] = {
        "review_id": review_id, "outcome": "PASSED", "root_cause": None,
        "against_draft_id": draft_id, "against_content": deepcopy(ctx.working_state["draft"]["content"]),
    }
    with pytest.raises(ReviewIdentityError):
        resolve_stage_model_proposal(output(post_state=deepcopy(ctx.working_state)), context=ctx)
