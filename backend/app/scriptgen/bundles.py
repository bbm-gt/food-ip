"""Rule-based multi-script planning for the no-model first phase."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .models import ResearchProfile, ScriptBundle, ScriptCandidate, ScriptModel, Shot
from .quality import annotate_script_quality


@dataclass(frozen=True)
class StrategyScore:
    key: str
    name: str
    positioning: str
    score: int
    reasons: list[str]
    difficulty: str
    scenes: list[str]
    requires_owner: bool


def _first(values: list[str], fallback: str) -> str:
    return next((value.strip() for value in values if value.strip()), fallback)


def _dishes(profile: ResearchProfile) -> str:
    values = [value.strip() for value in profile.store.signature_dishes if value.strip()]
    return "、".join(values[:3]) or "店内招牌菜"


def _allocate_durations(target: int) -> list[int]:
    target = max(0, target)
    weights = (0.14, 0.17, 0.19, 0.18, 0.17, 0.15)
    raw = [target * weight for weight in weights]
    durations = [int(value) for value in raw]
    remainder = target - sum(durations)
    order = sorted(range(6), key=lambda index: (-(raw[index] % 1), index))
    for index in order[:remainder]:
        durations[index] += 1
    return durations


def _cta(profile: ResearchProfile) -> str:
    # 与 AI 路径 _required_ctas 的目标文案保持一致，规避禁用表达
    # （先收藏/收藏这条视频/报到 等），并优先体现 content_goal。
    restaurant = profile.store.restaurant_name.strip() or "这家小店"
    main_dish = next(
        (dish.strip() for dish in profile.store.signature_dishes if dish.strip()),
        "招牌菜",
    )
    goal = profile.audience.content_goal
    if goal == "团购转化":
        return f"想尝尝{main_dish}的话，可以看看{restaurant}现在的团购套餐。"
    if goal == "账号涨粉":
        return f"关注{restaurant}，下一条继续带你看一家小店每天真实发生的事。"
    if goal == "建立信任":
        return f"我们把过程拍给你看，也欢迎你到{restaurant}亲自验证。"
    if goal == "品牌认知":
        return f"记住{restaurant}和{main_dish}，下次想吃这一口时就来找我们。"
    return f"想尝尝{main_dish}，来{restaurant}坐坐，看看今天现做的这口味道。"


def _shots(
    profile: ResearchProfile,
    rows: tuple[tuple[str, str, str, str], ...],
) -> list[Shot]:
    durations = _allocate_durations(profile.shooting.target_duration_seconds)
    return [
        Shot(
            shot_index=index,
            lines=line,
            shooting_tips=tips,
            duration_hint_seconds=durations[index - 1],
            location=location,
            angle=angle,
        )
        for index, (line, tips, location, angle) in enumerate(rows, start=1)
    ]


def _score_strategies(profile: ResearchProfile) -> list[StrategyScore]:
    store = profile.store
    owner = profile.owner
    audience = profile.audience
    shooting = profile.shooting
    owner_visible = owner.appearance_mode in {"真人口播", "旁白", "只拍手部"}

    dish_score = 52
    dish_reasons = ["招牌菜可以直接形成食欲画面"]
    if store.signature_dishes:
        dish_score += 12
    if store.differentiators:
        dish_score += 10
        dish_reasons.append("问卷提供了可讲清楚的门店差异")
    if store.ingredient_proofs:
        dish_score += 8
        dish_reasons.append("有食材证据支撑卖点")
    if audience.content_goal in {"吸引到店", "团购转化"}:
        dish_score += 8
        dish_reasons.append("符合当前到店或团购目标")

    owner_score = 35
    owner_reasons: list[str] = []
    if owner.owner_persona or owner.speaking_style:
        owner_score += 12
        owner_reasons.append("老板表达风格明确")
    if owner.origin_story:
        owner_score += 22
        owner_reasons.append("有真实开店原因可以建立人物记忆")
    if owner.hardest_moment or owner.proudest_moment or owner.unique_experience:
        owner_score += 16
        owner_reasons.append("有冲突、转折或成就作为故事证据")
    if owner_visible:
        owner_score += 10
    else:
        owner_score -= 45

    kitchen_score = 32
    kitchen_reasons: list[str] = []
    if shooting.can_show_kitchen:
        kitchen_score += 24
        kitchen_reasons.append("后厨允许拍摄")
    else:
        kitchen_score -= 60
    if store.visible_processes:
        kitchen_score += 18
        kitchen_reasons.append("有可视化制作步骤")
    if store.ingredient_proofs:
        kitchen_score += 12
        kitchen_reasons.append("能用原料画面证明品质")

    concerns = store.customer_misunderstandings + audience.customer_concerns
    pain_score = 42
    pain_reasons: list[str] = []
    if concerns:
        pain_score += 24
        pain_reasons.append("存在可以公开回答的顾客顾虑")
    if store.differentiators or store.ingredient_proofs:
        pain_score += 12
        pain_reasons.append("有事实可以回答问题，而不是空口承诺")
    if audience.content_goal == "建立信任":
        pain_score += 10
        pain_reasons.append("与建立信任目标一致")

    daily_score = 40
    daily_reasons = ["经营日常可以发展成连续栏目"]
    if shooting.available_locations:
        daily_score += 12
        daily_reasons.append("有多个可拍摄场景")
    if store.years_in_business:
        daily_score += 8
        daily_reasons.append("经营年限能增加真实感")
    if shooting.daily_minutes >= 15:
        daily_score += 8
    if store.customer_praises:
        daily_score += 6

    return sorted(
        [
            StrategyScore(
                "dish",
                "招牌菜爆点型",
                "先用食欲画面抓注意，再用真实卖点推动到店",
                min(95, max(0, dish_score)),
                dish_reasons,
                "简单",
                ["店门口", "出餐口", "菜品桌面"],
                False,
            ),
            StrategyScore(
                "owner_story",
                "老板故事型",
                "用真实经历和经营坚持建立老板人物记忆",
                min(95, max(0, owner_score)),
                owner_reasons or ["目前人物素材较少，建议补充开店故事"],
                "中等",
                ["老板工作区", "门店", "招牌菜前"],
                True,
            ),
            StrategyScore(
                "kitchen",
                "后厨揭秘型",
                "用原料和制作过程回答品质问题",
                min(95, max(0, kitchen_score)),
                kitchen_reasons or ["目前工艺素材较少，建议补充制作过程"],
                "中等",
                ["备料区", "灶台", "出餐口"],
                False,
            ),
            StrategyScore(
                "pain_point",
                "顾客问题型",
                "回应顾客真实顾虑，用证据建立信任",
                min(95, max(0, pain_score)),
                pain_reasons or ["可以从常见顾客问题开始测试"],
                "简单",
                ["门店", "证明画面", "招牌菜前"],
                owner_visible,
            ),
            StrategyScore(
                "daily",
                "经营纪实型",
                "记录小店一天，形成可以长期更新的内容栏目",
                min(95, max(0, daily_score)),
                daily_reasons,
                "较难",
                ["开店准备", "营业现场", "收尾工作"],
                False,
            ),
        ],
        key=lambda item: (-item.score, item.key),
    )


def _render_script(strategy: str, profile: ResearchProfile) -> ScriptModel:
    restaurant = profile.store.restaurant_name.strip() or "这家小店"
    dish = _dishes(profile)
    main_dish = _first(profile.store.signature_dishes, "招牌菜")
    owner = profile.owner.owner_name.strip() or "老板"
    persona = profile.owner.owner_persona.strip() or profile.owner.speaking_style
    audience = profile.audience.core_audience.strip() or "爱吃、懂吃的朋友"
    difference = _first(
        profile.store.differentiators,
        _first(profile.store.ingredient_proofs, "当天准备、认真制作"),
    )
    ingredient = _first(profile.store.ingredient_proofs, "当天准备的新鲜食材")
    process = _first(profile.store.visible_processes, "从备料到出锅的完整过程")
    concern = _first(
        profile.audience.customer_concerns + profile.store.customer_misunderstandings,
        f"{main_dish}是不是每天现做",
    )
    praise = _first(profile.store.customer_praises, "吃完还想再带朋友来")
    cta = _cta(profile)
    common_tips = "手机竖屏拍摄，画面保持稳定，保留现场自然声音。"

    if strategy == "owner_story":
        story = profile.owner.origin_story.strip() or f"我做餐饮，是想让{audience}吃到一口踏实味道"
        turning = (
            profile.owner.hardest_moment.strip()
            or profile.owner.unique_experience.strip()
            or "开店最难的时候，我也没有降低食材和出品标准"
        )
        proud = profile.owner.proudest_moment.strip() or f"最自豪的是熟客说这里的{main_dish}一直没变"
        hook = f"很多人只看见{restaurant}今天的热闹，不知道{owner}为什么开始做这家店。"
        rows = (
            (hook, "老板直视镜头，先说结果或转折，不从姓名履历讲起。", "店门口", "平视中近景"),
            (f"我是{owner}，大家觉得我是个{persona}的人。{story}。", common_tips, "老板工作区", "固定中景"),
            (turning, "搭配过去照片、空店或老板工作的真实画面。", "门店", "口播加细节特写"),
            (f"但有一件事我一直没变：{difference}。", "用手上正在做的动作证明这句话。", "工作区", "手部特写转中景"),
            (f"{proud}，这也是我继续把{dish}做下去的原因。", "拍招牌菜出锅和老板自然反应。", "出餐口", "菜品特写"),
            (cta, "老板和招牌菜同框，结尾停留两秒。", "店内招牌前", "平视中近景"),
        )
        title = f"{owner}为什么开了{restaurant}"
    elif strategy == "kitchen":
        hook = f"{main_dish}是不是现做的？今天不靠嘴说，直接带你看{restaurant}的后厨。"
        rows = (
            (hook, "从顾客最关心的问题开始，镜头快速进入备料区。", "后厨入口", "跟拍推进"),
            (f"每天先看这一批：{ingredient}，状态不过关就不用。", "俯拍原料，再切日期、纹理或称重细节。", "备料区", "俯拍转特写"),
            (f"接下来是{process}，真正影响味道的是每一步都不省。", "按动作顺序拍摄，保留切配、翻炒或炭火声。", "灶台", "侧面动作特写"),
            (f"{restaurant}一直坚持的是{difference}。", "让老板用一句短话解释标准，同时给证据画面。", "后厨工作区", "中景加插入镜头"),
            (f"最后看看{dish}刚出锅的状态，香气和火候都在画面里。", "热气、油亮和装盘动作连续拍摄。", "出餐口", "45度近景转特写"),
            (cta, "成品、店名和到店提示同屏出现。", "招牌菜桌面", "稳定近景"),
        )
        title = f"公开{restaurant}的{main_dish}制作过程"
    elif strategy == "pain_point":
        hook = f"顾客最常问：{concern}？今天{owner}把答案和证据一起给你看。"
        proof_location = "后厨" if profile.shooting.can_show_kitchen else "出餐口"
        rows = (
            (hook, "问题用大字幕同步出现，老板口播或旁白均可。", "店门口", "平视中近景"),
            (f"先说结论：我们坚持{difference}，不是临时为了拍视频。", common_tips, "门店工作区", "固定中景"),
            (f"第一个证据是{ingredient}。", "只拍能验证结论的细节，避免空泛摆拍。", proof_location, "俯拍特写"),
            (f"第二个证据是{process}。", "用连续动作展示，不用过多转场。", proof_location, "跟随动作近景"),
            (f"熟客最常说的是“{praise}”，但你可以自己来判断。", "不能拍顾客时，用菜品、评价截图或老板复述。", "就餐区", "环境中景"),
            (cta, "保持克制，不使用夸大承诺。", "店内招牌前", "稳定中近景"),
        )
        title = f"回答大家最关心的：{concern}"
    elif strategy == "daily":
        years = profile.store.years_in_business
        history = f"做了{years}年" if years else "每天重复"
        hook = f"一家小餐馆从开门到第一桌客人，要准备多少事情？跟着{restaurant}看一天。"
        customer_tip = (
            "营业后记录客人点单和上菜节奏，拍摄前先征得同意。"
            if profile.shooting.can_show_customers
            else "营业后只记录点单小票、上菜动作和桌面，不拍顾客正脸。"
        )
        rows = (
            (hook, "用开门、亮灯或卷帘门声音进入一天。", "店门口", "广角固定镜头"),
            (f"开门第一件事不是等客人，是把{ingredient}准备好。", "拍真实准备动作，不需要老板完整口播。", "备料区", "动作近景"),
            (f"{history}的流程是{process}，忙起来也不能乱。", "用三个连续动作表现节奏。", "工作区", "跟拍中景"),
            (f"今天第一份{dish}出锅，状态要先过我们自己这一关。", "拍装盘、检查和出餐。", "出餐口", "菜品特写"),
            ("从第一桌到忙起来，这就是小店最真实的烟火气。", f"{customer_tip}现场环境声作为主要声音。", "就餐区", "环境广角"),
            (cta, "老板收尾或店招画面作为固定栏目结尾。", "店内招牌前", "稳定中景"),
        )
        title = f"跟着{restaurant}看真实营业的一天"
    else:
        hook = profile.shooting.hook_preference.strip() or f"先别划走，看看{restaurant}的{main_dish}刚出锅是什么状态。"
        atmosphere = (
            "征得同意后拍顾客夹菜和真实反应。"
            if profile.shooting.can_show_customers
            else "用连续出餐和空盘细节表现受欢迎程度，不拍顾客正脸。"
        )
        rows = (
            (hook, "前三秒先给成品和热气，再出现老板或店名。", "出餐口", "近景快速推进"),
            (f"{restaurant}今天主推{dish}，先看刚出锅的状态。", "沿盘边缓慢移动，捕捉纹理和热气。", "菜品桌面", "45度近景转特写"),
            (f"这道菜最想让你记住的是：{difference}。", "卖点出现时同步展示对应证据。", "工作区", "细节特写"),
            (f"从{ingredient}到{process}，每一步都是这口味道的一部分。", common_tips, "出餐口", "动作近景"),
            (f"{atmosphere}我们希望{audience}吃到的是一口踏实味道。", "画面优先，台词保持一句话。", "就餐区", "环境中景"),
            (cta, "招牌菜和门店名称保持清楚可见。", "店内招牌前", "稳定中近景"),
        )
        title = f"{restaurant}｜{dish}为什么值得来吃"

    return ScriptModel(
        title=title,
        target_duration_seconds=profile.shooting.target_duration_seconds,
        style=profile.shooting.video_style,
        opening_hook=hook,
        cta=cta,
        shots=_shots(profile, rows),
    )


def generate_script_bundle(
    profile: ResearchProfile, candidate_count: int = 3
) -> ScriptBundle:
    """Generate distinct, scored script candidates without calling a model."""

    count = min(5, max(2, candidate_count))
    bundle_id = uuid4().hex[:12]
    scored = _score_strategies(profile)
    chosen = [
        item
        for item in scored
        if not (
            item.key == "kitchen" and not profile.shooting.can_show_kitchen
        )
        and not (
            item.key == "owner_story"
            and profile.owner.appearance_mode == "不出镜"
        )
    ][:count]
    candidates = [
        ScriptCandidate(
            id=f"{bundle_id}-{item.key}",
            strategy=item.key,
            strategy_name=item.name,
            positioning=item.positioning,
            score=item.score,
            reasons=item.reasons,
            difficulty=item.difficulty,  # type: ignore[arg-type]
            required_scenes=item.scenes,
            requires_owner=item.requires_owner,
            script=annotate_script_quality(
                _render_script(item.key, profile),
                profile,
            ),
        )
        for item in chosen
    ]
    restaurant = profile.store.restaurant_name.strip() or "这家小店"
    summary = (
        f"{restaurant}以{_dishes(profile)}为主要内容资产，"
        f"面向{profile.audience.core_audience or '本地目标顾客'}，"
        f"当前优先目标是{profile.audience.content_goal}。"
    )
    return ScriptBundle(
        id=bundle_id,
        generated_at=datetime.now(UTC).isoformat(),
        research_summary=summary,
        candidates=candidates,
    )
