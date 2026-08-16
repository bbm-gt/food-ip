from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from .. import config, director_runtime
from ..director_core.database import connect
from ..director_core.providers.deepseek import (
    DeepSeekHTTPStatusError,
    DeepSeekTransportError,
)
from ..director_core.repository import SQLiteBusyError
from ..main import app


def uid() -> str:
    return str(uuid4())


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


def wait_output(state: dict | None = None) -> dict:
    return {
        "output_format_version": 1,
        "run_control": "WAIT_FOR_OWNER",
        "target_stage": "EXPLORE",
        "transition_reason_code": "OWNER_INPUT_REQUIRED",
        "director_message": "请补充一个最关键的真实细节。",
        "gate": {
            "outcome": "BLOCKED",
            "gate_code": "DIRECTION_NOT_CONFIRMED",
            "explanation": "方向尚未确认。",
        },
        "review": None,
        "post_state": empty_state() if state is None else state,
    }


class WaitProvider:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _context) -> dict:
        self.calls += 1
        return wait_output()


class ReadyProvider:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, context) -> dict:
        self.calls += 1
        state = deepcopy(context.to_dict()["working_state"])
        stage = context.stage_contract["stage"]
        if stage == "EXPLORE":
            state["direction"] = {
                "item_id": "new:item:direction_1",
                "statement": "讲清这道菜真实的来历。",
                "owner_confirmed": True,
                "evidence_refs": [dict(context.owner_evidence_references[0])],
                "inherited_from": None,
            }
            return wait_output(
                state
            ) | {
                "run_control": "CONTINUE",
                "target_stage": "DEEPEN",
                "transition_reason_code": "DIRECTION_CONFIRMED",
                "director_message": None,
                "gate": None,
            }
        if stage == "DEEPEN":
            state["material_state"] = {
                "status": "SUFFICIENT", "required_confirmations": []
            }
            return wait_output(
                state
            ) | {
                "run_control": "CONTINUE",
                "target_stage": "CREATE",
                "transition_reason_code": "MATERIAL_SUFFICIENT",
                "director_message": None,
                "gate": None,
            }
        if stage == "CREATE":
            state["draft"] = {
                "draft_id": "new:draft:draft_1",
                "content": {
                    "title": "一碗面的真实来历",
                    "script_text": "这是一份能拍、且只基于老板确认素材的脚本。",
                    "shooting_notes": ["拍摄老板制作面条的手部特写。"],
                },
                "content_status": "FINAL_CANDIDATE",
                "based_on_ready_content_id": None,
            }
            return wait_output(
                state
            ) | {
                "run_control": "CONTINUE",
                "target_stage": "REVIEW",
                "transition_reason_code": "DRAFT_CREATED",
                "director_message": None,
                "gate": None,
            }
        state["review"] = {
            "review_id": "new:review:review_1",
            "outcome": "PASSED",
            "root_cause": None,
            "against_draft_id": state["draft"]["draft_id"],
            "against_content": deepcopy(state["draft"]["content"]),
        }
        return wait_output(
            state
        ) | {
            "run_control": "READY",
            "target_stage": "READY",
            "transition_reason_code": "REVIEW_PASSED",
            "director_message": "这版已经可以拍了。",
            "gate": {
                "outcome": "PASSED",
                "gate_code": "READINESS_PASSED",
                "explanation": "内容完整、真实且可拍。",
            },
            "review": {"outcome": "PASSED", "root_cause": None},
        }


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(config, "DIRECTOR_DB_PATH", tmp_path / "director.sqlite3")
    with TestClient(app) as test_client:
        yield test_client


def create_project(client: TestClient) -> str:
    response = client.post("/api/projects", json={"name": "Director API 测试"})
    assert response.status_code == 201
    return response.json()["id"]


def create_session(client: TestClient, project_id: str) -> str:
    response = client.post(f"/api/projects/{project_id}/director-sessions", json={})
    assert response.status_code == 201
    assert response.json()["lifecycle_status"] == "ACTIVE"
    assert response.json()["state_version"] == 0
    return response.json()["session_id"]


def submit_url(project_id: str, session_id: str) -> str:
    return f"/api/projects/{project_id}/director-sessions/{session_id}/messages"


def submit_body(*, client_message_id: str | None = None, expected_state_version: int = 0, content: str = "老板确认这道面每天现做。") -> dict:
    return {
        "client_message_id": client_message_id or uid(),
        "expected_state_version": expected_state_version,
        "content": content,
        "parameters": {},
    }


def install_provider(monkeypatch: pytest.MonkeyPatch, provider) -> None:
    monkeypatch.setattr(director_runtime, "create_director_stage_handler", lambda: provider)


def test_creates_ordinary_session(client: TestClient) -> None:
    project_id = create_project(client)
    response = client.post(f"/api/projects/{project_id}/director-sessions", json={})

    assert response.status_code == 201
    assert response.json()["source_ready_content_id"] is None


def test_creates_revision_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ReadyProvider()
    install_provider(monkeypatch, provider)
    project_id = create_project(client)
    session_id = create_session(client, project_id)
    ready = client.post(submit_url(project_id, session_id), json=submit_body())
    assert ready.status_code == 200

    response = client.post(
        f"/api/projects/{project_id}/director-sessions",
        json={"source_ready_content_id": ready.json()["ready_content"]["id"]},
    )

    assert response.status_code == 201
    assert response.json()["source_ready_content_id"] == ready.json()["ready_content"]["id"]
    assert response.json()["state_version"] == 0


def test_owner_message_succeeds_and_replays_once(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = WaitProvider()
    install_provider(monkeypatch, provider)
    project_id = create_project(client)
    session_id = create_session(client, project_id)
    body = submit_body()

    first = client.post(submit_url(project_id, session_id), json=body)
    replay = client.post(submit_url(project_id, session_id), json=body)

    assert first.status_code == replay.status_code == 200
    assert first.json()["status"] == "WAITING_FOR_OWNER"
    assert replay.json()["replayed"] is True
    assert replay.json()["turn_id"] == first.json()["turn_id"]
    assert provider.calls == 1


def test_idempotency_and_state_version_conflicts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = WaitProvider()
    install_provider(monkeypatch, provider)
    project_id = create_project(client)
    session_id = create_session(client, project_id)
    message_id = uid()
    assert client.post(
        submit_url(project_id, session_id), json=submit_body(client_message_id=message_id)
    ).status_code == 200

    conflict = client.post(
        submit_url(project_id, session_id),
        json=submit_body(client_message_id=message_id, content="另一条不同的内容。"),
    )
    stale = client.post(
        submit_url(project_id, session_id), json=submit_body(expected_state_version=0)
    )

    assert conflict.status_code == 409
    assert stale.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    assert stale.json()["code"] == "state_version_conflict"
    assert provider.calls == 1


def test_ready_returns_full_content_and_rejects_new_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ReadyProvider()
    install_provider(monkeypatch, provider)
    project_id = create_project(client)
    session_id = create_session(client, project_id)
    body = submit_body()

    ready = client.post(submit_url(project_id, session_id), json=body)
    replay = client.post(submit_url(project_id, session_id), json=body)
    rejected = client.post(
        submit_url(project_id, session_id),
        json=submit_body(expected_state_version=1),
    )

    assert ready.status_code == 200
    assert ready.json()["status"] == "READY"
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["turn_id"] == ready.json()["turn_id"]
    assert replay.json()["ready_content"] == ready.json()["ready_content"]
    assert ready.json()["ready_content"] == {
        "id": ready.json()["ready_content"]["id"],
        "title": "一碗面的真实来历",
        "script_text": "这是一份能拍、且只基于老板确认素材的脚本。",
        "shooting_notes": ["拍摄老板制作面条的手部特写。"],
    }
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "session_ready"
    assert provider.calls == 4


def test_missing_and_invalid_project_or_session_are_mapped(client: TestClient) -> None:
    missing_project = client.post("/api/projects/missing-project/director-sessions", json={})
    invalid_project = client.post("/api/projects/bad/director-sessions", json={})
    project_id = create_project(client)
    missing_session = client.post(
        submit_url(project_id, uid()), json=submit_body()
    )

    assert missing_project.status_code == 404
    assert invalid_project.status_code == 400
    assert missing_session.status_code == 404


@pytest.mark.parametrize(
    ("provider", "expected_status"),
    [
        (lambda _context: (_ for _ in ()).throw(DeepSeekTransportError("offline")), 503),
        (lambda _context: (_ for _ in ()).throw(DeepSeekHTTPStatusError(400)), 502),
    ],
)
def test_provider_failure_is_mapped_without_partial_turn(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    provider,
    expected_status: int,
) -> None:
    install_provider(monkeypatch, provider)
    project_id = create_project(client)
    session_id = create_session(client, project_id)

    response = client.post(submit_url(project_id, session_id), json=submit_body())

    assert response.status_code == expected_status
    connection = connect(config.DIRECTOR_DB_PATH, busy_timeout_ms=1000)
    try:
        assert connection.execute("SELECT count(*) FROM director_turns").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM director_messages").fetchone()[0] == 0
    finally:
        connection.close()


def test_sqlite_busy_is_mapped_to_retryable_service_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = create_project(client)
    original = director_runtime.open_director_connection

    def busy_connection():
        raise SQLiteBusyError("busy")

    monkeypatch.setattr(director_runtime, "open_director_connection", busy_connection)
    response = client.post(f"/api/projects/{project_id}/director-sessions", json={})
    monkeypatch.setattr(director_runtime, "open_director_connection", original)

    assert response.status_code == 503
