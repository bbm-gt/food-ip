"""AI 编导审稿：对既有脚本做结构化内容质量评价。

与剧本生成完全独立：只读、不重写、不选题、不新增经营事实、不修改 TopicCard / IP。
只有调用方显式调用才会触发 AI；本模块不接入 generate_ai_script_bundle 主流程，也不自动修稿。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import config
from . import ai
from .bundles import _score_strategies
from .models import (
    DirectorCandidateReview,
    DirectorReview,
    DirectorRevisionVerdict,
    ResearchProfile,
    ScriptBundle,
    ScriptCandidate,
    ScriptModel,
    Shot,
)
from .quality import annotate_script_quality


def _candidate_payload(bundle: ScriptBundle) -> list[dict[str, object]]:
    return [
        {
            "candidate_id": candidate.id,
            "strategy": candidate.strategy,
            "strategy_name": candidate.strategy_name,
            "script": candidate.script.model_dump(
                mode="json", exclude={"quality_risks"}
            ),
        }
        for candidate in bundle.candidates
    ]


_SYSTEM_PROMPT = """
你是餐饮老板短视频的 AI 编导，负责对已完成脚本做独立内容审稿。你只做评价，绝不重写脚本、重新选题、新增经营事实，也不修改 TopicCard、CreativeBrief 或 IP 定位。

职责与安全规则：
1. 只依据提供的候选脚本、research_profile 与 creative_context 中的事实进行评价；不得编造或补写任何内容。
2. 不输出改写后的台词、分镜或新选题；审稿结果只包含评分、问题、优点与是否建议修稿。
3. 真实性、敏感词、不可拍区域等程序硬校验由服务端负责；你专注内容质量：像不像真人说话、有没有网感、节奏与表达是否够强，不要重复报告程序风险。
4. 每套候选按以下 9 个维度给 1-10 整数分（数值越大越好；ad_feeling 为反向计分，10=完全不像广告，1=像硬推销）：
   - opening_hook_strength：开头吸引力
   - oral_naturalness：老板说话是否自然、有无 AI 腔
   - information_density：是否精炼、有无废话或重复
   - progression：内容是否持续推进、有无继续观看动力
   - evidence_strength：核心观点是否有真实证据支撑
   - ip_alignment：是否符合老板 IP
   - shootability：普通餐饮老板能否实际完成拍摄
   - ad_feeling：广告感控制
   - distinctiveness：多套候选之间差异是否足够
5. 若 locked_topic 为 true（本期主题已锁定 selected_topic_card）：distinctiveness 只评价三套在 Hook、叙事方式、证据展示、老板表达、镜头组织上的差异，而不是主题差异；不得建议换题。
6. issues 必须可执行：每条都要指出具体 shot_index（1-8）或具体字段名（如 title、opening_hook、cta、shots[N].lines），禁止只写一句泛泛评价。
7. should_revise：当存在会明显影响成片质量的问题时置 true，否则 false；strengths 用简短的完整句。

仅输出一个 JSON 对象：
{"reviews":[{"candidate_id":"...","strategy":"...","scores":{"opening_hook_strength":7,"oral_naturalness":8,"information_density":7,"progression":7,"evidence_strength":8,"ip_alignment":8,"shootability":9,"ad_feeling":8,"distinctiveness":7},"issues":[{"dimension":"oral_naturalness","message":"第3镜头台词偏书面，像念稿。","shot_index":3}],"strengths":["开场钩子简洁有力"],"should_revise":false}]}
""".strip()


def review_script_bundle(
    bundle: ScriptBundle,
    profile: ResearchProfile,
    *,
    creative_context: dict[str, object] | None = None,
) -> DirectorReview:
    """对一组候选脚本做独立 AI 编导审稿，返回新结构，不修改 bundle。

    每次调用都会发起一次审稿请求；由调用方决定何时使用，本模块不做自动接入。
    """
    if not bundle.candidates:
        raise ValueError("ScriptBundle 没有任何候选脚本")
    expected_by_id = {
        candidate.id: candidate.strategy for candidate in bundle.candidates
    }
    locked_topic = (
        creative_context.get("selected_topic_card") is not None
        if creative_context is not None
        else False
    )
    feedback = ""
    output: DirectorReview | None = None
    last_error: Exception | None = None
    for _attempt in range(2):
        payload: dict[str, object] = {
            "task": "对以下候选脚本进行独立 AI 编导内容审稿",
            "research_profile": ai._safe_profile_payload(profile),
            "candidates": _candidate_payload(bundle),
            "locked_topic": locked_topic,
        }
        if creative_context is not None:
            payload["creative_context"] = creative_context
        if feedback:
            payload["previous_output_errors"] = feedback
            payload["repair_instruction"] = "重新完整输出审稿 JSON，并修正全部错误"
        try:
            raw = ai._request_json(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ]
            )
            parsed = DirectorReview.model_validate(raw)
            ids = [item.candidate_id for item in parsed.reviews]
            if len(ids) != len(expected_by_id) or set(ids) != set(expected_by_id):
                raise ai.AIResponseError("审稿结果必须覆盖全部候选且不包含未知候选")
            for item in parsed.reviews:
                if expected_by_id[item.candidate_id] != item.strategy:
                    raise ai.AIResponseError("审稿候选的 strategy 与脚本不一致")
            output = parsed
            break
        except ValidationError as exc:
            last_error = exc
            feedback = "JSON 字段不完整或类型错误：" + str(exc)[:1200]
        except ai.AIResponseError as exc:
            last_error = exc
            feedback = str(exc)
    if output is None:
        raise ai.AIResponseError(
            f"DeepSeek 两次输出均未通过编导审稿校验：{last_error}"
        ) from last_error
    return output.model_copy(
        update={
            "bundle_id": bundle.id,
            "model_name": config.AI_SCRIPT_MODEL,
            "generated_at": datetime.now(UTC).isoformat(),
        }
    )


# --- 纯程序低分判定规则（不调 AI，不修改 bundle / review 任何内容） ---

OVERALL_REVISION_THRESHOLD = 7.0
CRITICAL_DIMENSION_THRESHOLD = 6
CRITICAL_DIMENSIONS: tuple[str, ...] = (
    "opening_hook_strength",
    "oral_naturalness",
    "progression",
    "evidence_strength",
    "shootability",
)


def judge_revision_needed(
    item: DirectorCandidateReview,
) -> DirectorRevisionVerdict:
    """根据 AI 编导评分做纯程序判定，输出该候选是否需要进入修稿。

    需要修稿 if：
    - overall_score < 7.0
    - 或任一关键维度 < 6：
      opening_hook_strength / oral_naturalness / progression / evidence_strength / shootability

    AI 的 should_revise 仅作参考，不单独决定是否修稿；最终由本函数的评分规则决定。
    纯计算：不修改 bundle / review 的任何字段。
    """
    scores = item.scores.model_dump()
    weak = [
        name
        for name in CRITICAL_DIMENSIONS
        if scores[name] < CRITICAL_DIMENSION_THRESHOLD
    ]
    overall = item.overall_score
    reasons: list[str] = []
    if overall < OVERALL_REVISION_THRESHOLD:
        reasons.append(f"总分 {overall} 低于 {OVERALL_REVISION_THRESHOLD}")
    reasons.extend(
        f"关键维度 {name} 得分 {scores[name]} 低于 {CRITICAL_DIMENSION_THRESHOLD}"
        for name in weak
    )
    return DirectorRevisionVerdict(
        candidate_id=item.candidate_id,
        needs_revision=bool(weak) or overall < OVERALL_REVISION_THRESHOLD,
        weak_dimensions=weak,
        reasons=reasons,
        issues=item.issues,
    )


# --- AI 局部修稿（独立于编剧生成与编导审稿的第三层：只修指定低质量位置） ---


class RevisionShotPatch(BaseModel):
    """单个镜头的修稿补丁：只允许出现可修的镜头字段，禁止整段替换。

    extra="forbid"：模型若返回 shot_index / duration_hint_seconds 或任何
    不属于镜头内容的键（例如完整分镜），整个补丁直接拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    shot_index: int = Field(ge=1, le=8)
    lines: str | None = None
    shooting_tips: str | None = None
    location: str | None = None
    angle: str | None = None
    purpose: str | None = None
    subject: str | None = None
    action_steps: list[str] | None = None
    phone_setup: str | None = None
    camera_movement: str | None = None
    audio: str | None = None
    lighting: str | None = None
    props: list[str] | None = None
    subtitle: str | None = None
    edit_note: str | None = None
    common_mistakes: list[str] | None = None
    retake_if: list[str] | None = None
    tone: str | None = None
    emotion: str | None = None
    speech_rate: str | None = None
    pause_guidance: str | None = None
    expression_guidance: str | None = None


class RevisionPatch(BaseModel):
    """AI 局部修稿输出：只包含需要修改的顶层字段与镜头补丁，不含完整脚本。

    extra="forbid"：模型若返回 strategy / candidates / quality_risks 等
    「整篇重写」性质的键，整个补丁直接拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    opening_hook: str | None = None
    cta: str | None = None
    shots: list[RevisionShotPatch] = Field(default_factory=list)


_TOPLEVEL_PATCHABLE = ("title", "opening_hook", "cta")
# 复用现有程序硬校验时，单候选 AIBundleOutput 的 research_summary 需要 ≥6 字。
_VALIDATION_SUMMARY = "修稿后的单个候选脚本程序校验。"

_REVISION_SYSTEM_PROMPT = """
你是餐饮老板短视频的 AI 局部修稿执行者，职责与「编剧」和「编导」完全分离：
- 编剧负责整篇生成，编导负责评分审稿；你只负责修复 director_issues 指出的具体低质量位置。
- 绝不整篇重写、绝不重新选题、绝不新增经营事实，绝不修改 IP 定位 / confirmed Brief / selected TopicCard。

修稿规则：
1. 只修改 director_issues 中指出的位置：shot_index 指向的镜头，或 field 指向的顶层字段（title / opening_hook / cta）。
2. 未指出的镜头与字段必须保持原样，不要顺手动其他位置。
3. 不得修改已确认经营事实，不得编造地址、销量、价格、顾客评价、食材来源或老板经历。
4. 不得引入夸大/假权威表达（如“我赌你”“天花板”“闭眼冲”“导航搜”）或绝对化承诺。
5. opening_hook 必须保持出现在第 1 镜头台词中，cta 必须保持出现在第 6 镜头台词中；若修改其中之一，必须同步修改对应的镜头台词。
6. 不得修改任何镜头的 shot_index 或 duration_hint_seconds，不得修改 target_duration_seconds / style。
7. 输出必须是 JSON 补丁对象：顶层可选 title / opening_hook / cta；shots 数组每项 {"shot_index": N, "字段": 新值}。只输出要改的字段，至少改一个；若无需修改可输出空对象 {}。
8. 不要输出完整脚本、candidate_id、strategy 或其他任何未要求的键。

JSON 示例：
{"shots":[{"shot_index":3,"lines":"把念稿感改成自然口语，像和熟客聊天。","subtitle":"对应字幕"}]}
""".strip()


def _is_locked(creative_context: dict[str, object] | None) -> bool:
    return (
        creative_context.get("selected_topic_card") is not None
        if creative_context is not None
        else False
    )


def _allowed_anchors(
    verdict: DirectorRevisionVerdict,
) -> tuple[set[int], set[str]]:
    """从纯程序判定结果中提取允许修改的镜头与顶层字段集合。"""
    allowed_shots: set[int] = set()
    allowed_fields: set[str] = set()
    for issue in verdict.issues:
        if issue.shot_index is not None:
            allowed_shots.add(issue.shot_index)
        if issue.field:
            match = re.fullmatch(r"shots\[(\d+)\](?:\.(\w+))?", issue.field)
            if match:
                # 审稿 field 用 0-based shots[N]，镜头 shot_index 是 1-based
                allowed_shots.add(int(match.group(1)) + 1)
            elif issue.field in _TOPLEVEL_PATCHABLE:
                allowed_fields.add(issue.field)
    # 一致性耦合：改 hook 字段必须能同步第 1 镜头，cta 字段必须能同步第 6 镜头，
    # 否则「hook 必须出现在第 1 镜头 / cta 必须出现在第 6 镜头」硬校验必然死锁。
    if 1 in allowed_shots:
        allowed_fields.add("opening_hook")
    if 6 in allowed_shots:
        allowed_fields.add("cta")
    return allowed_shots, allowed_fields


def _apply_revision_patch(
    script: ScriptModel,
    patch: RevisionPatch,
    allowed_shots: set[int],
    allowed_fields: set[str],
    locked_topic: bool,
) -> ScriptModel:
    """把补丁合并进原脚本，严格限定在锚点范围内。"""
    top_updates: dict[str, object] = {}
    for name in _TOPLEVEL_PATCHABLE:
        value = getattr(patch, name)
        if value is None:
            continue
        if locked_topic and name == "title":
            raise ai.AIResponseError("锁题模式下不允许修改标题（防止修稿换题）")
        if name not in allowed_fields:
            raise ai.AIResponseError(f"修稿被禁止修改未指出的顶层字段 {name}")
        top_updates[name] = value

    by_index = {shot.shot_index: shot for shot in script.shots}
    shot_updates: dict[int, dict[str, object]] = {}
    for shot_patch in patch.shots:
        if shot_patch.shot_index not in allowed_shots:
            raise ai.AIResponseError(
                f"修稿被禁止修改未指出的第{shot_patch.shot_index}镜头"
            )
        if shot_patch.shot_index not in by_index:
            raise ai.AIResponseError(
                f"修稿引用了不存在的镜头 {shot_patch.shot_index}"
            )
        fields = shot_patch.model_dump(exclude={"shot_index"}, exclude_none=True)
        if not fields:
            raise ai.AIResponseError(
                f"第{shot_patch.shot_index}镜头修稿未提供任何修改字段"
            )
        shot_updates[shot_patch.shot_index] = fields

    if not top_updates and not shot_updates:
        return script  # AI 认为无需修改，原样返回

    new_shots = [
        shot.model_copy(update=shot_updates[shot.shot_index])
        if shot.shot_index in shot_updates
        else shot
        for shot in script.shots
    ]
    return script.model_copy(update={**top_updates, "shots": new_shots})


def _to_ai_shot(shot: Shot) -> ai.AIDetailedShot:
    """把脚本镜头逆向为 AI 生成中间结构，以便复用现有程序硬校验。

    详细镜头字段可能为空（如模板候选），用中性占位补全以满足 AIDetailedShot
    的字段长度约束；占位内容不参与任何事实判断，也不出现在返回结果里。
    """
    action_steps = shot.action_steps or []
    if len(action_steps) < 2:
        action_steps = ["保持手机稳定", "完整录制本段内容"]
    action_steps = action_steps[:5]
    return ai.AIDetailedShot(
        shot_index=shot.shot_index,
        purpose=shot.purpose or "推进本镜头信息",
        lines=shot.lines,
        location=shot.location or "店内",
        angle=shot.angle or "平视中景",
        subject=shot.subject or "老板与菜品",
        action_steps=action_steps,
        phone_setup=shot.phone_setup or "手机竖屏稳定拍摄",
        camera_movement=shot.camera_movement or "固定机位",
        audio=shot.audio or "保留现场声",
        lighting=shot.lighting or "自然光为主",
        props=(shot.props or [])[:6],
        subtitle=shot.subtitle,
        edit_note=shot.edit_note or "本段内容完整录制",
        common_mistakes=(shot.common_mistakes or ["中途停止录制"])[:4],
        retake_if=(shot.retake_if or ["画面模糊或晃动明显"])[:4],
        tone=shot.tone,
        emotion=shot.emotion,
        speech_rate=shot.speech_rate,
        pause_guidance=shot.pause_guidance,
        expression_guidance=shot.expression_guidance,
        duration_seconds=max(2, min(30, shot.duration_hint_seconds)),
    )


def _to_ai_candidate(strategy: str, script: ScriptModel) -> ai.AIGeneratedCandidate:
    return ai.AIGeneratedCandidate(
        strategy=strategy,
        title=script.title,
        opening_hook=script.opening_hook,
        cta=script.cta,
        shots=[_to_ai_shot(shot) for shot in script.shots],
    )


def _validate_revision(
    original: ScriptCandidate,
    revised: ScriptModel,
    profile: ResearchProfile,
) -> None:
    """修稿后的脚本必须再次通过现有程序硬规则校验（复用 ai._validate_quality）。"""
    if len(revised.shots) != len(original.script.shots):
        raise ai.AIResponseError("修稿改变了镜头数量")
    if [shot.shot_index for shot in revised.shots] != [
        shot.shot_index for shot in original.script.shots
    ]:
        raise ai.AIResponseError("修稿改变了镜头编号")
    if revised.target_duration_seconds != original.script.target_duration_seconds:
        raise ai.AIResponseError("修稿改变了成片时长")
    strategies = [
        item
        for item in _score_strategies(profile)
        if item.key == original.strategy
    ]
    if not strategies:
        raise ai.AIResponseError(
            f"找不到候选 {original.strategy} 对应的策略规则"
        )
    output = ai.AIBundleOutput(
        research_summary=_VALIDATION_SUMMARY,
        candidates=[_to_ai_candidate(original.strategy, revised)],
    )
    ai._validate_quality(output, profile, strategies)


def _build_revision_messages(
    candidate: ScriptCandidate,
    review_item: DirectorCandidateReview,
    verdict: DirectorRevisionVerdict,
    profile: ResearchProfile,
    *,
    creative_context: dict[str, object] | None,
    locked_topic: bool,
    feedback: str = "",
) -> list[dict[str, str]]:
    system_prompt = _REVISION_SYSTEM_PROMPT
    if locked_topic:
        system_prompt += (
            "\n9. 本期主题已锁定：绝对不得更换主题；标题字段禁止出现在修稿补丁中；"
            "只允许在表达、台词自然度、拍摄指导等质量层面做局部修正。"
        )
    user_payload: dict[str, object] = {
        "task": "对下列候选脚本做局部修稿，只修改编导问题指出的具体位置",
        "candidate": {
            "candidate_id": candidate.id,
            "strategy": candidate.strategy,
            "strategy_name": candidate.strategy_name,
            "script": candidate.script.model_dump(
                mode="json", exclude={"quality_risks"}
            ),
        },
        "director_scores": review_item.scores.model_dump(),
        "revision_verdict": {
            "needs_revision": verdict.needs_revision,
            "weak_dimensions": verdict.weak_dimensions,
            "reasons": verdict.reasons,
        },
        "director_issues": [issue.model_dump() for issue in verdict.issues],
        "research_profile": ai._safe_profile_payload(profile),
        "locked_topic": locked_topic,
    }
    if creative_context is not None:
        user_payload["confirmed_creative_context"] = creative_context
    if feedback:
        user_payload["previous_output_errors"] = feedback
        user_payload["repair_instruction"] = "重新输出修稿补丁 JSON，并修正全部错误"
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False),
        },
    ]


def revise_script_candidate(
    candidate: ScriptCandidate,
    review_item: DirectorCandidateReview,
    verdict: DirectorRevisionVerdict,
    profile: ResearchProfile,
    *,
    creative_context: dict[str, object] | None = None,
) -> ScriptCandidate:
    """对单个候选做 AI 局部修稿，返回修稿后的新候选，不修改传入对象。

    只修改 verdict / issues 指向的低质量镜头或字段；未指出的内容保持原值；
    不修改 candidate_id / strategy / 已确认经营事实 / IP / Brief / TopicCard；
    锁题模式下禁止改标题，绝对不换题。修稿结果必须再次通过现有程序硬规则校验。
    本函数只做一次修稿：不自动接入生成流程，不重新审稿。
    """
    if verdict.candidate_id != candidate.id or review_item.candidate_id != candidate.id:
        raise ValueError("修稿输入与候选 candidate_id 不一致")
    if not verdict.needs_revision:
        raise ValueError("程序判定无需修稿，不应触发局部修稿")

    locked_topic = _is_locked(creative_context)
    allowed_shots, allowed_fields = _allowed_anchors(verdict)
    if not allowed_shots and not allowed_fields:
        raise ai.AIResponseError("审稿问题未定位到具体镜头或字段，无法执行局部修稿")

    feedback = ""
    revised: ScriptModel | None = None
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            raw = ai._request_json(
                _build_revision_messages(
                    candidate,
                    review_item,
                    verdict,
                    profile,
                    creative_context=creative_context,
                    locked_topic=locked_topic,
                    feedback=feedback,
                )
            )
            patch = RevisionPatch.model_validate(raw)
            revised = _apply_revision_patch(
                candidate.script, patch, allowed_shots, allowed_fields, locked_topic
            )
            _validate_revision(candidate, revised, profile)
            break
        except ValidationError as exc:
            revised = None
            last_error = exc
            feedback = "JSON 字段不完整或类型错误：" + str(exc)[:1200]
        except ai.AIResponseError as exc:
            revised = None
            last_error = exc
            feedback = str(exc)
    if revised is None:
        raise ai.AIResponseError(
            f"DeepSeek 两次修稿均未通过校验：{last_error}"
        ) from last_error
    return candidate.model_copy(
        update={
            "script": annotate_script_quality(
                revised, profile, creative_context=creative_context
            )
        }
    )
