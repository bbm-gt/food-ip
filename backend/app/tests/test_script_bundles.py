from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .. import config
from ..main import app
from ..scriptgen.bundles import generate_script_bundle
from ..scriptgen.models import (
    AudienceProfile,
    OwnerProfile,
    ResearchProfile,
    ShootingProfile,
    StoreProfile,
)


def rich_profile() -> ResearchProfile:
    return ResearchProfile(
        store=StoreProfile(
            restaurant_name="赵姐炭火小馆",
            city="青岛",
            business_district="软件园",
            cuisine_type="东北烧烤",
            years_in_business=8,
            price_per_person=68,
            signature_dishes=["炭烤羊肉串", "蒜香鸡翅"],
            differentiators=["当天现切现穿，卖完为止"],
            ingredient_proofs=["每天上午采购鲜羊肉"],
            visible_processes=["切肉、肥瘦搭配、手工穿串、炭火烤制"],
            customer_praises=["肉香但不膻"],
            customer_misunderstandings=["烧烤店会不会使用隔夜串"],
        ),
        owner=OwnerProfile(
            owner_name="赵姐",
            owner_persona="爽快、爱开玩笑的东北老板娘",
            origin_story="为了让在外工作的东北人吃到家乡味开了这家店",
            hardest_moment="开店第一年客人很少，但仍然每天买新鲜羊肉",
            proudest_moment="老顾客搬家后还会专程回来吃",
        ),
        audience=AudienceProfile(
            core_audience="附近上班族和夜宵人群",
            customer_concerns=["羊肉是否新鲜", "价格是否值得"],
            content_goal="吸引到店",
        ),
        shooting=ShootingProfile(
            video_style="烟火气纪实",
            target_duration_seconds=48,
            available_locations=["店门口", "后厨", "出餐口"],
            can_show_kitchen=True,
            can_show_customers=False,
        ),
    )


def test_bundle_generates_three_distinct_shootable_scripts() -> None:
    bundle = generate_script_bundle(rich_profile())

    assert len(bundle.candidates) == 3
    assert {candidate.strategy for candidate in bundle.candidates} == {
        "owner_story",
        "dish",
        "kitchen",
    }
    assert len({candidate.id for candidate in bundle.candidates}) == 3
    for candidate in bundle.candidates:
        assert candidate.score >= 70
        assert candidate.reasons
        assert len(candidate.script.shots) == 6
        assert [shot.shot_index for shot in candidate.script.shots] == list(range(1, 7))
        assert sum(
            shot.duration_hint_seconds for shot in candidate.script.shots
        ) == 48


def test_bundle_respects_no_owner_and_no_kitchen_constraints() -> None:
    profile = ResearchProfile(
        store=StoreProfile(
            restaurant_name="安静小面馆",
            signature_dishes=["牛肉面"],
            customer_misunderstandings=["汤底是不是调料包"],
        ),
        owner=OwnerProfile(appearance_mode="不出镜"),
        audience=AudienceProfile(content_goal="建立信任"),
        shooting=ShootingProfile(can_show_kitchen=False),
    )

    bundle = generate_script_bundle(profile, candidate_count=5)
    strategies = {candidate.strategy for candidate in bundle.candidates}

    assert strategies == {"pain_point", "dish", "daily"}
    assert "owner_story" not in strategies
    assert "kitchen" not in strategies


def test_rule_bundle_cta_is_goal_aware_and_ban_phrase_free() -> None:
    profile = ResearchProfile(
        store=StoreProfile(restaurant_name="糖棠甜品", signature_dishes=["杨枝甘露"]),
        audience=AudienceProfile(content_goal="团购转化"),
        shooting=ShootingProfile(),
    )

    bundle = generate_script_bundle(profile)
    cta = bundle.candidates[0].script.cta

    # 与 AI 路径目标文案一致：体现 content_goal 且不使用禁用表达。
    assert cta == "想尝尝杨枝甘露的话，可以看看糖棠甜品现在的团购套餐。"
    assert "团购" in cta
    for banned in ("先收藏", "收藏这条视频", "报到", "先别划走"):
        assert banned not in cta


@pytest.fixture
def bundle_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(config, "PROJECTS_ROOT", str(tmp_path / "projects"))
    return TestClient(app)


def test_research_bundle_selection_api(bundle_client: TestClient) -> None:
    project_id = bundle_client.post(
        "/api/projects", json={"name": "多方案测试"}
    ).json()["id"]
    research = rich_profile().model_dump(mode="json")

    saved = bundle_client.put(
        f"/api/projects/{project_id}/research", json=research
    )
    assert saved.status_code == 200
    assert saved.json()["store"]["restaurant_name"] == "赵姐炭火小馆"

    generated = bundle_client.post(
        f"/api/projects/{project_id}/script-bundles/template",
        json={"research": research, "candidate_count": 3},
    )
    assert generated.status_code == 200
    bundle = generated.json()
    assert len(bundle["candidates"]) == 3
    assert bundle["selected_script_id"] is None

    selected_candidate = bundle["candidates"][1]
    selected = bundle_client.post(
        f"/api/projects/{project_id}/script-bundles/{bundle['id']}"
        f"/select/{selected_candidate['id']}"
    )
    assert selected.status_code == 200
    assert selected.json() == selected_candidate["script"]
    assert bundle_client.get(f"/api/projects/{project_id}/script").json() == selected.json()

    latest = bundle_client.get(
        f"/api/projects/{project_id}/script-bundles/latest"
    ).json()
    assert latest["selected_script_id"] == selected_candidate["id"]
    project = bundle_client.get(f"/api/projects/{project_id}").json()
    assert project["research"] == research
    assert project["script_bundle"]["id"] == bundle["id"]
