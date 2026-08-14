from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import replace

import pytest

from backend.app.director_core.concurrency import SessionLockManager
from backend.app.director_core.database import (
    InvalidBusyTimeoutError,
    apply_migrations,
    connect,
    enable_and_verify_foreign_keys,
)
from backend.app.director_core.execution import prepare_successful_turn
from backend.app.director_core.repository import (
    AuthorizationScope,
    CommitOutcomeIndeterminateError,
    CommitRolledBackError,
    DirectorRepository,
    IdempotencyConflictError,
    SQLiteBusyError,
)
from backend.app.tests.test_director_core_execution import valid_non_ready_command


TABLES = (
    "director_sessions",
    "director_messages",
    "director_working_state",
    "director_turns",
    "director_context_checkpoints",
    "director_ready_content",
)


def _snapshot(repository: DirectorRepository) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {
        table: tuple(
            tuple(row)
            for row in repository.connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        )
        for table in TABLES
    }


def _prepare(repository: DirectorRepository, session_id: str, client_id: str):
    scope = AuthorizationScope("workspace-1", "project-1")
    state = repository.get_working_state(scope, session_id)
    command = replace(
        valid_non_ready_command(),
        session_id=session_id,
        client_message_id=client_id,
        expected_state_version=state.state_version,
    )
    return prepare_successful_turn(
        command,
        current_state_version=state.state_version,
        current_max_message_seq=2 * state.state_version,
        current_stage=state.stage,
        source_ready_content_id=None,
    )


@pytest.fixture
def file_repositories(tmp_path):
    path = tmp_path / "director.sqlite"
    first = connect(path, busy_timeout_ms=100, check_same_thread=False)
    apply_migrations(first)
    second = connect(path, busy_timeout_ms=100, check_same_thread=False)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = DirectorRepository(first).create_session(scope)
    return path, scope, session.id, DirectorRepository(first), DirectorRepository(second)


def test_session_lock_manager_serializes_reenters_and_cleans_entries() -> None:
    manager = SessionLockManager()
    session = "  Session-A  "
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def first_worker() -> None:
        with manager.lock(session):
            entered.set()
            with manager.lock("session-a"):
                assert manager.has_entry(session)
            release.wait(2)

    def second_worker() -> None:
        entered.wait(2)
        with manager.lock("SESSION-A"):
            second_entered.set()

    first = threading.Thread(target=first_worker)
    second = threading.Thread(target=second_worker)
    first.start()
    assert entered.wait(2)
    second.start()
    assert not second_entered.wait(0.05)
    release.set()
    first.join(2)
    second.join(2)
    assert second_entered.is_set()
    assert len(manager) == 0

    both = threading.Barrier(2)
    inside = threading.Barrier(2)

    def independent(session_id: str) -> None:
        with manager.lock(session_id):
            both.wait(2)
            inside.wait(2)

    threads = [threading.Thread(target=independent, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert len(manager) == 0


def test_same_session_same_request_is_one_turn_and_exact_replay(file_repositories) -> None:
    _, scope, session_id, first, second = file_repositories
    prepared = _prepare(first, session_id, "same-request")
    results: list[object] = [None, None]
    barrier = threading.Barrier(2)

    def submit(index: int, repository: DirectorRepository) -> None:
        try:
            barrier.wait(2)
            results[index] = repository.commit_successful_turn(scope, prepared)
        except BaseException as exc:  # pragma: no cover - surfaced below
            results[index] = exc

    threads = [
        threading.Thread(target=submit, args=(0, first)),
        threading.Thread(target=submit, args=(1, second)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert all(not isinstance(result, BaseException) for result in results), repr(results)
    assert sorted(result.replayed for result in results) == [False, True]
    assert first.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 1
    assert first.connection.execute("SELECT count(*) FROM director_messages").fetchone()[0] == 2
    assert first.get_working_state(scope, session_id).state_version == 1


def test_same_pre_state_different_requests_cannot_both_advance(file_repositories) -> None:
    _, scope, session_id, first, second = file_repositories
    first_candidate = _prepare(first, session_id, "message-a")
    second_candidate = _prepare(first, session_id, "message-b")
    results: list[object] = [None, None]
    barrier = threading.Barrier(2)

    def submit(index: int, repository: DirectorRepository, candidate) -> None:
        try:
            barrier.wait(2)
            results[index] = repository.commit_successful_turn(scope, candidate)
        except BaseException as exc:
            results[index] = exc

    threads = [
        threading.Thread(target=submit, args=(0, first, first_candidate)),
        threading.Thread(target=submit, args=(1, second, second_candidate)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert sum(not isinstance(result, BaseException) for result in results) == 1, repr(results)
    assert sum(isinstance(result, Exception) for result in results) == 1
    assert first.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 1
    assert first.connection.execute("SELECT count(*) FROM director_messages").fetchone()[0] == 2
    assert first.get_working_state(scope, session_id).state_version == 1


def test_busy_timeout_is_bounded_and_original_request_can_retry(file_repositories) -> None:
    path, scope, session_id, first, second = file_repositories
    candidate = _prepare(first, session_id, "busy-request")
    before = _snapshot(second)
    first.connection.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(SQLiteBusyError):
            second.commit_successful_turn(scope, candidate)
    finally:
        first.connection.rollback()
    assert time.monotonic() - started < 1
    assert _snapshot(second) == before
    assert second.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 0
    assert second.get_working_state(scope, session_id).state_version == 0
    assert second.commit_successful_turn(scope, candidate).replayed is False
    probe = connect(path, busy_timeout_ms=0)
    try:
        assert probe.execute("PRAGMA busy_timeout").fetchone()[0] == 0
    finally:
        probe.close()


def test_begin_preflight_read_busy_is_bounded_and_retryable(file_repositories) -> None:
    path, scope, session_id, first, second = file_repositories
    candidate = _prepare(first, session_id, "preflight-busy")
    before = _snapshot(second)
    first.connection.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    try:
        with pytest.raises(SQLiteBusyError):
            second.commit_successful_turn(scope, candidate)
    finally:
        first.connection.rollback()
    assert time.monotonic() - started < 1
    assert _snapshot(second) == before
    assert second.commit_successful_turn(scope, candidate).replayed is False
    assert second.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 1


@pytest.mark.parametrize("value", [-1, True, 1.5, "10", None])
def test_busy_timeout_rejects_ambiguous_values(value) -> None:
    with pytest.raises(InvalidBusyTimeoutError):
        connect(":memory:", busy_timeout_ms=value)


class _CommitFaultConnection(sqlite3.Connection):
    mode: str | None = None

    def commit(self) -> None:
        mode = self.mode
        if mode == "before":
            self.mode = None
            raise sqlite3.OperationalError("injected commit failure before COMMIT")
        super().commit()
        if mode == "after":
            self.mode = None
            raise sqlite3.OperationalError("injected response loss after COMMIT")


class _ReplayCommitGuardConnection(sqlite3.Connection):
    commit_calls = 0
    rollback_calls = 0
    reject_commit = False

    def commit(self) -> None:
        type(self).commit_calls += 1
        if self.reject_commit:
            raise AssertionError("replay path must not call COMMIT")
        super().commit()

    def rollback(self) -> None:
        type(self).rollback_calls += 1
        super().rollback()


class _FreshQueryBusyConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if "FROM director_turns" in sql:
            raise sqlite3.OperationalError("database is locked")
        return super().execute(sql, parameters)


def _fault_connection(path, mode: str):
    connection = sqlite3.connect(path, timeout=0, factory=_CommitFaultConnection)
    connection.row_factory = sqlite3.Row
    enable_and_verify_foreign_keys(connection)
    connection.mode = mode
    return connection


def test_transaction_replay_ends_with_rollback_without_commit(tmp_path) -> None:
    path = tmp_path / "replay-no-commit.sqlite"
    _ReplayCommitGuardConnection.commit_calls = 0
    _ReplayCommitGuardConnection.rollback_calls = 0
    original = sqlite3.connect(path, timeout=0, factory=_ReplayCommitGuardConnection)
    original.row_factory = sqlite3.Row
    enable_and_verify_foreign_keys(original)
    apply_migrations(original)
    scope = AuthorizationScope("workspace-1", "project-1")
    repository = DirectorRepository(original)
    session = repository.create_session(scope)
    candidate = _prepare(repository, session.id, "replay-no-commit")
    first = repository.commit_successful_turn(scope, candidate)
    before = _snapshot(repository)
    commits_before_replay = _ReplayCommitGuardConnection.commit_calls
    original.reject_commit = True

    replay = repository.commit_successful_turn(scope, candidate)

    assert replay.replayed is True
    assert replay.first_response_json == first.first_response_json
    assert _snapshot(repository) == before
    assert _ReplayCommitGuardConnection.commit_calls == commits_before_replay
    assert _ReplayCommitGuardConnection.rollback_calls >= 1
    assert not original.in_transaction


def test_commit_after_durable_write_replays_from_fresh_connection(tmp_path) -> None:
    path = tmp_path / "commit-after.sqlite"
    original = _fault_connection(path, "none")
    apply_migrations(original)
    scope = AuthorizationScope("workspace-1", "project-1")
    repository = DirectorRepository(
        original,
        fresh_connection_factory=lambda: connect(path, busy_timeout_ms=0),
    )
    session = repository.create_session(scope)
    candidate = _prepare(repository, session.id, "commit-after")
    original.mode = "after"
    result = repository.commit_successful_turn(scope, candidate)
    assert result.replayed is True
    assert repository.connection is not original
    assert repository.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 1
    assert repository.connection.execute("SELECT count(*) FROM director_messages").fetchone()[0] == 2


def test_commit_before_write_is_proven_rolled_back_from_fresh_connection(tmp_path) -> None:
    path = tmp_path / "commit-before.sqlite"
    original = _fault_connection(path, "none")
    apply_migrations(original)
    scope = AuthorizationScope("workspace-1", "project-1")
    repository = DirectorRepository(
        original,
        fresh_connection_factory=lambda: connect(path, busy_timeout_ms=0),
    )
    session = repository.create_session(scope)
    candidate = _prepare(repository, session.id, "commit-before")
    before = _snapshot(repository)
    original.mode = "before"
    with pytest.raises(CommitRolledBackError):
        repository.commit_successful_turn(scope, candidate)
    assert _snapshot(repository) == before


@pytest.mark.parametrize("factory_kind", ["missing", "raises", "same"])
def test_commit_disambiguation_failures_are_indeterminate(tmp_path, factory_kind: str) -> None:
    path = tmp_path / f"indeterminate-{factory_kind}.sqlite"
    original = _fault_connection(path, "none")
    apply_migrations(original)
    scope = AuthorizationScope("workspace-1", "project-1")
    if factory_kind == "missing":
        repository = DirectorRepository(original)
    elif factory_kind == "raises":
        def factory():
            raise OSError("cannot open fresh connection")
        repository = DirectorRepository(original, fresh_connection_factory=factory)
    else:
        repository = DirectorRepository(original, fresh_connection_factory=lambda: original)
    session = repository.create_session(scope)
    candidate = _prepare(repository, session.id, f"indeterminate-{factory_kind}")
    original.mode = "after"
    with pytest.raises(CommitOutcomeIndeterminateError):
        repository.commit_successful_turn(scope, candidate)
    assert repository.connection is original


def test_disambiguation_conflict_never_replays_different_request(tmp_path) -> None:
    path = tmp_path / "conflict.sqlite"
    original = _fault_connection(path, "none")
    apply_migrations(original)
    scope = AuthorizationScope("workspace-1", "project-1")
    repository = DirectorRepository(
        original,
        fresh_connection_factory=lambda: connect(path, busy_timeout_ms=0),
    )
    session = repository.create_session(scope)
    candidate = _prepare(repository, session.id, "conflict-id")
    original.mode = "none"
    repository.commit_successful_turn(scope, candidate)
    from backend.app.director_core.execution import prepare_idempotency_request

    request = prepare_idempotency_request(session.id, "conflict-id", 1, "different", {})
    with pytest.raises(IdempotencyConflictError):
        repository.resolve_commit_outcome(scope, request)


def test_fresh_connection_query_busy_is_indeterminate(tmp_path) -> None:
    path = tmp_path / "fresh-busy.sqlite"
    original = _fault_connection(path, "none")
    apply_migrations(original)
    scope = AuthorizationScope("workspace-1", "project-1")

    def factory():
        fresh = sqlite3.connect(path, timeout=0, factory=_FreshQueryBusyConnection)
        fresh.row_factory = sqlite3.Row
        enable_and_verify_foreign_keys(fresh)
        return fresh

    repository = DirectorRepository(original, fresh_connection_factory=factory)
    session = repository.create_session(scope)
    candidate = _prepare(repository, session.id, "fresh-busy")
    original.mode = "after"
    with pytest.raises(CommitOutcomeIndeterminateError):
        repository.commit_successful_turn(scope, candidate)
