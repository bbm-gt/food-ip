from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from backend.app.director_core.context import ContextBudget, ContextMessage, ModelContext, ModelContextAssembler
from backend.app.director_core.database import apply_migrations, connect
from backend.app.director_core.orchestrator import (
    DirectorOrchestrator,
    DirectorStageExecutor,
    DirectorTurnRequest,
)
from backend.app.director_core.providers.deepseek import DeepSeekStageHandler
from backend.app.director_core.repository import AuthorizationScope, DirectorRepository
from backend.app.director_core.semantic_only import (
    SemanticOutputSchemaError,
    convert_semantic_output,
    validate_semantic_output,
)
from backend.app.director_core.stage_contract import stage_execution_contract
from backend.app.director_core.stage_handler import validate_resolved_stage_model_output
from backend.app.director_core.stage_handler import StageModelOutputSchemaError


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


def semantic_context(stage: str, state: dict | None = None, owner_text: str = "我们店只卖现熬牛肉粉。") -> SimpleNamespace:
    session_id = uid()
    owner_id = uid()
    reference = {
        "evidence_type": "owner_message",
        "target_id": owner_id,
        "target_session_id": session_id,
    }
    state_value = deepcopy(empty_state() if state is None else state)
    references = [reference]
    for container in (state_value["owner_facts"], state_value["owner_constraints"]):
        for item in container:
            references.extend(item["evidence_refs"])
    if state_value["direction"] is not None:
        references.extend(state_value["direction"]["evidence_refs"])
    return SimpleNamespace(
        stage=stage,
        stage_contract=stage_execution_contract(stage),
        working_state=state_value,
        current_owner_message=ContextMessage(owner_id, "OWNER", owner_text, 1, "CURRENT_TURN"),
        owner_evidence_references=tuple({(item["target_id"], item["target_session_id"]): item for item in references}.values()),
        session_id=session_id,
        to_dict=lambda: {
            "stage_contract": stage_execution_contract(stage),
            "working_state": deepcopy(state_value),
            "current_owner_message": {
                "id": owner_id, "role": "OWNER", "content": owner_text,
                "message_seq": 1, "turn_id": "CURRENT_TURN",
            },
        },
    )


def ref(context) -> dict:
    return context.owner_evidence_references[0]


def valid_output(stage: str, state: dict, raw: dict, context=None) -> dict:
    context = context or semantic_context(stage, state)
    envelope = convert_semantic_output(
        stage,
        state,
        owner_text=context.current_owner_message.content,
        owner_message_id=context.current_owner_message.id,
        owner_session_id=context.session_id,
        semantic_output=raw,
    )
    validated = validate_resolved_stage_model_output(envelope, context=context)
    return validated.model_dump(mode="json")


def test_each_stage_accepts_only_its_small_semantic_object() -> None:
    explore = {
        "result": "ASK_OWNER", "message": "这道粉最想让客人记住什么？", "direction": None,
        "new_facts": [], "new_constraints": [], "reason": "老板还没有确认内容方向。",
    }
    assert validate_semantic_output("EXPLORE", explore).result == "ASK_OWNER"
    with pytest.raises(SemanticOutputSchemaError):
        validate_semantic_output("EXPLORE", {**explore, "post_state": empty_state()})
    with pytest.raises(SemanticOutputSchemaError):
        validate_semantic_output("EXPLORE", {**explore, "result": "NOPE"})
    with pytest.raises(SemanticOutputSchemaError):
        validate_semantic_output("CREATE", {"title": None, "shooting_notes": []})
    with pytest.raises(SemanticOutputSchemaError):
        validate_semantic_output("REVIEW", {"result": "PASS", "problem": "不该有", "reason": "判断"})


@pytest.mark.parametrize(
    "owner_text,direction,fact,script",
    [
        ("我们店每天现熬牛骨汤，想让客人知道为什么值得等。", "讲现熬牛骨汤为什么值得等", "每天现熬牛骨汤", "我们家的牛肉粉不是把汤兑热就端上来。每天现熬的牛骨汤，要等它把味道熬出来。"),
        ("我们做潮汕粿条，牛肉都是当天现切。", "讲当天现切牛肉", "牛肉当天现切", "我们家的牛肉不是提前切好放着，客人点了才现切，入口才有那个鲜。"),
        ("我们是街边烧烤摊，炭火每天收摊前都要清干净。", "讲炭火烧烤的真实讲究", "每天收摊前清理炭火", "烧烤好不好吃，火是底子。我们每天收摊前把炭火清干净，第二天重新起火。"),
    ],
)
def test_real_restaurant_cases_run_through_semantic_conversion(
    owner_text: str, direction: str, fact: str, script: str
) -> None:
    explore_context = semantic_context("EXPLORE", owner_text=owner_text)
    explored = valid_output("EXPLORE", explore_context.working_state, {
        "result": "DIRECTION_READY", "message": None, "direction": direction,
        "new_facts": [fact], "new_constraints": [], "reason": "老板给出了明确的真实推广方向。",
    }, explore_context)
    state = explored["post_state"]
    create_context = semantic_context("CREATE", state, owner_text=owner_text)
    created = valid_output("CREATE", state, {
        "title": direction, "script_text": script, "shooting_notes": ["拍真实制作过程"],
    }, create_context)
    assert created["target_stage"] == "REVIEW"
    assert created["post_state"]["draft"]["content"]["script_text"] == script


def test_explore_and_deepen_programmatically_bind_owner_evidence_and_preserve_state() -> None:
    context = semantic_context("EXPLORE")
    raw = {
        "result": "DIRECTION_READY", "message": None, "direction": "讲现熬牛肉粉为什么值得等",
        "new_facts": ["每天现熬牛骨汤"], "new_constraints": ["语气要像老板本人"],
        "reason": "老板明确给出可继续的真实方向。",
    }
    post = valid_output("EXPLORE", context.working_state, raw, context)
    assert post["target_stage"] == "DEEPEN"
    state = post["post_state"]
    assert state["direction"]["statement"] == "讲现熬牛肉粉为什么值得等"
    assert all(item["evidence_refs"] == [ref(context)] for item in state["owner_facts"] + state["owner_constraints"])
    assert all(UUID(item["item_id"]).version == 4 for item in state["owner_facts"] + state["owner_constraints"])

    deepen = semantic_context("DEEPEN", state)
    deepen_raw = {
        "result": "ASK_OWNER", "message": "牛骨汤每天几点开始熬？", "new_facts": [],
        "new_constraints": [], "missing_material": ["牛骨汤每天开始熬的时间"],
        "reason": "还缺一个能支撑脚本的真实细节。",
    }
    waited = valid_output("DEEPEN", state, deepen_raw, deepen)
    assert waited["run_control"] == "WAIT_FOR_OWNER"
    assert waited["post_state"]["material_state"]["status"] == "INSUFFICIENT"
    assert waited["post_state"]["owner_facts"] == state["owner_facts"]
    assert waited["post_state"]["owner_constraints"] == state["owner_constraints"]


def direction_and_material_state(context) -> dict:
    state = empty_state()
    state["direction"] = {
        "item_id": uid(), "statement": "讲现熬牛肉粉为什么值得等", "owner_confirmed": True,
        "evidence_refs": [ref(context)], "inherited_from": None,
    }
    state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
    return state


def test_create_replaces_draft_and_review_creates_review_and_routes_all_four_results() -> None:
    create_context = semantic_context("CREATE")
    state = direction_and_material_state(create_context)
    created = valid_output("CREATE", state, {
        "title": "一碗粉，为什么要等二十分钟",
        "script_text": "我们家的牛肉粉不是把汤兑热就端上来。每天现熬的牛骨汤，要等它把味道熬出来。",
        "shooting_notes": ["拍锅里牛骨汤翻滚", "老板出镜端碗"],
    }, create_context)
    assert created["target_stage"] == "REVIEW"
    draft_id = created["post_state"]["draft"]["draft_id"]
    assert UUID(draft_id).version == 4
    assert created["post_state"]["review"] is None

    outcomes = {
        "REWRITE": ("CREATE", "WRITING_PROBLEM", "CONTENT_INCOMPLETE"),
        "NEED_MATERIAL": ("DEEPEN", "MATERIAL_PROBLEM", "MATERIAL_INSUFFICIENT"),
        "CHANGE_DIRECTION": ("EXPLORE", "DIRECTION_PROBLEM", "DIRECTION_NOT_CONFIRMED"),
    }
    for result, (target, root, gate_code) in outcomes.items():
        review_context = semantic_context("REVIEW", created["post_state"])
        raw = {
            "result": result, "problem": "需要老板补充一个真实细节。" if result != "REWRITE" else "开头不够像老板在说话。",
            "reason": "这是当前最影响成片的根因。",
        }
        reviewed = valid_output("REVIEW", created["post_state"], raw, review_context)
        assert reviewed["target_stage"] == target
        assert reviewed["review"]["root_cause"] == root
        assert reviewed["gate"]["gate_code"] == gate_code
        assert reviewed["post_state"]["review"]["against_draft_id"] == draft_id
        assert UUID(reviewed["post_state"]["review"]["review_id"]).version == 4
        if result == "CHANGE_DIRECTION":
            assert reviewed["post_state"]["direction"] is None
            assert reviewed["post_state"]["rejected_items"][-1]["item_id"] == state["direction"]["item_id"]

    passed_context = semantic_context("REVIEW", created["post_state"])
    passed = valid_output("REVIEW", created["post_state"], {
        "result": "PASS", "problem": None, "reason": "方向、素材和表达都已经可以拍摄。",
    }, passed_context)
    assert passed["run_control"] == "READY"
    assert passed["target_stage"] == "READY"


def test_new_draft_clears_old_review_and_semantic_failures_happen_before_commit(tmp_path) -> None:
    path = tmp_path / "semantic.sqlite"
    connection = connect(path, busy_timeout_ms=100, check_same_thread=False)
    apply_migrations(connection)
    repo = DirectorRepository(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = repo.create_session(scope)
    calls: list[str] = []

    def model(context):
        calls.append(context.stage_contract["stage"])
        return {
            "EXPLORE": {
                "result": "DIRECTION_READY", "message": None, "direction": "讲现熬牛肉粉",
                "new_facts": ["每天现熬牛骨汤"], "new_constraints": [], "reason": "方向明确。",
            },
            "DEEPEN": {
                "result": "MATERIAL_READY", "message": None, "new_facts": [],
                "new_constraints": [], "missing_material": [], "reason": "素材足够。",
            },
            "CREATE": {
                "title": "现熬的一碗粉", "script_text": "我们每天现熬牛骨汤，汤好不好，客人喝一口就知道。",
                "shooting_notes": ["拍熬汤"],
            },
            "REVIEW": {"result": "PASS", "problem": None, "reason": "真实、完整、可拍。"},
        }[context.stage_contract["stage"]]

    assembler = ModelContextAssembler(repo, scope, ContextBudget(100000))
    executor = DirectorStageExecutor(assembler, model, mode="semantic_only")
    request = DirectorTurnRequest(
        session_id=session.id, client_message_id=uid(), expected_state_version=0,
        owner_text="老板说：我们店每天现熬牛骨汤，想推广这件事。",
        request_format_version=1, parameters={},
    )
    result = DirectorOrchestrator(repo, scope, executor, 6).run(request)
    assert result.response["run_control"] == "READY"
    assert calls == ["EXPLORE", "DEEPEN", "CREATE", "REVIEW"]
    persisted = repo.get_working_state(scope, session.id)
    assert persisted.stage == "READY"
    assert persisted.state_json["draft"]["draft_id"]
    assert repo.get_ready_content(scope, result.response["ready_content_id"])["final_content_json"]["script_text"].startswith("我们每天")

    before = repo.connection.execute("SELECT COUNT(*) FROM director_turns").fetchone()[0]
    replay = DirectorOrchestrator(repo, scope, executor, 6).run(request)
    assert replay.replayed is True
    assert repo.connection.execute("SELECT COUNT(*) FROM director_turns").fetchone()[0] == before


def test_semantic_schema_failure_after_an_internal_step_leaves_no_persistence(tmp_path) -> None:
    path = tmp_path / "semantic-failure.sqlite"
    connection = connect(path, busy_timeout_ms=100, check_same_thread=False)
    apply_migrations(connection)
    repo = DirectorRepository(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = repo.create_session(scope)
    calls = 0

    def invalid_after_explore(context):
        nonlocal calls
        calls += 1
        if context.stage_contract["stage"] == "EXPLORE":
            return {
                "result": "DIRECTION_READY", "message": None, "direction": "讲现熬牛肉粉",
                "new_facts": [], "new_constraints": [], "reason": "方向明确。",
            }
        return {"result": "MATERIAL_READY", "message": None, "new_facts": []}

    executor = DirectorStageExecutor(
        ModelContextAssembler(repo, scope, ContextBudget(100000)),
        invalid_after_explore,
        mode="semantic_only",
    )
    request = DirectorTurnRequest(
        session_id=session.id, client_message_id=uid(), expected_state_version=0,
        owner_text="我们店每天现熬牛骨汤。", request_format_version=1, parameters={},
    )
    with pytest.raises(StageModelOutputSchemaError):
        DirectorOrchestrator(repo, scope, executor, 6).run(request)
    assert calls == 2
    assert repo.connection.execute("SELECT COUNT(*) FROM director_turns").fetchone()[0] == 0
    assert repo.connection.execute("SELECT COUNT(*) FROM director_messages").fetchone()[0] == 0
    assert repo.get_working_state(scope, session.id).state_version == 0


def test_semantic_provider_prompt_is_small_and_has_no_state_contract() -> None:
    context = ModelContext(
        rules={"owner_fact_boundary": "Only owner"},
        stage_contract=stage_execution_contract("CREATE"),
        working_state=empty_state(),
        current_owner_message=ContextMessage(uid(), "OWNER", "老板说牛肉粉每天现熬。", 1, "CURRENT_TURN"),
        source_ready_content=None, checkpoint=None, history_turns=(), evidence_messages=(),
        owner_evidence_references=(), estimated_units=1,
    )
    body: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        body.append(json.loads(request.content))
        content = json.dumps({"title": "现熬牛肉粉", "script_text": "我们每天现熬牛骨汤。", "shooting_notes": []}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        DeepSeekStageHandler(api_key="test-secret", client=client, stage_mode="semantic_only")(context)
    legacy_body = DeepSeekStageHandler(api_key="test-secret")._request_body(
        context, stage="CREATE", regenerate_json=False
    )
    semantic_body = DeepSeekStageHandler(api_key="test-secret", stage_mode="semantic_only")._request_body(
        context, stage="CREATE", regenerate_json=False
    )
    user_payload = json.loads(body[0]["messages"][1]["content"])
    assert set(user_payload) == {"semantic_context"}
    assert "model_context" not in body[0]["messages"][1]["content"]
    assert "output_format_version" not in body[0]["messages"][0]["content"]
    assert "Working State" not in body[0]["messages"][0]["content"]
    assert len(semantic_body["messages"][0]["content"]) + len(semantic_body["messages"][1]["content"]) < (
        len(legacy_body["messages"][0]["content"]) + len(legacy_body["messages"][1]["content"])
    ) / 3
