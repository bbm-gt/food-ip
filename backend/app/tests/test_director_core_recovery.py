from __future__ import annotations

import json
import sqlite3
import time
from copy import deepcopy
from uuid import uuid4

import pytest

from backend.app.director_core.canonical import canonical_sha256, canonical_text, state_sha256
from backend.app.director_core.database import apply_migrations, connect
from backend.app.director_core.repository import (
    AuthorizationScope,
    DirectorIntegrityError,
    DirectorRepository,
    SQLiteBusyError,
)
from backend.app.tests.test_director_core_repository import (
    _drop_working_state,
    _empty_state_for_test,
    _finish_source,
    _insert_revision_turn,
)


def uid() -> str:
    return str(uuid4())


@pytest.fixture
def repository() -> DirectorRepository:
    connection = connect(":memory:")
    apply_migrations(connection)
    return DirectorRepository(connection)


def _all_rows(repository: DirectorRepository) -> dict[str, list[tuple[object, ...]]]:
    tables = (
        "director_sessions",
        "director_messages",
        "director_working_state",
        "director_turns",
        "director_context_checkpoints",
        "director_ready_content",
    )
    return {
        table: [tuple(row) for row in repository.connection.execute(
            f"SELECT * FROM {table} ORDER BY rowid"
        ).fetchall()]
        for table in tables
    }


def _install_fault_trigger(repository: DirectorRepository, operation: str, session_id: str) -> None:
    trigger = f"test_recovery_fault_{operation}"
    repository.connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    table_event = "INSERT" if operation == "insert" else "UPDATE"
    repository.connection.execute(
        f"""CREATE TRIGGER {trigger}
            BEFORE {table_event} ON director_working_state
            WHEN NEW.session_id = '{session_id}'
            BEGIN SELECT RAISE(ABORT, 'injected Working State write failure'); END"""
    )
    repository.connection.commit()


def _remove_fault_trigger(repository: DirectorRepository, operation: str) -> None:
    repository.connection.execute(f"DROP TRIGGER IF EXISTS test_recovery_fault_{operation}")
    repository.connection.commit()


def _corrupt_working_state(repository: DirectorRepository, session_id: str) -> None:
    connection = repository.connection
    connection.execute("DROP TRIGGER director_working_state_update_guard")
    connection.execute(
        "UPDATE director_working_state SET state_json = '{}', state_sha256 = ? WHERE session_id = ?",
        ("0" * 64, session_id),
    )
    connection.commit()
    apply_migrations(connection)


def _ready_recovery_fixture(repository: DirectorRepository) -> tuple[AuthorizationScope, str, str]:
    scope = AuthorizationScope("workspace-a", "project-a")
    session_id, ready_id, _ = _finish_source(repository, scope)
    _drop_working_state(repository, session_id)
    return scope, session_id, ready_id


def test_recovery_preflight_read_busy_is_bounded_and_retryable(tmp_path) -> None:
    path = tmp_path / "recovery-preflight-busy.sqlite"
    first_connection = connect(path, busy_timeout_ms=100)
    apply_migrations(first_connection)
    second_connection = connect(path, busy_timeout_ms=100)
    first = DirectorRepository(first_connection)
    second = DirectorRepository(second_connection)
    scope = AuthorizationScope("workspace-a", "project-a")
    session = first.create_session(scope)
    before = _all_rows(second)

    first_connection.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    try:
        with pytest.raises(SQLiteBusyError):
            second.recover_working_state(scope, session.id)
    finally:
        first_connection.rollback()

    assert time.monotonic() - started < 1
    assert _all_rows(second) == before
    assert second.recover_working_state(scope, session.id).state_version == 0


def _finish_revision_ready(
    repository: DirectorRepository, scope: AuthorizationScope, source_ready_content_id: str
) -> tuple[str, str]:
    """Create a valid READY revision using only the existing six-table contract."""
    session = repository.create_revision_session(scope, source_ready_content_id)
    state = repository.get_working_state(scope, session.id).state_json
    state["draft"] = {
        "draft_id": uid(),
        "content": {"title": "修订版", "script_text": "修订后可直接拍摄。", "shooting_notes": ["拍门店"]},
        "content_status": "FINAL_CANDIDATE",
        "based_on_ready_content_id": source_ready_content_id,
    }
    state["review"] = {
        "review_id": uid(),
        "outcome": "PASSED",
        "root_cause": None,
        "against_draft_id": state["draft"]["draft_id"],
        "against_content": state["draft"]["content"],
    }
    state["material_state"]["status"] = "SUFFICIENT"
    turn_id, owner_id, director_id, ready_id = uid(), uid(), uid(), uid()
    created_at = "2026-01-03T00:00:01.000Z"
    request = {"owner_text": "确认修订", "parameters": {}}
    trace = {"format_version": 1, "steps": [
        {"step_no": 1, "entered_stage": "EXPLORE", "run_control": "CONTINUE", "target_stage": "DEEPEN", "transition_reason_code": "DIRECTION_CONFIRMED", "gate": None, "review": None, "candidate_revision": 1},
        {"step_no": 2, "entered_stage": "DEEPEN", "run_control": "CONTINUE", "target_stage": "CREATE", "transition_reason_code": "MATERIAL_SUFFICIENT", "gate": None, "review": None, "candidate_revision": 2},
        {"step_no": 3, "entered_stage": "CREATE", "run_control": "CONTINUE", "target_stage": "REVIEW", "transition_reason_code": "DRAFT_CREATED", "gate": None, "review": None, "candidate_revision": 3},
        {"step_no": 4, "entered_stage": "REVIEW", "run_control": "READY", "target_stage": "READY", "transition_reason_code": "REVIEW_PASSED", "gate": {"outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": "内容可拍"}, "review": {"outcome": "PASSED", "root_cause": None}, "candidate_revision": 4},
    ]}
    response = {
        "session_id": session.id, "turn_id": turn_id, "owner_message_id": owner_id,
        "director_message_id": director_id, "state_version": 1, "stage": "READY",
        "run_control": "READY", "director_message": "修订版已经可以拍摄。", "ready_content_id": ready_id,
    }
    snapshot = {"snapshot_format_version": 1, "state_version": 1, "stage": "READY", "state_json": state}
    digest = state_sha256(1, "READY", state)
    connection = repository.connection
    with connection:
        connection.execute(
            """INSERT INTO director_turns VALUES
            (?, ?, 'revision-ready', 1, ?, ?, 0, 1, 'READY', 'READY', 'REVIEW_PASSED', 'PASSED', NULL,
             1, ?, 1, ?, 1, ?, ?, ?)""",
            (turn_id, session.id, canonical_text(request), canonical_sha256(request), canonical_text(trace),
             canonical_text(response), canonical_text(snapshot), digest, created_at),
        )
        connection.execute(
            "INSERT INTO director_messages VALUES (?, ?, 1, 'OWNER', '确认修订', ?, ?)",
            (owner_id, session.id, turn_id, created_at),
        )
        connection.execute(
            "INSERT INTO director_messages VALUES (?, ?, 2, 'DIRECTOR', '修订版已经可以拍摄。', ?, ?)",
            (director_id, session.id, turn_id, created_at),
        )
        connection.execute(
            """UPDATE director_working_state SET state_version = 1, stage = 'READY', state_json = ?,
               state_sha256 = ?, latest_successful_turn_id = ?, updated_at = ? WHERE session_id = ?""",
            (canonical_text(state), digest, turn_id, created_at, session.id),
        )
        connection.execute(
            "INSERT INTO director_ready_content VALUES (?, ?, 1, ?, ?, ?)",
            (ready_id, session.id, canonical_text(state["draft"]["content"]), turn_id, created_at),
        )
    return session.id, ready_id


def test_recovery_insert_failure_rolls_back_and_retry_succeeds(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    _, ready_id, _ = _finish_source(repository, scope)
    session = repository.create_revision_session(scope, ready_id)
    _insert_revision_turn(repository, session.id, repository.get_working_state(scope, session.id).state_json)
    expected = repository.get_working_state(scope, session.id)
    _drop_working_state(repository, session.id)
    before = _all_rows(repository)
    _install_fault_trigger(repository, "insert", session.id)
    with pytest.raises(sqlite3.DatabaseError):
        repository.recover_working_state(scope, session.id)
    assert not repository.connection.in_transaction
    assert _all_rows(repository) == before
    _remove_fault_trigger(repository, "insert")
    assert repository.recover_working_state(scope, session.id).state_json == expected.state_json


def test_recovery_update_failure_rolls_back_corrupt_projection(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    _, ready_id, _ = _finish_source(repository, scope)
    session = repository.create_revision_session(scope, ready_id)
    _insert_revision_turn(repository, session.id, repository.get_working_state(scope, session.id).state_json)
    _corrupt_working_state(repository, session.id)
    before = _all_rows(repository)
    _install_fault_trigger(repository, "update", session.id)
    with pytest.raises(sqlite3.DatabaseError):
        repository.recover_working_state(scope, session.id)
    assert not repository.connection.in_transaction
    assert _all_rows(repository) == before
    _remove_fault_trigger(repository, "update")
    assert repository.recover_working_state(scope, session.id).state_version == 1


@pytest.mark.parametrize("missing", [True, False])
def test_final_validation_failure_rolls_back_insert_or_update(
    repository: DirectorRepository, monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    _, ready_id, _ = _finish_source(repository, scope)
    session = repository.create_revision_session(scope, ready_id)
    _insert_revision_turn(repository, session.id, repository.get_working_state(scope, session.id).state_json)
    if missing:
        _drop_working_state(repository, session.id)
    else:
        _corrupt_working_state(repository, session.id)
    before = _all_rows(repository)

    def fail_after_write(*_args: object, **_kwargs: object) -> object:
        raise DirectorIntegrityError("injected final validation failure")

    monkeypatch.setattr(repository, "_working_state_from_row", fail_after_write)
    with pytest.raises(DirectorIntegrityError):
        repository.recover_working_state(scope, session.id)
    assert not repository.connection.in_transaction
    assert _all_rows(repository) == before


def test_successful_recovery_changes_only_working_state(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    _, ready_id, _ = _finish_source(repository, scope)
    session = repository.create_revision_session(scope, ready_id)
    _insert_revision_turn(repository, session.id, repository.get_working_state(scope, session.id).state_json)
    expected = repository.get_working_state(scope, session.id)
    _drop_working_state(repository, session.id)
    before = _all_rows(repository)
    recovered = repository.recover_working_state(scope, session.id)
    after = _all_rows(repository)
    assert recovered.state_json == expected.state_json
    for table in before:
        if table != "director_working_state":
            assert after[table] == before[table]
    assert len(after["director_working_state"]) == len(before["director_working_state"]) + 1
    assert after["director_context_checkpoints"] == before["director_context_checkpoints"]


def test_recovery_accepts_historical_explore_wait_with_null_gate(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    _, ready_id, _ = _finish_source(repository, scope)
    session = repository.create_revision_session(scope, ready_id)
    _insert_revision_turn(
        repository, session.id, repository.get_working_state(scope, session.id).state_json
    )
    row = repository.connection.execute(
        "SELECT id, execution_trace_json FROM director_turns WHERE session_id = ?",
        (session.id,),
    ).fetchone()
    trace = json.loads(row["execution_trace_json"])
    trace["steps"][0]["gate"] = None
    repository.connection.execute("DROP TRIGGER director_turns_update_guard")
    repository.connection.execute(
        "UPDATE director_turns SET execution_trace_json = ?, gate_outcome = NULL WHERE id = ?",
        (canonical_text(trace), row["id"]),
    )
    repository.connection.commit()
    apply_migrations(repository.connection)
    _drop_working_state(repository, session.id)

    recovered = repository.recover_working_state(scope, session.id)
    assert recovered.stage == "EXPLORE"
    assert recovered.state_version == 1


@pytest.mark.parametrize("gate", [
    None,
    {"outcome": "PASSED", "gate_code": "CONTENT_INCOMPLETE", "explanation": "错误 Gate。"},
])
def test_recovery_rejects_review_passed_without_exact_readiness_gate(
    repository: DirectorRepository, gate: dict | None
) -> None:
    scope, session_id, _ready_id = _ready_recovery_fixture(repository)
    row = repository.connection.execute(
        "SELECT id, execution_trace_json FROM director_turns WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    trace = json.loads(row["execution_trace_json"])
    trace["steps"][-1]["gate"] = gate
    repository.connection.execute("DROP TRIGGER director_turns_update_guard")
    repository.connection.execute(
        "UPDATE director_turns SET execution_trace_json = ? WHERE id = ?",
        (canonical_text(trace), row["id"]),
    )
    repository.connection.commit()
    apply_migrations(repository.connection)

    with pytest.raises(DirectorIntegrityError):
        repository.recover_working_state(scope, session_id)


def test_recovery_accepts_review_passed_with_exact_readiness_gate(repository: DirectorRepository) -> None:
    scope, session_id, _ready_id = _ready_recovery_fixture(repository)
    recovered = repository.recover_working_state(scope, session_id)
    assert recovered.stage == "READY"


def test_revision_v0_recovery_restores_exact_direct_baseline(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    _, ready_id, _ = _finish_source(repository, scope)
    revision = repository.create_revision_session(scope, ready_id)
    expected = repository.get_working_state(scope, revision.id)
    _drop_working_state(repository, revision.id)
    recovered = repository.recover_working_state(scope, revision.id)
    assert recovered.state_version == 0
    assert recovered.stage == "EXPLORE"
    assert recovered.latest_successful_turn_id is None
    assert recovered.state_json["owner_facts"] == expected.state_json["owner_facts"]
    assert recovered.state_json["owner_constraints"] == expected.state_json["owner_constraints"]
    assert recovered.state_json["direction"] == expected.state_json["direction"]
    assert recovered.state_json["draft"]["content"] == repository.get_ready_content(scope, ready_id)["final_content_json"]
    assert recovered.state_json["draft"]["draft_id"] is None
    assert recovered.state_json["draft"]["based_on_ready_content_id"] == ready_id
    assert recovered.state_json["ai_judgments"] == []
    assert recovered.state_json["unconfirmed_inferences"] == []
    assert recovered.state_json["rejected_items"] == []
    assert recovered.state_json["review"] is None
    assert recovered.state_json["material_state"] == {"status": "UNKNOWN", "required_confirmations": []}


def test_revision_v0_does_not_recurse_to_grandparent(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    source_session_id, source_ready_id, source_state = _finish_source(repository, scope)
    _, middle_ready_id = _finish_revision_ready(repository, scope, source_ready_id)
    middle_state = repository.get_working_state(scope, _session_for_ready(repository, middle_ready_id)).state_json

    # Add a new fact only to A after B has closed.  C must read B's final
    # state, never scan A as an additional inheritance source.
    extra = deepcopy(source_state["owner_facts"][0])
    extra["item_id"] = uid()
    extra["statement"] = "祖先新增事实"
    source_state["owner_facts"].append(extra)
    connection = repository.connection
    connection.execute("DROP TRIGGER director_working_state_update_guard")
    connection.execute(
        "UPDATE director_working_state SET state_json = ?, state_sha256 = ? WHERE session_id = ?",
        (canonical_text(source_state), state_sha256(1, "READY", source_state), source_session_id),
    )
    connection.commit()
    apply_migrations(connection)

    child = repository.create_revision_session(scope, middle_ready_id)
    recovered = repository.get_working_state(scope, child.id)
    def without_inheritance(items: list[dict[str, object]]) -> list[dict[str, object]]:
        return [{key: value for key, value in item.items() if key != "inherited_from"} for item in items]

    assert without_inheritance(recovered.state_json["owner_facts"]) == without_inheritance(middle_state["owner_facts"])
    assert all(
        item["inherited_from"]["source_ready_content_id"] == middle_ready_id
        for item in recovered.state_json["owner_facts"]
    )
    assert all(item["statement"] != "祖先新增事实" for item in recovered.state_json["owner_facts"])


def _session_for_ready(repository: DirectorRepository, ready_id: str) -> str:
    return repository.connection.execute(
        "SELECT session_id FROM director_ready_content WHERE id = ?", (ready_id,)
    ).fetchone()[0]


def _mutate_ready_row(repository: DirectorRepository, ready_id: str, mutation: str) -> None:
    connection = repository.connection
    turn_id = connection.execute(
        "SELECT created_by_turn_id FROM director_ready_content WHERE id = ?", (ready_id,)
    ).fetchone()[0]
    if mutation in {"id", "session_id", "created_at", "final_content", "invalid_content", "missing", "other_turn"}:
        connection.execute("DROP TRIGGER director_ready_content_update_guard")
    if mutation == "id":
        connection.commit(); connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("UPDATE director_ready_content SET id = 'not-a-uuid' WHERE id = ?", (ready_id,))
        connection.commit(); connection.execute("PRAGMA ignore_check_constraints = OFF")
    elif mutation == "session_id":
        other = repository.create_session(AuthorizationScope("workspace-a", "project-a"))
        connection.commit(); connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("UPDATE director_ready_content SET session_id = ? WHERE id = ?", (other.id, ready_id))
        connection.commit(); connection.execute("PRAGMA foreign_keys = ON")
    elif mutation == "created_at":
        connection.commit(); connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("UPDATE director_ready_content SET created_at = 'bad-time' WHERE id = ?", (ready_id,))
        connection.commit(); connection.execute("PRAGMA ignore_check_constraints = OFF")
    elif mutation == "final_content":
        content = json.loads(connection.execute(
            "SELECT final_content_json FROM director_ready_content WHERE id = ?", (ready_id,)
        ).fetchone()[0])
        content["script_text"] = "与 snapshot 不同"
        connection.execute("UPDATE director_ready_content SET final_content_json = ? WHERE id = ?", (canonical_text(content), ready_id))
        connection.commit()
    elif mutation == "invalid_content":
        connection.execute("UPDATE director_ready_content SET final_content_json = ? WHERE id = ?", (canonical_text({"title": "缺字段"}), ready_id))
        connection.commit()
    elif mutation == "missing":
        connection.execute("DROP TRIGGER director_ready_content_delete_guard")
        connection.execute("DELETE FROM director_ready_content WHERE id = ?", (ready_id,))
        connection.commit()
    elif mutation == "other_turn":
        other_scope = AuthorizationScope("workspace-a", "project-a")
        source_id, source_ready_id, _ = _finish_source(repository, other_scope)
        other_session = repository.create_revision_session(other_scope, source_ready_id)
        _insert_revision_turn(repository, other_session.id, repository.get_working_state(other_scope, other_session.id).state_json)
        other_turn = connection.execute(
            "SELECT id FROM director_turns WHERE session_id = ?", (other_session.id,)
        ).fetchone()[0]
        connection.commit(); connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("UPDATE director_ready_content SET created_by_turn_id = ? WHERE id = ?", (other_turn, ready_id))
        connection.commit(); connection.execute("PRAGMA foreign_keys = ON")
    elif mutation == "response_null":
        connection.execute("DROP TRIGGER director_turns_update_guard")
        response = json.loads(connection.execute("SELECT first_response_json FROM director_turns WHERE id = ?", (turn_id,)).fetchone()[0])
        response["ready_content_id"] = None
        connection.execute("UPDATE director_turns SET first_response_json = ? WHERE id = ?", (canonical_text(response), turn_id))
        connection.commit()
    elif mutation == "response_other":
        connection.execute("DROP TRIGGER director_turns_update_guard")
        _, other_ready_id, _ = _finish_source(repository, AuthorizationScope("workspace-a", "project-a"))
        response = json.loads(connection.execute("SELECT first_response_json FROM director_turns WHERE id = ?", (turn_id,)).fetchone()[0])
        response["ready_content_id"] = other_ready_id
        connection.execute("UPDATE director_turns SET first_response_json = ? WHERE id = ?", (canonical_text(response), turn_id))
        connection.commit()
    apply_migrations(connection)


@pytest.mark.parametrize("mutation", [
    "id", "session_id", "created_at", "final_content", "invalid_content", "missing",
    "other_turn", "response_null", "response_other",
])
def test_ready_content_v1_remaining_counterexamples(repository: DirectorRepository, mutation: str) -> None:
    scope, session_id, ready_id = _ready_recovery_fixture(repository)
    _mutate_ready_row(repository, ready_id, mutation)
    with pytest.raises(DirectorIntegrityError):
        repository.recover_working_state(scope, session_id)


def _max_turn_case_repository() -> tuple[DirectorRepository, AuthorizationScope, str]:
    repository = DirectorRepository(connect(":memory:"))
    apply_migrations(repository.connection)
    scope = AuthorizationScope("workspace-a", "project-a")
    _, ready_id, _ = _finish_source(repository, scope)
    session = repository.create_revision_session(scope, ready_id)
    _insert_revision_turn(repository, session.id, repository.get_working_state(scope, session.id).state_json, state_version=1)
    _insert_revision_turn(repository, session.id, repository.get_working_state(scope, session.id).state_json, state_version=2)
    return repository, scope, session.id


@pytest.mark.parametrize("case", [
    "version_low", "version_high", "stage_mismatch", "latest_turn_mismatch", "hash_mismatch",
    "state_json_mismatch", "snapshot_version_mismatch", "snapshot_stage_mismatch", "earlier_turn",
    "other_session_turn",
])
def test_trigger_rejects_maximum_turn_recovery_mismatches(case: str) -> None:
    repository, scope, session_id = _max_turn_case_repository()
    connection = repository.connection
    current = connection.execute("SELECT * FROM director_working_state WHERE session_id = ?", (session_id,)).fetchone()
    max_turn = connection.execute("SELECT * FROM director_turns WHERE session_id = ? ORDER BY post_state_version DESC", (session_id,)).fetchall()[0]
    earlier = connection.execute("SELECT * FROM director_turns WHERE session_id = ? ORDER BY post_state_version", (session_id,)).fetchone()
    values = {
        "state_version": current["state_version"], "stage": current["stage"], "state_json": current["state_json"],
        "state_sha256": current["state_sha256"], "latest_successful_turn_id": current["latest_successful_turn_id"],
    }
    if case == "version_low": values["state_version"] = 1
    elif case == "version_high": values["state_version"] = 3
    elif case == "stage_mismatch": values["stage"] = "DEEPEN"
    elif case == "latest_turn_mismatch": values["latest_successful_turn_id"] = earlier["id"]
    elif case == "hash_mismatch": values["state_sha256"] = "0" * 64
    elif case == "state_json_mismatch": values["state_json"] = canonical_text(_empty_state_for_test())
    elif case == "snapshot_version_mismatch":
        connection.execute("DROP TRIGGER director_turns_update_guard")
        snap = json.loads(max_turn["post_state_snapshot_json"]); snap["state_version"] = 999
        connection.execute("UPDATE director_turns SET post_state_snapshot_json = ? WHERE id = ?", (canonical_text(snap), max_turn["id"]))
        connection.commit(); apply_migrations(connection)
    elif case == "snapshot_stage_mismatch":
        connection.execute("DROP TRIGGER director_turns_update_guard")
        snap = json.loads(max_turn["post_state_snapshot_json"]); snap["stage"] = "DEEPEN"
        connection.execute("UPDATE director_turns SET post_state_snapshot_json = ? WHERE id = ?", (canonical_text(snap), max_turn["id"]))
        connection.commit(); apply_migrations(connection)
    elif case == "earlier_turn":
        values.update(state_version=earlier["post_state_version"], stage=earlier["target_stage"], state_json=earlier["post_state_snapshot_json"], state_sha256=earlier["post_state_sha256"], latest_successful_turn_id=earlier["id"])
    else:
        other = DirectorRepository(connect(":memory:")); apply_migrations(other.connection)
        other_scope = AuthorizationScope("workspace-a", "project-a")
        other_session_id, _, _ = _finish_source(other, other_scope)
        values["latest_successful_turn_id"] = other.connection.execute("SELECT id FROM director_turns WHERE session_id = ?", (other_session_id,)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """UPDATE director_working_state SET state_version = ?, stage = ?, state_json = ?,
               state_sha256 = ?, latest_successful_turn_id = ? WHERE session_id = ?""",
            (values["state_version"], values["stage"], values["state_json"], values["state_sha256"], values["latest_successful_turn_id"], session_id),
        )
    connection.rollback()


@pytest.mark.parametrize("case", [
    "nonready_state_ready_session", "ready_state_active_session", "ready_state_no_content", "ready_content_other_turn",
    "active_state_with_ready_content", "format_version", "has_turn_v0", "ready_v0", "v0_wrong_stage", "v0_latest_turn", "session_id_change",
    "delete_state", "same_version_mismatch", "ready_higher_version",
])
def test_trigger_rejects_lifecycle_v0_and_protection_mismatches(case: str) -> None:
    repository, scope, session_id = _max_turn_case_repository()
    connection = repository.connection
    current = connection.execute("SELECT * FROM director_working_state WHERE session_id = ?", (session_id,)).fetchone()
    max_turn = connection.execute("SELECT * FROM director_turns WHERE session_id = ? ORDER BY post_state_version DESC", (session_id,)).fetchone()
    if case == "nonready_state_ready_session":
        values = (2, "READY", current["state_json"], current["state_sha256"], current["latest_successful_turn_id"])
    elif case == "ready_state_active_session":
        connection.execute("DROP TRIGGER director_sessions_update_guard")
        connection.execute("UPDATE director_sessions SET lifecycle_status = 'ACTIVE', ready_at = NULL WHERE id = ?", (session_id,))
        connection.commit(); apply_migrations(connection)
        values = (2, "READY", current["state_json"], current["state_sha256"], current["latest_successful_turn_id"])
    elif case == "ready_state_no_content":
        values = (2, "READY", current["state_json"], current["state_sha256"], current["latest_successful_turn_id"])
    elif case == "ready_content_other_turn":
        values = (2, current["stage"], current["state_json"], current["state_sha256"], uid())
    elif case == "active_state_with_ready_content":
        ready_id = uid()
        connection.execute(
            "INSERT INTO director_ready_content VALUES (?, ?, 1, ?, ?, ?)",
            (ready_id, session_id, canonical_text({"title": "临时", "script_text": "临时内容", "shooting_notes": []}), max_turn["id"], "2026-01-03T00:00:02.000Z"),
        )
        connection.execute("DROP TRIGGER director_sessions_update_guard")
        connection.execute("UPDATE director_sessions SET lifecycle_status = 'ACTIVE', ready_at = NULL WHERE id = ?", (session_id,))
        connection.commit(); apply_migrations(connection)
        values = (2, current["stage"], current["state_json"], current["state_sha256"], current["latest_successful_turn_id"])
    elif case == "format_version":
        ready_id = connection.execute(
            "SELECT id FROM director_ready_content WHERE session_id = ?", (session_id,)
        ).fetchone()
        # A revision session is ACTIVE, so this is a deliberately malformed
        # READY projection candidate; the trigger must reject it because the
        # associated ReadyContent is not format v1.
        if ready_id is not None:
            connection.execute("DROP TRIGGER director_ready_content_update_guard")
            connection.execute(
                "UPDATE director_ready_content SET content_format_version = 2 WHERE id = ?",
                (ready_id[0],),
            )
            connection.commit(); apply_migrations(connection)
        values = (2, "READY", current["state_json"], current["state_sha256"], current["latest_successful_turn_id"])
    elif case in {"has_turn_v0", "ready_v0", "v0_wrong_stage", "v0_latest_turn"}:
        values = (0, "READY" if case == "ready_v0" else ("DEEPEN" if case == "v0_wrong_stage" else "EXPLORE"), canonical_text(_empty_state_for_test()), state_sha256(0, "EXPLORE", _empty_state_for_test()), current["latest_successful_turn_id"] if case == "v0_latest_turn" else None)
    elif case == "session_id_change":
        values = (current["state_version"], current["stage"], current["state_json"], current["state_sha256"], current["latest_successful_turn_id"])
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE director_working_state SET session_id = ? WHERE session_id = ?", (uid(), session_id))
        connection.rollback(); return
    elif case == "delete_state":
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM director_working_state WHERE session_id = ?", (session_id,))
        connection.rollback(); return
    elif case == "same_version_mismatch":
        values = (current["state_version"], current["stage"], canonical_text(_empty_state_for_test()), "0" * 64, current["latest_successful_turn_id"])
    else:
        values = (current["state_version"] + 1, current["stage"], current["state_json"], current["state_sha256"], current["latest_successful_turn_id"])
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE director_working_state SET state_version = ?, stage = ?, state_json = ?, state_sha256 = ?, latest_successful_turn_id = ? WHERE session_id = ?",
            (*values, session_id),
        )
    connection.rollback()


def test_maximum_snapshot_ignores_invalid_older_history(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    _, ready_id, _ = _finish_source(repository, scope)
    session = repository.create_revision_session(scope, ready_id)
    first_owner = _insert_revision_turn(repository, session.id, repository.get_working_state(scope, session.id).state_json, state_version=1)
    first_turn = repository.connection.execute("SELECT id FROM director_turns WHERE session_id = ? AND post_state_version = 1", (session.id,)).fetchone()[0]
    state = repository.get_working_state(scope, session.id).state_json
    second_owner = _insert_revision_turn(repository, session.id, state, state_version=2)
    connection = repository.connection
    second_turn = connection.execute("SELECT id FROM director_turns WHERE session_id = ? AND post_state_version = 2", (session.id,)).fetchone()[0]
    connection.execute("DROP TRIGGER director_turns_update_guard")
    connection.commit(); connection.execute("PRAGMA ignore_check_constraints = ON")
    connection.execute("UPDATE director_turns SET post_state_snapshot_json = ? WHERE id = ?", ("{}", first_turn))
    connection.commit(); connection.execute("PRAGMA ignore_check_constraints = OFF")
    connection.execute("UPDATE director_turns SET first_response_json = ? WHERE id = ?", ("{}", first_turn))
    connection.commit(); apply_migrations(connection)
    _drop_working_state(repository, session.id)
    assert repository.recover_working_state(scope, session.id).latest_successful_turn_id == second_turn


def test_maximum_snapshot_damaged_evidence_fails_closed(repository: DirectorRepository) -> None:
    scope = AuthorizationScope("workspace-a", "project-a")
    _, ready_id, _ = _finish_source(repository, scope)
    session = repository.create_revision_session(scope, ready_id)
    first_owner = _insert_revision_turn(repository, session.id, repository.get_working_state(scope, session.id).state_json, state_version=1)
    state = repository.get_working_state(scope, session.id).state_json
    state["owner_facts"][0]["evidence_refs"] = [{"evidence_type": "owner_message", "target_id": first_owner, "target_session_id": session.id}]
    _insert_revision_turn(repository, session.id, state, state_version=2)
    first_turn = repository.connection.execute("SELECT id FROM director_turns WHERE session_id = ? AND post_state_version = 1", (session.id,)).fetchone()[0]
    connection = repository.connection
    connection.execute("DROP TRIGGER director_turns_update_guard")
    connection.execute("UPDATE director_turns SET first_response_json = ? WHERE id = ?", ("{}", first_turn))
    connection.commit(); apply_migrations(connection)
    _drop_working_state(repository, session.id)
    with pytest.raises(DirectorIntegrityError):
        repository.recover_working_state(scope, session.id)
