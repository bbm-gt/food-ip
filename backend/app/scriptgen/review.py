"""AI 编导审稿：对既有脚本做结构化内容质量评价。

与剧本生成完全独立：只读、不重写、不选题、不新增经营事实、不修改 TopicCard / IP。
只有调用方显式调用才会触发 AI；本模块不接入 generate_ai_script_bundle 主流程，也不自动修稿。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import ValidationError

from .. import config
from . import ai
from .models import (
    DirectorCandidateReview,
    DirectorReview,
    DirectorRevisionVerdict,
    ResearchProfile,
    ScriptBundle,
)


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
