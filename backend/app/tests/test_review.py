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
    OwnerProfile,
    ResearchProfile,
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
