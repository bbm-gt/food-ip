from __future__ import annotations

from copy import deepcopy
import json
from uuid import uuid4

import pytest

from backend.app.director_core.execution import (
    DirectorExecutionValidationError,
    StaleStateVersionError,
)
from backend.app.director_core.orchestrator import (
    DirectorOrchestrator,
    DirectorTurnRequest,
    StageExecutionContext,
    StageExecutionResult,
)
from backend.app.director_core.repository import AuthorizationScope, DirectorRepository


TABLES = (
    "director_sessions",
    "director_messages",
    "director_working_state",
    "director_turns",
    "director_context_checkpoints",
    "director_ready_content",
)


def uid() -> str:
    return str(uuid4())


def request(session_id: str, client_id: str, expected: int = 0) -> DirectorTurnRequest:
    return DirectorTurnRequest(
        session_id=session_id,
        client_message_id=client_id,
        expected_state_version=expected,
        owner_text="老板补充了一个真实细节。",
        request_format_version=1,
        parameters={},
    )


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


def snapshot(repository: DirectorRepository) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {
        table: tuple(
            tuple(row)
            for row in repository.connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        )
        for table in TABLES
    }


@pytest.fixture
def repository(tmp_path):
    from backend.app.director_core.database import apply_migrations, connect

    connection = connect(tmp_path / "loop.sqlite", busy_timeout_ms=100)
    apply_migrations(connection)
    repository = DirectorRepository(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = repository.create_session(scope)
    return repository, scope, session.id


def result(
    context: StageExecutionContext,
    *,
    post_state: dict | None = None,
    run_control: str,
    target_stage: str,
    reason: str,
    gate: dict | None = None,
    review: dict | None = None,
    message: str = "请继续补充真实内容。",
) -> StageExecutionResult:
    return StageExecutionResult(
        director_message=message,
        post_state=deepcopy(context.working_state if post_state is None else post_state),
        run_control=run_control,
        target_stage=target_stage,
        transition_reason_code=reason,
        gate=gate,
        review=review,
    )


def _ready_state(context: StageExecutionContext) -> dict:
    state = deepcopy(context.working_state)
    direction_id = state["direction"]["item_id"] if state["direction"] else uid()
    state["direction"] = {
        "item_id": direction_id,
        "statement": "讲清这道菜为什么值得被记住",
        "owner_confirmed": True,
        "evidence_refs": [{
            "evidence_type": "owner_message",
            "target_id": context.owner_message_id,
            "target_session_id": context.session_id,
        }],
        "inherited_from": None,
    }
    state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
    draft_id = state["draft"]["draft_id"] if state["draft"] else uid()
    content = {
        "title": "一碗汤的来历",
        "script_text": "这道汤不是为了复杂，而是为了让客人喝到我们真正熟悉的味道。",
        "shooting_notes": ["从出锅画面开始"],
    }
    state["draft"] = {
        "draft_id": draft_id,
        "content": content,
        "content_status": "FINAL_CANDIDATE",
        "based_on_ready_content_id": None,
    }
    state["review"] = {
        "review_id": uid(),
        "outcome": "PASSED",
        "root_cause": None,
        "against_draft_id": draft_id,
        "against_content": deepcopy(content),
    }
    return state


def test_explore_deepen_wait_uses_latest_candidate_and_trace_order(repository) -> None:
    repo, scope, session_id = repository
    seen: list[tuple[str, dict]] = []

    def executor(context: StageExecutionContext) -> StageExecutionResult:
        seen.append((context.stage, deepcopy(context.working_state)))
        if context.stage == "EXPLORE":
            state = deepcopy(context.working_state)
            state["ai_judgments"].append({
                "item_id": uid(),
                "judgment_kind": "DIRECTION_CANDIDATE",
                "statement": "围绕一道菜的真实来历展开",
            })
            return result(
                context,
                post_state=state,
                run_control="CONTINUE",
                target_stage="DEEPEN",
                reason="DIRECTION_CONFIRMED",
            )
        return result(
            context,
            run_control="WAIT_FOR_OWNER",
            target_stage="DEEPEN",
            reason="OWNER_INPUT_REQUIRED",
        )

    outcome = DirectorOrchestrator(
        repo, scope, stage_executor=executor, max_internal_steps=4
    ).run(request(session_id, "explore-deepen"))

    assert outcome.replayed is False
    assert [stage for stage, _ in seen] == ["EXPLORE", "DEEPEN"]
    assert seen[1][1]["ai_judgments"]
    trace = repo.connection.execute(
        "SELECT execution_trace_json FROM director_turns WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]
    steps = json.loads(trace)["steps"]
    assert [step["entered_stage"] for step in steps] == ["EXPLORE", "DEEPEN"]
    assert [step["target_stage"] for step in steps] == ["DEEPEN", "DEEPEN"]
    assert repo.get_working_state(scope, session_id).stage == "DEEPEN"


def test_deepen_create_review_ready_stops_and_derives_ready_content(repository) -> None:
    repo, scope, session_id = repository
    calls: list[str] = []

    def executor(context: StageExecutionContext) -> StageExecutionResult:
        calls.append(context.stage)
        if context.stage == "EXPLORE":
            return result(
                context,
                post_state=_ready_state(context),
                run_control="CONTINUE",
                target_stage="DEEPEN",
                reason="DIRECTION_CONFIRMED",
            )
        if context.stage == "DEEPEN":
            state = deepcopy(context.working_state)
            state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
            return result(
                context,
                post_state=state,
                run_control="CONTINUE",
                target_stage="CREATE",
                reason="MATERIAL_SUFFICIENT",
            )
        if context.stage == "CREATE":
            state = deepcopy(context.working_state)
            state["draft"] = _ready_state(context)["draft"]
            return result(
                context,
                post_state=state,
                run_control="CONTINUE",
                target_stage="REVIEW",
                reason="DRAFT_CREATED",
            )
        return result(
            context,
            post_state=_ready_state(context),
            run_control="READY",
            target_stage="READY",
            reason="REVIEW_PASSED",
            gate={
                "outcome": "PASSED",
                "gate_code": "READINESS_PASSED",
                "explanation": "内容完整、真实且可拍。",
            },
            review={"outcome": "PASSED", "root_cause": None},
            message="这版内容已经可以拍了。",
        )

    outcome = DirectorOrchestrator(
        repo, scope, stage_executor=executor, max_internal_steps=8
    ).run(request(session_id, "ready-loop"))

    assert calls == ["EXPLORE", "DEEPEN", "CREATE", "REVIEW"]
    assert outcome.response["run_control"] == "READY"
    ready = repo.get_ready_content(scope, outcome.response["ready_content_id"])
    assert ready["final_content_json"]["script_text"].startswith("这道汤不是为了复杂")
    assert repo.get_working_state(scope, session_id).stage == "READY"


@pytest.mark.parametrize(
    ("root_cause", "target_stage"),
    [
        ("WRITING_PROBLEM", "CREATE"),
        ("MATERIAL_PROBLEM", "DEEPEN"),
        ("DIRECTION_PROBLEM", "EXPLORE"),
    ],
)
def test_review_fallback_follows_root_cause(repository, root_cause: str, target_stage: str) -> None:
    repo, scope, session_id = repository
    seen: list[str] = []
    review_complete = False
    routed: list[str] = []

    def executor(context: StageExecutionContext) -> StageExecutionResult:
        nonlocal review_complete
        seen.append(context.stage)
        if review_complete:
            if target_stage == "CREATE" and context.stage == "CREATE":
                return result(
                    context,
                    post_state=_ready_state(context),
                    run_control="CONTINUE",
                    target_stage="REVIEW",
                    reason="DRAFT_CREATED",
                )
            if target_stage == "CREATE" and context.stage == "REVIEW":
                state = _ready_state(context)
                state["review"] = {
                    "review_id": uid(),
                    "outcome": "BLOCKED",
                    "root_cause": "MATERIAL_PROBLEM",
                    "against_draft_id": state["draft"]["draft_id"],
                    "against_content": deepcopy(state["draft"]["content"]),
                }
                return result(
                    context,
                    post_state=state,
                    run_control="CONTINUE",
                    target_stage="DEEPEN",
                    reason="MATERIAL_GAP",
                    review={"outcome": "BLOCKED", "root_cause": "MATERIAL_PROBLEM"},
                )
            return result(
                context,
                post_state=context.working_state,
                run_control="WAIT_FOR_OWNER",
                target_stage=context.stage,
                reason="OWNER_INPUT_REQUIRED",
            )
        if context.stage == "EXPLORE":
            return result(
                context,
                post_state=_ready_state(context),
                run_control="CONTINUE",
                target_stage="DEEPEN",
                reason="DIRECTION_CONFIRMED",
            )
        if context.stage == "DEEPEN":
            return result(
                context,
                post_state=_ready_state(context),
                run_control="CONTINUE",
                target_stage="CREATE",
                reason="MATERIAL_SUFFICIENT",
            )
        if context.stage == "CREATE":
            return result(
                context,
                post_state=_ready_state(context),
                run_control="CONTINUE",
                target_stage="REVIEW",
                reason="DRAFT_CREATED",
            )
        state = _ready_state(context)
        review_complete = True
        routed.append(target_stage)
        state["review"] = {
            "review_id": uid(),
            "outcome": "BLOCKED",
            "root_cause": root_cause,
            "against_draft_id": state["draft"]["draft_id"],
            "against_content": deepcopy(state["draft"]["content"]),
        }
        return result(
            context,
            post_state=state,
            run_control="CONTINUE",
            target_stage=target_stage,
            reason={
                "CREATE": "WRITING_REPAIR",
                "DEEPEN": "MATERIAL_GAP",
                "EXPLORE": "DIRECTION_INVALID",
            }[target_stage],
            review={"outcome": "BLOCKED", "root_cause": root_cause},
        )

    outcome = DirectorOrchestrator(
        repo, scope, stage_executor=executor, max_internal_steps=8
    ).run(request(session_id, f"review-{root_cause}"))
    assert target_stage in routed
    assert seen[0:4] == ["EXPLORE", "DEEPEN", "CREATE", "REVIEW"]


def test_wait_and_ready_are_terminal_executor_controls(repository) -> None:
    repo, scope, session_id = repository
    wait_calls: list[str] = []

    def wait_executor(context: StageExecutionContext) -> StageExecutionResult:
        wait_calls.append(context.stage)
        return result(
            context,
            run_control="WAIT_FOR_OWNER",
            target_stage=context.stage,
            reason="OWNER_INPUT_REQUIRED",
        )

    DirectorOrchestrator(
        repo, scope, stage_executor=wait_executor, max_internal_steps=3
    ).run(request(session_id, "terminal-wait"))
    assert wait_calls == ["EXPLORE"]


def test_invalid_transition_root_cause_executor_error_and_step_limit_are_atomic(repository) -> None:
    repo, scope, session_id = repository
    before = snapshot(repo)

    def invalid_transition(context: StageExecutionContext) -> StageExecutionResult:
        return result(
            context,
            run_control="CONTINUE",
            target_stage="REVIEW",
            reason="DRAFT_CREATED",
        )

    with pytest.raises(DirectorExecutionValidationError):
        DirectorOrchestrator(
            repo, scope, stage_executor=invalid_transition, max_internal_steps=3
        ).run(request(session_id, "invalid-transition"))
    assert snapshot(repo) == before

    def invalid_review_root(context: StageExecutionContext) -> StageExecutionResult:
        if context.stage == "EXPLORE":
            return result(
                context,
                post_state=_ready_state(context),
                run_control="CONTINUE",
                target_stage="DEEPEN",
                reason="DIRECTION_CONFIRMED",
            )
        if context.stage == "DEEPEN":
            return result(
                context,
                post_state=_ready_state(context),
                run_control="CONTINUE",
                target_stage="CREATE",
                reason="MATERIAL_SUFFICIENT",
            )
        if context.stage == "CREATE":
            return result(
                context,
                post_state=_ready_state(context),
                run_control="CONTINUE",
                target_stage="REVIEW",
                reason="DRAFT_CREATED",
            )
        state = _ready_state(context)
        state["review"] = {
            "review_id": uid(),
            "outcome": "BLOCKED",
            "root_cause": "MATERIAL_PROBLEM",
            "against_draft_id": state["draft"]["draft_id"],
            "against_content": deepcopy(state["draft"]["content"]),
        }
        return result(
            context,
            post_state=state,
            run_control="CONTINUE",
            target_stage="CREATE",
            reason="WRITING_REPAIR",
            review={"outcome": "BLOCKED", "root_cause": "MATERIAL_PROBLEM"},
        )

    with pytest.raises(DirectorExecutionValidationError):
        DirectorOrchestrator(
            repo, scope, stage_executor=invalid_review_root, max_internal_steps=8
        ).run(request(session_id, "invalid-review-root"))
    assert snapshot(repo) == before

    def exploding(_context: StageExecutionContext) -> StageExecutionResult:
        raise RuntimeError("stage failed")

    with pytest.raises(RuntimeError, match="stage failed"):
        DirectorOrchestrator(
            repo, scope, stage_executor=exploding, max_internal_steps=3
        ).run(request(session_id, "executor-error"))
    assert snapshot(repo) == before

    def endless(context: StageExecutionContext) -> StageExecutionResult:
        target = "DEEPEN" if context.stage == "EXPLORE" else "DEEPEN"
        reason = "DIRECTION_CONFIRMED" if context.stage == "EXPLORE" else "MATERIAL_GAP"
        return result(
            context,
            run_control="CONTINUE",
            target_stage=target,
            reason=reason,
        )

    with pytest.raises(DirectorExecutionValidationError, match="max_internal_steps"):
        DirectorOrchestrator(
            repo, scope, stage_executor=endless, max_internal_steps=2
        ).run(request(session_id, "step-limit"))
    assert snapshot(repo) == before


def test_max_internal_steps_is_explicit_positive_and_stale_skips_executor(repository) -> None:
    repo, scope, session_id = repository

    def executor(context: StageExecutionContext) -> StageExecutionResult:
        return result(
            context,
            run_control="WAIT_FOR_OWNER",
            target_stage=context.stage,
            reason="OWNER_INPUT_REQUIRED",
        )

    for invalid in (0, -1, True, 1.5, "2", None):
        kwargs = {"stage_executor": executor, "max_internal_steps": invalid}
        with pytest.raises(DirectorExecutionValidationError):
            DirectorOrchestrator(repo, scope, **kwargs)

    calls: list[str] = []

    def stale_executor(context: StageExecutionContext) -> StageExecutionResult:
        calls.append(context.stage)
        return executor(context)

    with pytest.raises(StaleStateVersionError):
        DirectorOrchestrator(
            repo, scope, stage_executor=stale_executor, max_internal_steps=2
        ).run(request(session_id, "stale-loop", expected=1))
    assert calls == []


def test_idempotent_replay_does_not_rerun_internal_loop(repository) -> None:
    repo, scope, session_id = repository
    calls: list[str] = []

    def executor(context: StageExecutionContext) -> StageExecutionResult:
        calls.append(context.stage)
        return result(
            context,
            run_control="WAIT_FOR_OWNER",
            target_stage=context.stage,
            reason="OWNER_INPUT_REQUIRED",
        )

    orchestrator = DirectorOrchestrator(
        repo, scope, stage_executor=executor, max_internal_steps=2
    )
    first = orchestrator.run(request(session_id, "replay-loop"))
    replay = orchestrator.run(request(session_id, "replay-loop"))
    assert first.first_response_json == replay.first_response_json
    assert calls == ["EXPLORE"]
