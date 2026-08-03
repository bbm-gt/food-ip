"""Deterministic, zero-cost restaurant script generator."""

from dataclasses import dataclass

from ..models import BossInfo, ScriptModel, Shot
from . import register


@dataclass(frozen=True)
class CuisineTemplate:
    hook: str
    process: str
    atmosphere: str


TEMPLATES: dict[str, CuisineTemplate] = {
    "川菜": CuisineTemplate(
        hook="爱吃川味的先别划走，这一口麻辣鲜香才叫过瘾！",
        process="旺火爆炒，辣椒和花椒的香气一起被激发出来",
        atmosphere="热辣菜一上桌，客人边擦汗边说还想再来一碗饭",
    ),
    "火锅": CuisineTemplate(
        hook="这一锅刚沸起来，香味已经把隔壁桌都馋到了！",
        process="锅底慢熬出层次，食材现点现切、下锅正好入味",
        atmosphere="锅里咕嘟冒泡，朋友围坐涮菜，现场热气腾腾",
    ),
    "烧烤": CuisineTemplate(
        hook="听见这声滋啦了吗？今晚的快乐已经上烤架了！",
        process="食材现串现烤，翻面、撒料、锁汁一步都不省",
        atmosphere="炭火正旺，客人举杯撸串，烟火气直接拉满",
    ),
    "家常菜": CuisineTemplate(
        hook="想吃一口踏实的家常味，这家小店值得你记住！",
        process="食材当天准备，按家常做法现炒现出锅",
        atmosphere="熟客围桌吃得自在，每一桌都是热乎的家常氛围",
    ),
    "通用": CuisineTemplate(
        hook="别急着划走，这家店的招牌味道值得你看完！",
        process="从备料到出锅都在店内完成，把新鲜和火候做到位",
        atmosphere="客人边吃边点头，店里的真实烟火气就是最好反馈",
    ),
}


def _select_template(cuisine_type: str) -> CuisineTemplate:
    normalized = cuisine_type.strip()
    for name in ("川菜", "火锅", "烧烤", "家常菜"):
        if name in normalized:
            return TEMPLATES[name]
    if "四川" in normalized or "川味" in normalized:
        return TEMPLATES["川菜"]
    return TEMPLATES["通用"]


def _allocate_durations(target: int) -> list[int]:
    """Allocate a non-negative target deterministically across six shots."""

    target = max(0, target)
    weights = (0.14, 0.18, 0.20, 0.18, 0.15, 0.15)
    raw = [target * weight for weight in weights]
    durations = [int(value) for value in raw]
    remainder = target - sum(durations)
    order = sorted(range(len(raw)), key=lambda index: (-(raw[index] % 1), index))
    for index in order[:remainder]:
        durations[index] += 1
    return durations


@register("template")
class TemplateGenerator:
    """Generate a six-shot vertical video script from questionnaire fields."""

    def generate(self, boss_info: BossInfo) -> ScriptModel:
        restaurant = boss_info.restaurant_name.strip() or "这家小店"
        dishes = [dish.strip() for dish in boss_info.signature_dishes if dish.strip()]
        signature = "、".join(dishes[:3]) or "店内招牌菜"
        persona = boss_info.owner_persona.strip() or "认真做菜、实在待客的老板"
        audience = boss_info.audience.strip() or "爱吃、懂吃的朋友"
        template = _select_template(boss_info.cuisine_type)
        opening_hook = boss_info.hook_preference.strip() or template.hook
        cta = f"想尝尝{signature}，记得收藏这条视频，到{restaurant}报到！"
        durations = _allocate_durations(boss_info.target_duration_seconds)

        shot_data = (
            (
                f"{opening_hook}这里是{restaurant}。",
                "手机竖屏，店门口中近景；镜头快速推进，老板抬手招呼并直视镜头。",
                "店门口",
                "中近景推进",
            ),
            (
                f"看清楚，{restaurant}的{signature}刚出锅就是这个状态。",
                "手机竖屏，菜品45度近景转特写；沿盘边缓慢横移，捕捉热气和油亮质感。",
                "出餐口",
                "近景转特写",
            ),
            (
                f"{signature}的关键就在这里：{template.process}，这也是{restaurant}一直坚持的做法。",
                "手机竖屏贴近灶台，先俯拍备料再侧拍下锅；跟随厨师动作，保留翻炒或炭火声。",
                "后厨",
                "俯拍转侧面特写",
            ),
            (
                f"我是{restaurant}的老板，大家都说我是个{persona}；我只想让{audience}吃到一口踏实味道。",
                "手机竖屏固定中景，老板站在明亮工作区口播；视线看镜头，手上保留自然动作。",
                "店内工作区",
                "平视中景",
            ),
            (
                f"来{restaurant}看看现场：{template.atmosphere}，{signature}上桌很快就见底。",
                "手机竖屏广角扫过就餐区，再切顾客夹菜和点头的近景；拍摄前先征得顾客同意。",
                "就餐区",
                "广角转反应近景",
            ),
            (
                cta,
                "手机竖屏中近景，老板端着招牌菜面向镜头；结尾停留两秒，画面叠加店名与到店提示。",
                "店内招牌前",
                "平视中近景",
            ),
        )

        shots = [
            Shot(
                shot_index=index,
                lines=lines,
                shooting_tips=tips,
                duration_hint_seconds=durations[index - 1],
                location=location,
                angle=angle,
            )
            for index, (lines, tips, location, angle) in enumerate(shot_data, start=1)
        ]
        return ScriptModel(
            title=f"{restaurant}｜{signature}招牌短视频",
            target_duration_seconds=boss_info.target_duration_seconds,
            style=boss_info.video_style,
            opening_hook=opening_hook,
            cta=cta,
            shots=shots,
        )
