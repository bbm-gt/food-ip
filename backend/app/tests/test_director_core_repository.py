from uuid import uuid4

import pytest

from backend.app.director_core.canonical import canonical_sha256, canonical_text, state_sha256
from backend.app.director_core.database import apply_migrations, connect
from backend.app.director_core.repository import (
    AuthorizationScope,
    DirectorIntegrityError,
    DirectorNotFoundError,
    DirectorRepository,
)


def uid() -> str:
    return str(uuid4())


@pytest.fixture
def repository() -> DirectorRepository:
    connection = connect(":memory:")
    apply_migrations(connection)
    return DirectorRepository(connection)


def _finish_source(repository: DirectorRepository, scope: AuthorizationScope) -> tuple[str, str, dict]:
    session = repository.create_session(scope)
    connection = repository.connection
    turn_id, owner_id, director_id, ready_id = uid(), uid(), uid(), uid()
    draft_id, review_id, direction_id, fact_id, constraint_id = uid(), uid(), uid(), uid(), uid()
    evidence = {"evidence_type": "owner_message", "target_id": owner_id, "target_session_id": session.id}
    content = {"title": "招牌菜", "script_text": "这是可直接拍摄的完整内容。", "shooting_notes": ["拍门店"]}
    state = {
        "format_version": 1,
        "owner_facts": [{
            "item_id": fact_id, "statement": "招牌菜每天现做", "evidence_refs": [evidence],
            "supersedes_item_ids": [], "inherited_from": None,
        }],
        "ai_judgments": [{"item_id": uid(), "judgment_kind": "STRUCTURE", "statement": "从制作过程切入"}],
        "unconfirmed_inferences": [{"item_id": uid(), "statement": "顾客喜欢", "reason": "尚未确认"}],
        "rejected_items": [{
            "item_id": uid(), "item_kind": "AI_JUDGMENT", "statement": "旧结构",
            "rejection_code": "NO_LONGER_USED", "evidence_refs": [],
            "rejected_by_evidence_refs": [], "superseded_by_item_id": None, "inherited_from": None,
        }],
        "owner_constraints": [{
            "item_id": constraint_id, "statement": "不要夸张", "evidence_refs": [evidence],
            "constraint_kind": "EXPRESSION", "inherited_from": None,
        }],
        "direction": {
            "item_id": direction_id, "statement": "讲现做过程", "owner_confirmed": True,
            "evidence_refs": [evidence], "inherited_from": None,
        },
        "material_state": {"status": "SUFFICIENT", "required_confirmations": []},
        "draft": {"draft_id": draft_id, "content": content, "content_status": "FINAL_CANDIDATE", "based_on_ready_content_id": None},
        "review": {"review_id": review_id, "outcome": "PASSED", "root_cause": None, "against_draft_id": draft_id, "against_content": content},
    }
    snapshot = {"snapshot_format_version": 1, "state_version": 1, "stage": "READY", "state_json": state}
    request = {"owner_text": "确认", "parameters": {}}
    trace = {"format_version": 1, "steps": [{
        "step_no": 1, "entered_stage": "REVIEW", "run_control": "READY", "target_stage": "READY",
        "transition_reason_code": "REVIEW_PASSED",
        "gate": {"outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": "内容可拍"},
        "review": {"outcome": "PASSED", "root_cause": None}, "candidate_revision": 1,
    }]}
    response = {
        "session_id": session.id, "turn_id": turn_id, "owner_message_id": owner_id,
        "director_message_id": director_id, "state_version": 1, "stage": "READY",
        "run_control": "READY", "director_message": "内容已经可以拍摄。", "ready_content_id": ready_id,
    }
    digest = state_sha256(1, "READY", state)
    created_at = "2026-01-01T00:00:00.000Z"
    with connection:
        connection.execute(
            """INSERT INTO director_turns VALUES
            (?, ?, 'client-1', 1, ?, ?, 0, 1, 'READY', 'READY', 'REVIEW_PASSED', 'PASSED', NULL,
             1, ?, 1, ?, 1, ?, ?, ?)""",
            (turn_id, session.id, canonical_text(request), canonical_sha256(request), canonical_text(trace),
             canonical_text(response), canonical_text(snapshot), digest, created_at),
        )
        connection.execute(
            "INSERT INTO director_messages VALUES (?, ?, 1, 'OWNER', '确认', ?, ?)",
            (owner_id, session.id, turn_id, created_at),
        )
        connection.execute(
            "INSERT INTO director_messages VALUES (?, ?, 2, 'DIRECTOR', '内容已经可以拍摄。', ?, ?)",
            (director_id, session.id, turn_id, created_at),
        )
        connection.execute(
            """UPDATE director_working_state SET state_version = 1, stage = 'READY', state_json = ?,
               state_sha256 = ?, latest_successful_turn_id = ?, updated_at = ? WHERE session_id = ?""",
            (canonical_text(state), digest, turn_id, created_at, session.id),
        )
        connection.execute(
            "INSERT INTO director_ready_content VALUES (?, ?, 1, ?, ?, ?)",
            (ready_id, session.id, canonical_text(content), turn_id, created_at),
        )
    return session.id, ready_id, state


def test_normal_session_version_zero_is_atomic_and_empty(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session = repository.create_session(scope)
    state = repository.get_working_state(scope, session.id)
    assert session.lifecycle_status == "ACTIVE"
    assert state.state_version == 0
    assert state.stage == "EXPLORE"
    assert state.latest_successful_turn_id is None
    assert state.state_json["owner_facts"] == []
    assert state.state_json["material_state"] == {"status": "UNKNOWN", "required_confirmations": []}
    assert state.state_json["draft"] is None
    assert state.state_sha256 == state_sha256(0, "EXPLORE", state.state_json)


def test_revision_session_inherits_only_direct_allowed_baseline(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    source_session_id, ready_id, source_state = _finish_source(repository, scope)
    revision = repository.create_revision_session(scope, ready_id)
    state = repository.get_working_state(scope, revision.id)

    inherited = {"source_ready_content_id": ready_id, "source_session_id": source_session_id}
    assert state.state_version == 0 and state.stage == "EXPLORE"
    assert state.state_json["owner_facts"][0]["statement"] == "招牌菜每天现做"
    assert state.state_json["owner_facts"][0]["evidence_refs"] == source_state["owner_facts"][0]["evidence_refs"]
    assert state.state_json["owner_facts"][0]["inherited_from"] == inherited
    assert state.state_json["owner_constraints"][0]["inherited_from"] == inherited
    assert state.state_json["direction"]["inherited_from"] == inherited
    assert state.state_json["ai_judgments"] == []
    assert state.state_json["unconfirmed_inferences"] == []
    assert state.state_json["rejected_items"] == []
    assert state.state_json["review"] is None
    assert state.state_json["material_state"] == {"status": "UNKNOWN", "required_confirmations": []}
    assert state.state_json["draft"] == {
        "draft_id": None,
        "content": {"title": "招牌菜", "script_text": "这是可直接拍摄的完整内容。", "shooting_notes": ["拍门店"]},
        "content_status": "WORKING",
        "based_on_ready_content_id": ready_id,
    }
    assert repository.get_complete_message_turns(scope, revision.id) == []


def test_cross_scope_reads_and_revision_sources_are_not_visible(repository: DirectorRepository) -> None:
    owner_scope = AuthorizationScope("workspace-a", "project-a")
    other_workspace = AuthorizationScope("workspace-b", "project-a")
    other_project = AuthorizationScope("workspace-a", "project-b")
    session_id, ready_id, _ = _finish_source(repository, owner_scope)
    for scope in (other_workspace, other_project):
        with pytest.raises(DirectorNotFoundError):
            repository.get_session(scope, session_id)
        with pytest.raises(DirectorNotFoundError):
            repository.get_ready_content(scope, ready_id)
        with pytest.raises(DirectorNotFoundError):
            repository.create_revision_session(scope, ready_id)


def test_turn_transcript_ready_content_and_hash_reads_are_validated(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session_id, ready_id, _ = _finish_source(repository, scope)
    turn = repository.find_successful_turn(scope, session_id, "client-1")
    assert turn is not None and turn["post_state_sha256"] == repository.get_working_state(scope, session_id).state_sha256
    assert len(repository.get_recent_successful_turns(scope, session_id, limit=5)) == 1
    transcript = repository.get_complete_message_turns(scope, session_id)
    assert transcript[0]["owner"]["content"] == "确认"
    assert repository.get_ready_content(scope, ready_id)["final_content_json"]["title"] == "招牌菜"

    repository.connection.execute("DROP TRIGGER director_working_state_update_guard")
    repository.connection.execute(
        "UPDATE director_working_state SET state_sha256 = ? WHERE session_id = ?", ("0" * 64, session_id)
    )
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, session_id)
