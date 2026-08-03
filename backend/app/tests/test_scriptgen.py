import pytest

from ..scriptgen.generators import get
from ..scriptgen.generators.template import TemplateGenerator
from ..scriptgen.models import BossInfo, ScriptModel


def test_template_generator_returns_complete_deterministic_script() -> None:
    boss_info = BossInfo(
        restaurant_name="老周川菜馆",
        cuisine_type="川菜",
        signature_dishes=["水煮鱼", "辣子鸡"],
        owner_persona="直爽又讲究火候的川菜师傅",
        audience="附近上班族和爱吃辣的年轻人",
        video_style="竖屏口播",
        target_duration_seconds=60,
        platform="抖音",
        hook_preference="成都朋友认证的这一口，到底有多香？",
    )

    script = TemplateGenerator().generate(boss_info)

    assert isinstance(script, ScriptModel)
    assert len(script.shots) >= 4
    assert [shot.shot_index for shot in script.shots] == list(
        range(1, len(script.shots) + 1)
    )
    assert all(shot.lines and shot.shooting_tips for shot in script.shots)
    assert all(
        "老周川菜馆" in shot.lines or "水煮鱼" in shot.lines
        for shot in script.shots
    )
    assert abs(sum(shot.duration_hint_seconds for shot in script.shots) - 60) <= 10
    assert script == TemplateGenerator().generate(boss_info)


@pytest.mark.parametrize("cuisine", ["川菜", "重庆火锅", "烧烤", "家常菜", "秘鲁菜"])
def test_template_generator_supports_cuisines_and_fallback(cuisine: str) -> None:
    script = TemplateGenerator().generate(
        BossInfo(
            restaurant_name="好味小馆",
            cuisine_type=cuisine,
            signature_dishes=["今日招牌"],
            target_duration_seconds=45,
        )
    )

    assert len(script.shots) == 6
    assert sum(shot.duration_hint_seconds for shot in script.shots) == 45


def test_codex_generator_is_a_placeholder() -> None:
    with pytest.raises(NotImplementedError, match="二期接入"):
        get("codex").generate(BossInfo())
