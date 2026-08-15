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
from backend.app.director_core.orchestrator import (
    DirectorOrchestrator,
    DirectorStageExecutor,
    DirectorTurnRequest,
    StageExecutionContext,
    StageExecutionResult,
)
from backend.app.director_core.repository import AuthorizationScope, DirectorRepository


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
    return StageExecutionResult(
        director_message="请继续补充真实内容。",
        post_state=deepcopy(context.working_state),
        run_control="WAIT_FOR_OWNER",
        target_stage=context.stage,
        transition_reason_code="OWNER_INPUT_REQUIRED",
        gate=None,
        review=None,
    )


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
    seen: list[tuple[str, dict, int]] = []

    def handler(context):
        state = context.to_dict()["working_state"]
        seen.append((context.stage_contract["stage"], state, len(context.candidate_steps)))
        if context.stage_contract["stage"] == "EXPLORE":
            state["ai_judgments"].append({
                "item_id": uid(),
                "judgment_kind": "DIRECTION_CANDIDATE",
                "statement": "从老板真实经历展开。",
            })
            return StageExecutionResult(
                director_message=None,
                post_state=state,
                run_control="CONTINUE",
                target_stage="DEEPEN",
                transition_reason_code="DIRECTION_CONFIRMED",
                gate=None,
                review=None,
            )
        return StageExecutionResult(
            director_message="请补充最关键的真实细节。",
            post_state=state,
            run_control="WAIT_FOR_OWNER",
            target_stage="DEEPEN",
            transition_reason_code="OWNER_INPUT_REQUIRED",
            gate=None,
            review=None,
        )

    wrapped = DirectorStageExecutor(assembler(repo, scope), handler)
    outcome = DirectorOrchestrator(repo, scope, wrapped, max_internal_steps=3).run(
        make_request(session_id, "reassemble")
    )

    assert [stage for stage, _, _ in seen] == ["EXPLORE", "DEEPEN"]
    assert seen[1][1]["ai_judgments"]
    assert seen[1][2] == 1
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
        return StageExecutionResult(
            director_message="已记录这条真实内容。",
            post_state=state,
            run_control="WAIT_FOR_OWNER",
            target_stage="DEEPEN",
            transition_reason_code="OWNER_INPUT_REQUIRED",
            gate=None,
            review=None,
        )

    DirectorOrchestrator(
        repo, scope, DirectorStageExecutor(assembler(repo, scope), handler), max_internal_steps=2
    ).run(make_request(session_id, "evidence", expected=1))
    assert seen == [(old_owner_id, "老板说了一条可追溯的真实内容。")]


def test_checkpoint_loads_only_boundary_after_complete_turns(repository) -> None:
    repo, scope, session_id = repository
    orchestrator = DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    )
    orchestrator.run(make_request(session_id, "one"))
    orchestrator.run(make_request(session_id, "two", expected=1))
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
    assert len(model_context.history_turns) == 1
    assert model_context.history_turns[0].owner.content == "老板说了一条可追溯的真实内容。"
    assert model_context.history_turns[0].owner.message_seq == 3


@dataclass
class UnitCounter:
    def estimate(self, value):
        return 10 if isinstance(value, ContextTurn) else 1


def test_missing_checkpoint_history_over_budget_has_explicit_rebuild_error(repository) -> None:
    repo, scope, session_id = repository
    DirectorOrchestrator(
        repo, scope, lambda context: wait_result(context), max_internal_steps=1
    ).run(make_request(session_id, "history"))
    limited = ModelContextAssembler(repo, scope, ContextBudget(17, UnitCounter()))
    with pytest.raises(CheckpointRebuildRequiredError, match="Checkpoint"):
        limited.assemble(
            session_id=session_id,
            stage="EXPLORE",
            working_state=repo.get_working_state(scope, session_id).state_json,
            owner_message_id=uid(),
            owner_text="当前老板消息。",
        )


def test_protected_context_over_budget_fails_before_orchestrator_commit(repository) -> None:
    repo, scope, session_id = repository
    limited = ModelContextAssembler(repo, scope, ContextBudget(7, UnitCounter()))

    def handler(context):  # pragma: no cover - assembly must fail first
        raise AssertionError("handler must not run")

    before = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
              for table in ("director_messages", "director_turns", "director_working_state")]
    with pytest.raises(ContextBudgetExceededError, match="protected"):
        DirectorOrchestrator(
            repo, scope, DirectorStageExecutor(limited, handler), max_internal_steps=1
        ).run(make_request(session_id, "budget"))
    after = [repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
             for table in ("director_messages", "director_turns", "director_working_state")]
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
    with pytest.raises(EvidenceReferenceError, match="crosses"):
        assembler(repo, scope).assemble(
            session_id=session_id,
            stage="EXPLORE",
            working_state=state,
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
        repo, scope, DirectorStageExecutor(assembler(repo, scope), ready_handler), max_internal_steps=4
    ).run(make_request(source_session_id, "make-ready"))
    ready_id = ready_outcome.response["ready_content_id"]
    revision = repo.create_revision_session(scope, ready_id)
    revision_state = repo.get_working_state(scope, revision.id).state_json

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
