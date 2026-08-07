import json

import pytest
from pydantic import ValidationError

from ..scriptgen import ai
from ..scriptgen import review
from ..scriptgen.bundles import generate_script_bundle
from ..scriptgen.models import (
    AudienceProfile,
    DirectorCandidateReview,
    DirectorDimensionScores,
    DirectorIssue,
    DirectorReview,
    DirectorRevisionVerdict,
    OwnerProfile,
    ResearchProfile,
    ScriptCandidate,
    ShootingProfile,
    StoreProfile,
)

DIMENSIONS = [
    "opening_hook_strength",
    "oral_naturalness",
    "information_density",
    "progression",
    "evidence_strength",
    "ip_alignment",
    "shootability",
    "ad_feeling",
    "distinctiveness",
]


def profile() -> ResearchProfile:
    return ResearchProfile(
        store=StoreProfile(
            restaurant_name="久和原味烧烤",
            signature_dishes=["碳烤大油边\\羊肉串"],
        ),
        owner=OwnerProfile(owner_name="宏亮"),
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


def review_payload(bundle) -> dict[str, object]:
    return {
        "reviews": [
            {
                "candidate_id": candidate.id,
                "strategy": candidate.strategy,
                "scores": {name: 7 for name in DIMENSIONS},
                "issues": [
                    {
                        "dimension": "oral_naturalness",
                        "message": "第3镜头台词偏书面，像念稿。",
                        "shot_index": 3,
                    }
                ],
                "strengths": ["开场钩子简洁有力"],
                "should_revise": False,
            }
            for candidate in bundle.candidates
        ]
    }


def test_dimension_scores_have_all_nine_fields() -> None:
    scores = DirectorDimensionScores.model_validate(
        {name: 7 for name in DIMENSIONS}
    )

    assert set(scores.model_dump()) == set(DIMENSIONS)


def test_ai_review_json_parses() -> None:
    bundle = generate_script_bundle(profile(), 3)
    result = DirectorReview.model_validate(review_payload(bundle))

    assert len(result.reviews) == 3
    first = result.reviews[0]
    assert first.overall_score == 7.0
    assert 1 <= first.overall_score <= 10
    assert first.should_revise is False


@pytest.mark.parametrize("bad_score", [0, 11, 100])
def test_invalid_score_rejected(bad_score: int) -> None:
    scores = {name: 7 for name in DIMENSIONS}
    scores["progression"] = bad_score

    with pytest.raises(ValidationError):
        DirectorDimensionScores.model_validate(scores)


def test_missing_dimension_rejected() -> None:
    scores = {name: 7 for name in DIMENSIONS}
    del scores["ip_alignment"]

    with pytest.raises(ValidationError):
        DirectorDimensionScores.model_validate(scores)


def test_issue_can_locate_shot() -> None:
    issue = DirectorIssue(
        dimension="oral_naturalness",
        message="第3镜头台词偏书面。",
        shot_index=3,
    )

    assert issue.shot_index == 3
    assert issue.model_dump()["shot_index"] == 3


def test_issue_requires_concrete_location() -> None:
    with pytest.raises(ValidationError):
        DirectorIssue(
            dimension="oral_naturalness",
            message="整体可以更好。",
        )


def test_review_does_not_mutate_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = generate_script_bundle(profile(), 3)
    before = bundle.model_dump(mode="json")
    monkeypatch.setattr(ai, "_request_json", lambda messages: review_payload(bundle))

    result = review.review_script_bundle(bundle, profile())

    assert bundle.model_dump(mode="json") == before
    assert result.bundle_id == bundle.id
    assert result.model_name
    assert len(result.reviews) == 3


def test_review_rejects_unknown_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = generate_script_bundle(profile(), 3)
    payload = review_payload(bundle)
    payload["reviews"][0]["candidate_id"] = "ghost"
    monkeypatch.setattr(ai, "_request_json", lambda messages: payload)

    with pytest.raises(ai.AIResponseError, match="未知候选"):
        review.review_script_bundle(bundle, profile())


def test_review_passes_creative_context_and_locked_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = generate_script_bundle(profile(), 3)
    captured: dict[str, object] = {}

    def fake_request(messages: list[dict[str, str]]) -> dict[str, object]:
        captured["user"] = json.loads(messages[1]["content"])
        return review_payload(bundle)

    monkeypatch.setattr(ai, "_request_json", fake_request)
    context = {"selected_topic_card": {"id": "tc-1", "title": "炭烤大油边"}}

    review.review_script_bundle(bundle, profile(), creative_context=context)

    assert captured["user"]["creative_context"] == context
    assert captured["user"]["locked_topic"] is True


# --- 纯程序低分判定（judge_revision_needed） ---


def candidate_review(
    dims: dict[str, int] | None = None,
    *,
    should_revise: bool = False,
    candidate_id: str = "c1",
    strategy: str = "owner_story",
    issues: list[DirectorIssue] | None = None,
) -> DirectorCandidateReview:
    scores = {name: 8 for name in DIMENSIONS}
    if dims:
        scores.update(dims)
    return DirectorCandidateReview(
        candidate_id=candidate_id,
        strategy=strategy,
        scores=scores,
        issues=issues or [],
        should_revise=should_revise,
    )


def test_low_overall_score_requires_revision() -> None:
    verdict = review.judge_revision_needed(
        candidate_review({name: 6 for name in DIMENSIONS})
    )

    assert verdict.candidate_id == "c1"
    assert verdict.needs_revision is True
    assert verdict.weak_dimensions == []
    assert any("总分" in reason and "6.0" in reason for reason in verdict.reasons)


def test_overall_low_via_non_critical_dimension() -> None:
    # 非关键维度低 → 总分 6.9 < 7.0，但 weak_dimensions 为空
    dims = {name: 7 for name in DIMENSIONS}
    dims["information_density"] = 6

    verdict = review.judge_revision_needed(candidate_review(dims))

    assert verdict.needs_revision is True
    assert verdict.weak_dimensions == []
    assert any("总分" in reason for reason in verdict.reasons)


def test_critical_dimension_low_requires_revision() -> None:
    # 总分合格（其余 8 分 → 7.67）但关键维度 progression=5
    dims = {name: 8 for name in DIMENSIONS}
    dims["progression"] = 5

    verdict = review.judge_revision_needed(candidate_review(dims))

    assert verdict.needs_revision is True
    assert "progression" in verdict.weak_dimensions
    assert any("progression" in reason for reason in verdict.reasons)


def test_all_acceptable_no_revision() -> None:
    verdict = review.judge_revision_needed(candidate_review())

    assert verdict.needs_revision is False
    assert verdict.weak_dimensions == []
    assert verdict.reasons == []


def test_ai_should_revise_not_enough_when_scores_pass() -> None:
    # AI 建议修稿，但程序评分全部合格 → 不强制进入修稿
    verdict = review.judge_revision_needed(candidate_review(should_revise=True))

    assert verdict.needs_revision is False
    assert verdict.weak_dimensions == []
    assert verdict.reasons == []


def test_boundary_overall_7_0_is_acceptable() -> None:
    # 总分恰好 7.0（不小于 7.0）且无关键维度低于 6 → 不修稿
    verdict = review.judge_revision_needed(
        candidate_review({name: 7 for name in DIMENSIONS})
    )

    assert verdict.needs_revision is False
    assert verdict.weak_dimensions == []


def test_boundary_critical_dimension_6_is_acceptable() -> None:
    # 关键维度恰好 6（不小于 6）→ 不算弱点
    dims = {name: 8 for name in DIMENSIONS}
    dims["opening_hook_strength"] = 6

    verdict = review.judge_revision_needed(candidate_review(dims))

    assert verdict.needs_revision is False
    assert verdict.weak_dimensions == []


def test_verdict_carries_issues_from_review() -> None:
    verdict = review.judge_revision_needed(
        candidate_review(
            {"progression": 4},
            issues=[
                DirectorIssue(
                    dimension="progression",
                    message="第2镜头节奏拖沓。",
                    shot_index=2,
                )
            ],
        )
    )

    assert verdict.needs_revision is True
    assert len(verdict.issues) == 1
    assert verdict.issues[0].shot_index == 2


# --- AI 局部修稿（revise_script_candidate） ---


def revision_context(
    bundle, index: int = 0
) -> tuple[ScriptCandidate, DirectorCandidateReview, DirectorRevisionVerdict]:
    """构造一个低分候选：总分 6.0 → 需要修稿，问题定位到第 3 镜头。"""
    candidate = bundle.candidates[index]
    review_item = DirectorCandidateReview(
        candidate_id=candidate.id,
        strategy=candidate.strategy,
        scores={name: 6 for name in DIMENSIONS},
        issues=[
            DirectorIssue(
                dimension="oral_naturalness",
                message="第3镜头台词偏书面，像念稿。",
                shot_index=3,
            )
        ],
    )
    verdict = review.judge_revision_needed(review_item)
    assert verdict.needs_revision is True
    return candidate, review_item, verdict


def test_revision_only_changes_pointed_shot(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = generate_script_bundle(profile(), 3)
    candidate, review_item, verdict = revision_context(bundle)
    original = candidate.script
    patch = {"shots": [{"shot_index": 3, "lines": "改成更自然的第三镜头口语。"}]}
    monkeypatch.setattr(ai, "_request_json", lambda messages: patch)

    revised = review.revise_script_candidate(
        candidate, review_item, verdict, profile()
    )

    # candidate_id / strategy / strategy_name 不变
    assert revised.id == candidate.id
    assert revised.strategy == candidate.strategy
    assert revised.strategy_name == candidate.strategy_name
    # 镜头数与镜头编号不变
    assert [shot.shot_index for shot in revised.script.shots] == [
        shot.shot_index for shot in original.shots
    ]
    # 只修改指定的第 3 镜头，其余镜头保持原值
    for index, shot in enumerate(revised.script.shots, start=1):
        original_shot = original.shots[index - 1]
        if index == 3:
            assert shot.lines == "改成更自然的第三镜头口语。"
        else:
            assert shot.model_dump() == original_shot.model_dump()
    assert revised.script.target_duration_seconds == original.target_duration_seconds
    assert revised.script.style == original.style


def test_revision_rejects_change_to_unpointed_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = generate_script_bundle(profile(), 3)
    candidate, review_item, verdict = revision_context(bundle)
    # 问题只指向第 3 镜头，AI 试图改第 4 镜头 → 拒绝
    patch = {"shots": [{"shot_index": 4, "lines": "改了没被指出的镜头。"}]}
    monkeypatch.setattr(ai, "_request_json", lambda messages: patch)

    with pytest.raises(ai.AIResponseError, match="被禁止修改未指出的"):
        review.revise_script_candidate(candidate, review_item, verdict, profile())


def test_revision_cannot_change_topic_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = generate_script_bundle(profile(), 3)
    candidate = bundle.candidates[0]
    review_item = DirectorCandidateReview(
        candidate_id=candidate.id,
        strategy=candidate.strategy,
        scores={name: 6 for name in DIMENSIONS},
        issues=[
            DirectorIssue(
                dimension="opening_hook_strength",
                message="标题不够抓人。",
                field="title",
            )
        ],
    )
    verdict = review.judge_revision_needed(review_item)
    assert verdict.needs_revision is True
    context = {
        "selected_topic_card": {
            "id": "tc-1",
            "title": "碳烤大油边为什么是招牌",
            "hook": "刚上桌的大油边，先看这口油脂",
            "angle": "从口感到食材来源",
            "target_customer": "附近喜欢夜宵的人",
            "ip_alignment": "真实现切现烤",
            "evidence_needed": ["当天鲜羊肉"],
            "shoot_difficulty": "medium",
            "estimated_duration_sec": 48,
            "cta": "想尝尝这口大油边，评论区告诉我们。",
        }
    }
    # 即使 issue 指向 title，锁题模式下改标题仍被拒绝（防换题）
    monkeypatch.setattr(
        ai, "_request_json", lambda messages: {"title": "换一个不相关的主题"}
    )

    with pytest.raises(ai.AIResponseError, match="锁题"):
        review.revise_script_candidate(
            candidate,
            review_item,
            verdict,
            profile(),
            creative_context=context,
        )


def test_revision_rejects_full_script_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = generate_script_bundle(profile(), 3)
    candidate, review_item, verdict = revision_context(bundle)
    # 非法：携带 strategy 等「整篇重写」性质的键，补丁 schema 拒绝
    illegal = {
        "strategy": "dish",
        "title": "新标题",
        "shots": [{"shot_index": 3, "lines": "自然的新台词。"}],
    }
    monkeypatch.setattr(ai, "_request_json", lambda messages: illegal)

    with pytest.raises(ai.AIResponseError, match="两次修稿均未通过校验"):
        review.revise_script_candidate(candidate, review_item, verdict, profile())


def test_revision_rejects_unknown_shot_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = generate_script_bundle(profile(), 3)
    candidate, review_item, verdict = revision_context(bundle)
    # 非法：duration_hint_seconds 不属于可修字段，补丁 schema 拒绝
    illegal = {
        "shots": [
            {"shot_index": 3, "lines": "新台词", "duration_hint_seconds": 10}
        ]
    }
    monkeypatch.setattr(ai, "_request_json", lambda messages: illegal)

    with pytest.raises(ai.AIResponseError, match="两次修稿均未通过校验"):
        review.revise_script_candidate(candidate, review_item, verdict, profile())


def test_revision_must_pass_program_hard_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = generate_script_bundle(profile(), 3)
    candidate, review_item, verdict = revision_context(bundle)
    # 修稿结果若引入夸大/假权威表达，必须被现有程序硬校验拒绝
    bad = {"shots": [{"shot_index": 3, "lines": "我赌你吃完还想来，这味道是天花板。"}]}
    monkeypatch.setattr(ai, "_request_json", lambda messages: bad)

    with pytest.raises(ai.AIResponseError, match="我赌你|天花板"):
        review.revise_script_candidate(candidate, review_item, verdict, profile())


def test_revision_requires_actionable_issue() -> None:
    bundle = generate_script_bundle(profile(), 3)
    candidate = bundle.candidates[0]
    review_item = DirectorCandidateReview(
        candidate_id=candidate.id,
        strategy=candidate.strategy,
        scores={name: 6 for name in DIMENSIONS},
        issues=[],
    )
    verdict = review.judge_revision_needed(review_item)
    assert verdict.needs_revision is True

    # 低分但没有定位到任何镜头/字段 → 无法安全做局部修稿
    with pytest.raises(ai.AIResponseError, match="未定位"):
        review.revise_script_candidate(candidate, review_item, verdict, profile())


def test_revision_prompt_is_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = generate_script_bundle(profile(), 3)
    candidate, review_item, verdict = revision_context(bundle)
    captured: dict[str, object] = {}

    def fake_request(messages: list[dict[str, str]]) -> dict[str, object]:
        captured["system"] = messages[0]["content"]
        captured["user"] = json.loads(messages[1]["content"])
        return {"shots": [{"shot_index": 3, "lines": "自然一点的口语化新台词。"}]}

    monkeypatch.setattr(ai, "_request_json", fake_request)

    review.revise_script_candidate(candidate, review_item, verdict, profile())

    system = captured["system"]
    # 独立于编剧 prompt 与编导审稿 prompt：明确是「修稿/补丁」任务，
    # 不包含编导审稿的「9 个维度」评分要求
    assert "修稿" in system
    assert "补丁" in system
    assert "9 个维度" not in system
    user = captured["user"]
    assert user["candidate"]["strategy"] == candidate.strategy
    assert user["director_issues"][0]["shot_index"] == 3
    assert user["locked_topic"] is False


def test_revision_empty_patch_keeps_script(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = generate_script_bundle(profile(), 3)
    candidate, review_item, verdict = revision_context(bundle)
    monkeypatch.setattr(ai, "_request_json", lambda messages: {})

    revised = review.revise_script_candidate(
        candidate, review_item, verdict, profile()
    )

    assert revised.script.model_dump(mode="json", exclude={"quality_risks"}) == (
        candidate.script.model_dump(mode="json", exclude={"quality_risks"})
    )
