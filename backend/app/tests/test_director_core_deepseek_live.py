"""Opt-in paid DeepSeek acceptance test for the Phase 1F vertical slice."""

from __future__ import annotations

import os

import pytest

from backend.app.director_core.context import ContextBudget, ModelContextAssembler
from backend.app.director_core.database import apply_migrations, connect
from backend.app.director_core.orchestrator import (
    DirectorOrchestrator,
    DirectorStageExecutor,
    DirectorTurnRequest,
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


def test_deepseek_live_multi_turn_owner_conversation_commits_atomically(tmp_path) -> None:
    connection = connect(tmp_path / "director-deepseek-live.sqlite", busy_timeout_ms=100)
    apply_migrations(connection)
    repository = DirectorRepository(connection)
    scope = AuthorizationScope("live-workspace", "live-project")
    session = repository.create_session(scope)
    executor = DirectorStageExecutor(
        ModelContextAssembler(repository, scope, ContextBudget(1_000_000)),
        DeepSeekStageHandler.from_environment(),
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
        expected_version = result.response["state_version"]
        committed_turns += 1

    assert committed_turns >= 2
    assert repository.connection.execute(
        "SELECT count(*) FROM director_turns WHERE session_id = ?", (session.id,)
    ).fetchone()[0] == committed_turns
    assert repository.connection.execute(
        "SELECT count(*) FROM director_messages WHERE session_id = ?", (session.id,)
    ).fetchone()[0] == committed_turns * 2
    assert repository.get_working_state(scope, session.id).state_version == committed_turns
