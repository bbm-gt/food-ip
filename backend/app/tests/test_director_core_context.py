from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from uuid import uuid4

import pytest

from backend.app.director_core.canonical import checkpoint_sha256, canonical_text
from backend.app.director_core.context import (
    CheckpointRebuildRequiredError,
    ContextBudget,
    ContextBudgetExceededError,
    ContextMessage,
    ContextTurn,
    EvidenceReferenceError,
    ModelContextAssembler,
)
from backend.app.director_core.execution import DirectorExecutionValidationError
from backend.app.director_core.orchestrator import (
    DisabledSourceReadyContentPolicy,
    DirectorOrchestrator,
    DirectorStageExecutor,
    DirectorTurnRequest,
    StageExecutionContext,
    StageExecutionResult,
)
from backend.app.director_core.repository import AuthorizationScope, DirectorRepository
from backend.app.director_core.stage_handler import (
    StageModelOutputError,
    validate_stage_model_output,
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


def make_request(session_id: str, client_id: str, expected: int = 0) -> DirectorTurnRequest:
    return DirectorTurnRequest(
        session_id=session_id,
        client_message_id=client_id,
        expected_state_version=expected,
        owner_text="老板说了一条可追溯的真实内容。",
        request_format_version=1,
        parameters={},
    )


def wait_result(context: StageExecutionContext) -> StageExecutionResult:
    state = deepcopy(context.working_state)
    if context.stage == "DEEPEN":
        state["material_state"] = {
            "status": "INSUFFICIENT",
            "required_confirmations": [{
                "item_id": uid(), "statement": "补充关键真实素材", "reason": "素材不足",
                "evidence_refs": [], "inherited_from": None,
            }],
        }
        gate = {"outcome": "BLOCKED", "gate_code": "MATERIAL_INSUFFICIENT", "explanation": "仍缺关键素材。"}
    else:
        gate = {"outcome": "BLOCKED", "gate_code": "DIRECTION_NOT_CONFIRMED", "explanation": "方向尚未确认。"}
    return StageExecutionResult(
        director_message="请继续补充真实内容。",
        post_state=state,
        run_control="WAIT_FOR_OWNER",
        target_stage=context.stage,
        transition_reason_code="OWNER_INPUT_REQUIRED",
        gate=gate,
        review=None,
    )


def model_handler(handler):
    """Adapt an internal test DTO into the explicit StageModelOutputV1 shape."""

    def wrapped(context):
        result = handler(context)
        if not isinstance(result, StageExecutionResult):
            return result
        return {
            "output_format_version": 1,
            "run_control": result.run_control,
            "target_stage": result.target_stage,
            "transition_reason_code": result.transition_reason_code,
            "director_message": result.director_message,
            "gate": deepcopy(result.gate),
            "review": deepcopy(result.review),
            "post_state": deepcopy(result.post_state),
        }

    return wrapped


@pytest.fixture
def repository(tmp_path):
    from backend.app.director_core.database import apply_migrations, connect

    connection = connect(tmp_path / "context.sqlite", busy_timeout_ms=100)
    apply_migrations(connection)
    repository = DirectorRepository(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = repository.create_session(scope)
    return repository, scope, session.id


def assembler(repository, scope, *, max_units: int = 100_000):
    return ModelContextAssembler(
        repository,
        scope,
        ContextBudget(max_units=max_units),
    )


def test_context_is_structured_immutable_and_keeps_current_owner(repository) -> None:
    repo, scope, session_id = repository
    model_context = assembler(repo, scope).assemble(
        session_id=session_id,
        stage="EXPLORE",
        working_state=empty_state(),
        owner_message_id=uid(),
        owner_text="  原始老板文本\n仍然保留  ",
    )

    assert model_context.current_owner_message.content == "  原始老板文本\n仍然保留  "
    with pytest.raises(TypeError):
        model_context.working_state["direction"] = {}  # type: ignore[index]
    detached = model_context.to_dict()
    detached["working_state"]["direction"] = "changed"
    assert model_context.working_state["direction"] is None


def test_each_internal_step_reassembles_latest_candidate_and_handler_cannot_commit(repository) -> None:
    repo, scope, session_id = repository
    seen: list[tuple[str, dict]] = []

    def handler(context):
        state = context.to_dict()["working_state"]
        seen.append((context.stage_contract["stage"], state))
        if context.stage_contract["stage"] == "EXPLORE":
            state["direction"] = {
                "item_id": uid(),
                "statement": "从老板真实经历展开。",
                "owner_confirmed": True,
                "evidence_refs": [{
                    "evidence_type": "owner_message",
                    "target_id": context.current_owner_message.id,
                    "target_session_id": session_id,
                }],
                "inherited_from": None,
            }
            return StageExecutionResult(
                director_message=None,
                post_state=state,
                run_control="CONTINUE",
                target_stage="DEEPEN",
                transition_reason_code="DIRECTION_CONFIRMED",
                gate=None,
                review=None,
            )
        state["material_state"] = {
            "status": "INSUFFICIENT",
            "required_confirmations": [{
                "item_id": uid(), "statement": "补充关键细节", "reason": "素材不足",
                "evidence_refs": [], "inherited_from": None,
            }],
        }
        return StageExecutionResult(
            director_message="请补充最关键的真实细节。",
            post_state=state,
            run_control="WAIT_FOR_OWNER",
            target_stage="DEEPEN",
            transition_reason_code="OWNER_INPUT_REQUIRED",
            gate={"outcome": "BLOCKED", "gate_code": "MATERIAL_INSUFFICIENT", "explanation": "仍缺关键素材。"},
            review=None,
        )

    wrapped = DirectorStageExecutor(assembler(repo, scope), model_handler(handler))
    outcome = DirectorOrchestrator(repo, scope, wrapped, max_internal_steps=3).run(
        make_request(session_id, "reassemble")
    )

    assert [stage for stage, _ in seen] == ["EXPLORE", "DEEPEN"]
    assert seen[1][1]["direction"]["owner_confirmed"] is True
    assembled = assembler(repo, scope).assemble(
        session_id=session_id,
        stage="DEEPEN",
        working_state=seen[1][1],
        owner_message_id=uid(),
        owner_text="当前老板消息。",
    )
    assert not hasattr(assembled, "candidate_" + "steps")
    assert outcome.response["run_control"] == "WAIT_FOR_OWNER"
    assert repo.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 1


def test_historical_owner_evidence_is_loaded_even_when_not_current(repository) -> None:
    repo, scope, session_id = repository
    DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    ).run(make_request(session_id, "first"))
    old_owner_id = repo.connection.execute(
        "SELECT id FROM director_messages WHERE visible_role = 'OWNER'"
    ).fetchone()[0]
    seen: list[tuple[str, str]] = []
    seen_references: list[dict[str, str]] = []

    def handler(context):
        state = context.to_dict()["working_state"]
        if context.stage_contract["stage"] == "EXPLORE":
            state["owner_facts"] = [{
                "item_id": uid(),
                "statement": "老板说过一条真实内容。",
                "evidence_refs": [{
                    "evidence_type": "owner_message",
                    "target_id": old_owner_id,
                    "target_session_id": session_id,
                }],
                "supersedes_item_ids": [],
                "inherited_from": None,
            }]
            state["direction"] = {
                "item_id": uid(), "statement": "沿老板确认的真实内容展开", "owner_confirmed": True,
                "evidence_refs": [{"evidence_type": "owner_message", "target_id": old_owner_id, "target_session_id": session_id}],
                "inherited_from": None,
            }
            return StageExecutionResult(
                director_message=None,
                post_state=state,
                run_control="CONTINUE",
                target_stage="DEEPEN",
                transition_reason_code="DIRECTION_CONFIRMED",
                gate=None,
                review=None,
            )
        seen.extend((message.id, message.content) for message in context.evidence_messages)
        seen_references.extend(context.to_dict()["owner_evidence_references"])
        state["material_state"] = {
            "status": "INSUFFICIENT",
            "required_confirmations": [{
                "item_id": uid(), "statement": "补充关键细节", "reason": "素材不足",
                "evidence_refs": [], "inherited_from": None,
            }],
        }
        return StageExecutionResult(
            director_message="已记录这条真实内容。",
            post_state=state,
            run_control="WAIT_FOR_OWNER",
            target_stage="DEEPEN",
            transition_reason_code="OWNER_INPUT_REQUIRED",
            gate={"outcome": "BLOCKED", "gate_code": "MATERIAL_INSUFFICIENT", "explanation": "仍缺关键素材。"},
            review=None,
        )

    DirectorOrchestrator(
        repo, scope, DirectorStageExecutor(assembler(repo, scope), model_handler(handler)), max_internal_steps=2
    ).run(make_request(session_id, "evidence", expected=1))
    assert seen == [(old_owner_id, "老板说了一条可追溯的真实内容。")]
    assert {
        "evidence_type": "owner_message",
        "target_id": old_owner_id,
        "target_session_id": session_id,
    } in seen_references


def test_checkpoint_loads_only_boundary_after_complete_turns(repository) -> None:
    repo, scope, session_id = repository
    orchestrator = DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    )
    orchestrator.run(make_request(session_id, "one"))
    orchestrator.run(make_request(session_id, "two", expected=1))
    orchestrator.run(make_request(session_id, "three", expected=2))
    rows = repo.get_complete_message_turns(scope, session_id)
    payload = {
        "conversation_summary": "第一轮老板已经说过一条内容。",
        "confirmed_owner_positions": [{
            "statement": "第一轮老板表达过真实内容。",
            "message_refs": [rows[0]["owner"]["id"]],
        }],
        "open_threads": [],
        "abandoned_directions": [],
    }
    repo.connection.execute(
        """INSERT INTO director_context_checkpoints
           (id, session_id, covered_through_seq, format_version, checkpoint_json,
            integrity_sha256, status, discarded_at, discard_reason_code, created_at)
           VALUES (?, ?, ?, 1, ?, ?, 'VALID', NULL, NULL, ?)""",
        (
            uid(), session_id, 2, canonical_text(payload),
            checkpoint_sha256(session_id, 2, payload, format_version=1),
            "2026-01-01T00:00:00.000Z",
        ),
    )
    repo.connection.commit()

    model_context = assembler(repo, scope).assemble(
        session_id=session_id,
        stage="EXPLORE",
        working_state=repo.get_working_state(scope, session_id).state_json,
        owner_message_id=uid(),
        owner_text="当前老板消息。",
    )
    assert model_context.checkpoint["covered_through_seq"] == 2
    assert len(model_context.history_turns) == 2
    assert model_context.history_turns[0].owner.content == "老板说了一条可追溯的真实内容。"
    assert model_context.history_turns[0].owner.message_seq == 3
    assert model_context.history_turns[1].owner.content == "老板说了一条可追溯的真实内容。"
    assert model_context.history_turns[1].owner.message_seq == 5


def test_old_checkpoint_history_over_budget_requires_rebuild_before_handler(repository) -> None:
    repo, scope, session_id = repository
    orchestrator = DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    )
    orchestrator.run(make_request(session_id, "one"))
    orchestrator.run(make_request(session_id, "two", expected=1))
    orchestrator.run(make_request(session_id, "three", expected=2))
    rows = repo.get_complete_message_turns(scope, session_id)
    payload = {
        "conversation_summary": "前两轮已经压缩。",
        "confirmed_owner_positions": [],
        "open_threads": [],
        "abandoned_directions": [],
    }
    repo.connection.execute(
        """INSERT INTO director_context_checkpoints
           (id, session_id, covered_through_seq, format_version, checkpoint_json,
            integrity_sha256, status, discarded_at, discard_reason_code, created_at)
           VALUES (?, ?, ?, 1, ?, ?, 'VALID', NULL, NULL, ?)""",
        (
            uid(), session_id, 2, canonical_text(payload),
            checkpoint_sha256(session_id, 2, payload, format_version=1),
            "2026-01-01T00:00:00.000Z",
        ),
    )
    repo.connection.commit()

    limited = ModelContextAssembler(repo, scope, ContextBudget(17, UnitCounter()))
    called = 0

    def handler(context):
        nonlocal called
        called += 1
        return wait_result(context)

    tables = (
        "director_messages", "director_turns", "director_working_state",
        "director_context_checkpoints", "director_ready_content", "director_sessions",
    )
    before = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables]
    with pytest.raises(CheckpointRebuildRequiredError, match="Checkpoint and"):
        DirectorOrchestrator(
            repo, scope, DirectorStageExecutor(limited, model_handler(handler)), max_internal_steps=1
        ).run(make_request(session_id, "too-large", expected=3))
    after = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables]
    assert called == 0
    assert after == before


@dataclass
class UnitCounter:
    def estimate(self, value):
        return 10 if isinstance(value, ContextTurn) else 1


def test_missing_checkpoint_history_over_budget_has_explicit_rebuild_error(repository) -> None:
    repo, scope, session_id = repository
    DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    ).run(make_request(session_id, "history"))
    limited = ModelContextAssembler(repo, scope, ContextBudget(15, UnitCounter()))
    with pytest.raises(CheckpointRebuildRequiredError, match="Checkpoint"):
        limited.assemble(
            session_id=session_id,
            stage="EXPLORE",
            working_state=repo.get_working_state(scope, session_id).state_json,
            owner_message_id=uid(),
            owner_text="当前老板消息。",
        )


def test_missing_checkpoint_history_that_fits_is_loaded_completely(repository) -> None:
    repo, scope, session_id = repository
    orchestrator = DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    )
    orchestrator.run(make_request(session_id, "one"))
    orchestrator.run(make_request(session_id, "two", expected=1))
    model_context = ModelContextAssembler(
        repo, scope, ContextBudget(26, UnitCounter())
    ).assemble(
        session_id=session_id,
        stage="EXPLORE",
        working_state=repo.get_working_state(scope, session_id).state_json,
        owner_message_id=uid(),
        owner_text="当前老板消息。",
    )
    assert model_context.checkpoint is None
    assert [turn.owner.message_seq for turn in model_context.history_turns] == [1, 3]


def test_protected_context_over_budget_fails_before_orchestrator_commit(repository) -> None:
    repo, scope, session_id = repository
    limited = ModelContextAssembler(repo, scope, ContextBudget(5, UnitCounter()))

    def handler(context):  # pragma: no cover - assembly must fail first
        raise AssertionError("handler must not run")

    tables = (
        "director_sessions", "director_messages", "director_working_state",
        "director_turns", "director_context_checkpoints", "director_ready_content",
    )
    before = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
              for table in tables]
    with pytest.raises(ContextBudgetExceededError, match="irreducible"):
        DirectorOrchestrator(
            repo, scope, DirectorStageExecutor(limited, model_handler(handler)), max_internal_steps=1
        ).run(make_request(session_id, "budget"))
    after = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
             for table in tables]
    assert after == before


def test_oversized_checkpoint_requires_rebuild_not_budget_failure(repository) -> None:
    repo, scope, session_id = repository
    DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    ).run(make_request(session_id, "checkpoint-source"))
    rows = repo.get_complete_message_turns(scope, session_id)
    payload = {
        "conversation_summary": "一个有效但过大的 Checkpoint。",
        "confirmed_owner_positions": [{
            "statement": "一条真实内容。",
            "message_refs": [rows[0]["owner"]["id"]],
        }],
        "open_threads": [],
        "abandoned_directions": [],
    }
    repo.connection.execute(
        """INSERT INTO director_context_checkpoints
           (id, session_id, covered_through_seq, format_version, checkpoint_json,
            integrity_sha256, status, discarded_at, discard_reason_code, created_at)
           VALUES (?, ?, ?, 1, ?, ?, 'VALID', NULL, NULL, ?)""",
        (
            uid(), session_id, 2, canonical_text(payload),
            checkpoint_sha256(session_id, 2, payload, format_version=1),
            "2026-01-01T00:00:00.000Z",
        ),
    )
    repo.connection.commit()

    @dataclass
    class OversizedCheckpointCounter:
        def estimate(self, value):
            if isinstance(value, dict) and "covered_through_seq" in value:
                return 100
            return 1

    limited = ModelContextAssembler(
        repo, scope, ContextBudget(20, OversizedCheckpointCounter())
    )
    called = 0

    def handler(context):
        nonlocal called
        called += 1
        return wait_result(context)

    tables = (
        "director_sessions", "director_messages", "director_working_state",
        "director_turns", "director_context_checkpoints", "director_ready_content",
    )
    before = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
              for table in tables]
    with pytest.raises(CheckpointRebuildRequiredError, match="Checkpoint"):
        DirectorOrchestrator(
            repo, scope, DirectorStageExecutor(limited, model_handler(handler)), max_internal_steps=1
        ).run(make_request(session_id, "oversized-checkpoint", expected=1))
    after = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
             for table in tables]
    assert called == 0
    assert after == before


def test_create_stage_cannot_wait_for_owner_and_failure_is_atomic(repository) -> None:
    repo, scope, session_id = repository

    def handler(context):
        state = context.to_dict()["working_state"]
        if context.stage_contract["stage"] == "EXPLORE":
            state["direction"] = {
                "item_id": uid(), "statement": "老板确认的方向", "owner_confirmed": True,
                "evidence_refs": [{"evidence_type": "owner_message", "target_id": context.current_owner_message.id, "target_session_id": session_id}],
                "inherited_from": None,
            }
            return StageExecutionResult(None, state, "CONTINUE", "DEEPEN", "DIRECTION_CONFIRMED", None, None)
        if context.stage_contract["stage"] == "DEEPEN":
            state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
            return StageExecutionResult(None, state, "CONTINUE", "CREATE", "MATERIAL_SUFFICIENT", None, None)
        return StageExecutionResult(
            "不能在 CREATE 阶段直接向老板补问。", state, "WAIT_FOR_OWNER", "CREATE",
            "OWNER_INPUT_REQUIRED", None, None,
        )

    tables = (
        "director_sessions", "director_messages", "director_working_state",
        "director_turns", "director_context_checkpoints", "director_ready_content",
    )
    before = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
              for table in tables]
    with pytest.raises(StageModelOutputError):
        DirectorOrchestrator(
            repo, scope, DirectorStageExecutor(assembler(repo, scope), model_handler(handler)), max_internal_steps=3
        ).run(make_request(session_id, "invalid-create-wait"))
    after = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
             for table in tables]
    assert after == before


def test_invalid_cross_session_and_director_evidence_fail_explicitly(repository) -> None:
    repo, scope, session_id = repository
    other = repo.create_session(scope)
    state = empty_state()
    state["owner_facts"] = [{
        "item_id": uid(),
        "statement": "不应通过。",
        "evidence_refs": [{
            "evidence_type": "owner_message",
            "target_id": uid(),
            "target_session_id": other.id,
        }],
        "supersedes_item_ids": [],
        "inherited_from": None,
    }]
    with pytest.raises(EvidenceReferenceError, match="Evidence"):
        assembler(repo, scope).assemble(
            session_id=session_id,
            stage="EXPLORE",
            working_state=state,
            owner_message_id=uid(),
            owner_text="当前老板消息。",
        )


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("EXPLORE", [("WAIT_FOR_OWNER", "EXPLORE"), ("CONTINUE", "DEEPEN")]),
        (
                "DEEPEN",
                [
                    ("CONTINUE", "DEEPEN"),
                    ("WAIT_FOR_OWNER", "DEEPEN"),
                    ("CONTINUE", "CREATE"),
            ],
        ),
        ("CREATE", [("CONTINUE", "REVIEW")]),
        (
            "REVIEW",
            [
                ("CONTINUE", "CREATE"),
                ("CONTINUE", "DEEPEN"),
                ("CONTINUE", "EXPLORE"),
                ("READY", "READY"),
            ],
        ),
    ],
)
def test_stage_contract_exposes_only_shared_legal_combinations(repository, stage, expected) -> None:
    repo, scope, session_id = repository
    context = assembler(repo, scope).assemble(
        session_id=session_id,
        stage=stage,
        working_state=empty_state(),
        owner_message_id=uid(),
        owner_text="当前老板消息。",
    )
    combinations = [
        (entry["run_control"], entry["target_stage"])
        for entry in context.stage_contract["allowed_combinations"]
    ]
    assert combinations == expected
    assert all(
        not (entry["run_control"] == "READY" and stage != "REVIEW")
        for entry in context.stage_contract["allowed_combinations"]
    )
    assert context.stage_contract["outcomes"]
    for outcome in context.stage_contract["outcomes"]:
        assert set(outcome["state_requirements"]) == {
            "confirmed_direction",
            "material_status",
            "required_confirmations",
            "draft",
            "review",
            "active_direction",
            "state_change",
        }


def insert_checkpoint(repo: DirectorRepository, session_id: str, covered_through_seq: int) -> None:
    payload = {
        "conversation_summary": "",
        "confirmed_owner_positions": [],
        "open_threads": [],
        "abandoned_directions": [],
    }
    repo.connection.execute(
        """INSERT INTO director_context_checkpoints
           (id, session_id, covered_through_seq, format_version, checkpoint_json,
            integrity_sha256, status, discarded_at, discard_reason_code, created_at)
           VALUES (?, ?, ?, 1, ?, ?, 'VALID', NULL, NULL, ?)""",
        (
            uid(), session_id, covered_through_seq, canonical_text(payload),
            checkpoint_sha256(session_id, covered_through_seq, payload, format_version=1),
            "2026-01-01T00:00:00.000Z",
        ),
    )
    repo.connection.commit()


def test_model_context_exposes_copyable_current_owner_evidence_reference(repository) -> None:
    repo, scope, session_id = repository
    owner_message_id = uid()
    context = assembler(repo, scope).assemble(
        session_id=session_id,
        stage="EXPLORE",
        working_state=empty_state(),
        owner_message_id=owner_message_id,
        owner_text="当前老板消息。",
    )
    assert context.to_dict()["owner_evidence_references"] == [{
        "evidence_type": "owner_message",
        "target_id": owner_message_id,
        "target_session_id": session_id,
    }]


def test_checkpoint_covered_unrelated_owner_is_not_authorized_but_loaded_history_is(repository) -> None:
    repo, scope, session_id = repository
    orchestrator = DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    )
    orchestrator.run(make_request(session_id, "covered"))
    orchestrator.run(make_request(session_id, "loaded", expected=1))
    rows = repo.get_complete_message_turns(scope, session_id)
    covered_owner_id = rows[0]["owner"]["id"]
    loaded_owner_id = rows[1]["owner"]["id"]
    insert_checkpoint(repo, session_id, covered_through_seq=2)

    context = assembler(repo, scope).assemble(
        session_id=session_id,
        stage="EXPLORE",
        working_state=repo.get_working_state(scope, session_id).state_json,
        owner_message_id=uid(),
        owner_text="当前老板消息。",
    )
    bindings = context.to_dict()["owner_evidence_references"]
    binding_ids = {binding["target_id"] for binding in bindings}
    assert [turn.owner.id for turn in context.history_turns] == [loaded_owner_id]
    assert loaded_owner_id in binding_ids
    assert covered_owner_id not in binding_ids


def test_checkpoint_covered_owner_is_authorized_only_when_point_loaded_as_evidence(repository) -> None:
    repo, scope, session_id = repository
    orchestrator = DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    )
    orchestrator.run(make_request(session_id, "early"))
    early_owner_id = repo.get_complete_message_turns(scope, session_id)[0]["owner"]["id"]
    insert_checkpoint(repo, session_id, covered_through_seq=2)
    state = repo.get_working_state(scope, session_id).state_json
    evidence_ref = {
        "evidence_type": "owner_message",
        "target_id": early_owner_id,
        "target_session_id": session_id,
    }
    state["owner_facts"] = [{
        "item_id": uid(),
        "statement": "老板早期提供的真实内容。",
        "evidence_refs": [evidence_ref],
        "supersedes_item_ids": [],
        "inherited_from": None,
    }]
    context = assembler(repo, scope).assemble(
        session_id=session_id,
        stage="EXPLORE",
        working_state=state,
        owner_message_id=uid(),
        owner_text="当前老板消息。",
    )
    assert context.history_turns == ()
    assert [message.id for message in context.evidence_messages] == [early_owner_id]
    assert evidence_ref in context.to_dict()["owner_evidence_references"]

    post_state = context.to_dict()["working_state"]
    post_state["direction"] = {
        "item_id": uid(),
        "statement": "沿早期真实内容展开。",
        "owner_confirmed": True,
        "evidence_refs": [evidence_ref],
        "inherited_from": None,
    }
    validated = validate_stage_model_output({
        "output_format_version": 1,
        "run_control": "CONTINUE",
        "target_stage": "DEEPEN",
        "transition_reason_code": "DIRECTION_CONFIRMED",
        "director_message": None,
        "gate": None,
        "review": None,
        "post_state": post_state,
    }, context=context)
    assert validated.post_state.direction is not None


def test_unloaded_historical_owner_reference_is_rejected(repository) -> None:
    repo, scope, session_id = repository
    DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    ).run(make_request(session_id, "unloaded"))
    old_owner_id = repo.get_complete_message_turns(scope, session_id)[0]["owner"]["id"]
    insert_checkpoint(repo, session_id, covered_through_seq=2)
    context = assembler(repo, scope).assemble(
        session_id=session_id,
        stage="EXPLORE",
        working_state=repo.get_working_state(scope, session_id).state_json,
        owner_message_id=uid(),
        owner_text="当前老板消息。",
    )
    post_state = context.to_dict()["working_state"]
    post_state["direction"] = {
        "item_id": uid(),
        "statement": "试图引用未加载历史。",
        "owner_confirmed": True,
        "evidence_refs": [{
            "evidence_type": "owner_message",
            "target_id": old_owner_id,
            "target_session_id": session_id,
        }],
        "inherited_from": None,
    }
    with pytest.raises(StageModelOutputError, match="authorized OWNER Evidence Reference"):
        validate_stage_model_output({
            "output_format_version": 1,
            "run_control": "CONTINUE",
            "target_stage": "DEEPEN",
            "transition_reason_code": "DIRECTION_CONFIRMED",
            "director_message": None,
            "gate": None,
            "review": None,
            "post_state": post_state,
        }, context=context)


def test_checkpoint_keeps_long_history_bindings_bounded_and_bindings_use_budget(repository) -> None:
    repo, scope, session_id = repository
    orchestrator = DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    )
    for index in range(20):
        orchestrator.run(make_request(session_id, f"history-{index}", expected=index))
    insert_checkpoint(repo, session_id, covered_through_seq=40)
    current_owner_id = uid()
    context = assembler(repo, scope).assemble(
        session_id=session_id,
        stage="EXPLORE",
        working_state=repo.get_working_state(scope, session_id).state_json,
        owner_message_id=current_owner_id,
        owner_text="当前老板消息。",
    )
    assert context.history_turns == ()
    assert context.to_dict()["owner_evidence_references"] == [{
        "evidence_type": "owner_message",
        "target_id": current_owner_id,
        "target_session_id": session_id,
    }]

    class BindingCounter:
        def estimate(self, value):
            if isinstance(value, dict) and "owner_references" in value:
                return 10 * len(value["owner_references"])
            return 0

    with pytest.raises(ContextBudgetExceededError, match="irreducible"):
        ModelContextAssembler(repo, scope, ContextBudget(5, BindingCounter())).assemble(
            session_id=session_id,
            stage="EXPLORE",
            working_state=repo.get_working_state(scope, session_id).state_json,
            owner_message_id=uid(),
            owner_text="当前老板消息。",
        )


def test_revision_source_ready_content_is_loaded_only_when_explicit(repository) -> None:
    repo, scope, source_session_id = repository

    def ready_handler(context):
        state = context.to_dict()["working_state"]
        stage = context.stage_contract["stage"]
        if stage == "EXPLORE":
            state["direction"] = {
                "item_id": uid(),
                "statement": "讲清这道菜的真实来历。",
                "owner_confirmed": True,
                "evidence_refs": [{
                    "evidence_type": "owner_message",
                    "target_id": context.current_owner_message.id,
                    "target_session_id": source_session_id,
                }],
                "inherited_from": None,
            }
            return StageExecutionResult(None, state, "CONTINUE", "DEEPEN", "DIRECTION_CONFIRMED", None, None)
        if stage == "DEEPEN":
            state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
            return StageExecutionResult(None, state, "CONTINUE", "CREATE", "MATERIAL_SUFFICIENT", None, None)
        if stage == "CREATE":
            state["draft"] = {
                "draft_id": uid(),
                "content": {
                    "title": "真实来历",
                    "script_text": "这是一段有真实依据的内容。",
                    "shooting_notes": ["从厨房开始拍"],
                },
                "content_status": "FINAL_CANDIDATE",
                "based_on_ready_content_id": None,
            }
            return StageExecutionResult(None, state, "CONTINUE", "REVIEW", "DRAFT_CREATED", None, None)
        draft = state["draft"]
        state["review"] = {
            "review_id": uid(),
            "outcome": "PASSED",
            "root_cause": None,
            "against_draft_id": draft["draft_id"],
            "against_content": deepcopy(draft["content"]),
        }
        return StageExecutionResult(
            "这版内容可以拍了。", state, "READY", "READY", "REVIEW_PASSED",
            {"outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": "内容完整。"},
            {"outcome": "PASSED", "root_cause": None},
        )

    ready_outcome = DirectorOrchestrator(
        repo, scope, DirectorStageExecutor(assembler(repo, scope), model_handler(ready_handler)), max_internal_steps=4
    ).run(make_request(source_session_id, "make-ready"))
    ready_id = ready_outcome.response["ready_content_id"]
    revision = repo.create_revision_session(scope, ready_id)
    revision_state = repo.get_working_state(scope, revision.id).state_json
    inherited_context = assembler(repo, scope).assemble(
        session_id=revision.id,
        stage="EXPLORE",
        working_state=revision_state,
        owner_message_id=uid(),
        owner_text="修改来源内容。",
    )
    assert any(
        reference["target_session_id"] == source_session_id
        for reference in inherited_context.to_dict()["owner_evidence_references"]
    )

    class StepPolicy:
        def __init__(self) -> None:
            self.decisions: list[tuple[str, bool, bool]] = []

        def should_include(self, context: StageExecutionContext) -> bool:
            include = context.stage == "DEEPEN"
            self.decisions.append((context.stage, context.is_revision_session, include))
            return include

    policy = StepPolicy()
    loaded: list[tuple[str, bool]] = []

    def revision_handler(context):
        state = context.to_dict()["working_state"]
        stage = context.stage_contract["stage"]
        loaded.append((stage, context.source_ready_content is not None))
        if stage == "EXPLORE":
            return StageExecutionResult(None, state, "CONTINUE", "DEEPEN", "DIRECTION_CONFIRMED", None, None)
        state["material_state"] = {
            "status": "INSUFFICIENT",
            "required_confirmations": [{
                "item_id": uid(), "statement": "确认修改重点", "reason": "素材不足",
                "evidence_refs": [], "inherited_from": None,
            }],
        }
        return StageExecutionResult(
            "请继续补充修改内容。", state, "WAIT_FOR_OWNER", "DEEPEN",
            "OWNER_INPUT_REQUIRED",
            {"outcome": "BLOCKED", "gate_code": "MATERIAL_INSUFFICIENT", "explanation": "仍缺修改素材。"},
            None,
        )

    executor = DirectorStageExecutor(assembler(repo, scope), model_handler(revision_handler), policy)
    first = executor(StageExecutionContext(
        stage="EXPLORE",
        working_state=revision_state,
        owner_text="修改来源内容。",
        parameters={},
        candidate_revision=0,
        session_id=revision.id,
        owner_message_id=uid(),
        is_revision_session=True,
    ))
    executor(StageExecutionContext(
        stage="DEEPEN",
        working_state=first.post_state,
        owner_text="修改来源内容。",
        parameters={},
        candidate_revision=1,
        session_id=revision.id,
        owner_message_id=uid(),
        is_revision_session=True,
    ))
    assert policy.decisions == [("EXPLORE", True, False), ("DEEPEN", True, True)]
    assert loaded == [("EXPLORE", False), ("DEEPEN", True)]

    ordinary_loaded: list[bool] = []

    class AlwaysInclude:
        def should_include(self, context: StageExecutionContext) -> bool:
            return True

    def ordinary_handler(context):
        ordinary_loaded.append(context.source_ready_content is not None)
        return StageExecutionResult(
            "请继续补充真实内容。",
            context.to_dict()["working_state"],
            "WAIT_FOR_OWNER",
                "EXPLORE",
                "OWNER_INPUT_REQUIRED",
                {"outcome": "BLOCKED", "gate_code": "DIRECTION_NOT_CONFIRMED", "explanation": "方向尚未确认。"},
                None,
        )

    DirectorStageExecutor(
        assembler(repo, scope), model_handler(ordinary_handler), AlwaysInclude()
    )(StageExecutionContext(
        stage="EXPLORE",
        working_state=repo.get_working_state(scope, source_session_id).state_json,
        owner_text="当前老板消息。",
        parameters={},
        candidate_revision=0,
        session_id=source_session_id,
        owner_message_id=uid(),
        is_revision_session=False,
    ))
    assert ordinary_loaded == [False]

    without_source = assembler(repo, scope).assemble(
        session_id=revision.id,
        stage="EXPLORE",
        working_state=revision_state,
        owner_message_id=uid(),
        owner_text="修改来源内容。",
    )
    with_source = assembler(repo, scope).assemble(
        session_id=revision.id,
        stage="EXPLORE",
        working_state=revision_state,
        owner_message_id=uid(),
        owner_text="修改来源内容。",
        include_source_ready_content=True,
    )
    assert without_source.source_ready_content is None
    assert with_source.source_ready_content["id"] == ready_id

    tables = (
        "director_messages", "director_turns", "director_working_state",
        "director_context_checkpoints", "director_ready_content", "director_sessions",
    )
    before = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables]
    for field in ("statement", "item_id", "evidence_refs"):
        bad_state = deepcopy(revision_state)
        if field == "statement":
            bad_state["direction"][field] = "伪造的来源表达。"
        elif field == "item_id":
            bad_state["direction"][field] = uid()
        else:
            bad_state["direction"][field] = []
        called = 0

        def invalid_handler(context):
            nonlocal called
            called += 1
            return wait_result(context)

        with pytest.raises(EvidenceReferenceError, match="closure"):
            DirectorStageExecutor(
                assembler(repo, scope), model_handler(invalid_handler), DisabledSourceReadyContentPolicy()
            )(StageExecutionContext(
                stage="EXPLORE",
                working_state=bad_state,
                owner_text="修改来源内容。",
                parameters={},
                candidate_revision=0,
                session_id=revision.id,
                owner_message_id=uid(),
                is_revision_session=True,
            ))
        assert called == 0
    after = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables]
    assert after == before
