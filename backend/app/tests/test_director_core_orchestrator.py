from __future__ import annotations

import sqlite3
import threading
from copy import deepcopy
from dataclasses import fields, replace
from uuid import UUID, uuid4

import pytest

from backend.app.director_core.database import (
    apply_migrations,
    connect,
    enable_and_verify_foreign_keys,
)
from backend.app.director_core.execution import (
    DirectorExecutionValidationError,
    IdempotencyConflictError,
    StaleStateVersionError,
)
from backend.app.director_core.orchestrator import (
    DirectorOrchestrator,
    DirectorTurnRequest,
    TurnCandidate,
    TurnOrchestrationContext,
)
from backend.app.director_core.repository import (
    AuthorizationScope,
    CommitOutcomeIndeterminateError,
    CommitRolledBackError,
    DirectorIntegrityError,
    DirectorRepository,
)


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


def snapshot(repository: DirectorRepository) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {
        table: tuple(
            tuple(row)
            for row in repository.connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        )
        for table in TABLES
    }


def request(
    session_id: str,
    client_id: str,
    expected_version: int = 0,
    text: str = "老板说了一个真实细节。",
) -> DirectorTurnRequest:
    return DirectorTurnRequest(
        session_id=session_id,
        client_message_id=client_id,
        expected_state_version=expected_version,
        owner_text=text,
        request_format_version=1,
        parameters={},
    )


def wait_candidate(
    context: TurnOrchestrationContext,
    message: str = "请再补充一个真实细节。",
) -> TurnCandidate:
    stage = context.working_state.stage
    return TurnCandidate(
        director_message=message,
        execution_trace={
            "format_version": 1,
            "steps": [{
                "step_no": 1,
                "entered_stage": stage,
                "run_control": "WAIT_FOR_OWNER",
                "target_stage": stage,
                "transition_reason_code": "OWNER_INPUT_REQUIRED",
                "gate": None,
                "review": None,
                "candidate_revision": 1,
            }],
        },
        post_state=deepcopy(context.working_state.state_json),
        final_run_control="WAIT_FOR_OWNER",
        target_stage=stage,
        transition_reason_code="OWNER_INPUT_REQUIRED",
        gate_outcome=None,
        review_root_cause=None,
        ready_content=None,
    )


@pytest.fixture
def repository(tmp_path):
    path = tmp_path / "orchestrator.sqlite"
    connection = connect(path, busy_timeout_ms=100, check_same_thread=False)
    apply_migrations(connection)
    repository = DirectorRepository(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = repository.create_session(scope)
    return repository, scope, session.id


def test_same_request_replays_and_skips_candidate_builder(repository) -> None:
    repo, scope, session_id = repository
    calls: list[int] = []

    def builder(context):
        calls.append(context.working_state.state_version)
        return wait_candidate(context)

    orchestrator = DirectorOrchestrator(repo, scope, builder)
    first_request = request(session_id, "same-request")
    first = orchestrator.run(first_request)
    before = snapshot(repo)
    replay = orchestrator.run(first_request)

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.first_response_json == first.first_response_json
    assert calls == [0]
    assert snapshot(repo) == before
    assert repo.get_working_state(scope, session_id).state_version == 1


def test_stale_expected_version_fails_before_candidate_builder(repository) -> None:
    repo, scope, session_id = repository
    calls: list[int] = []
    orchestrator = DirectorOrchestrator(
        repo, scope, lambda context: (calls.append(1) or wait_candidate(context))
    )
    before = snapshot(repo)

    with pytest.raises(StaleStateVersionError):
        orchestrator.run(request(session_id, "stale", expected_version=1))

    assert calls == []
    assert snapshot(repo) == before


def test_missing_working_state_recovers_without_model_and_builder_sees_authority(repository) -> None:
    repo, scope, session_id = repository
    repo.connection.execute("DROP TRIGGER director_working_state_delete_guard")
    repo.connection.execute(
        "DELETE FROM director_working_state WHERE session_id = ?", (session_id,)
    )
    repo.connection.commit()
    seen: list[tuple[int, str]] = []

    def builder(context):
        seen.append((context.working_state.state_version, context.working_state.stage))
        return wait_candidate(context)

    result = DirectorOrchestrator(repo, scope, builder).run(
        request(session_id, "recover-missing")
    )

    assert result.replayed is False
    assert seen == [(0, "EXPLORE")]
    assert repo.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 1
    assert repo.connection.execute("SELECT count(*) FROM director_messages").fetchone()[0] == 2
    assert repo.connection.execute("SELECT count(*) FROM director_ready_content").fetchone()[0] == 0
    assert repo.get_working_state(scope, session_id).state_version == 1


def test_hash_mismatched_working_state_recovers_latest_authority_once(repository) -> None:
    repo, scope, session_id = repository
    first = DirectorOrchestrator(repo, scope, wait_candidate).run(
        request(session_id, "recover-seed")
    )
    assert first.replayed is False
    repo.connection.execute("DROP TRIGGER director_working_state_update_guard")
    repo.connection.execute(
        "UPDATE director_working_state SET state_sha256 = ? WHERE session_id = ?",
        ("0" * 64, session_id),
    )
    repo.connection.commit()
    seen: list[tuple[int, str]] = []
    calls = 0

    def builder(context):
        nonlocal calls
        calls += 1
        seen.append((context.working_state.state_version, context.working_state.stage))
        return wait_candidate(context)

    result = DirectorOrchestrator(repo, scope, builder).run(
        request(session_id, "recover-hash", expected_version=1)
    )

    assert result.replayed is False
    assert calls == 1
    assert seen == [(1, "EXPLORE")]
    assert repo.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 2
    assert repo.get_working_state(scope, session_id).state_version == 2


def test_unrecoverable_working_state_skips_builder_and_writes_nothing(repository) -> None:
    repo, scope, session_id = repository
    DirectorOrchestrator(repo, scope, wait_candidate).run(
        request(session_id, "unrecoverable-seed")
    )
    repo.connection.execute("DROP TRIGGER director_working_state_update_guard")
    repo.connection.execute("DROP TRIGGER director_turns_update_guard")
    repo.connection.execute(
        "UPDATE director_working_state SET state_sha256 = ? WHERE session_id = ?",
        ("0" * 64, session_id),
    )
    repo.connection.execute(
        "UPDATE director_turns SET post_state_snapshot_json = ? WHERE session_id = ?",
        ("{}", session_id),
    )
    repo.connection.commit()
    before = snapshot(repo)
    calls: list[int] = []

    def builder(_context):
        calls.append(1)
        return wait_candidate(_context)

    with pytest.raises(DirectorIntegrityError):
        DirectorOrchestrator(repo, scope, builder).run(
            request(session_id, "unrecoverable-next", expected_version=1)
        )

    assert calls == []
    assert snapshot(repo) == before


def test_same_client_id_with_different_normalized_request_conflicts_before_builder(repository) -> None:
    repo, scope, session_id = repository
    calls: list[int] = []

    def builder(context):
        calls.append(1)
        return wait_candidate(context)

    orchestrator = DirectorOrchestrator(repo, scope, builder)
    orchestrator.run(request(session_id, "conflict-key", text="第一次原文。"))
    before = snapshot(repo)

    with pytest.raises(IdempotencyConflictError):
        orchestrator.run(
            request(session_id, "conflict-key", expected_version=1, text="第二次不同原文。")
        )

    assert calls == [1]
    assert snapshot(repo) == before


def test_turn_candidate_is_business_only_and_orchestrator_owns_persistence_boundary(
    repository, monkeypatch
) -> None:
    repo, scope, session_id = repository
    candidate_fields = {field.name for field in fields(TurnCandidate)}
    assert candidate_fields == {
        "director_message",
        "execution_trace",
        "post_state",
        "final_run_control",
        "target_stage",
        "transition_reason_code",
        "gate_outcome",
        "review_root_cause",
        "ready_content",
    }
    candidate = TurnCandidate(
        director_message="回复",
        execution_trace={},
        post_state={},
        final_run_control="WAIT_FOR_OWNER",
        target_stage="EXPLORE",
        transition_reason_code="OWNER_INPUT_REQUIRED",
        gate_outcome=None,
        review_root_cause=None,
        ready_content=None,
    )
    with pytest.raises(TypeError):
        replace(candidate, turn_id=uid())

    generated_ids = iter(
        UUID(value)
        for value in (
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
            "00000000-0000-4000-8000-000000000003",
            "00000000-0000-4000-8000-000000000004",
        )
    )
    import importlib

    orchestrator_module = importlib.import_module("backend.app.director_core.orchestrator")
    monkeypatch.setattr(orchestrator_module, "uuid4", lambda: next(generated_ids))
    monkeypatch.setattr(orchestrator_module, "_utc_now", lambda: "2026-08-14T10:20:30.123Z")
    owner_text = "  老板原文\r\n第二行  "

    result = DirectorOrchestrator(repo, scope, wait_candidate).run(
        request(session_id, "boundary-request", text=owner_text)
    )
    turn = repo.connection.execute(
        "SELECT * FROM director_turns WHERE client_message_id = ?", ("boundary-request",)
    ).fetchone()
    owner_message = repo.connection.execute(
        """SELECT * FROM director_messages
           WHERE session_id = ? AND visible_role = 'OWNER'""",
        (session_id,),
    ).fetchone()

    assert result.response["turn_id"] == "00000000-0000-4000-8000-000000000001"
    assert turn["id"] == "00000000-0000-4000-8000-000000000001"
    assert turn["session_id"] == session_id
    assert turn["client_message_id"] == "boundary-request"
    assert turn["request_format_version"] == 1
    assert turn["normalized_request_json"] == '{"owner_text":"  老板原文\\n第二行  ","parameters":{}}'
    assert turn["created_at"] == "2026-08-14T10:20:30.123Z"
    assert owner_message["id"] == "00000000-0000-4000-8000-000000000002"
    assert owner_message["content"] == owner_text
    assert owner_message["created_at"] == "2026-08-14T10:20:30.123Z"


def test_ready_session_replays_success_and_rejects_new_request(repository) -> None:
    repo, scope, session_id = repository
    calls: list[int] = []

    def builder(context):
        calls.append(context.working_state.state_version)
        state = deepcopy(context.working_state.state_json)
        content = {
            "title": "一碗汤的来历",
            "script_text": "这道汤不是为了复杂，而是为了让客人喝到我们真正熟悉的味道。",
            "shooting_notes": ["从出锅画面开始"],
        }
        draft_id = uid()
        state["direction"] = {
            "item_id": uid(),
            "statement": "讲清这道汤为什么值得被记住",
            "owner_confirmed": True,
            "evidence_refs": [{
            "evidence_type": "owner_message",
            "target_id": context.owner_message_id,
            "target_session_id": session_id,
            }],
            "inherited_from": None,
        }
        state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
        state["draft"] = {
            "draft_id": draft_id, "content": content,
            "content_status": "FINAL_CANDIDATE", "based_on_ready_content_id": None,
        }
        state["review"] = {
            "review_id": uid(), "outcome": "PASSED", "root_cause": None,
            "against_draft_id": draft_id, "against_content": content,
        }
        candidate = wait_candidate(context, "这版已经可以拍了。")
        return replace(
            candidate,
            execution_trace={"format_version": 1, "steps": [
                {"step_no": 1, "entered_stage": "EXPLORE", "run_control": "CONTINUE", "target_stage": "DEEPEN", "transition_reason_code": "DIRECTION_CONFIRMED", "gate": None, "review": None, "candidate_revision": 1},
                {"step_no": 2, "entered_stage": "DEEPEN", "run_control": "CONTINUE", "target_stage": "CREATE", "transition_reason_code": "MATERIAL_SUFFICIENT", "gate": None, "review": None, "candidate_revision": 2},
                {"step_no": 3, "entered_stage": "CREATE", "run_control": "CONTINUE", "target_stage": "REVIEW", "transition_reason_code": "DRAFT_CREATED", "gate": None, "review": None, "candidate_revision": 3},
                {"step_no": 4, "entered_stage": "REVIEW", "run_control": "READY", "target_stage": "READY", "transition_reason_code": "REVIEW_PASSED", "gate": {"outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": "内容完整、真实且可拍。"}, "review": {"outcome": "PASSED", "root_cause": None}, "candidate_revision": 4},
            ]},
            post_state=state,
            final_run_control="READY", target_stage="READY",
            transition_reason_code="REVIEW_PASSED", gate_outcome="PASSED",
            review_root_cause=None, ready_content=content,
        )

    orchestrator = DirectorOrchestrator(repo, scope, builder)
    ready_request = request(session_id, "ready-request", expected_version=0)
    first = orchestrator.run(ready_request)
    assert first.response["run_control"] == "READY"
    before = snapshot(repo)
    replay = orchestrator.run(ready_request)
    assert replay.replayed is True
    assert replay.first_response_json == first.first_response_json
    assert calls == [0]
    assert snapshot(repo) == before

    with pytest.raises(DirectorExecutionValidationError):
        orchestrator.run(request(session_id, "new-after-ready", expected_version=1))
    assert calls == [0]
    assert snapshot(repo) == before


def test_same_session_turns_are_serial_and_second_reads_latest_version(tmp_path) -> None:
    path = tmp_path / "same-session.sqlite"
    first_connection = connect(path, busy_timeout_ms=200, check_same_thread=False)
    apply_migrations(first_connection)
    second_connection = connect(path, busy_timeout_ms=200, check_same_thread=False)
    scope = AuthorizationScope("workspace-1", "project-1")
    first_repo = DirectorRepository(first_connection)
    second_repo = DirectorRepository(second_connection)
    session_id = first_repo.create_session(scope).id
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    seen_versions: list[int] = []
    results: list[object] = [None, None]

    def first_builder(context):
        seen_versions.append(context.working_state.state_version)
        first_entered.set()
        assert release_first.wait(2)
        return wait_candidate(context, "第一轮完成。")

    def second_builder(context):
        seen_versions.append(context.working_state.state_version)
        second_entered.set()
        return wait_candidate(context, "第二轮完成。")

    first_orchestrator = DirectorOrchestrator(first_repo, scope, first_builder)
    second_orchestrator = DirectorOrchestrator(second_repo, scope, second_builder)

    def run(index: int, orchestrator: DirectorOrchestrator, turn_request: DirectorTurnRequest):
        try:
            results[index] = orchestrator.run(turn_request)
        except BaseException as exc:  # pragma: no cover - surfaced below
            results[index] = exc

    first_thread = threading.Thread(target=run, args=(0, first_orchestrator, request(session_id, "serial-a", 0)))
    second_thread = threading.Thread(target=run, args=(1, second_orchestrator, request(session_id, "serial-b", 1)))
    first_thread.start()
    assert first_entered.wait(2)
    second_thread.start()
    assert not second_entered.wait(0.1)
    release_first.set()
    first_thread.join(3)
    second_thread.join(3)

    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert all(not isinstance(result, BaseException) for result in results), repr(results)
    assert seen_versions == [0, 1]
    assert first_repo.get_working_state(scope, session_id).state_version == 2
    assert first_repo.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 2


def test_different_sessions_can_construct_candidates_in_parallel(tmp_path) -> None:
    path = tmp_path / "different-sessions.sqlite"
    first_connection = connect(path, busy_timeout_ms=200, check_same_thread=False)
    apply_migrations(first_connection)
    second_connection = connect(path, busy_timeout_ms=200, check_same_thread=False)
    scope = AuthorizationScope("workspace-1", "project-1")
    first_repo = DirectorRepository(first_connection)
    second_repo = DirectorRepository(second_connection)
    first_session = first_repo.create_session(scope).id
    second_session = first_repo.create_session(scope).id
    barrier = threading.Barrier(2)
    entered: list[str] = []
    results: list[object] = [None, None]

    def builder(context):
        entered.append(context.session.id)
        barrier.wait(2)
        return wait_candidate(context)

    first_orchestrator = DirectorOrchestrator(first_repo, scope, builder)
    second_orchestrator = DirectorOrchestrator(second_repo, scope, builder)

    def run(index: int, orchestrator: DirectorOrchestrator, session_id: str):
        try:
            results[index] = orchestrator.run(request(session_id, f"parallel-{index}"))
        except BaseException as exc:  # pragma: no cover - surfaced below
            results[index] = exc

    threads = [
        threading.Thread(target=run, args=(0, first_orchestrator, first_session)),
        threading.Thread(target=run, args=(1, second_orchestrator, second_session)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    assert all(not thread.is_alive() for thread in threads)
    assert all(not isinstance(result, BaseException) for result in results), repr(results)
    assert set(entered) == {first_session, second_session}


def test_candidate_exception_leaves_all_six_tables_unchanged(repository) -> None:
    repo, scope, session_id = repository
    before = snapshot(repo)

    def builder(_context):
        raise RuntimeError("candidate construction failed")

    with pytest.raises(RuntimeError, match="candidate construction failed"):
        DirectorOrchestrator(repo, scope, builder).run(request(session_id, "builder-failure"))
    assert snapshot(repo) == before


def test_prepare_failure_leaves_all_six_tables_unchanged(repository) -> None:
    repo, scope, session_id = repository
    before = snapshot(repo)

    def builder(context):
        candidate = wait_candidate(context)
        return replace(candidate, target_stage="DEEPEN")

    with pytest.raises(DirectorExecutionValidationError):
        DirectorOrchestrator(repo, scope, builder).run(request(session_id, "prepare-failure"))
    assert snapshot(repo) == before


def test_authoritative_success_is_one_turn_two_messages_and_version_increment(repository) -> None:
    repo, scope, session_id = repository
    result = DirectorOrchestrator(repo, scope, wait_candidate).run(
        request(session_id, "authoritative-success")
    )
    assert result.replayed is False
    assert repo.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 1
    assert repo.connection.execute("SELECT count(*) FROM director_messages").fetchone()[0] == 2
    assert repo.get_working_state(scope, session_id).state_version == 1


class CommitFaultConnection(sqlite3.Connection):
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


def fault_connection(path, mode: str) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path, timeout=0, factory=CommitFaultConnection, check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    enable_and_verify_foreign_keys(connection)
    connection.mode = mode
    return connection


def test_rolled_back_commit_does_not_reconstruct_candidate(tmp_path) -> None:
    path = tmp_path / "rolled-back.sqlite"
    connection = fault_connection(path, "none")
    apply_migrations(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    repo = DirectorRepository(
        connection, fresh_connection_factory=lambda: connect(path, busy_timeout_ms=0)
    )
    session_id = repo.create_session(scope).id
    calls: list[int] = []

    def builder(context):
        calls.append(1)
        return wait_candidate(context)

    connection.mode = "before"
    with pytest.raises(CommitRolledBackError):
        DirectorOrchestrator(repo, scope, builder).run(request(session_id, "rolled-back"))
    assert calls == [1]
    assert repo.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 0


def test_indeterminate_commit_does_not_reconstruct_candidate(tmp_path) -> None:
    path = tmp_path / "indeterminate.sqlite"
    connection = fault_connection(path, "none")
    apply_migrations(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    repo = DirectorRepository(connection)
    session_id = repo.create_session(scope).id
    calls: list[int] = []

    def builder(context):
        calls.append(1)
        return wait_candidate(context)

    connection.mode = "after"
    with pytest.raises(CommitOutcomeIndeterminateError):
        DirectorOrchestrator(repo, scope, builder).run(request(session_id, "indeterminate"))
    assert calls == [1]


def test_commit_disambiguation_keeps_lock_until_resolution(tmp_path) -> None:
    path = tmp_path / "disambiguation-lock.sqlite"
    connection = fault_connection(path, "none")
    apply_migrations(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    repo = DirectorRepository(
        connection,
        fresh_connection_factory=lambda: connect(path, busy_timeout_ms=0, check_same_thread=False),
    )
    session_id = repo.create_session(scope).id
    entered_resolution = threading.Event()
    release_resolution = threading.Event()
    second_builder_entered = threading.Event()
    original_resolve = repo.resolve_commit_outcome

    def blocking_resolve(*args, **kwargs):
        entered_resolution.set()
        assert release_resolution.wait(2)
        return original_resolve(*args, **kwargs)

    repo.resolve_commit_outcome = blocking_resolve
    results: list[object] = [None, None]

    connection.mode = "after"

    def first_builder(context):
        return wait_candidate(context)

    def second_builder(context):
        second_builder_entered.set()
        return wait_candidate(context)

    first_orchestrator = DirectorOrchestrator(repo, scope, first_builder)
    second_orchestrator = DirectorOrchestrator(repo, scope, second_builder)

    def run_first():
        try:
            results[0] = first_orchestrator.run(request(session_id, "commit-a"))
        except BaseException as exc:  # pragma: no cover - surfaced below
            results[0] = exc

    def run_second():
        try:
            results[1] = second_orchestrator.run(request(session_id, "commit-b", expected_version=1))
        except BaseException as exc:  # pragma: no cover - surfaced below
            results[1] = exc

    first_thread = threading.Thread(target=run_first)
    second_thread = threading.Thread(target=run_second)
    first_thread.start()
    assert entered_resolution.wait(2)
    second_thread.start()
    assert not second_builder_entered.wait(0.1)
    release_resolution.set()
    first_thread.join(3)
    second_thread.join(3)

    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert not isinstance(results[0], BaseException), repr(results)
    assert not isinstance(results[1], BaseException), repr(results)
    assert second_builder_entered.is_set()
    assert repo.connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 2
