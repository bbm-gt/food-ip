import json
import sqlite3
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
    trace = {"format_version": 1, "steps": [
        {"step_no": 1, "entered_stage": "EXPLORE", "run_control": "CONTINUE", "target_stage": "DEEPEN", "transition_reason_code": "DIRECTION_CONFIRMED", "gate": None, "review": None, "candidate_revision": 1},
        {"step_no": 2, "entered_stage": "DEEPEN", "run_control": "CONTINUE", "target_stage": "CREATE", "transition_reason_code": "MATERIAL_SUFFICIENT", "gate": None, "review": None, "candidate_revision": 2},
        {"step_no": 3, "entered_stage": "CREATE", "run_control": "CONTINUE", "target_stage": "REVIEW", "transition_reason_code": "DRAFT_CREATED", "gate": None, "review": None, "candidate_revision": 3},
        {"step_no": 4, "entered_stage": "REVIEW", "run_control": "READY", "target_stage": "READY",
        "transition_reason_code": "REVIEW_PASSED",
        "gate": {"outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": "内容可拍"},
        "review": {"outcome": "PASSED", "root_cause": None}, "candidate_revision": 4,
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


def _insert_revision_turn(
    repository: DirectorRepository,
    session_id: str,
    state: dict,
    *,
    owner_text: str = "老板纠正这条事实",
) -> str:
    """Persist one complete non-READY Turn for revision-session evidence tests."""
    connection = repository.connection
    turn_id, owner_id, director_id = uid(), uid(), uid()
    state = json.loads(json.dumps(state))
    state["draft"] = {
        "draft_id": uid(),
        "content": {"title": "修订中", "script_text": "等待老板确认。", "shooting_notes": ["拍摄门店"]},
        "content_status": "WORKING",
        "based_on_ready_content_id": state["draft"]["based_on_ready_content_id"],
    }
    request = {"owner_text": owner_text, "parameters": {}}
    trace = {"format_version": 1, "steps": [{
        "step_no": 1, "entered_stage": "EXPLORE", "run_control": "WAIT_FOR_OWNER",
        "target_stage": "EXPLORE", "transition_reason_code": "OWNER_INPUT_REQUIRED",
        "gate": None, "review": None, "candidate_revision": 1,
    }]}
    response = {
        "session_id": session_id, "turn_id": turn_id, "owner_message_id": owner_id,
        "director_message_id": director_id, "state_version": 1, "stage": "EXPLORE",
        "run_control": "WAIT_FOR_OWNER", "director_message": "已记录老板意见。", "ready_content_id": None,
    }
    snapshot = {"snapshot_format_version": 1, "state_version": 1, "stage": "EXPLORE", "state_json": state}
    digest = state_sha256(1, "EXPLORE", state)
    created_at = "2026-01-02T00:00:00.000Z"
    with connection:
        connection.execute(
            """INSERT INTO director_turns VALUES
            (?, ?, ?, 1, ?, ?, 0, 1, 'WAIT_FOR_OWNER', 'EXPLORE', 'OWNER_INPUT_REQUIRED', NULL, NULL,
             1, ?, 1, ?, 1, ?, ?, ?)""",
            (turn_id, session_id, f"revision-{turn_id}", canonical_text(request), canonical_sha256(request),
             canonical_text(trace), canonical_text(response), canonical_text(snapshot), digest, created_at),
        )
        connection.execute(
            "INSERT INTO director_messages VALUES (?, ?, 1, 'OWNER', ?, ?, ?)",
            (owner_id, session_id, owner_text, turn_id, created_at),
        )
        connection.execute(
            "INSERT INTO director_messages VALUES (?, ?, 2, 'DIRECTOR', '已记录老板意见。', ?, ?)",
            (director_id, session_id, turn_id, created_at),
        )
        connection.execute(
            """UPDATE director_working_state SET state_version = 1, stage = 'EXPLORE', state_json = ?,
               state_sha256 = ?, latest_successful_turn_id = ?, updated_at = ? WHERE session_id = ?""",
            (canonical_text(state), digest, turn_id, created_at, session_id),
        )
    return owner_id


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


def test_read_validation_closes_owner_text_ready_lifecycle_and_response_id(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session_id, ready_id, _ = _finish_source(repository, scope)
    connection = repository.connection
    original_owner_text = connection.execute("SELECT content FROM director_messages WHERE session_id = ? AND visible_role = 'OWNER'", (session_id,)).fetchone()[0]
    connection.execute("DROP TRIGGER director_messages_update_guard")
    connection.execute("UPDATE director_messages SET content = 'different raw owner text' WHERE session_id = ? AND visible_role = 'OWNER'", (session_id,))
    with pytest.raises(DirectorIntegrityError):
        repository.find_successful_turn(scope, session_id, "client-1")
    connection.execute("UPDATE director_messages SET content = ? WHERE session_id = ? AND visible_role = 'OWNER'", (original_owner_text, session_id))

    connection.execute("DROP TRIGGER director_sessions_update_guard")
    connection.execute("UPDATE director_sessions SET lifecycle_status = 'ACTIVE', ready_at = NULL WHERE id = ?", (session_id,))
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, session_id)

    connection.execute("UPDATE director_sessions SET lifecycle_status = 'READY', ready_at = '2026-01-01T00:00:00.000Z' WHERE id = ?", (session_id,))
    connection.execute("DROP TRIGGER director_turns_update_guard")
    response = repository.connection.execute("SELECT first_response_json FROM director_turns WHERE session_id = ?", (session_id,)).fetchone()[0]
    changed = repository.connection.execute("SELECT id FROM director_ready_content WHERE id = ?", (ready_id,)).fetchone()[0]
    payload = __import__("json").loads(response)
    payload["ready_content_id"] = uid()
    connection.execute("UPDATE director_turns SET first_response_json = ? WHERE session_id = ?", (canonical_text(payload), session_id))
    with pytest.raises(DirectorIntegrityError):
        repository.get_ready_content(scope, changed)


def test_read_validation_rejects_forged_or_modified_inheritance(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    normal = repository.create_session(scope)
    state = repository.get_working_state(scope, normal.id).state_json
    state["owner_facts"] = [{"item_id": uid(), "statement": "forged", "evidence_refs": [{"evidence_type": "owner_message", "target_id": uid(), "target_session_id": uid()}], "supersedes_item_ids": [], "inherited_from": {"source_ready_content_id": uid(), "source_session_id": uid()}}]
    repository.connection.execute("UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?", (canonical_text(state), state_sha256(0, "EXPLORE", state), normal.id))
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, normal.id)

    _, ready_id, _ = _finish_source(repository, scope)
    revision = repository.create_revision_session(scope, ready_id)
    inherited = repository.get_working_state(scope, revision.id).state_json
    inherited["owner_facts"][0]["statement"] = "modified inherited fact"
    repository.connection.execute("UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?", (canonical_text(inherited), state_sha256(0, "EXPLORE", inherited), revision.id))
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, revision.id)


def test_ready_turn_replay_requires_its_ready_content_and_ready_lifecycle(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session_id, ready_id, _ = _finish_source(repository, scope)
    repository.connection.execute("DROP TRIGGER director_ready_content_delete_guard")
    repository.connection.execute("DELETE FROM director_ready_content WHERE id = ?", (ready_id,))
    with pytest.raises(DirectorIntegrityError):
        repository.find_successful_turn(scope, session_id, "client-1")

    session_id, ready_id, _ = _finish_source(repository, scope)
    other_session_id, other_ready_id, _ = _finish_source(repository, scope)
    turn_one = repository.connection.execute("SELECT id FROM director_turns WHERE session_id = ?", (session_id,)).fetchone()[0]
    turn_two = repository.connection.execute("SELECT id FROM director_turns WHERE session_id = ?", (other_session_id,)).fetchone()[0]
    repository.connection.execute("DROP TRIGGER director_ready_content_update_guard")
    repository.connection.execute("DELETE FROM director_ready_content WHERE id = ?", (other_ready_id,))
    repository.connection.execute("UPDATE director_ready_content SET session_id = ?, created_by_turn_id = ? WHERE id = ?", (other_session_id, turn_two, ready_id))
    with pytest.raises(DirectorIntegrityError):
        repository.find_successful_turn(scope, session_id, "client-1")


def test_ready_turn_replay_rejects_active_session_and_nonready_response_binding(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session_id, ready_id, _ = _finish_source(repository, scope)
    repository.connection.execute("DROP TRIGGER director_sessions_update_guard")
    repository.connection.execute("UPDATE director_sessions SET lifecycle_status = 'ACTIVE', ready_at = NULL WHERE id = ?", (session_id,))
    with pytest.raises(DirectorIntegrityError):
        repository.find_successful_turn(scope, session_id, "client-1")

    session_id, ready_id, _ = _finish_source(repository, scope)
    repository.connection.execute("DROP TRIGGER director_turns_update_guard")
    turn_row = repository.connection.execute("SELECT first_response_json FROM director_turns WHERE session_id = ?", (session_id,)).fetchone()
    response = json.loads(turn_row[0])
    response["run_control"] = "WAIT_FOR_OWNER"
    response["stage"] = "REVIEW"
    response["ready_content_id"] = ready_id
    repository.connection.execute("UPDATE director_turns SET first_response_json = ?, final_run_control = 'WAIT_FOR_OWNER', target_stage = 'REVIEW' WHERE session_id = ?", (canonical_text(response), session_id))
    with pytest.raises(DirectorIntegrityError):
        repository.find_successful_turn(scope, session_id, "client-1")


def test_inherited_owner_fact_can_be_rejected_without_losing_source_closure(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    source_session_id, ready_id, _ = _finish_source(repository, scope)
    revision = repository.create_revision_session(scope, ready_id)
    state = repository.get_working_state(scope, revision.id).state_json
    original = state["owner_facts"][0]
    state["owner_facts"] = []
    correction_owner_id = uid()
    correction_evidence = {
        "evidence_type": "owner_message", "target_id": correction_owner_id, "target_session_id": revision.id,
    }
    state["rejected_items"] = [{
        "item_id": original["item_id"], "item_kind": "OWNER_FACT", "statement": original["statement"],
        "rejection_code": "OWNER_CORRECTED", "evidence_refs": original["evidence_refs"],
        "rejected_by_evidence_refs": [correction_evidence], "superseded_by_item_id": None,
        "inherited_from": {"source_ready_content_id": ready_id, "source_session_id": source_session_id},
    }]
    # Replace the generated ID so the helper's OWNER Message is the evidence target.
    owner_id = _insert_revision_turn(repository, revision.id, state)
    state = json.loads(repository.connection.execute(
        "SELECT state_json FROM director_working_state WHERE session_id = ?", (revision.id,)
    ).fetchone()[0])
    state["rejected_items"][0]["rejected_by_evidence_refs"][0]["target_id"] = owner_id
    # This intentionally manufactures a damaged projection; production writes
    # must go through the recovery entry point and the trigger must reject it.
    repository.connection.execute("DROP TRIGGER director_working_state_update_guard")
    repository.connection.execute(
        "UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?",
        (canonical_text(state), state_sha256(1, "EXPLORE", state), revision.id),
    )
    # Correct the persisted Turn snapshot to include the final rejection payload.
    turn = repository.connection.execute(
        "SELECT id, post_state_snapshot_json FROM director_turns WHERE session_id = ?", (revision.id,)
    ).fetchone()
    snapshot = json.loads(turn["post_state_snapshot_json"])
    snapshot["state_json"] = state
    digest = state_sha256(1, "EXPLORE", state)
    repository.connection.execute("DROP TRIGGER director_turns_update_guard")
    repository.connection.execute(
        "UPDATE director_turns SET post_state_snapshot_json = ?, post_state_sha256 = ? WHERE id = ?",
        (canonical_text(snapshot), digest, turn["id"]),
    )
    assert repository.get_working_state(scope, revision.id).state_json["rejected_items"][0]["item_id"] == original["item_id"]


def test_inherited_rejected_item_tampering_and_ordinary_session_are_rejected(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    normal = repository.create_session(scope)
    state = repository.get_working_state(scope, normal.id).state_json
    state["rejected_items"] = [{
        "item_id": uid(), "item_kind": "OWNER_FACT", "statement": "fake", "rejection_code": "NO_LONGER_USED",
        "evidence_refs": [], "rejected_by_evidence_refs": [], "superseded_by_item_id": None,
        "inherited_from": {"source_ready_content_id": uid(), "source_session_id": uid()},
    }]
    repository.connection.execute("UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?", (canonical_text(state), state_sha256(0, "EXPLORE", state), normal.id))
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, normal.id)

    source_session_id, ready_id, _ = _finish_source(repository, scope)
    revision = repository.create_revision_session(scope, ready_id)
    state = repository.get_working_state(scope, revision.id).state_json
    source = state["owner_facts"][0]
    state["owner_facts"] = []
    state["rejected_items"] = [{
        "item_id": source["item_id"], "item_kind": "OWNER_FACT", "statement": "tampered", "rejection_code": "OWNER_CORRECTED",
        "evidence_refs": source["evidence_refs"], "rejected_by_evidence_refs": [], "superseded_by_item_id": None,
        "inherited_from": {"source_ready_content_id": ready_id, "source_session_id": source_session_id},
    }]
    repository.connection.execute("UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?", (canonical_text(state), state_sha256(0, "EXPLORE", state), revision.id))
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, revision.id)


def test_inherited_rejected_item_cannot_use_source_evidence_as_rejection_evidence(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    source_session_id, ready_id, _ = _finish_source(repository, scope)
    revision = repository.create_revision_session(scope, ready_id)
    state = repository.get_working_state(scope, revision.id).state_json
    original = state["owner_facts"].pop(0)
    state["rejected_items"] = [{
        "item_id": original["item_id"], "item_kind": "OWNER_FACT", "statement": original["statement"],
        "rejection_code": "OWNER_CORRECTED", "evidence_refs": original["evidence_refs"],
        "rejected_by_evidence_refs": original["evidence_refs"], "superseded_by_item_id": None,
        "inherited_from": {"source_ready_content_id": ready_id, "source_session_id": source_session_id},
    }]
    repository.connection.execute(
        "UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?",
        (canonical_text(state), state_sha256(0, "EXPLORE", state), revision.id),
    )
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, revision.id)


def test_inherited_rejected_item_cannot_use_another_session_rejection_evidence(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    source_session_id, ready_id, _ = _finish_source(repository, scope)
    other_session_id, _, _ = _finish_source(repository, scope)
    revision = repository.create_revision_session(scope, ready_id)
    state = repository.get_working_state(scope, revision.id).state_json
    original = state["owner_facts"].pop(0)
    other_owner_id = repository.connection.execute(
        "SELECT id FROM director_messages WHERE session_id = ? AND visible_role = 'OWNER'", (other_session_id,)
    ).fetchone()[0]
    state["rejected_items"] = [{
        "item_id": original["item_id"], "item_kind": "OWNER_FACT", "statement": original["statement"],
        "rejection_code": "OWNER_CORRECTED", "evidence_refs": original["evidence_refs"],
        "rejected_by_evidence_refs": [{"evidence_type": "owner_message", "target_id": other_owner_id, "target_session_id": other_session_id}],
        "superseded_by_item_id": None,
        "inherited_from": {"source_ready_content_id": ready_id, "source_session_id": source_session_id},
    }]
    repository.connection.execute(
        "UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?",
        (canonical_text(state), state_sha256(0, "EXPLORE", state), revision.id),
    )
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, revision.id)


@pytest.mark.parametrize("source_kind", ["owner_facts", "owner_constraints", "direction"])
def test_inherited_owner_objects_can_be_required_confirmations(
    repository: DirectorRepository, source_kind: str
) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    source_session_id, ready_id, _ = _finish_source(repository, scope)
    revision = repository.create_revision_session(scope, ready_id)
    state = repository.get_working_state(scope, revision.id).state_json
    source_item = state[source_kind][0] if source_kind != "direction" else state["direction"]
    if source_kind == "direction":
        state["direction"] = None
    else:
        state[source_kind] = [item for item in state[source_kind] if item["item_id"] != source_item["item_id"]]
    state["material_state"]["required_confirmations"] = [{
        "item_id": source_item["item_id"], "statement": source_item["statement"],
        "reason": "当前语境变化，需要老板重新确认。", "evidence_refs": source_item["evidence_refs"],
        "inherited_from": {"source_ready_content_id": ready_id, "source_session_id": source_session_id},
    }]
    repository.connection.execute(
        "UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?",
        (canonical_text(state), state_sha256(0, "EXPLORE", state), revision.id),
    )
    confirmations = repository.get_working_state(scope, revision.id).state_json["material_state"]["required_confirmations"]
    assert confirmations[0]["item_id"] == source_item["item_id"]


@pytest.mark.parametrize("source_kind", ["owner_facts", "owner_constraints", "direction"])
def test_inherited_required_confirmation_cannot_remain_current_effective_object(
    repository: DirectorRepository, source_kind: str
) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    source_session_id, ready_id, _ = _finish_source(repository, scope)
    revision = repository.create_revision_session(scope, ready_id)
    state = repository.get_working_state(scope, revision.id).state_json
    source_item = state[source_kind][0] if source_kind != "direction" else state["direction"]
    state["material_state"]["required_confirmations"] = [{
        "item_id": source_item["item_id"], "statement": source_item["statement"],
        "reason": "需要重新确认。", "evidence_refs": source_item["evidence_refs"],
        "inherited_from": {"source_ready_content_id": ready_id, "source_session_id": source_session_id},
    }]
    repository.connection.execute(
        "UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?",
        (canonical_text(state), state_sha256(0, "EXPLORE", state), revision.id),
    )
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, revision.id)


def test_required_confirmation_inheritance_is_exact_and_direct(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    source_session_id, ready_id, _ = _finish_source(repository, scope)
    revision = repository.create_revision_session(scope, ready_id)
    base = repository.get_working_state(scope, revision.id).state_json
    source_item = base["owner_facts"][0]

    for mutation in ("statement", "evidence_empty", "evidence_added", "evidence_replaced", "source_ready_content_id", "source_session_id"):
        state = json.loads(json.dumps(base))
        inherited = {"source_ready_content_id": ready_id, "source_session_id": source_session_id}
        confirmation = {
            "item_id": source_item["item_id"], "statement": source_item["statement"], "reason": "再次确认",
            "evidence_refs": source_item["evidence_refs"], "inherited_from": inherited,
        }
        if mutation == "statement":
            confirmation["statement"] = "被扩大后的说法"
        elif mutation == "evidence_empty":
            confirmation["evidence_refs"] = []
        elif mutation == "evidence_added":
            confirmation["evidence_refs"] = source_item["evidence_refs"] + source_item["evidence_refs"]
        elif mutation == "evidence_replaced":
            confirmation["evidence_refs"] = [{"evidence_type": "owner_message", "target_id": uid(), "target_session_id": source_session_id}]
        elif mutation == "source_ready_content_id":
            confirmation["inherited_from"]["source_ready_content_id"] = uid()
        else:
            confirmation["inherited_from"]["source_session_id"] = uid()
        state["material_state"]["required_confirmations"] = [confirmation]
        repository.connection.execute(
            "UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?",
            (canonical_text(state), state_sha256(0, "EXPLORE", state), revision.id),
        )
        with pytest.raises(DirectorIntegrityError):
            repository.get_working_state(scope, revision.id)


def test_ordinary_session_cannot_have_inherited_required_confirmation(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session = repository.create_session(scope)
    state = repository.get_working_state(scope, session.id).state_json
    state["material_state"]["required_confirmations"] = [{
        "item_id": uid(), "statement": "伪造事实", "reason": "伪造继承", "evidence_refs": [],
        "inherited_from": {"source_ready_content_id": uid(), "source_session_id": uid()},
    }]
    repository.connection.execute(
        "UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?",
        (canonical_text(state), state_sha256(0, "EXPLORE", state), session.id),
    )
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, session.id)


def test_evidence_turn_request_format_version_must_be_one(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session_id, _, _ = _finish_source(repository, scope)
    repository.connection.execute("DROP TRIGGER director_turns_update_guard")
    repository.connection.execute(
        "UPDATE director_turns SET request_format_version = 2 WHERE session_id = ?", (session_id,)
    )
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, session_id)


def test_complete_v1_turn_owner_message_remains_valid_evidence(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session_id, _, _ = _finish_source(repository, scope)
    state = repository.get_working_state(scope, session_id)
    assert state.state_json["owner_facts"][0]["evidence_refs"]


@pytest.mark.parametrize("corruption", ["half_pair", "orphan_turn", "bad_sequence"])
def test_evidence_requires_complete_successful_turn(repository: DirectorRepository, corruption: str) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session_id, _, _ = _finish_source(repository, scope)
    connection = repository.connection
    owner = connection.execute(
        "SELECT id, turn_id FROM director_messages WHERE session_id = ? AND visible_role = 'OWNER'", (session_id,)
    ).fetchone()
    connection.execute("DROP TRIGGER director_messages_delete_guard")
    connection.execute("DROP TRIGGER director_messages_update_guard")
    if corruption == "half_pair":
        connection.execute("DELETE FROM director_messages WHERE session_id = ? AND visible_role = 'DIRECTOR'", (session_id,))
    elif corruption == "orphan_turn":
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("UPDATE director_messages SET turn_id = ? WHERE id = ?", (uid(), owner["id"]))
    else:
        connection.execute("UPDATE director_messages SET message_seq = 99 WHERE id = ?", (owner["id"],))
    with pytest.raises(DirectorIntegrityError):
        repository.get_working_state(scope, session_id)


def test_recover_missing_active_version_zero_is_deterministic(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session = repository.create_session(scope)
    repository.connection.execute("DROP TRIGGER director_working_state_delete_guard")
    repository.connection.execute("DELETE FROM director_working_state WHERE session_id = ?", (session.id,))
    repository.connection.commit()
    recovered = repository.recover_working_state(scope, session.id)
    assert recovered.state_version == 0
    assert recovered.stage == "EXPLORE"
    assert recovered.latest_successful_turn_id is None
    assert recovered.state_json == _empty_state_for_test()
    assert repository.connection.execute(
        "SELECT count(*) FROM director_turns WHERE session_id = ?", (session.id,)
    ).fetchone()[0] == 0


def _empty_state_for_test() -> dict:
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


def test_recover_missing_and_corrupt_active_state_from_maximum_turn(
    repository: DirectorRepository,
) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    _, ready_id, _ = _finish_source(repository, scope)
    session = repository.create_revision_session(scope, ready_id)
    original = repository.get_working_state(scope, session.id)
    _insert_revision_turn(repository, session.id, original.state_json)
    expected = repository.get_working_state(scope, session.id)
    turn_count = repository.connection.execute(
        "SELECT count(*) FROM director_turns WHERE session_id = ?", (session.id,)
    ).fetchone()[0]
    message_count = repository.connection.execute(
        "SELECT count(*) FROM director_messages WHERE session_id = ?", (session.id,)
    ).fetchone()[0]
    repository.connection.execute("DROP TRIGGER director_working_state_delete_guard")
    repository.connection.execute("DELETE FROM director_working_state WHERE session_id = ?", (session.id,))
    repository.connection.commit()
    assert repository.recover_working_state(scope, session.id).state_json == expected.state_json
    repository.connection.execute("DROP TRIGGER director_working_state_update_guard")
    repository.connection.execute(
        "UPDATE director_working_state SET state_json = '{}', state_sha256 = ? WHERE session_id = ?",
        ("0" * 64, session.id),
    )
    repository.connection.commit()
    repaired = repository.recover_working_state(scope, session.id)
    assert repaired.state_version == expected.state_version
    assert repaired.state_sha256 == expected.state_sha256
    assert repository.connection.execute(
        "SELECT count(*) FROM director_turns WHERE session_id = ?", (session.id,)
    ).fetchone()[0] == turn_count
    assert repository.connection.execute(
        "SELECT count(*) FROM director_messages WHERE session_id = ?", (session.id,)
    ).fetchone()[0] == message_count


def test_recover_ready_state_from_ready_turn_and_reject_arbitrary_same_version_update(
    repository: DirectorRepository,
) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session_id, ready_id, _ = _finish_source(repository, scope)
    expected = repository.get_working_state(scope, session_id)
    repository.connection.execute("DROP TRIGGER director_working_state_delete_guard")
    repository.connection.execute("DELETE FROM director_working_state WHERE session_id = ?", (session_id,))
    repository.connection.commit()
    recovered = repository.recover_working_state(scope, session_id)
    assert recovered.stage == "READY"
    assert recovered.latest_successful_turn_id == expected.latest_successful_turn_id
    assert repository.get_ready_content(scope, ready_id)["final_content_json"] == recovered.state_json["draft"]["content"]

    changed = dict(recovered.state_json)
    changed["ai_judgments"] = [{"item_id": uid(), "judgment_kind": "STRUCTURE", "statement": "篡改"}]
    with pytest.raises(sqlite3.IntegrityError):
        repository.connection.execute(
            "UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?",
            (canonical_text(changed), state_sha256(recovered.state_version, recovered.stage, changed), session_id),
        )


def test_recovery_fails_closed_when_maximum_turn_snapshot_is_damaged(
    repository: DirectorRepository,
) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    session_id, _, _ = _finish_source(repository, scope)
    turn_id = repository.connection.execute(
        "SELECT id FROM director_turns WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    repository.connection.execute("DROP TRIGGER director_turns_update_guard")
    repository.connection.execute(
        "UPDATE director_turns SET post_state_sha256 = ? WHERE id = ?", ("0" * 64, turn_id)
    )
    repository.connection.execute("DROP TRIGGER director_working_state_delete_guard")
    repository.connection.execute("DELETE FROM director_working_state WHERE session_id = ?", (session_id,))
    repository.connection.commit()
    with pytest.raises(DirectorIntegrityError):
        repository.recover_working_state(scope, session_id)
    assert repository.connection.execute(
        "SELECT count(*) FROM director_working_state WHERE session_id = ?", (session_id,)
    ).fetchone()[0] == 0
