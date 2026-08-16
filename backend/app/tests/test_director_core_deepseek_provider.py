from __future__ import annotations

from copy import deepcopy
import json
from uuid import uuid4

import httpx
import pytest

from backend.app.director_core.context import (
    ContextBudget,
    ContextBudgetExceededError,
    ContextMessage,
    ModelContext,
    ModelContextAssembler,
)
from backend.app.director_core.database import apply_migrations, connect
from backend.app.director_core.orchestrator import (
    DirectorOrchestrator,
    DirectorStageExecutor,
    DirectorTurnRequest,
)
from backend.app.director_core.providers.deepseek import (
    DEEPSEEK_STAGE_PROMPTS,
    DeepSeekConfigurationError,
    DeepSeekEmptyResponseError,
    DeepSeekHTTPStatusError,
    DeepSeekNonJSONResponseError,
    DeepSeekResponseSchemaError,
    DeepSeekStageHandler,
    DeepSeekTimeoutError,
    DeepSeekTransportError,
    DeepSeekUnexpectedFinishReasonError,
)
from backend.app.director_core.repository import AuthorizationScope, DirectorRepository
from backend.app.director_core.stage_contract import stage_execution_contract
from backend.app.director_core.stage_handler import (
    ForgedUUIDError,
    StageContractViolationError,
    StageModelOutputSchemaError,
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


def model_context(stage: str = "EXPLORE") -> ModelContext:
    session_id = uid()
    owner_id = uid()
    owner = ContextMessage(
        id=owner_id,
        role="OWNER",
        content="老板本轮提供的真实内容。",
        message_seq=1,
        turn_id="CURRENT_TURN",
    )
    return ModelContext(
        rules={"owner_fact_boundary": "OWNER only"},
        stage_contract=stage_execution_contract(stage),
        working_state=empty_state(),
        current_owner_message=owner,
        source_ready_content=None,
        checkpoint=None,
        history_turns=(),
        evidence_messages=(),
        owner_evidence_references=({
            "evidence_type": "owner_message",
            "target_id": owner_id,
            "target_session_id": session_id,
        },),
        estimated_units=1,
    )


def wait_proposal(state: dict | None = None) -> dict:
    return {
        "output_format_version": 1,
        "run_control": "WAIT_FOR_OWNER",
        "target_stage": "EXPLORE",
        "transition_reason_code": "OWNER_INPUT_REQUIRED",
        "director_message": "请再补充一个最关键的真实细节。",
        "gate": {
            "outcome": "BLOCKED",
            "gate_code": "DIRECTION_NOT_CONFIRMED",
            "explanation": "方向尚未得到老板确认。",
        },
        "review": None,
        "post_state": empty_state() if state is None else state,
    }


def completion(content: str | None, *, finish_reason: str = "stop") -> dict:
    return {
        "id": "completion-id",
        "choices": [{
            "index": 0,
            "finish_reason": finish_reason,
            "message": {"role": "assistant", "content": content},
        }],
    }


def make_handler(client: httpx.Client) -> DeepSeekStageHandler:
    return DeepSeekStageHandler(api_key="test-secret", client=client)


def snapshot(repository: DirectorRepository) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {
        table: tuple(
            tuple(row)
            for row in repository.connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        )
        for table in TABLES
    }


def test_normal_request_is_sync_non_streaming_json_and_returns_plain_dict() -> None:
    seen: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=completion(json.dumps(wait_proposal(), ensure_ascii=False)),
        )

    context = model_context()
    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        handler = make_handler(client)
        result = handler(context)

    assert type(result) is dict
    assert result == wait_proposal()
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url == httpx.URL("https://api.deepseek.com/chat/completions")
    assert request.headers["Authorization"] == "Bearer test-secret"
    body = json.loads(request.content)
    assert body["model"] == "deepseek-v4-flash"
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 8000
    assert json.loads(body["messages"][1]["content"]) == context.to_dict()
    assert "test-secret" not in repr(handler)


@pytest.mark.parametrize("stage", ["EXPLORE", "DEEPEN", "CREATE", "REVIEW"])
def test_exactly_four_stage_prompts_use_the_existing_model_context(stage: str) -> None:
    bodies: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion('{"plain":"object"}'))

    context = model_context(stage)
    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        assert make_handler(client)(context) == {"plain": "object"}

    assert set(DEEPSEEK_STAGE_PROMPTS) == {"EXPLORE", "DEEPEN", "CREATE", "REVIEW"}
    assert len(bodies) == 1
    system_prompt = bodies[0]["messages"][0]["content"]
    assert DEEPSEEK_STAGE_PROMPTS[stage] in system_prompt
    assert "只输出一个完整 JSON object" in system_prompt
    assert json.loads(bodies[0]["messages"][1]["content"]) == context.to_dict()


def test_ready_never_sends_an_http_request() -> None:
    calls = 0

    def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completion("{}"))

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(DeepSeekConfigurationError, match="READY"):
            make_handler(client)(model_context("READY"))
    assert calls == 0


@pytest.mark.parametrize("status_code", [408, 429, 500, 503])
def test_retryable_http_status_uses_same_request_and_at_most_two_calls(
    status_code: int,
) -> None:
    bodies: list[bytes] = []

    def responder(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(status_code)
        return httpx.Response(200, json=completion('{"ok":true}'))

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        assert make_handler(client)(model_context()) == {"ok": True}
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize("kind", ["connect", "timeout"])
def test_transport_failure_retries_same_request_once(kind: str) -> None:
    bodies: list[bytes] = []

    def responder(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            if kind == "timeout":
                raise httpx.ReadTimeout("timeout", request=request)
            raise httpx.ConnectError("connect", request=request)
        return httpx.Response(200, json=completion('{"ok":true}'))

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        assert make_handler(client)(model_context()) == {"ok": True}
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize("first_content", [None, "", "```json\n{}\n```", "not json"])
def test_empty_or_non_json_gets_one_full_json_regeneration(first_content: str | None) -> None:
    bodies: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        content = first_content if len(bodies) == 1 else '{"ok":true}'
        return httpx.Response(200, json=completion(content))

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        assert make_handler(client)(model_context()) == {"ok": True}
    assert len(bodies) == 2
    assert bodies[0]["messages"][1] == bodies[1]["messages"][1]
    assert "上一次响应为空或不是一个完整 JSON 文档" not in bodies[0]["messages"][0]["content"]
    assert "上一次响应为空或不是一个完整 JSON 文档" in bodies[1]["messages"][0]["content"]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("connect", DeepSeekTransportError),
        ("timeout", DeepSeekTimeoutError),
        ("http", DeepSeekHTTPStatusError),
        ("empty", DeepSeekEmptyResponseError),
        ("non-json", DeepSeekNonJSONResponseError),
    ],
)
def test_retry_budget_never_allows_a_third_request(failure: str, expected: type[Exception]) -> None:
    calls = 0

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "connect":
            raise httpx.ConnectError("connect", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if failure == "http":
            return httpx.Response(500)
        if failure == "empty":
            return httpx.Response(200, json=completion(""))
        return httpx.Response(200, json=completion("not json"))

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(expected):
            make_handler(client)(model_context())
    assert calls == 2


def test_non_retryable_status_schema_and_finish_reason_stop_after_one_request() -> None:
    cases = [
        (lambda: httpx.Response(400), DeepSeekHTTPStatusError),
        (lambda: httpx.Response(200, json=completion("[]")), DeepSeekResponseSchemaError),
        (
            lambda: httpx.Response(200, json=completion("{}", finish_reason="length")),
            DeepSeekUnexpectedFinishReasonError,
        ),
        (
            lambda: httpx.Response(
                200,
                json=completion('{"a":1,"a":2}'),
            ),
            DeepSeekResponseSchemaError,
        ),
    ]
    for response_factory, expected in cases:
        calls = 0

        def responder(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return response_factory()

        with httpx.Client(transport=httpx.MockTransport(responder)) as client:
            with pytest.raises(expected):
                make_handler(client)(model_context())
        assert calls == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_key": ""},
        {"api_key": "key", "model": "another-model"},
        {"api_key": "key", "thinking_mode": "enabled"},
        {"api_key": "key", "timeout_seconds": 0},
        {"api_key": "key", "max_output_tokens": True},
    ],
)
def test_phase1f_configuration_is_fail_closed(kwargs: dict) -> None:
    with pytest.raises(DeepSeekConfigurationError):
        DeepSeekStageHandler(**kwargs)


@pytest.fixture
def director_repository(tmp_path):
    connection = connect(tmp_path / "deepseek-provider.sqlite", busy_timeout_ms=100)
    apply_migrations(connection)
    repository = DirectorRepository(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = repository.create_session(scope)
    return repository, scope, session.id


def request(session_id: str, client_id: str = "deepseek-turn") -> DirectorTurnRequest:
    return DirectorTurnRequest(
        session_id=session_id,
        client_message_id=client_id,
        expected_state_version=0,
        owner_text="老板提供了一条真实但还需要继续确认的内容。",
        request_format_version=1,
        parameters={},
    )


def proposal_from_request(request_body: bytes, kind: str) -> dict:
    body = json.loads(request_body)
    context = json.loads(body["messages"][1]["content"])
    state = deepcopy(context["working_state"])
    reference = deepcopy(context["owner_evidence_references"][0])
    if kind == "schema":
        return {"unexpected": True}
    if kind == "evidence":
        reference["target_id"] = uid()
    if kind in {"evidence", "identity", "semantic"}:
        state["direction"] = {
            "item_id": uid() if kind == "identity" else "new:item:direction_1",
            "statement": "讲老板真实经营经历。",
            "owner_confirmed": True,
            "evidence_refs": [reference],
            "inherited_from": None,
        }
        if kind == "semantic":
            state["direction"] = None
        return {
            "output_format_version": 1,
            "run_control": "CONTINUE",
            "target_stage": "DEEPEN",
            "transition_reason_code": "DIRECTION_CONFIRMED",
            "director_message": None,
            "gate": None,
            "review": None,
            "post_state": state,
        }
    return wait_proposal(state)


def test_real_provider_vertical_slice_commits_through_existing_executor(
    director_repository,
) -> None:
    repository, scope, session_id = director_repository
    calls = 0

    def responder(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        proposal = proposal_from_request(http_request.content, "valid")
        return httpx.Response(
            200,
            json=completion(json.dumps(proposal, ensure_ascii=False)),
        )

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        executor = DirectorStageExecutor(
            ModelContextAssembler(repository, scope, ContextBudget(100_000)),
            make_handler(client),
        )
        result = DirectorOrchestrator(
            repository, scope, executor, max_internal_steps=2
        ).run(request(session_id))

    assert calls == 1
    assert result.response["run_control"] == "WAIT_FOR_OWNER"
    assert repository.connection.execute(
        "SELECT count(*) FROM director_turns"
    ).fetchone()[0] == 1
    assert repository.connection.execute(
        "SELECT count(*) FROM director_messages"
    ).fetchone()[0] == 2
    assert repository.get_working_state(scope, session_id).state_version == 1


@pytest.mark.parametrize(
    ("kind", "expected", "expected_calls"),
    [
        ("connect", DeepSeekTransportError, 2),
        ("timeout", DeepSeekTimeoutError, 2),
        ("http-400", DeepSeekHTTPStatusError, 1),
        ("http-500", DeepSeekHTTPStatusError, 2),
        ("empty", DeepSeekEmptyResponseError, 2),
        ("non-json", DeepSeekNonJSONResponseError, 2),
        ("schema", StageModelOutputSchemaError, 1),
        ("evidence", StageContractViolationError, 1),
        ("identity", ForgedUUIDError, 1),
        ("semantic", StageContractViolationError, 1),
        ("response-schema", DeepSeekResponseSchemaError, 1),
        ("finish-reason", DeepSeekUnexpectedFinishReasonError, 1),
    ],
)
def test_every_provider_and_validation_failure_leaves_all_six_tables_unchanged(
    director_repository,
    kind: str,
    expected: type[Exception],
    expected_calls: int,
) -> None:
    repository, scope, session_id = director_repository
    before = snapshot(repository)
    calls = 0

    def responder(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if kind == "connect":
            raise httpx.ConnectError("connect", request=http_request)
        if kind == "timeout":
            raise httpx.ReadTimeout("timeout", request=http_request)
        if kind == "http-400":
            return httpx.Response(400)
        if kind == "http-500":
            return httpx.Response(500)
        if kind == "empty":
            return httpx.Response(200, json=completion(""))
        if kind == "non-json":
            return httpx.Response(200, json=completion("not json"))
        if kind == "response-schema":
            return httpx.Response(200, json=completion("[]"))
        if kind == "finish-reason":
            return httpx.Response(
                200, json=completion("{}", finish_reason="length")
            )
        proposal = proposal_from_request(http_request.content, kind)
        return httpx.Response(
            200,
            json=completion(json.dumps(proposal, ensure_ascii=False)),
        )

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        executor = DirectorStageExecutor(
            ModelContextAssembler(repository, scope, ContextBudget(100_000)),
            make_handler(client),
        )
        with pytest.raises(expected):
            DirectorOrchestrator(
                repository, scope, executor, max_internal_steps=2
            ).run(request(session_id, f"failure-{kind}"))

    assert calls == expected_calls
    assert snapshot(repository) == before


def test_context_budget_failure_never_calls_provider_and_writes_nothing(
    director_repository,
) -> None:
    repository, scope, session_id = director_repository
    before = snapshot(repository)
    calls = 0

    def responder(_http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completion("{}"))

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        executor = DirectorStageExecutor(
            ModelContextAssembler(repository, scope, ContextBudget(1)),
            make_handler(client),
        )
        with pytest.raises(ContextBudgetExceededError):
            DirectorOrchestrator(
                repository, scope, executor, max_internal_steps=2
            ).run(request(session_id, "context-budget"))

    assert calls == 0
    assert snapshot(repository) == before
