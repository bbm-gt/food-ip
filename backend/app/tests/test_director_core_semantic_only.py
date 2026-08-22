from __future__ import annotations

from copy import deepcopy
import json
import os
import subprocess
import sys
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
    SemanticConversionError,
    SemanticOutputSchemaError,
    build_business_feedback,
    convert_semantic_output,
    semantic_model_input,
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


def fact_change(statement: str, quote: str, action: str = "ADD", replaces: str | None = None) -> dict:
    return {
        "action": action, "statement": statement if action != "REMOVE" else None,
        "owner_quote": quote, "replaces_statement": replaces,
    }


def constraint_change(
    statement: str | None, quote: str, kind: str = "CONTENT_REQUIREMENT",
    action: str = "ADD", replaces: str | None = None,
) -> dict:
    return {
        "action": action, "statement": statement, "owner_quote": quote,
        "replaces_statement": replaces, "constraint_kind": kind,
    }


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
        "owner_quote": None, "new_facts": [], "new_constraints": [], "reason": "老板还没有确认内容方向。",
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
        ("我确认就讲每天现熬牛骨汤。", "每天现熬牛骨汤", "每天现熬牛骨汤", "我们家的牛肉粉不是把汤兑热就端上来。每天现熬的牛骨汤，要等它把味道熬出来。"),
        ("我确认就讲牛肉当天现切。", "牛肉当天现切", "牛肉当天现切", "我们家的牛肉不是提前切好放着，客人点了才现切，入口才有那个鲜。"),
        ("我确认就讲每天收摊前清理炭火。", "每天收摊前清理炭火", "每天收摊前清理炭火", "烧烤好不好吃，火是底子。我们每天收摊前把炭火清干净，第二天重新起火。"),
    ],
)
def test_real_restaurant_cases_run_through_semantic_conversion(
    owner_text: str, direction: str, fact: str, script: str
) -> None:
    explore_context = semantic_context("EXPLORE", owner_text=owner_text)
    explored = valid_output("EXPLORE", explore_context.working_state, {
        "result": "DIRECTION_READY", "message": None, "direction": direction,
        "owner_quote": direction, "new_facts": [fact_change(fact, fact)], "new_constraints": [],
        "reason": "老板给出了明确的真实推广方向。",
    }, explore_context)
    state = explored["post_state"]
    create_context = semantic_context("CREATE", state, owner_text=owner_text)
    created = valid_output("CREATE", state, {
        "title": direction, "script_text": script, "shooting_notes": ["拍真实制作过程"],
    }, create_context)
    assert created["target_stage"] == "REVIEW"
    assert created["post_state"]["draft"]["content"]["script_text"] == script
    assert created["post_state"]["draft"]["content"]["shooting_notes"] == []


def test_script_core_enforces_three_directions_one_recommendation_and_one_question() -> None:
    base = {
        "result": "DIRECTION_OPTIONS", "message": "选一个方向。", "direction": None,
        "owner_quote": None, "new_facts": [], "new_constraints": [], "reason": "方向已形成。",
    }
    with pytest.raises(SemanticOutputSchemaError):
        validate_semantic_output("EXPLORE", base | {"directions": [
            {"direction": "方向一", "reason": "理由一", "recommended": True},
            {"direction": "方向二", "reason": "理由二", "recommended": False},
        ]})
    with pytest.raises(SemanticOutputSchemaError):
        validate_semantic_output("EXPLORE", base | {"directions": [
            {"direction": "方向一", "reason": "理由一", "recommended": True},
            {"direction": "方向二", "reason": "理由二", "recommended": True},
            {"direction": "方向三", "reason": "理由三", "recommended": False},
        ]})
    with pytest.raises(SemanticOutputSchemaError):
        validate_semantic_output("DEEPEN", {
            "result": "ASK_OWNER", "message": "请回答两个问题。", "new_facts": [],
            "new_constraints": [], "missing_material": ["问题一", "问题二"], "reason": "素材不足。",
        })

    known_context = semantic_context("DEEPEN", owner_text="我们每天九点开门。")
    known_state = empty_state()
    known_state["owner_facts"] = [{
        "item_id": uid(), "statement": "营业时间是每天九点", "evidence_refs": [ref(known_context)],
        "supersedes_item_ids": [], "inherited_from": None,
    }]
    with pytest.raises(SemanticConversionError):
        convert_semantic_output(
            "DEEPEN", known_state, owner_text=known_context.current_owner_message.content,
            owner_message_id=known_context.current_owner_message.id,
            owner_session_id=ref(known_context)["target_session_id"],
            semantic_output={
                "result": "ASK_OWNER", "message": "你们几点营业？", "new_facts": [],
                "new_constraints": [], "missing_material": ["营业时间"], "reason": "需要时间。",
            },
        )


def test_owner_confirmation_clears_only_matching_unconfirmed_inference() -> None:
    owner_text = "我确认，牛骨汤每天凌晨四点开始熬，而且视频里不能提价格。"
    context = semantic_context("DEEPEN", owner_text=owner_text)
    state = direction_and_material_state(context)
    state["unconfirmed_inferences"] = [{
        "item_id": uid(), "statement": "牛骨汤每天凌晨四点开始熬", "reason": "AI 推测，老板未确认",
    }, {
        "item_id": uid(), "statement": "视频里不能提价格", "reason": "AI 推测，老板未确认",
    }, {
        "item_id": uid(), "statement": "老板每天亲自看火", "reason": "AI 推测，老板未确认",
    }]

    confirmed = valid_output("DEEPEN", state, {
        "result": "MATERIAL_READY", "message": None,
        "new_facts": [fact_change("牛骨汤每天凌晨四点开始熬", "牛骨汤每天凌晨四点开始熬")],
        "new_constraints": [constraint_change("视频里不能提价格", "视频里不能提价格")],
        "missing_material": [], "reason": "老板明确确认了熬汤时间和内容限制。",
    }, context)

    assert [item["statement"] for item in confirmed["post_state"]["owner_facts"]] == [
        "牛骨汤每天凌晨四点开始熬"
    ]
    assert confirmed["post_state"]["owner_facts"][0]["supersedes_item_ids"] == []
    assert confirmed["post_state"]["rejected_items"] == []
    assert [item["statement"] for item in confirmed["post_state"]["owner_constraints"]] == [
        "视频里不能提价格"
    ]
    assert [item["statement"] for item in confirmed["post_state"]["unconfirmed_inferences"]] == [
        "老板每天亲自看火"
    ]


def test_review_blocks_when_draft_depends_on_unconfirmed_inference() -> None:
    context = semantic_context("REVIEW")
    state = direction_and_material_state(context)
    state["unconfirmed_inferences"] = [{
        "item_id": uid(), "statement": "每天凌晨四点开始熬汤", "reason": "AI 推测，老板未确认",
    }]
    state["draft"] = {
        "draft_id": uid(),
        "content": {"title": "每天现熬", "script_text": "我们每天凌晨四点开始熬汤。", "shooting_notes": []},
        "content_status": "FINAL_CANDIDATE", "based_on_ready_content_id": None,
    }
    output = {
        "result": "PASS", "problem": None, "reason": "表达自然。", "preserve": [], "change": [],
    }
    reviewed = valid_output("REVIEW", state, output, semantic_context("REVIEW", state))
    assert reviewed["run_control"] == "CONTINUE"
    assert reviewed["target_stage"] == "DEEPEN"
    assert reviewed["review"]["root_cause"] == "MATERIAL_PROBLEM"
    assert len(reviewed["post_state"]["material_state"]["required_confirmations"]) == 1
    assert build_business_feedback("REVIEW", state, output)["target_stage"] == "DEEPEN"


def test_review_allows_ready_when_unconfirmed_inference_is_unrelated_to_draft() -> None:
    context = semantic_context("REVIEW")
    state = direction_and_material_state(context)
    state["unconfirmed_inferences"] = [{
        "item_id": uid(), "statement": "老板平时喜欢穿黑色围裙", "reason": "AI 推测，老板未确认",
    }]
    state["draft"] = {
        "draft_id": uid(),
        "content": {
            "title": "每天现熬",
            "script_text": "我们每天凌晨四点开始熬汤，想把这一口认真做好。",
            "shooting_notes": [],
        },
        "content_status": "FINAL_CANDIDATE", "based_on_ready_content_id": None,
    }
    output = {
        "result": "PASS", "problem": None, "reason": "事实、方向和表达都已通过。",
        "preserve": [], "change": [],
    }

    reviewed = valid_output("REVIEW", state, output, semantic_context("REVIEW", state))

    assert reviewed["run_control"] == "READY"
    assert reviewed["target_stage"] == "READY"
    assert reviewed["post_state"]["unconfirmed_inferences"] == state["unconfirmed_inferences"]
    assert build_business_feedback("REVIEW", state, output) is None


def test_review_model_input_contains_active_truth_and_uncertainty_context() -> None:
    context = semantic_context("REVIEW")
    state = direction_and_material_state(context)
    state["owner_facts"] = [{
        "item_id": uid(), "statement": "早上六点开始熬汤",
        "evidence_refs": [ref(context)], "supersedes_item_ids": [], "inherited_from": None,
    }]
    state["owner_constraints"] = [{
        "item_id": uid(), "statement": "不要提价格", "evidence_refs": [ref(context)],
        "constraint_kind": "PROHIBITION", "inherited_from": None,
    }]
    state["unconfirmed_inferences"] = [{
        "item_id": uid(), "statement": "老板每天亲自看火", "reason": "AI 推测，老板未确认",
    }]
    state["draft"] = {
        "draft_id": uid(),
        "content": {"title": "每天现熬", "script_text": "早上六点，我们开始熬汤。", "shooting_notes": []},
        "content_status": "FINAL_CANDIDATE", "based_on_ready_content_id": None,
    }

    payload = semantic_model_input(semantic_context("REVIEW", state))

    assert payload["facts"] == [{"statement": "早上六点开始熬汤"}]
    assert payload["constraints"] == [{"statement": "不要提价格", "category": "PROHIBITION"}]
    assert payload["unconfirmed_inferences"] == [{
        "statement": "老板每天亲自看火", "reason": "AI 推测，老板未确认",
    }]
    assert payload["draft"] == state["draft"]["content"]


@pytest.mark.parametrize("stage", ["EXPLORE", "DEEPEN", "CREATE", "REVIEW"])
def test_each_stage_model_input_contains_unconfirmed_inferences(stage: str) -> None:
    state = empty_state()
    state["unconfirmed_inferences"] = [{
        "item_id": uid(), "statement": "汤一般凌晨四点开火慢熬", "reason": "AI 推测，老板未确认",
    }]

    payload = semantic_model_input(semantic_context(stage, state))

    assert payload["unconfirmed_inferences"] == [{
        "statement": "汤一般凌晨四点开火慢熬", "reason": "AI 推测，老板未确认",
    }]


def test_explore_and_deepen_programmatically_bind_owner_evidence_and_preserve_state() -> None:
    context = semantic_context("EXPLORE")
    raw = {
        "result": "DIRECTION_READY", "message": None, "direction": "现熬牛肉粉",
        "owner_quote": "现熬牛肉粉", "new_facts": [fact_change("现熬牛肉粉", "现熬牛肉粉")],
        "new_constraints": [constraint_change("现熬牛肉粉", "现熬牛肉粉", "PREFERENCE")],
        "reason": "老板明确给出可继续的真实方向。",
    }
    post = valid_output("EXPLORE", context.working_state, raw, context)
    assert post["target_stage"] == "DEEPEN"
    state = post["post_state"]
    assert state["direction"]["statement"] == "现熬牛肉粉"
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
            "reason": "这是当前最影响成片的根因。", "preserve": ["真实事实"],
            "change": ["补足审核指出的问题"],
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
        "preserve": ["真实事实"], "change": [],
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
                "result": "DIRECTION_READY", "message": None, "direction": "现熬牛骨汤",
                "owner_quote": "现熬牛骨汤", "new_facts": [fact_change("现熬牛骨汤", "现熬牛骨汤")],
                "new_constraints": [], "reason": "方向明确。",
            },
            "DEEPEN": {
                "result": "MATERIAL_READY", "message": None, "new_facts": [],
                "new_constraints": [], "missing_material": [], "reason": "素材足够。",
            },
            "CREATE": {
                "title": "现熬的一碗粉", "script_text": "我们每天现熬牛骨汤，汤好不好，客人喝一口就知道。",
                "shooting_notes": ["拍熬汤"],
            },
            "REVIEW": {"result": "PASS", "problem": None, "reason": "真实、完整、可拍。", "preserve": ["真实事实"], "change": []},
        }[context.stage_contract["stage"]]

    assembler = ModelContextAssembler(repo, scope, ContextBudget(100000))
    executor = DirectorStageExecutor(assembler, model, mode="semantic_only")
    request = DirectorTurnRequest(
        session_id=session.id, client_message_id=uid(), expected_state_version=0,
        owner_text="老板说：我确认就讲现熬牛骨汤，我们店每天现熬牛骨汤，想推广这件事。",
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
                "result": "DIRECTION_READY", "message": None, "direction": "现熬牛骨汤",
                "owner_quote": "现熬牛骨汤", "new_facts": [], "new_constraints": [], "reason": "方向明确。",
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


def test_direction_candidate_is_not_owner_confirmed_and_quote_is_required() -> None:
    context = semantic_context("EXPLORE", owner_text="我想推广套餐，但还没想好讲什么。")
    candidate = valid_output("EXPLORE", context.working_state, {
        "result": "DIRECTION_CANDIDATE", "message": "可以先考虑讲套餐怎么解决一顿饭的选择。",
        "direction": "讲套餐怎么解决一顿饭的选择", "owner_quote": None,
        "new_facts": [], "new_constraints": [], "reason": "这是一个值得验证的创意方向。",
    }, context)
    assert candidate["post_state"]["direction"] is None
    assert candidate["post_state"]["ai_judgments"][0]["judgment_kind"] == "DIRECTION_CANDIDATE"
    assert candidate["run_control"] == "WAIT_FOR_OWNER"

    with pytest.raises(SemanticOutputSchemaError):
        validate_semantic_output("EXPLORE", {
            "result": "DIRECTION_READY", "message": None,
            "direction": "推广套餐", "owner_quote": None,
            "new_facts": [], "new_constraints": [], "reason": "确认。",
        })
    with pytest.raises(SemanticConversionError):
        valid_output("EXPLORE", context.working_state, {
            "result": "DIRECTION_READY", "message": None,
            "direction": "套餐的爆款创意", "owner_quote": "我想推广套餐",
            "new_facts": [], "new_constraints": [], "reason": "确认。",
        }, context)


def test_owner_quote_safety_downgrades_expansion_and_rejects_missing_quote() -> None:
    context = semantic_context("DEEPEN", owner_text="我们店每天现熬牛骨汤。")
    context.working_state = direction_and_material_state(context)
    expanded = valid_output("DEEPEN", context.working_state, {
        "result": "MATERIAL_READY", "message": None,
        "new_facts": [fact_change("每天现熬牛骨汤，汤底要熬八小时", "每天现熬牛骨汤")],
        "new_constraints": [], "missing_material": [], "reason": "材料足够。",
    }, context)
    assert expanded["post_state"]["owner_facts"] == []
    assert expanded["post_state"]["unconfirmed_inferences"][0]["statement"].startswith("每天现熬")

    with pytest.raises(SemanticConversionError):
        valid_output("DEEPEN", context.working_state, {
            "result": "MATERIAL_READY", "message": None,
            "new_facts": [fact_change("每天现熬牛骨汤", "老板说每天现熬牛骨汤")],
            "new_constraints": [], "missing_material": [], "reason": "材料足够。",
        }, context)


def test_fact_correction_replaces_current_fact_without_hidden_history() -> None:
    context = semantic_context("DEEPEN", owner_text="我们不是凌晨四点熬汤，是早上六点。买一送一活动也不做了。")
    old_ref = {"evidence_type": "owner_message", "target_id": uid(), "target_session_id": context.session_id}
    state = empty_state()
    state["direction"] = {
        "item_id": uid(), "statement": "讲活动期限和价格变化", "owner_confirmed": True,
        "evidence_refs": [old_ref], "inherited_from": None,
    }
    state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}
    state["owner_facts"] = [{
        "item_id": uid(), "statement": "凌晨四点熬汤", "evidence_refs": [old_ref],
        "supersedes_item_ids": [], "inherited_from": None,
    }, {
        "item_id": uid(), "statement": "买一送一活动", "evidence_refs": [old_ref],
        "supersedes_item_ids": [], "inherited_from": None,
    }]
    state["unconfirmed_inferences"] = [{
        "item_id": uid(), "statement": "凌晨四点熬汤", "reason": "此前尚未确认",
    }]
    context.working_state = state
    context.owner_evidence_references = tuple([*context.owner_evidence_references, old_ref])
    corrected = valid_output("DEEPEN", state, {
        "result": "MATERIAL_READY", "message": None,
        "new_facts": [
            fact_change("早上六点熬汤", "不是凌晨四点熬汤，是早上六点", "CORRECT", "凌晨四点熬汤"),
            fact_change("", "买一送一活动也不做了", "REMOVE", "买一送一活动"),
        ],
        "new_constraints": [], "missing_material": [], "reason": "老板纠正了活动信息。",
    }, context)
    facts = corrected["post_state"]["owner_facts"]
    assert [item["statement"] for item in facts] == ["早上六点熬汤"]
    assert facts[0]["supersedes_item_ids"] == []
    assert corrected["post_state"]["rejected_items"] == []
    assert corrected["post_state"]["unconfirmed_inferences"] == []

    ambiguous = deepcopy(state)
    ambiguous["owner_facts"].append({
        "item_id": uid(), "statement": "凌晨四点熬汤", "evidence_refs": [old_ref],
        "supersedes_item_ids": [], "inherited_from": None,
    })
    ambiguous_context = semantic_context("DEEPEN", ambiguous, context.current_owner_message.content)
    ambiguous_context.owner_evidence_references = tuple([*ambiguous_context.owner_evidence_references, old_ref])
    with pytest.raises(SemanticConversionError):
        valid_output("DEEPEN", ambiguous, {
            "result": "MATERIAL_READY", "message": None,
            "new_facts": [fact_change(
                "早上六点熬汤", "不是凌晨四点熬汤，是早上六点", "CORRECT", "凌晨四点熬汤",
            )],
            "new_constraints": [], "missing_material": [], "reason": "纠正。",
        }, ambiguous_context)


def test_fact_correction_adds_current_value_when_old_value_was_never_active() -> None:
    context = semantic_context("DEEPEN", owner_text="我们不是凌晨四点熬汤，是早上六点。")
    state = direction_and_material_state(context)
    state["unconfirmed_inferences"] = [{
        "item_id": uid(), "statement": "汤一般凌晨四点开火慢熬", "reason": "AI 推测，老板未确认",
    }]

    corrected = valid_output("DEEPEN", state, {
        "result": "MATERIAL_READY", "message": None,
        "new_facts": [fact_change(
            "早上六点熬汤", "不是凌晨四点熬汤，是早上六点", "CORRECT", "汤一般凌晨四点开火慢熬",
        )],
        "new_constraints": [], "missing_material": [], "reason": "老板明确说明了实际熬汤时间。",
    }, context)

    assert [item["statement"] for item in corrected["post_state"]["owner_facts"]] == ["早上六点熬汤"]
    assert corrected["post_state"]["owner_facts"][0]["supersedes_item_ids"] == []
    assert corrected["post_state"]["unconfirmed_inferences"] == []
    assert corrected["post_state"]["rejected_items"] == []


def test_fact_correction_without_replacement_adds_current_value_but_remove_stays_strict() -> None:
    context = semantic_context("DEEPEN", owner_text="我们不是凌晨四点熬汤，是早上六点。")
    state = direction_and_material_state(context)
    correction = fact_change(
        "早上六点熬汤", "不是凌晨四点熬汤，是早上六点", "CORRECT", None,
    )

    validated = validate_semantic_output("DEEPEN", {
        "result": "MATERIAL_READY", "message": None, "new_facts": [correction],
        "new_constraints": [], "missing_material": [], "reason": "老板说明了实际时间。",
    })
    assert validated.new_facts[0].replaces_statement is None

    corrected = valid_output("DEEPEN", state, {
        "result": "MATERIAL_READY", "message": None, "new_facts": [correction],
        "new_constraints": [], "missing_material": [], "reason": "老板说明了实际时间。",
    }, context)
    assert [item["statement"] for item in corrected["post_state"]["owner_facts"]] == ["早上六点熬汤"]
    assert corrected["post_state"]["rejected_items"] == []

    with pytest.raises(SemanticOutputSchemaError):
        validate_semantic_output("DEEPEN", {
            "result": "MATERIAL_READY", "message": None,
            "new_facts": [fact_change("", "不再凌晨四点熬汤", "REMOVE", None)],
            "new_constraints": [], "missing_material": [], "reason": "老板要求删除。",
        })


def test_missing_material_is_reconciled_and_review_feedback_reaches_create() -> None:
    context = semantic_context("DEEPEN", owner_text="老板回答了营业时间是早上九点。")
    state = empty_state()
    state["material_state"] = {
        "status": "INSUFFICIENT",
        "required_confirmations": [{
            "item_id": uid(), "statement": "营业时间", "reason": "需要确认",
            "evidence_refs": [], "inherited_from": None,
        }],
    }
    asked = valid_output("DEEPEN", state, {
        "result": "ASK_OWNER", "message": "还需要确认招牌菜是什么。",
        "new_facts": [], "new_constraints": [], "missing_material": ["招牌菜"],
        "reason": "营业时间已经得到回答。",
    }, context)
    assert [item["statement"] for item in asked["post_state"]["material_state"]["required_confirmations"]] == ["招牌菜"]

    review_state = direction_and_material_state(semantic_context("REVIEW"))
    review_state["draft"] = {
        "draft_id": uid(), "content": {"title": None, "script_text": "脚本", "shooting_notes": []},
        "content_status": "FINAL_CANDIDATE", "based_on_ready_content_id": None,
    }
    review_context = semantic_context("REVIEW", review_state)
    feedback = valid_output("REVIEW", review_state, {
        "result": "REWRITE", "problem": "开头没有说清招牌菜为什么值得来。",
        "reason": "核心吸引点没有进入第一句。", "preserve": ["现有真实做法"],
        "change": ["第一句直接说清招牌菜和来店理由"],
    }, review_context)
    assert feedback["target_stage"] == "CREATE"
    business_feedback = build_business_feedback("REVIEW", review_state, {
        "result": "REWRITE", "problem": "开头没有说清招牌菜为什么值得来。",
        "reason": "核心吸引点没有进入第一句。", "preserve": ["现有真实做法"],
        "change": ["第一句直接说清招牌菜和来店理由"],
    })
    create_context = semantic_context("CREATE", feedback["post_state"])
    create_context.business_feedback = business_feedback
    create_input = semantic_model_input(create_context)
    assert create_input["draft"]["script_text"] == "脚本"
    assert create_input["modification_goal"]["problem"].startswith("开头")
    assert create_input["modification_goal"]["change"]


def test_internal_rewrite_loop_passes_review_feedback_and_replaces_draft(tmp_path) -> None:
    path = tmp_path / "semantic-rewrite.sqlite"
    connection = connect(path, busy_timeout_ms=100, check_same_thread=False)
    apply_migrations(connection)
    repo = DirectorRepository(connection)
    scope = AuthorizationScope("workspace-1", "project-1")
    session = repo.create_session(scope)
    create_inputs: list[dict] = []
    draft_ids: list[str] = []

    def model(context):
        stage = context.stage_contract["stage"]
        if stage == "EXPLORE":
            return {
                "result": "DIRECTION_READY", "message": None, "direction": "现熬牛骨汤",
                "owner_quote": "我确认就讲现熬牛骨汤", "new_facts": [], "new_constraints": [],
                "reason": "老板明确确认方向。",
            }
        if stage == "DEEPEN":
            return {
                "result": "MATERIAL_READY", "message": None, "new_facts": [],
                "new_constraints": [], "missing_material": [], "reason": "材料足够。",
            }
        if stage == "CREATE":
            payload = semantic_model_input(context)
            create_inputs.append(payload)
            draft_ids.append(context.working_state["draft"]["draft_id"] if context.working_state["draft"] else "")
            script = "开头先说清每天现熬牛骨汤。" if len(create_inputs) == 1 else "我们每天现熬牛骨汤，客人愿意等，是因为这一锅汤值得。"
            return {"title": "现熬牛骨汤", "script_text": script, "shooting_notes": ["拍熬汤"]}
        if len(create_inputs) == 1:
            return {
                "result": "REWRITE", "problem": "开头没有说清为什么值得等。",
                "reason": "第一句没有把老板想表达的核心说出来。",
                "preserve": ["每天现熬牛骨汤"], "change": ["开头直接说明值得等待的理由"],
            }
        return {
            "result": "PASS", "problem": None, "reason": "修改目标已落实，可以拍摄。",
            "preserve": ["每天现熬牛骨汤"], "change": [],
        }

    executor = DirectorStageExecutor(
        ModelContextAssembler(repo, scope, ContextBudget(100000)), model, mode="semantic_only"
    )
    request = DirectorTurnRequest(
        session_id=session.id, client_message_id=uid(), expected_state_version=0,
        owner_text="我确认就讲现熬牛骨汤，我们店每天现熬牛骨汤。", request_format_version=1, parameters={},
    )
    result = DirectorOrchestrator(repo, scope, executor, 8).run(request)

    assert result.response["run_control"] == "READY"
    assert len(create_inputs) == 2
    assert draft_ids[0] == ""
    assert create_inputs[1]["draft"]["script_text"] == "开头先说清每天现熬牛骨汤。"
    assert create_inputs[1]["modification_goal"] == {
        "kind": "review", "target_stage": "CREATE",
        "problem": "开头没有说清为什么值得等。",
        "reason": "第一句没有把老板想表达的核心说出来。",
        "preserve": ["每天现熬牛骨汤"], "change": ["开头直接说明值得等待的理由"],
    }
    persisted = repo.get_working_state(scope, session.id)
    assert draft_ids[1]
    assert persisted.state_json["draft"]["draft_id"] != draft_ids[1]
    assert persisted.state_json["draft"]["content"]["script_text"].startswith("我们每天现熬")
    assert persisted.state_json["review"]["outcome"] == "PASSED"


def test_change_direction_feedback_is_visible_and_rejected_direction_cannot_repeat() -> None:
    context = semantic_context("REVIEW")
    state = direction_and_material_state(context)
    state["direction"]["statement"] = "讲现熬牛肉粉"
    state["draft"] = {
        "draft_id": uid(), "content": {"title": "旧方向", "script_text": "旧稿", "shooting_notes": []},
        "content_status": "FINAL_CANDIDATE", "based_on_ready_content_id": None,
    }
    reviewed = valid_output("REVIEW", state, {
        "result": "CHANGE_DIRECTION", "problem": "这个方向和老板真正想说的重点不一致。",
        "reason": "成片会把重点带偏。", "preserve": ["真实材料"], "change": ["换一个方向"],
    }, context)
    feedback = build_business_feedback("REVIEW", state, {
        "result": "CHANGE_DIRECTION", "problem": "这个方向和老板真正想说的重点不一致。",
        "reason": "成片会把重点带偏。", "preserve": ["真实材料"], "change": ["换一个方向"],
    })
    explore_context = semantic_context("EXPLORE", reviewed["post_state"])
    explore_context.business_feedback = feedback
    payload = semantic_model_input(explore_context)
    assert payload["modification_goal"]["rejected_direction"] == "讲现熬牛肉粉"
    with pytest.raises(SemanticConversionError):
        valid_output("EXPLORE", reviewed["post_state"], {
            "result": "DIRECTION_CANDIDATE", "message": "换个角度。", "direction": "讲现熬牛肉粉",
            "owner_quote": None, "new_facts": [], "new_constraints": [], "reason": "再试一次。",
        }, explore_context)


def test_source_ready_content_and_recent_dialogue_are_semantic_context() -> None:
    context = ModelContext(
        rules={}, stage_contract=stage_execution_contract("CREATE"), working_state=empty_state(),
        current_owner_message=ContextMessage(uid(), "OWNER", "我要改一下旧脚本。", 1, "CURRENT_TURN"),
        source_ready_content={"id": uid(), "session_id": uid(), "final_content": {"script_text": "旧脚本"}},
        checkpoint=None, history_turns=(), evidence_messages=(), owner_evidence_references=(), estimated_units=1,
    )
    payload = semantic_model_input(context)
    assert payload["source_ready_content"]["script_text"] == "旧脚本"
    assert "model_context" not in json.dumps(payload, ensure_ascii=False)


def test_invalid_director_stage_mode_fails_loudly() -> None:
    environment = os.environ.copy()
    environment["DIRECTOR_STAGE_MODE"] = "not-a-mode"
    result = subprocess.run(
        [sys.executable, "-c", "import backend.app.config"],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[3]),
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "DIRECTOR_STAGE_MODE must be exactly" in result.stderr
