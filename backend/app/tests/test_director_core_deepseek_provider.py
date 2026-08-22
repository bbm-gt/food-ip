from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
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
    StageExecutionContext,
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
    validate_stage_model_output,
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


def make_semantic_handler(client: httpx.Client) -> DeepSeekStageHandler:
    return DeepSeekStageHandler(
        api_key="test-secret", client=client, stage_mode="semantic_only"
    )


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
    assert json.loads(body["messages"][1]["content"]) == {
        "model_context": context.to_dict()
    }
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
    assert json.loads(bodies[0]["messages"][1]["content"]) == {
        "model_context": context.to_dict()
    }


def test_semantic_requests_govern_natural_corrections_and_meaning_level_review() -> None:
    bodies: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion('{"plain":"object"}'))

    state = empty_state()
    state["owner_facts"] = [{
        "item_id": uid(),
        "statement": "每天凌晨四点开始熬汤",
        "evidence_refs": [],
        "supersedes_item_ids": [],
        "inherited_from": None,
    }]
    state["unconfirmed_inferences"] = [{
        "item_id": uid(),
        "statement": "汤要熬十二个小时",
        "reason": "尚未得到老板确认",
        "evidence_refs": [],
        "inherited_from": None,
    }]
    state["draft"] = {
        "item_id": uid(),
        "content": "天不亮就开火，这锅汤要熬足十二小时。",
        "based_on_fact_ids": [],
        "based_on_constraint_ids": [],
        "revision": 1,
        "inherited_from": None,
    }

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        handler = make_semantic_handler(client)
        for stage in ("EXPLORE", "DEEPEN", "REVIEW"):
            base = model_context(stage)
            context = replace(
                base,
                working_state=state,
                current_owner_message=replace(
                    base.current_owner_message,
                    content="我们不是凌晨四点熬汤，是早上六点。",
                ),
            )
            assert handler(context) == {"plain": "object"}

    assert len(bodies) == 3
    prompts = {
        stage: body["messages"][0]["content"]
        for stage, body in zip(("EXPLORE", "DEEPEN", "REVIEW"), bodies, strict=True)
    }
    for stage in ("EXPLORE", "DEEPEN"):
        assert "按完整语义理解老板" in prompts[stage]
        assert "不得把被否定的A新增为事实" in prompts[stage] or "不得新增被否定的A" in prompts[stage]
        assert "只输出一条针对B的事实变化" in prompts[stage]
        assert "必须先按含义在 semantic_context.facts" in prompts[stage]
        assert "replaces_statement 必须逐字复制该有效事实的完整 statement" in prompts[stage]
        assert "只有 facts 无匹配时" in prompts[stage]
        assert "semantic_context.unconfirmed_inferences" in prompts[stage]
        assert "逐字复制该待确认项的完整 statement" in prompts[stage]
        assert "均无匹配时" in prompts[stage]
        assert "replaces_statement 必须为 null" in prompts[stage]
        assert "不要填写老板原话中的A" in prompts[stage]
        assert "也可以把B作为 ADD" in prompts[stage]
        assert "均无匹配时直接新增B" in prompts[stage]
        assert "不使用关键词或字面匹配" in prompts[stage]
        assert "semantic_context.owner_message 明确否定A并确认B" in prompts[stage]
        assert "本轮 new_facts 就必须包含这次更正" in prompts[stage]
        assert "不得因A不在 facts、素材看似已足够" in prompts[stage]
        assert "若无法可靠理解B，必须 ASK_OWNER 澄清" in prompts[stage]
        assert "statement 可以忠实整理老板明确表达的完整语义" in prompts[stage]
        assert "不要求逐字复制 owner_quote" in prompts[stage]

    assert "不能用方向结果忽略这次更正" in prompts["EXPLORE"]
    assert "不能用 MATERIAL_READY 忽略这次更正" in prompts["DEEPEN"]

    assert "DEEPEN 顶层禁止 owner_quote" in prompts["DEEPEN"]
    assert "owner_quote 只允许出现在 new_facts/new_constraints 的每个变化对象内" in prompts[
        "DEEPEN"
    ]

    for body in bodies:
        semantic_context = json.loads(body["messages"][1]["content"])[
            "semantic_context"
        ]
        assert semantic_context["unconfirmed_inferences"] == [{
            "statement": "汤要熬十二个小时",
            "reason": "尚未得到老板确认",
        }]

    assert "direction 必须逐字复制 owner_quote 中一个连续、非空的原文片段" in prompts[
        "EXPLORE"
    ]
    assert "不能改写、换序或补词" in prompts["EXPLORE"]

    review_prompt = prompts["REVIEW"]
    assert "按含义而不是字面相似度" in review_prompt
    assert "unconfirmed_inferences" in review_prompt
    assert "知识、案例和外部信息只能指导写法和判断" in review_prompt
    assert "不要求逐句事实来源清单" in review_prompt

    review_payload = json.loads(bodies[2]["messages"][1]["content"])[
        "semantic_context"
    ]
    assert review_payload["facts"] == [{"statement": "每天凌晨四点开始熬汤"}]
    assert review_payload["unconfirmed_inferences"] == [
        {"statement": "汤要熬十二个小时", "reason": "尚未得到老板确认"}
    ]
    assert review_payload["draft"] == "天不亮就开火，这锅汤要熬足十二小时。"


def test_prompt_contains_complete_contract_and_a_strictly_legal_json_example() -> None:
    bodies: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion('{"plain":"object"}'))

    context = model_context("EXPLORE")
    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        make_handler(client)(context)

    prompt = bodies[0]["messages"][0]["content"]
    for structure_name in (
        "OwnerFact",
        "OwnerConstraint",
        "AIJudgment",
        "UnconfirmedInference",
        "RejectedItem",
        "Direction",
        "RequiredConfirmation",
        "MaterialState",
        "Content",
        "Draft",
        "Working State Review",
        "GateResult",
        "Trace Review",
        "StageModelProposalV1",
    ):
        assert structure_name in prompt
    assert "所有对象（包括嵌套对象）都禁止额外字段" in prompt
    assert "post_state 必须是应用本阶段结果后的完整状态，不是 patch" in prompt
    assert "new:item:<local_key>" in prompt
    assert "new:draft:<local_key>" in prompt
    assert "new:review:<local_key>" in prompt
    assert "禁止拒绝同一输出中新建的对象" in prompt

    example_text = prompt.split("完整 JSON 示例开始\n", 1)[1].split(
        "\n完整 JSON 示例结束", 1
    )[0]
    example = json.loads(example_text)
    validated = validate_stage_model_output(example, context=context)
    assert validated.output_format_version == 1
    assert validated.post_state.format_version == 1


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


def test_duplicate_model_json_key_gets_one_full_json_regeneration() -> None:
    bodies: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        content = '{"result":"first","result":"duplicate"}' if len(bodies) == 1 else '{"ok":true}'
        return httpx.Response(200, json=completion(content))

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        assert make_handler(client)(model_context()) == {"ok": True}

    assert len(bodies) == 2
    assert bodies[0]["messages"][1] == bodies[1]["messages"][1]
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
    context = json.loads(body["messages"][1]["content"])["model_context"]
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


def test_semantic_explore_repairs_real_owner_quote_schema_failure_once(
    director_repository,
) -> None:
    repository, scope, session_id = director_repository
    bodies: list[dict] = []
    invalid = {
        "result": "DIRECTION_OPTIONS",
        "message": "我先给你三个方向。",
        "direction": None,
        "owner_quote": "老板本轮提供的真实内容。",
        "new_facts": [],
        "new_constraints": [],
        "reason": "三个方向都可以继续。",
        "directions": [
            {"direction": "讲真实做法", "reason": "最具体", "recommended": True},
            {"direction": "讲老板态度", "reason": "有观点", "recommended": False},
            {"direction": "讲顾客选择", "reason": "好理解", "recommended": False},
        ],
    }
    repaired = deepcopy(invalid)
    repaired["owner_quote"] = None

    def responder(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        bodies.append(body)
        output = invalid if len(bodies) == 1 else repaired
        return httpx.Response(
            200, json=completion(json.dumps(output, ensure_ascii=False))
        )

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        executor = DirectorStageExecutor(
            ModelContextAssembler(repository, scope, ContextBudget(100_000)),
            make_semantic_handler(client),
            mode="semantic_only",
        )
        result = DirectorOrchestrator(
            repository, scope, executor, max_internal_steps=2
        ).run(request(session_id, "repair-explore-owner-quote"))

    assert result.response["run_control"] == "WAIT_FOR_OWNER"
    assert len(bodies) == 2
    repair_payload = json.loads(bodies[1]["messages"][1]["content"])
    assert repair_payload["invalid_output"] == invalid
    assert "owner_quote" in repair_payload["validation_error"]
    assert "严格 schema 校验拒绝" in bodies[1]["messages"][0]["content"]
    assert "extra field 时必须删除该多余字段" in bodies[1]["messages"][0]["content"]
    assert "阶段规则要求 null 时必须输出 null" in bodies[0]["messages"][0]["content"]


def test_semantic_deepen_repairs_real_null_missing_material_failure_once(
    director_repository,
) -> None:
    repository, scope, session_id = director_repository
    owner_message_id = uid()
    state = empty_state()
    state["direction"] = {
        "item_id": uid(),
        "statement": "讲现熬牛骨汤",
        "owner_confirmed": True,
        "evidence_refs": [{
            "evidence_type": "owner_message",
            "target_id": owner_message_id,
            "target_session_id": session_id,
        }],
        "inherited_from": None,
    }
    invalid = {
        "result": "MATERIAL_READY",
        "message": None,
        "new_facts": [],
        "new_constraints": [],
        "missing_material": None,
        "reason": "素材足够。",
    }
    repaired = deepcopy(invalid)
    repaired["missing_material"] = []
    bodies: list[dict] = []

    def responder(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        bodies.append(body)
        output = invalid if len(bodies) == 1 else repaired
        return httpx.Response(
            200, json=completion(json.dumps(output, ensure_ascii=False))
        )

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        result = DirectorStageExecutor(
            ModelContextAssembler(repository, scope, ContextBudget(100_000)),
            make_semantic_handler(client),
            mode="semantic_only",
        )(StageExecutionContext(
            stage="DEEPEN",
            working_state=state,
            owner_text="我们每天现熬牛骨汤。",
            parameters={},
            candidate_revision=0,
            session_id=session_id,
            owner_message_id=owner_message_id,
        ))

    assert result.target_stage == "CREATE"
    assert len(bodies) == 2
    repair_payload = json.loads(bodies[1]["messages"][1]["content"])
    assert repair_payload["invalid_output"] == invalid
    assert "missing_material" in repair_payload["validation_error"]


def test_invalid_schema_repair_is_not_repaired_again_within_the_same_stage(
    director_repository,
) -> None:
    repository, scope, session_id = director_repository
    calls = 0
    invalid = {
        "result": "DIRECTION_OPTIONS",
        "message": "我先给你三个方向。",
        "direction": None,
        "owner_quote": "不该出现在这里",
        "new_facts": [],
        "new_constraints": [],
        "reason": "待确认。",
        "directions": [
            {"direction": "方向一", "reason": "理由一", "recommended": True},
            {"direction": "方向二", "reason": "理由二", "recommended": False},
            {"direction": "方向三", "reason": "理由三", "recommended": False},
        ],
    }

    def responder(_http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json=completion(json.dumps(invalid, ensure_ascii=False))
        )

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        executor = DirectorStageExecutor(
            ModelContextAssembler(repository, scope, ContextBudget(100_000)),
            make_semantic_handler(client),
            mode="semantic_only",
        )
        with pytest.raises(StageModelOutputSchemaError):
            DirectorOrchestrator(
                repository, scope, executor, max_internal_steps=2
            ).run(request(session_id, "one-repair-per-stage"))

    assert calls == 2


@pytest.mark.parametrize(
    ("repair_failure", "expected"),
    [
        ("timeout", DeepSeekTimeoutError),
        ("non-json", DeepSeekNonJSONResponseError),
        ("duplicate-json-key", DeepSeekNonJSONResponseError),
    ],
)
def test_schema_repair_transport_or_json_failure_never_sends_a_second_repair_request(
    director_repository,
    repair_failure: str,
    expected: type[Exception],
) -> None:
    repository, scope, session_id = director_repository
    before = snapshot(repository)
    calls = 0
    invalid = {
        "result": "DIRECTION_OPTIONS",
        "message": "我先给你三个方向。",
        "direction": None,
        "owner_quote": "不该出现在这里",
        "new_facts": [],
        "new_constraints": [],
        "reason": "待确认。",
        "directions": [
            {"direction": "方向一", "reason": "理由一", "recommended": True},
            {"direction": "方向二", "reason": "理由二", "recommended": False},
            {"direction": "方向三", "reason": "理由三", "recommended": False},
        ],
    }

    def responder(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200, json=completion(json.dumps(invalid, ensure_ascii=False))
            )
        if repair_failure == "timeout":
            raise httpx.ReadTimeout("repair timeout", request=http_request)
        content = (
            '{"result":"first","result":"duplicate"}'
            if repair_failure == "duplicate-json-key"
            else "not json"
        )
        return httpx.Response(200, json=completion(content))

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        executor = DirectorStageExecutor(
            ModelContextAssembler(repository, scope, ContextBudget(100_000)),
            make_semantic_handler(client),
            mode="semantic_only",
        )
        with pytest.raises(expected):
            DirectorOrchestrator(
                repository, scope, executor, max_internal_steps=2
            ).run(request(session_id, f"repair-{repair_failure}-no-retry"))

    assert calls == 2
    assert snapshot(repository) == before


def test_semantic_schema_repair_is_one_per_stage_and_two_per_turn(
    director_repository,
) -> None:
    repository, scope, session_id = director_repository
    before = snapshot(repository)
    bodies: list[dict] = []

    def responder(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        bodies.append(body)
        payload = json.loads(body["messages"][1]["content"])
        stage = payload["semantic_context"]["stage"]
        repairing = "invalid_output" in payload
        if stage == "EXPLORE":
            output = {
                "result": "DIRECTION_READY",
                "message": None,
                "direction": "现熬牛骨汤",
                "owner_quote": "现熬牛骨汤" if repairing else None,
                "new_facts": [{
                    "action": "ADD",
                    "statement": "每天现熬牛骨汤",
                    "owner_quote": "每天现熬牛骨汤",
                    "replaces_statement": None,
                }],
                "new_constraints": [],
                "reason": "老板已明确确认。",
                "directions": [],
            }
        elif stage == "DEEPEN":
            output = {
                "result": "MATERIAL_READY",
                "message": None,
                "new_facts": [],
                "new_constraints": [],
                "missing_material": [] if repairing else None,
                "reason": "素材足够。",
            }
        else:
            assert stage == "CREATE"
            output = {
                "title": None,
                "script_text": "我们每天现熬牛骨汤。",
                "shooting_notes": [],
            }
        return httpx.Response(
            200, json=completion(json.dumps(output, ensure_ascii=False))
        )

    turn = DirectorTurnRequest(
        session_id=session_id,
        client_message_id="repair-turn-budget",
        expected_state_version=0,
        owner_text="我确认就讲现熬牛骨汤，我们每天现熬牛骨汤。",
        request_format_version=1,
        parameters={},
    )
    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        executor = DirectorStageExecutor(
            ModelContextAssembler(repository, scope, ContextBudget(100_000)),
            make_semantic_handler(client),
            mode="semantic_only",
        )
        with pytest.raises(StageModelOutputSchemaError):
            DirectorOrchestrator(
                repository, scope, executor, max_internal_steps=5
            ).run(turn)

    assert [
        json.loads(body["messages"][1]["content"])["semantic_context"]["stage"]
        for body in bodies
    ] == ["EXPLORE", "EXPLORE", "DEEPEN", "DEEPEN", "CREATE"]
    assert sum(
        "invalid_output" in json.loads(body["messages"][1]["content"])
        for body in bodies
    ) == 2
    assert snapshot(repository) == before


def test_real_handler_mock_closes_explore_deepen_wait_in_one_atomic_owner_turn(
    director_repository,
    monkeypatch,
) -> None:
    repository, scope, session_id = director_repository
    bodies: list[dict] = []
    commit_calls = 0
    original_commit = repository.commit_successful_turn

    def counting_commit(commit_scope, prepared):
        nonlocal commit_calls
        commit_calls += 1
        return original_commit(commit_scope, prepared)

    monkeypatch.setattr(repository, "commit_successful_turn", counting_commit)

    def responder(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        bodies.append(body)
        context = json.loads(body["messages"][1]["content"])["model_context"]
        stage = context["stage_contract"]["stage"]
        state = deepcopy(context["working_state"])
        if stage == "EXPLORE":
            state["direction"] = {
                "item_id": "new:item:direction_1",
                "statement": "讲老板为什么一直坚持一道真实菜品。",
                "owner_confirmed": True,
                "evidence_refs": [
                    deepcopy(context["owner_evidence_references"][0])
                ],
                "inherited_from": None,
            }
            proposal = {
                "output_format_version": 1,
                "run_control": "CONTINUE",
                "target_stage": "DEEPEN",
                "transition_reason_code": "DIRECTION_CONFIRMED",
                "director_message": None,
                "gate": None,
                "review": None,
                "post_state": state,
            }
        else:
            assert stage == "DEEPEN"
            state["material_state"] = {
                "status": "INSUFFICIENT",
                "required_confirmations": [{
                    "item_id": "new:item:confirmation_1",
                    "statement": "补充这道菜被长期保留的一个具体经历。",
                    "reason": "核心表达仍缺一个可追溯的真实细节。",
                    "evidence_refs": [],
                    "inherited_from": None,
                }],
            }
            proposal = {
                "output_format_version": 1,
                "run_control": "WAIT_FOR_OWNER",
                "target_stage": "DEEPEN",
                "transition_reason_code": "OWNER_INPUT_REQUIRED",
                "director_message": "请补充这道菜一直保留到现在的一个具体经历。",
                "gate": {
                    "outcome": "BLOCKED",
                    "gate_code": "MATERIAL_INSUFFICIENT",
                    "explanation": "方向已确认，但还缺支撑核心表达的真实细节。",
                },
                "review": None,
                "post_state": state,
            }
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
            repository, scope, executor, max_internal_steps=3
        ).run(request(session_id, "mock-multistage"))

    assert len(bodies) == 2
    assert [
        json.loads(body["messages"][1]["content"])["model_context"]
        ["stage_contract"]["stage"]
        for body in bodies
    ] == ["EXPLORE", "DEEPEN"]
    for body, stage in zip(bodies, ("EXPLORE", "DEEPEN"), strict=True):
        assert DEEPSEEK_STAGE_PROMPTS[stage] in body["messages"][0]["content"]
    assert result.response["run_control"] == "WAIT_FOR_OWNER"
    assert result.response["stage"] == "DEEPEN"
    assert commit_calls == 1
    assert repository.connection.execute(
        "SELECT count(*) FROM director_turns WHERE session_id = ?", (session_id,)
    ).fetchone()[0] == 1
    assert repository.connection.execute(
        "SELECT count(*) FROM director_messages WHERE session_id = ?", (session_id,)
    ).fetchone()[0] == 2
    trace = json.loads(repository.connection.execute(
        "SELECT execution_trace_json FROM director_turns WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0])
    assert [step["entered_stage"] for step in trace["steps"]] == [
        "EXPLORE",
        "DEEPEN",
    ]
    state = repository.get_working_state(scope, session_id)
    assert state.state_version == 1
    assert state.stage == "DEEPEN"


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
