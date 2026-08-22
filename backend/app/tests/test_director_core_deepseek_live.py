"""Opt-in paid DeepSeek acceptance test for the Phase 1F vertical slice."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

from backend.app import config
from backend.app.director_core.context import ContextBudget, ModelContextAssembler
from backend.app.director_core.database import apply_migrations, connect
from backend.app.director_core.models import validate_working_state
from backend.app.director_core.orchestrator import (
    DirectorOrchestrator,
    DirectorStageExecutor,
    DirectorTurnRequest,
    StageExecutionContext,
    StageExecutionResult,
)
from backend.app.director_core.providers.deepseek import DeepSeekStageHandler
from backend.app.director_core.repository import AuthorizationScope, DirectorRepository


LIVE_ENABLED = bool(os.environ.get("DIRECTOR_DEEPSEEK_API_KEY")) and (
    os.environ.get("RUN_DEEPSEEK_LIVE_TESTS") == "1"
)

pytestmark = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason=(
        "requires DIRECTOR_DEEPSEEK_API_KEY and RUN_DEEPSEEK_LIVE_TESTS=1"
    ),
)


def live_repository(tmp_path, name: str):
    connection = connect(tmp_path / name, busy_timeout_ms=100)
    apply_migrations(connection)
    repository = DirectorRepository(connection)
    scope = AuthorizationScope("live-workspace", "live-project")
    session = repository.create_session(scope)
    executor = DirectorStageExecutor(
        ModelContextAssembler(repository, scope, ContextBudget(1_000_000)),
        DeepSeekStageHandler.from_environment(),
        mode=config.DIRECTOR_STAGE_MODE,
    )
    return repository, scope, session, executor


def test_deepseek_live_single_stage_produces_strict_structured_output(tmp_path) -> None:
    repository, scope, session, executor = live_repository(
        tmp_path, "director-deepseek-live-single-stage.sqlite"
    )
    working_state = repository.get_working_state(scope, session.id)

    result = executor(StageExecutionContext(
        stage="EXPLORE",
        working_state=working_state.state_json,
        owner_text=(
            "我开一家社区小餐馆，现在只确定想做一条真实的短视频，但还没有确认讲什么方向。"
            "不要替我补事实，请只根据这些信息判断下一步。"
        ),
        parameters={},
        candidate_revision=0,
        session_id=session.id,
        owner_message_id=str(uuid4()),
        is_revision_session=False,
    ))

    assert isinstance(result, StageExecutionResult)
    assert result.run_control in {"CONTINUE", "WAIT_FOR_OWNER"}
    if result.run_control == "WAIT_FOR_OWNER":
        assert result.target_stage == "EXPLORE"
        assert isinstance(result.director_message, str) and result.director_message.strip()
        assert result.gate is not None and result.gate["outcome"] == "BLOCKED"
    else:
        assert result.target_stage == "DEEPEN"
        assert result.director_message is None
        assert result.post_state["direction"] is not None


def test_deepseek_live_multi_turn_owner_conversation_commits_atomically(tmp_path) -> None:
    repository, scope, session, executor = live_repository(
        tmp_path, "director-deepseek-live-multi-turn.sqlite"
    )
    orchestrator = DirectorOrchestrator(
        repository, scope, executor, max_internal_steps=8
    )

    owner_messages = [
        "我是开社区小餐馆的，想做一条短视频，但现在还没想好最值得讲什么，请先帮我找方向。",
        (
            "我确认讲为什么店里一直保留手工包馄饨这个方向。真实情况是：我妈妈最早教我包，"
            "现在每天上午我和两位店员一起包；我们不说这是全城最好，只想让客人知道它为什么一直没从菜单上拿掉。"
        ),
        (
            "再补充一个真实细节：每天第一锅通常在十一点前下锅，我希望语气朴实，不卖惨，"
            "拍摄可以从包馄饨的手部近景开始。"
        ),
    ]

    committed_turns = 0
    expected_version = 0
    responses: list[dict] = []
    for index, owner_text in enumerate(owner_messages, 1):
        if repository.get_session(scope, session.id).lifecycle_status == "READY":
            break
        result = orchestrator.run(
            DirectorTurnRequest(
                session_id=session.id,
                client_message_id=f"live-owner-{index}",
                expected_state_version=expected_version,
                owner_text=owner_text,
                request_format_version=1,
                parameters={},
            )
        )
        assert result.replayed is False
        response = result.response
        assert response["session_id"] == session.id
        assert response["run_control"] in {"WAIT_FOR_OWNER", "READY"}
        assert isinstance(response["director_message"], str)
        assert response["director_message"].strip()
        responses.append(response)
        expected_version = response["state_version"]
        committed_turns += 1

    assert committed_turns >= 2
    assert [response["state_version"] for response in responses] == list(
        range(1, committed_turns + 1)
    )
    assert repository.connection.execute(
        "SELECT count(*) FROM director_turns WHERE session_id = ?", (session.id,)
    ).fetchone()[0] == committed_turns
    assert repository.connection.execute(
        "SELECT count(*) FROM director_messages WHERE session_id = ?", (session.id,)
    ).fetchone()[0] == committed_turns * 2
    working_state = repository.get_working_state(scope, session.id)
    assert working_state.state_version == committed_turns
    validated_state = validate_working_state(
        working_state.state_json,
        stage=working_state.stage,
        state_version=working_state.state_version,
    )
    assert validated_state.direction is not None
    assert validated_state.direction.evidence_refs
    assert working_state.stage != "EXPLORE"

    trace_rows = repository.connection.execute(
        "SELECT execution_trace_json FROM director_turns WHERE session_id = ? "
        "ORDER BY post_state_version",
        (session.id,),
    ).fetchall()
    assert len(trace_rows) == committed_turns
    assert all(json.loads(row[0])["steps"] for row in trace_rows)

    if working_state.stage == "READY":
        ready = repository.connection.execute(
            "SELECT final_content_json FROM director_ready_content WHERE session_id = ?",
            (session.id,),
        ).fetchone()
        assert ready is not None
        assert json.loads(ready[0])["script_text"].strip()


@pytest.mark.parametrize("existing_old_fact", [False, True])
def test_deepseek_live_understands_natural_language_fact_correction(
    tmp_path, existing_old_fact: bool,
) -> None:
    repository, scope, session, executor = live_repository(
        tmp_path, "director-deepseek-live-fact-correction.sqlite"
    )
    owner_message_id = str(uuid4())
    evidence = {
        "evidence_type": "owner_message",
        "target_id": owner_message_id,
        "target_session_id": session.id,
    }
    state = repository.get_working_state(scope, session.id).state_json
    state["direction"] = {
        "item_id": str(uuid4()),
        "statement": "讲每天熬汤的真实时间",
        "owner_confirmed": True,
        "evidence_refs": [evidence],
        "inherited_from": None,
    }
    state["owner_facts"] = []
    if existing_old_fact:
        state["owner_facts"].append({
            "item_id": str(uuid4()),
            "statement": "凌晨四点开始熬汤",
            "evidence_refs": [evidence],
            "supersedes_item_ids": [],
            "inherited_from": None,
        })
    state["material_state"] = {
        "status": "SUFFICIENT",
        "required_confirmations": [],
    }

    result = executor(StageExecutionContext(
        stage="DEEPEN",
        working_state=state,
        owner_text="我们不是凌晨四点熬汤，是早上六点开始熬汤。",
        parameters={},
        candidate_revision=0,
        session_id=session.id,
        owner_message_id=owner_message_id,
        is_revision_session=False,
    ))

    facts = result.post_state["owner_facts"]
    assert len(facts) == 1
    assert "六点" in facts[0]["statement"]
    assert "四点" not in facts[0]["statement"]
    assert facts[0]["supersedes_item_ids"] == []
    assert result.post_state["rejected_items"] == []


def test_deepseek_live_review_catches_semantic_use_of_unconfirmed_fact(tmp_path) -> None:
    repository, scope, session, executor = live_repository(
        tmp_path, "director-deepseek-live-semantic-review.sqlite"
    )
    owner_message_id = str(uuid4())
    evidence = {
        "evidence_type": "owner_message",
        "target_id": owner_message_id,
        "target_session_id": session.id,
    }
    state = repository.get_working_state(scope, session.id).state_json
    state["direction"] = {
        "item_id": str(uuid4()),
        "statement": "讲每天熬汤的真实过程",
        "owner_confirmed": True,
        "evidence_refs": [evidence],
        "inherited_from": None,
    }
    state["owner_facts"] = [{
        "item_id": str(uuid4()),
        "statement": "早上六点开始熬汤",
        "evidence_refs": [evidence],
        "supersedes_item_ids": [],
        "inherited_from": None,
    }]
    state["unconfirmed_inferences"] = [{
        "item_id": str(uuid4()),
        "statement": "牛骨汤从凌晨四点开始熬",
        "reason": "老板尚未确认这个时间",
    }]
    state["material_state"] = {
        "status": "SUFFICIENT",
        "required_confirmations": [],
    }
    state["draft"] = {
        "draft_id": str(uuid4()),
        "content": {
            "title": "每天现熬",
            "script_text": "每天凌晨4点，我们就开火熬牛骨汤。",
            "shooting_notes": [],
        },
        "content_status": "FINAL_CANDIDATE",
        "based_on_ready_content_id": None,
    }

    result = executor(StageExecutionContext(
        stage="REVIEW",
        working_state=state,
        owner_text="请审核当前这篇稿件。",
        parameters={},
        candidate_revision=0,
        session_id=session.id,
        owner_message_id=owner_message_id,
        is_revision_session=False,
    ))

    assert result.run_control == "CONTINUE"
    assert result.target_stage == "DEEPEN"
    assert result.transition_reason_code == "MATERIAL_GAP"
    assert result.review == {
        "outcome": "BLOCKED",
        "root_cause": "MATERIAL_PROBLEM",
    }
