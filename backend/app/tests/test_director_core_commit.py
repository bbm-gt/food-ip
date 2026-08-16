from copy import deepcopy
from dataclasses import replace

import pytest

from backend.app.director_core.database import apply_migrations, connect
from backend.app.director_core.canonical import canonical_sha256
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


def _authoritative_snapshot(repository: DirectorRepository) -> dict[str, tuple[tuple, ...]]:
    """Capture complete rows for every Phase 1B authoritative table."""
    snapshot: dict[str, tuple[tuple, ...]] = {}
    for table in (
        "director_sessions", "director_turns", "director_messages",
        "director_working_state", "director_ready_content", "director_context_checkpoints",
    ):
        rows = repository.connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        snapshot[table] = tuple(tuple(row) for row in rows)
    return snapshot


def _state_with_evidence(target_id: str, target_session_id: str) -> dict:
    state = deepcopy(valid_ready_command().post_state)
    state["material_state"] = {"status": "UNKNOWN", "required_confirmations": []}
    state["draft"] = None
    state["review"] = None
    state["direction"]["evidence_refs"] = [{
        "evidence_type": "owner_message",
        "target_id": target_id,
        "target_session_id": target_session_id,
    }]
    return state


def _ready_prepared(
    repository: DirectorRepository, scope: AuthorizationScope, session_id: str, *, client_id: str,
    state_version: int = 0,
):
    """Prepare a legal READY candidate before the Session becomes READY."""
    session = repository.get_session(scope, session_id)
    template = valid_ready_command()
    state = deepcopy(template.post_state)
    state["direction"]["evidence_refs"][0].update(
        target_id=template.owner_message_id, target_session_id=session_id
    )
    command = replace(
        template,
        session_id=session_id,
        client_message_id=client_id,
        expected_state_version=state_version,
        post_state=state,
        execution_trace={"format_version": 1, "steps": [
            {"step_no": 1, "entered_stage": "EXPLORE", "run_control": "CONTINUE", "target_stage": "DEEPEN", "transition_reason_code": "DIRECTION_CONFIRMED", "gate": None, "review": None, "candidate_revision": 1},
            {"step_no": 2, "entered_stage": "DEEPEN", "run_control": "CONTINUE", "target_stage": "CREATE", "transition_reason_code": "MATERIAL_SUFFICIENT", "gate": None, "review": None, "candidate_revision": 2},
            {"step_no": 3, "entered_stage": "CREATE", "run_control": "CONTINUE", "target_stage": "REVIEW", "transition_reason_code": "DRAFT_CREATED", "gate": None, "review": None, "candidate_revision": 3},
            {"step_no": 4, "entered_stage": "REVIEW", "run_control": "READY", "target_stage": "READY", "transition_reason_code": "REVIEW_PASSED", "gate": {"outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": "内容完整、真实且可拍。"}, "review": {"outcome": "PASSED", "root_cause": None}, "candidate_revision": 4},
        ]},
    )
    return prepare_successful_turn(
        command, current_state_version=state_version, current_max_message_seq=2 * state_version,
        current_stage=session.lifecycle_status if session.lifecycle_status == "REVIEW" else "EXPLORE",
        source_ready_content_id=session.source_ready_content_id,
    )


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
    other_json = '{"owner_text":"other","parameters":{}}'
    for request in (
        replace(base, request_format_version=2),
        replace(base, request_sha256="0" * 64),
        replace(base, normalized_request_json=other_json),
        replace(
            base, normalized_request_json=other_json,
            request_sha256=canonical_sha256({"owner_text": "other", "parameters": {}}),
        ),
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
    after_ready = replace(
        valid_non_ready_command(), session_id=session.id, client_message_id="after-ready",
        expected_state_version=0,
    )
    prepared_after_ready = prepare_successful_turn(
        after_ready, current_state_version=0, current_max_message_seq=0,
        current_stage="EXPLORE", source_ready_content_id=None,
    )
    result = repository.commit_successful_turn(scope, prepared)
    assert result.replayed is False and result.response["ready_content_id"] == prepared.ready_content_id
    assert repository.get_session(scope, session.id).lifecycle_status == "READY"
    assert repository.get_working_state(scope, session.id).stage == "READY"
    ready = repository.get_ready_content(scope, prepared.ready_content_id)
    assert ready["final_content_json"] == prepared.ready_content
    before = _authoritative_snapshot(repository)
    with pytest.raises(DirectorExecutionValidationError):
        repository.commit_successful_turn(scope, prepared_after_ready)
    assert _authoritative_snapshot(repository) == before


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


@pytest.mark.parametrize("role", ["OWNER", "DIRECTOR"])
def test_message_insert_failure_rolls_back_turn_and_all_authority(
    repository: DirectorRepository, role: str
) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _prepared(repository, scope, session.id)
    before = _authoritative_snapshot(repository)
    repository.connection.execute(
        f"""CREATE TRIGGER fail_{role.lower()}_message_insert
            BEFORE INSERT ON director_messages
            WHEN NEW.visible_role = '{role}'
            BEGIN SELECT RAISE(ABORT, 'injected {role} message failure'); END"""
    )
    with pytest.raises(Exception, match=f"injected {role} message failure"):
        repository.commit_successful_turn(scope, prepared)
    assert _authoritative_snapshot(repository) == before


def test_evidence_validation_failure_after_both_messages_rolls_back(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    missing_owner_id = "00000000-0000-4000-8000-000000000001"
    command = replace(
        valid_non_ready_command(), session_id=session.id, expected_state_version=0,
        post_state=_state_with_evidence(missing_owner_id, session.id),
    )
    prepared = prepare_successful_turn(
        command, current_state_version=0, current_max_message_seq=0,
        current_stage="EXPLORE", source_ready_content_id=None,
    )
    before = _authoritative_snapshot(repository)
    with pytest.raises(DirectorIntegrityError, match="Evidence"):
        repository.commit_successful_turn(scope, prepared)
    assert _authoritative_snapshot(repository) == before


def test_working_state_cas_failure_after_evidence_rolls_back(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _prepared(repository, scope, session.id)
    before = _authoritative_snapshot(repository)
    repository.connection.execute("""CREATE TRIGGER fail_working_state_cas
        BEFORE UPDATE ON director_working_state
        BEGIN SELECT RAISE(ABORT, 'injected CAS failure'); END""")
    with pytest.raises(Exception, match="injected CAS failure"):
        repository.commit_successful_turn(scope, prepared)
    assert _authoritative_snapshot(repository) == before


def test_ready_content_insert_failure_rolls_back_working_state_and_lifecycle(
    repository: DirectorRepository,
) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _ready_prepared(repository, scope, session.id, client_id="ready-insert-failure")
    before = _authoritative_snapshot(repository)
    repository.connection.execute("""CREATE TRIGGER fail_ready_content_insert
        BEFORE INSERT ON director_ready_content
        BEGIN SELECT RAISE(ABORT, 'injected ReadyContent failure'); END""")
    with pytest.raises(Exception, match="injected ReadyContent failure"):
        repository.commit_successful_turn(scope, prepared)
    assert _authoritative_snapshot(repository) == before
    assert repository.get_session(scope, session.id).lifecycle_status == "ACTIVE"


def test_ready_session_transition_failure_rolls_back_ready_content_and_turn(
    repository: DirectorRepository,
) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _ready_prepared(repository, scope, session.id, client_id="ready-session-failure")
    before = _authoritative_snapshot(repository)
    repository.connection.execute("""CREATE TRIGGER fail_ready_session_update
        BEFORE UPDATE ON director_sessions
        WHEN NEW.lifecycle_status = 'READY'
        BEGIN SELECT RAISE(ABORT, 'injected Session READY failure'); END""")
    with pytest.raises(Exception, match="injected Session READY failure"):
        repository.commit_successful_turn(scope, prepared)
    assert _authoritative_snapshot(repository) == before
    assert repository.get_session(scope, session.id).lifecycle_status == "ACTIVE"


def test_final_closure_failure_before_commit_rolls_back_everything(
    repository: DirectorRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _prepared(repository, scope, session.id)
    before = _authoritative_snapshot(repository)
    original = repository._validate_recovery_turn

    def fail_final(session_record, row, *, require_current_lifecycle, **kwargs):
        if row["id"] == prepared.turn_id:
            raise DirectorIntegrityError("injected final closure failure")
        return original(session_record, row, require_current_lifecycle=require_current_lifecycle, **kwargs)

    monkeypatch.setattr(repository, "_validate_recovery_turn", fail_final)
    with pytest.raises(DirectorIntegrityError, match="injected final closure failure"):
        repository.commit_successful_turn(scope, prepared)
    assert _authoritative_snapshot(repository) == before


def test_normalized_request_variants_replay_without_writing(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    first_command = replace(
        valid_non_ready_command(), session_id=session.id, client_message_id="normalized",
        expected_state_version=0, owner_message="A\r\nB",
    )
    first = prepare_successful_turn(
        first_command, current_state_version=0, current_max_message_seq=0,
        current_stage="EXPLORE", source_ready_content_id=None,
    )
    original = repository.commit_successful_turn(scope, first)
    before = _authoritative_snapshot(repository)
    for owner_message in ("A\nB", "A\rB"):
        variant = replace(
            first_command,
            owner_message=owner_message,
            expected_state_version=1,
            turn_id=first.turn_id,
            owner_message_id=first.owner_message_id,
            director_message_id=first.director_message_id,
        )
        candidate = prepare_successful_turn(
            variant, current_state_version=1, current_max_message_seq=2,
            current_stage="EXPLORE", source_ready_content_id=None,
        )
        replay = repository.commit_successful_turn(scope, candidate)
        assert replay.replayed is True and replay.first_response_json == original.first_response_json
    assert _authoritative_snapshot(repository) == before
    owner = repository.get_complete_message_turns(scope, session.id)[0]["owner"]["content"]
    assert owner == "A\r\nB"

    nfc_first_command = replace(
        valid_non_ready_command(), session_id=session.id, client_message_id="nfc",
        expected_state_version=1, owner_message="é",
    )
    nfc_first = prepare_successful_turn(
        nfc_first_command, current_state_version=1, current_max_message_seq=2,
        current_stage="EXPLORE", source_ready_content_id=None,
    )
    repository.commit_successful_turn(scope, nfc_first)
    nfc_variant = replace(
        nfc_first_command, owner_message="e\u0301", expected_state_version=2,
    )
    nfc_candidate = prepare_successful_turn(
        nfc_variant, current_state_version=2, current_max_message_seq=4,
        current_stage="EXPLORE", source_ready_content_id=None,
    )
    assert repository.commit_successful_turn(scope, nfc_candidate).replayed is True


def test_expected_state_version_is_not_part_of_idempotency_identity(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    first = _prepared(repository, scope, session.id, client_id="version-independent")
    original = repository.commit_successful_turn(scope, first)
    second_command = replace(
        valid_non_ready_command(), session_id=session.id, client_message_id="version-independent",
        expected_state_version=1,
    )
    second = prepare_successful_turn(
        second_command, current_state_version=1, current_max_message_seq=2,
        current_stage="EXPLORE", source_ready_content_id=None,
    )
    replay = repository.commit_successful_turn(scope, second)
    assert replay.replayed is True and replay.first_response_json == original.first_response_json


def test_history_non_ready_turn_replays_after_session_reaches_ready(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    first = _prepared(repository, scope, session.id, client_id="historical")
    original = repository.commit_successful_turn(scope, first)
    ready_prepared = _ready_prepared(
        repository, scope, session.id, client_id="ready-after-history", state_version=1
    )
    repository.commit_successful_turn(scope, ready_prepared)
    assert repository.get_session(scope, session.id).lifecycle_status == "READY"
    before = _authoritative_snapshot(repository)
    replay = repository.precheck_successful_turn(
        scope, prepare_idempotency_request(session.id, "historical", 1, first.owner_message, {})
    )
    assert replay is not None and replay.replayed is True
    assert replay.first_response_json == original.first_response_json
    assert _authoritative_snapshot(repository) == before


def test_manual_idempotency_request_is_rejected_on_database_miss(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    request = PreparedIdempotencyRequest(
        session_id=session.id, client_message_id="manual", request_format_version=2,
        normalized_request_json='{"owner_text":"x","parameters":{}}', request_sha256="0" * 64,
    )
    with pytest.raises(DirectorExecutionValidationError):
        repository.precheck_successful_turn(scope, request)


@pytest.mark.parametrize("corruption", ["missing", "canonical", "hash", "latest_link"])
def test_authoritative_working_state_corruption_fails_closed(
    repository: DirectorRepository, corruption: str
) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    prepared = _prepared(repository, scope, session.id)
    conn = repository.connection
    conn.execute("DROP TRIGGER director_working_state_update_guard")
    if corruption == "missing":
        conn.execute("DROP TRIGGER director_working_state_delete_guard")
        conn.execute("DELETE FROM director_working_state WHERE session_id = ?", (session.id,))
    elif corruption == "canonical":
        conn.execute(
            "UPDATE director_working_state SET state_json = ? WHERE session_id = ?",
            ('{"format_version":1}', session.id),
        )
    elif corruption == "hash":
        conn.execute(
            "UPDATE director_working_state SET state_sha256 = ? WHERE session_id = ?",
            ("0" * 64, session.id),
        )
    else:
        first_turn_id = prepared.turn_id
        repository.commit_successful_turn(scope, prepared)
        second = _prepared(repository, scope, session.id, client_id="link-second")
        repository.commit_successful_turn(scope, second)
        prepared = _prepared(repository, scope, session.id, client_id="after-link-corruption")
        conn.execute(
            "UPDATE director_working_state SET latest_successful_turn_id = ? WHERE session_id = ?",
            (first_turn_id, session.id),
        )
    conn.commit()
    before = _authoritative_snapshot(repository)
    with pytest.raises(DirectorIntegrityError):
        repository.commit_successful_turn(scope, prepared)
    assert _authoritative_snapshot(repository) == before


def test_authoritative_message_sequence_mismatch_fails_closed(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    first = _prepared(repository, scope, session.id, client_id="seq-first")
    repository.commit_successful_turn(scope, first)
    second = _prepared(repository, scope, session.id, client_id="seq-second")
    repository.connection.execute("DROP TRIGGER director_messages_update_guard")
    repository.connection.execute(
        "UPDATE director_messages SET message_seq = 99 WHERE id = ?", (first.director_message_id,)
    )
    repository.connection.commit()
    before = _authoritative_snapshot(repository)
    with pytest.raises(DirectorIntegrityError, match="Message sequence"):
        repository.commit_successful_turn(scope, second)
    assert _authoritative_snapshot(repository) == before


def test_authoritative_pre_stage_mismatch_fails_closed(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    command = replace(
        valid_non_ready_command(), session_id=session.id, expected_state_version=0,
        target_stage="DEEPEN", transition_reason_code="OWNER_INPUT_REQUIRED",
        execution_trace={"format_version": 1, "steps": [{
            "step_no": 1, "entered_stage": "DEEPEN", "run_control": "WAIT_FOR_OWNER",
            "target_stage": "DEEPEN", "transition_reason_code": "OWNER_INPUT_REQUIRED",
                "gate": {"outcome": "BLOCKED", "gate_code": "MATERIAL_INSUFFICIENT", "explanation": "素材不足。"},
                "review": None, "candidate_revision": 1,
        }]},
    )
    prepared = prepare_successful_turn(
        command, current_state_version=0, current_max_message_seq=0,
        current_stage="DEEPEN", source_ready_content_id=None,
    )
    before = _authoritative_snapshot(repository)
    with pytest.raises(DirectorIntegrityError, match="pre-stage"):
        repository.commit_successful_turn(scope, prepared)
    assert _authoritative_snapshot(repository) == before


def test_evidence_current_history_and_invalid_targets_are_closed(repository: DirectorRepository) -> None:
    scope = _scope()
    session = repository.create_session(scope)
    first = _prepared(repository, scope, session.id, client_id="evidence-history")
    repository.commit_successful_turn(scope, first)

    historical_state = _state_with_evidence(first.owner_message_id, session.id)
    second_command = replace(
        valid_non_ready_command(), session_id=session.id, client_message_id="evidence-current",
        expected_state_version=1, post_state=historical_state,
    )
    second = prepare_successful_turn(
        second_command, current_state_version=1, current_max_message_seq=2,
        current_stage="EXPLORE", source_ready_content_id=None,
    )
    assert repository.commit_successful_turn(scope, second).replayed is False

    for target_id, target_session_id in (
        (second.director_message_id, session.id),
        ("00000000-0000-4000-8000-000000000002", session.id),
    ):
        bad_command = replace(
            valid_non_ready_command(), session_id=session.id,
            client_message_id=f"bad-evidence-{target_id}", expected_state_version=2,
            post_state=_state_with_evidence(target_id, target_session_id),
        )
        bad = prepare_successful_turn(
            bad_command, current_state_version=2, current_max_message_seq=4,
            current_stage="EXPLORE", source_ready_content_id=None,
        )
        before = _authoritative_snapshot(repository)
        with pytest.raises(DirectorIntegrityError, match="Evidence"):
            repository.commit_successful_turn(scope, bad)
        assert _authoritative_snapshot(repository) == before
