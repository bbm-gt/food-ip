from copy import deepcopy
from dataclasses import replace

import pytest

from backend.app.director_core.database import apply_migrations, connect
from backend.app.director_core.execution import (
    DirectorExecutionValidationError,
    IdempotencyConflictError,
    PreparedIdempotencyRequest,
    StaleStateVersionError,
    prepare_idempotency_request,
    prepare_successful_turn,
)
from backend.app.director_core.repository import AuthorizationScope, DirectorIntegrityError, DirectorRepository
from backend.app.tests.test_director_core_execution import valid_non_ready_command, valid_ready_command


@pytest.fixture
def repository() -> DirectorRepository:
    connection = connect(":memory:")
    apply_migrations(connection)
    return DirectorRepository(connection)


def _scope() -> AuthorizationScope:
    return AuthorizationScope("workspace-1", "project-1")


def _prepared(repository: DirectorRepository, scope: AuthorizationScope, session_id: str, *, client_id: str = "client-1"):
    state = repository.get_working_state(scope, session_id)
    command = replace(
        valid_non_ready_command(), session_id=session_id, client_message_id=client_id,
        expected_state_version=state.state_version,
    )
    return prepare_successful_turn(
        command, current_state_version=state.state_version,
        current_max_message_seq=2 * state.state_version, current_stage=state.stage,
        source_ready_content_id=None,
    )


def _authoritative_counts(repository: DirectorRepository) -> tuple[int, int, int, int, int, int]:
    connection = repository.connection
    return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
        "director_sessions", "director_turns", "director_messages", "director_working_state",
        "director_ready_content", "director_context_checkpoints",
    ))


def test_precheck_miss_and_exact_replay_do_not_write(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _prepared(repository, scope, session.id)
    request = prepare_idempotency_request(
        session.id, prepared.client_message_id, 1, prepared.owner_message, {}
    )
    assert repository.precheck_successful_turn(scope, request) is None
    first = repository.commit_successful_turn(scope, prepared)
    before = _authoritative_counts(repository)
    replay = repository.precheck_successful_turn(scope, request)
    assert replay is not None and replay.replayed is True
    assert replay.first_response_json == first.first_response_json
    assert _authoritative_counts(repository) == before
    state = repository.get_working_state(scope, session.id)
    assert state.state_version == 1 and state.latest_successful_turn_id == prepared.turn_id


def test_second_commit_replays_and_preserves_raw_owner_text(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _prepared(repository, scope, session.id)
    assert repository.commit_successful_turn(scope, prepared).replayed is False
    before = _authoritative_counts(repository)
    replay = repository.commit_successful_turn(scope, prepared)
    assert replay.replayed is True
    assert _authoritative_counts(repository) == before
    pair = repository.get_complete_message_turns(scope, session.id)[0]
    assert pair["owner"]["content"] == prepared.owner_message
    assert (pair["owner"]["message_seq"], pair["director"]["message_seq"]) == (1, 2)


def test_idempotency_conflict_compares_format_hash_and_canonical_request(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _prepared(repository, scope, session.id)
    repository.commit_successful_turn(scope, prepared)
    base = prepare_idempotency_request(session.id, prepared.client_message_id, 1, prepared.owner_message, {})
    for request in (
        replace(base, request_format_version=2),
        replace(base, request_sha256="0" * 64),
        replace(base, normalized_request_json='{"owner_text":"other","parameters":{}}'),
    ):
        with pytest.raises(IdempotencyConflictError):
            repository.precheck_successful_turn(scope, request)


def test_same_client_message_id_is_independent_per_session(repository: DirectorRepository) -> None:
    scope = _scope()
    one, two = repository.create_session(scope), repository.create_session(scope)
    first = _prepared(repository, scope, one.id, client_id="same")
    second = _prepared(repository, scope, two.id, client_id="same")
    repository.commit_successful_turn(scope, first)
    assert repository.precheck_successful_turn(
        scope, prepare_idempotency_request(two.id, "same", 1, second.owner_message, {})
    ) is None
    assert repository.commit_successful_turn(scope, second).replayed is False


def test_ready_commit_closes_content_state_and_session(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    template = valid_ready_command()
    post_state = deepcopy(template.post_state)
    post_state["direction"]["evidence_refs"][0].update(
        target_id=template.owner_message_id, target_session_id=session.id
    )
    command = replace(
        template, session_id=session.id, expected_state_version=0, post_state=post_state,
        execution_trace={"format_version": 1, "steps": [
            {"step_no": 1, "entered_stage": "EXPLORE", "run_control": "CONTINUE", "target_stage": "DEEPEN", "transition_reason_code": "DIRECTION_CONFIRMED", "gate": None, "review": None, "candidate_revision": 1},
            {"step_no": 2, "entered_stage": "DEEPEN", "run_control": "CONTINUE", "target_stage": "CREATE", "transition_reason_code": "MATERIAL_SUFFICIENT", "gate": None, "review": None, "candidate_revision": 2},
            {"step_no": 3, "entered_stage": "CREATE", "run_control": "CONTINUE", "target_stage": "REVIEW", "transition_reason_code": "DRAFT_CREATED", "gate": None, "review": None, "candidate_revision": 3},
            {"step_no": 4, "entered_stage": "REVIEW", "run_control": "READY", "target_stage": "READY", "transition_reason_code": "REVIEW_PASSED", "gate": {"outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": "内容完整、真实且可拍。"}, "review": {"outcome": "PASSED", "root_cause": None}, "candidate_revision": 4},
        ]},
    )
    prepared = prepare_successful_turn(
        command, current_state_version=0, current_max_message_seq=0, current_stage="EXPLORE",
        source_ready_content_id=None,
    )
    result = repository.commit_successful_turn(scope, prepared)
    assert result.replayed is False and result.response["ready_content_id"] == prepared.ready_content_id
    assert repository.get_session(scope, session.id).lifecycle_status == "READY"
    assert repository.get_working_state(scope, session.id).stage == "READY"
    ready = repository.get_ready_content(scope, prepared.ready_content_id)
    assert ready["final_content_json"] == prepared.ready_content
    with pytest.raises(DirectorExecutionValidationError):
        repository.commit_successful_turn(scope, _prepared(repository, scope, session.id, client_id="after-ready"))


def test_stale_and_tampered_prepared_values_roll_back(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _prepared(repository, scope, session.id)
    before = _authoritative_counts(repository)
    with pytest.raises(DirectorExecutionValidationError):
        repository.commit_successful_turn(scope, replace(prepared, post_state_sha256="0" * 64))
    assert _authoritative_counts(repository) == before
    stale = _prepared(repository, scope, session.id, client_id="client-2")
    # A committed prior Turn makes the separately prepared version-zero candidate stale.
    repository.commit_successful_turn(scope, prepared)
    with pytest.raises(StaleStateVersionError):
        repository.commit_successful_turn(scope, stale)
    assert repository.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 1


def test_missing_or_corrupt_working_state_fails_closed_without_recovery(repository: DirectorRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _prepared(repository, scope, session.id)
    repository.connection.execute("DROP TRIGGER director_working_state_delete_guard")
    repository.connection.execute("DELETE FROM director_working_state WHERE session_id = ?", (session.id,))
    repository.connection.commit()
    called = False

    def recovery(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("commit must not recover")

    monkeypatch.setattr(repository, "recover_working_state", recovery)
    with pytest.raises(DirectorIntegrityError):
        repository.commit_successful_turn(scope, prepared)
    assert called is False
    assert repository.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 0
