import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..main import app
from ..scriptgen import ai
from ..scriptgen.models import (
    AudienceProfile,
    OwnerProfile,
    ResearchProfile,
    ScriptModel,
    Shot,
    ShootingProfile,
    StoreProfile,
)
from ..scriptgen.quality import scan_script_quality


def profile(*, allow_personal_story: bool = False) -> ResearchProfile:
    return ResearchProfile(
        store=StoreProfile(
            restaurant_name="久和原味烧烤",
            signature_dishes=["碳烤大油边\\羊肉串"],
            differentiators=["每天现切现穿"],
            ingredient_proofs=["当天鲜羊肉"],
            visible_processes=["切肉、穿串、炭火烤制"],
        ),
        owner=OwnerProfile(
            owner_name="宏亮",
            origin_story="想把家乡的烧烤味道留在这座城市",
            hardest_moment="开店第一年客人很少",
            proudest_moment="我有个好孩子",
            allow_personal_story=allow_personal_story,
        ),
        audience=AudienceProfile(
            core_audience="附近喜欢夜宵的人",
            content_goal="吸引到店",
        ),
        shooting=ShootingProfile(
            target_duration_seconds=48,
            available_locations=["店门口", "出餐口", "后厨"],
            can_show_kitchen=True,
        ),
    )


def generated_payload(strategies: list[str]) -> dict[str, object]:
    candidates = []
    for candidate_index, strategy in enumerate(strategies):
        hook = f"{strategy}方案的自然开场钩子"
        cta_options = [
            f"你喜欢{strategy}里的哪种做法？留言告诉我",
            f"关注我，下一条继续看{strategy}的真实过程",
            f"路过的时候，可以来尝尝{strategy}里的这口味道",
            f"这就是我们每天坚持{strategy}的原因",
        ]
        cta = cta_options[candidate_index % len(cta_options)]
        shots = []
        for shot_index in range(1, 7):
            lines = f"这是{strategy}方案第{shot_index}个连续镜头。"
            if shot_index == 1:
                lines = f"{hook}。{lines}"
            if shot_index == 6:
                lines = f"{lines}{cta}"
            shots.append(
                {
                    "shot_index": shot_index,
                    "purpose": f"推进{strategy}的第{shot_index}步",
                    "lines": lines,
                    "location": "出餐口",
                    "angle": "45度近景",
                    "subject": "老板和刚出炉的烤串",
                    "action_steps": ["提前擦净手机镜头", "从装盘前开始连续录制"],
                    "phone_setup": "手机竖屏使用1倍镜头，距离主体约80厘米，与盘子保持同高",
                    "camera_movement": "双手固定手机，不变焦",
                    "audio": "保留炭火和装盘的现场声",
                    "lighting": "站在灯光侧前方，避免手机遮住菜品光线",
                    "props": ["干净餐盘"],
                    "subtitle": f"{strategy}重点字幕",
                    "edit_note": "动作完成后多停留两秒再结束录制",
                    "common_mistakes": ["拍到一半突然变焦"],
                    "retake_if": ["菜品被手完全挡住"],
                    "tone": "自然真诚",
                    "emotion": "放松",
                    "speech_rate": "比平时聊天慢一点",
                    "pause_guidance": "重点词后停顿1秒",
                    "expression_guidance": "像和熟客聊天",
                    "duration_seconds": 8,
                }
            )
        candidates.append(
            {
                "strategy": strategy,
                "title": f"{strategy}方向的真实烧烤故事",
                "opening_hook": hook,
                "cta": cta,
                "shots": shots,
            }
        )
    return {"research_summary": "根据真实问卷生成三套差异化烧烤脚本。", "candidates": candidates}


def test_safe_payload_excludes_unapproved_personal_story() -> None:
    payload = ai._safe_profile_payload(profile())
    owner = payload["owner"]

    assert isinstance(owner, dict)
    assert owner["proudest_moment"] == ""
    assert owner["hardest_moment"] == ""
    assert owner["personal_story_note"]


def test_signature_dishes_accept_common_separators() -> None:
    dishes = profile().store.signature_dishes

    assert dishes == ["碳烤大油边", "羊肉串"]


def test_ai_bundle_has_detailed_shooting_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research = profile()
    strategies = [item.key for item in ai._eligible_strategies(research, 3)]
    payload = generated_payload(strategies)
    monkeypatch.setattr(ai, "_request_json", lambda messages: payload)

    bundle = ai.generate_ai_script_bundle(research)

    assert bundle.generator == "ai"
    assert bundle.model_name
    assert len(bundle.candidates) == 3
    assert len({candidate.script.cta for candidate in bundle.candidates}) == 3
    for candidate in bundle.candidates:
        assert sum(
            shot.duration_hint_seconds for shot in candidate.script.shots
        ) == 48
        for shot in candidate.script.shots:
            assert shot.purpose
            assert len(shot.action_steps) >= 2
            assert "竖屏" in shot.phone_setup
            assert shot.common_mistakes
            assert shot.retake_if
            assert shot.tone == "自然真诚"
            assert shot.emotion == "放松"
            assert shot.speech_rate
            assert shot.pause_guidance
            assert shot.expression_guidance


def test_quality_scan_marks_risks_without_rewriting() -> None:
    research = profile().model_copy(
        update={
            "owner": profile().owner.model_copy(update={"avoided_topics": ["儿童"]}),
            "shooting": profile().shooting.model_copy(
                update={"unavailable_locations": ["后厨"], "can_show_kitchen": False}
            ),
        }
    )
    original_lines = "全城第一的味道，保证顾客都说好，儿童也会喜欢。"
    script = ScriptModel(
        title="风险提示测试",
        target_duration_seconds=15,
        style="竖屏",
        opening_hook="开场",
        cta="结尾",
        shots=[
            Shot(
                shot_index=1,
                lines=original_lines,
                shooting_tips="",
                duration_hint_seconds=15,
                location="后厨",
                subject="顾客反应",
                action_steps=["先拍火焰", "再靠近热油", "同时拍顾客", "最后拍装盘"],
                phone_setup="手机竖屏",
                camera_movement="贴近推进",
            )
        ],
    )

    risks = scan_script_quality(script, research)

    assert script.shots[0].lines == original_lines
    assert {risk.category for risk in risks} == {"真实性", "可拍摄性", "IP一致性"}
    assert all(risk.shot_index == 1 for risk in risks if risk.shot_index is not None)


def test_invalid_ai_output_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research = profile()
    strategies = [item.key for item in ai._eligible_strategies(research, 3)]
    invalid = generated_payload(strategies)
    invalid["candidates"][0]["title"] = "我赌你吃完还想来"  # type: ignore[index]
    valid = generated_payload(strategies)
    responses = iter([invalid, valid])
    calls: list[list[dict[str, str]]] = []

    def fake_request(messages: list[dict[str, str]]) -> dict[str, object]:
        calls.append(messages)
        return next(responses)

    monkeypatch.setattr(ai, "_request_json", fake_request)

    bundle = ai.generate_ai_script_bundle(research)

    assert bundle.generator == "ai"
    assert len(calls) == 2
    repair_payload = json.loads(calls[1][1]["content"])
    assert "previous_output_errors" in repair_payload


def test_same_street_tenure_cannot_be_invented() -> None:
    research = profile()
    strategies = ai._eligible_strategies(research, 3)
    payload = generated_payload([item.key for item in strategies])
    output = ai.AIBundleOutput.model_validate(payload)
    output = ai._apply_required_ctas(output, research, strategies)
    first = output.candidates[0]
    shots = list(first.shots)
    shots[1] = shots[1].model_copy(
        update={"lines": "我在这条街做了6年烧烤。"}
    )
    output = output.model_copy(
        update={
            "candidates": [
                first.model_copy(update={"shots": shots}),
                *output.candidates[1:],
            ]
        }
    )

    with pytest.raises(ai.AIResponseError, match="同一条街"):
        ai._validate_quality(output, research, strategies)


def test_missing_api_key_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai.config, "AI_SCRIPT_API_KEY", "")

    with pytest.raises(ai.AIConfigurationError, match="DEEPSEEK_API_KEY"):
        ai._request_json([{"role": "user", "content": "json"}])


def test_ai_endpoint_does_not_replace_bundle_without_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(ai.config, "AI_SCRIPT_API_KEY", "")
    client = TestClient(app)
    project_id = client.post("/api/projects", json={"name": "AI 配置测试"}).json()["id"]

    response = client.post(
        f"/api/projects/{project_id}/script-bundles/ai",
        json={"research": profile().model_dump(mode="json"), "candidate_count": 3},
    )

    assert response.status_code == 503
    assert "DEEPSEEK_API_KEY" in response.json()["message"]
    assert client.get(
        f"/api/projects/{project_id}/script-bundles/latest"
    ).status_code == 404


def test_group_buy_goal_injects_group_buy_cta_in_top_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research = profile().model_copy(
        update={
            "audience": profile().audience.model_copy(
                update={"content_goal": "团购转化"}
            ),
        }
    )
    strategies = [item.key for item in ai._eligible_strategies(research, 3)]
    monkeypatch.setattr(
        ai, "_request_json", lambda messages: generated_payload(strategies)
    )

    bundle = ai.generate_ai_script_bundle(research)

    assert bundle.generator == "ai"
    assert len(bundle.candidates) == 3
    ctas = [candidate.script.cta for candidate in bundle.candidates]
    assert "团购" in ctas[0]
    assert len(set(ctas)) == 3


def test_ai_quality_failure_falls_back_to_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ..api import script as script_api

    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    client = TestClient(app)
    project_id = client.post("/api/projects", json={"name": "AI 兜底测试"}).json()["id"]

    def boom(*args: object, **kwargs: object) -> object:
        raise ai.AIResponseError("两次输出均未通过质量校验")

    monkeypatch.setattr(script_api, "generate_ai_script_bundle", boom)

    response = client.post(
        f"/api/projects/{project_id}/script-bundles/ai",
        json={"research": profile().model_dump(mode="json"), "candidate_count": 3},
    )

    assert response.status_code == 200
    bundle = response.json()
    assert bundle["generator"] == "template_fallback"
    assert len(bundle["candidates"]) == 3
    assert any("兜底" in warning for warning in bundle["warnings"])
    assert client.get(
        f"/api/projects/{project_id}/script-bundles/latest"
    ).status_code == 200


def test_hook_phrase_allowed_in_opening_hook() -> None:
    research = profile()
    scored = ai._eligible_strategies(research, 3)
    payload = generated_payload([item.key for item in scored])
    payload["candidates"][0]["shots"][0]["lines"] = (
        f"先别划走，{payload['candidates'][0]['shots'][0]['lines']}"
    )
    output = ai.AIBundleOutput.model_validate(payload)

    ai._validate_quality(output, research, scored)  # 不应抛异常


def test_cta_phrase_allowed_in_last_shot() -> None:
    research = profile()
    scored = ai._eligible_strategies(research, 3)
    payload = generated_payload([item.key for item in scored])
    last = payload["candidates"][-1]
    new_cta = "想尝尝这个味道，收藏这条视频。"
    last["cta"] = new_cta
    last["shots"][5]["lines"] = f"{last['shots'][5]['lines']}{new_cta}"
    output = ai.AIBundleOutput.model_validate(payload)

    ai._validate_quality(output, research, scored)  # 不应抛异常


def test_hook_phrase_rejected_outside_hook() -> None:
    research = profile()
    scored = ai._eligible_strategies(research, 3)
    payload = generated_payload([item.key for item in scored])
    payload["candidates"][0]["shots"][2]["lines"] = (
        f"先别划走，{payload['candidates'][0]['shots'][2]['lines']}"
    )
    output = ai.AIBundleOutput.model_validate(payload)

    with pytest.raises(ai.AIResponseError, match="开场钩子"):
        ai._validate_quality(output, research, scored)


def test_cta_phrase_stacking_rejected() -> None:
    research = profile()
    scored = ai._eligible_strategies(research, 3)
    payload = generated_payload([item.key for item in scored])
    last = payload["candidates"][-1]
    new_cta = "想尝尝这个味道，收藏这条视频，到店报到。"
    last["cta"] = new_cta
    last["shots"][5]["lines"] = f"{last['shots'][5]['lines']}{new_cta}"
    output = ai.AIBundleOutput.model_validate(payload)

    with pytest.raises(ai.AIResponseError, match="堆叠"):
        ai._validate_quality(output, research, scored)
