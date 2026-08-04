"""Lightweight, user-facing script risk markers.

These checks intentionally produce review prompts only. They do not rewrite,
reject, or score scripts.
"""

from __future__ import annotations

import re
from typing import Any

from .models import ResearchProfile, ScriptModel, ScriptQualityRisk


_EXTREME_PHRASES = (
    "全网第一",
    "全城第一",
    "第一名",
    "NO.1",
    "顶级",
    "绝无仅有",
    "唯一",
    "百分百",
    "100%",
    "最好",
    "冠军",
)
_PROMISE_PHRASES = (
    "保证",
    "一定不会",
    "绝对不会",
    "永远不会",
    "治愈",
    "根治",
    "不发胖",
)
_NUMERIC_CLAIM = re.compile(r"\d+(?:\.\d+)?\s*(?:万|%|人|份|家|斤|元|桌|位|公里|年)")
_CUSTOMER_BEHAVIOR = re.compile(r"顾客|客人|熟客|排队|点头|夹菜|称赞|反应")
_DANGEROUS_SHOOTING = re.compile(r"炭火|热油|沸油|刀具|切肉|灶台|明火|火焰")
_COMPLEX_ACTION = re.compile(r"同时|一边.+一边|先.+再.+最后")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _script_text(script: ScriptModel) -> str:
    parts = [script.title, script.opening_hook, script.cta]
    for shot in script.shots:
        parts.extend(
            [
                shot.lines,
                shot.subject,
                *shot.action_steps,
                shot.phone_setup,
                shot.camera_movement,
                shot.subtitle,
            ]
        )
    return "\n".join(item for item in parts if item)


def scan_script_quality(
    script: ScriptModel,
    profile: ResearchProfile,
    *,
    creative_context: dict[str, object] | None = None,
) -> list[ScriptQualityRisk]:
    """Return lightweight risk prompts without changing the supplied script."""

    risks: list[ScriptQualityRisk] = []
    seen: set[tuple[str, str, int | None]] = set()

    def add(category: str, message: str, shot_index: int | None = None) -> None:
        key = (category, message, shot_index)
        if key not in seen:
            seen.add(key)
            risks.append(
                ScriptQualityRisk(
                    category=category, message=message, shot_index=shot_index
                )
            )

    full_text = _script_text(script)
    for phrase in _EXTREME_PHRASES:
        if phrase.lower() in full_text.lower():
            add("真实性", f"包含极限或排名表达“{phrase}”，发布前请核实依据。")
    for phrase in _PROMISE_PHRASES:
        if phrase in full_text:
            add("真实性", f"包含绝对化或结果承诺“{phrase}”，请确认是否有事实依据。")

    for shot in script.shots:
        shot_text = "\n".join(
            [
                _text(shot.lines),
                _text(shot.subject),
                _text(shot.phone_setup),
                _text(shot.camera_movement),
                "\n".join(shot.action_steps),
            ]
        )
        numeric_claim = _NUMERIC_CLAIM.search(shot.lines)
        if numeric_claim:
            add(
                "真实性",
                f"包含数字或结果性表述“{numeric_claim.group(0)}”，请核对来源。",
                shot.shot_index,
            )

        for blocked_location in profile.shooting.unavailable_locations:
            location = blocked_location.strip()
            if location and location in shot.location:
                add("可拍摄性", f"地点“{location}”被调研标记为不可拍。", shot.shot_index)
        if not profile.shooting.can_show_kitchen and any(
            place in shot.location for place in ("后厨", "备料区", "灶台")
        ):
            add("可拍摄性", "该镜头安排了当前不可展示的后厨区域。", shot.shot_index)
        if _CUSTOMER_BEHAVIOR.search(shot_text):
            add("可拍摄性", "镜头可能需要顾客配合、授权或可控现场反应。", shot.shot_index)
        if len(shot.action_steps) >= 4 or _COMPLEX_ACTION.search(shot_text):
            add("可拍摄性", "动作步骤较多，建议拆成简单、可重复的拍摄动作。", shot.shot_index)
        if _DANGEROUS_SHOOTING.search(shot_text) and re.search(
            r"靠近|贴近|推进|跟拍|低机位", shot_text
        ):
            add("可拍摄性", "涉及火、热油或刀具，拍摄时请保持安全距离并避免危险运镜。", shot.shot_index)

    appearance = profile.owner.appearance_mode
    if appearance == "不出镜" and re.search(r"老板[^\n]{0,12}(口播|出镜|直视镜头)|露脸", full_text):
        add("IP一致性", "脚本出现老板出镜或口播要求，但当前老板设置为不出镜。")
    if appearance == "只拍手部" and re.search(r"老板[^\n]{0,12}(口播|直视镜头)|露脸", full_text):
        add("IP一致性", "脚本出现正面口播或露脸要求，但当前老板设置为只拍手部。")

    topics = list(profile.owner.avoided_topics)
    if creative_context:
        ip_profile = creative_context.get("ip_profile")
        if isinstance(ip_profile, dict):
            values = ip_profile.get("avoided_topics", [])
            if isinstance(values, list):
                topics.extend(_text(value) for value in values)
    for topic in topics:
        clean_topic = topic.strip()
        if len(clean_topic) >= 2 and clean_topic in full_text:
            add("IP一致性", f"脚本涉及已标记为需避开的主题“{clean_topic}”。")

    return risks


def annotate_script_quality(
    script: ScriptModel,
    profile: ResearchProfile,
    *,
    creative_context: dict[str, object] | None = None,
) -> ScriptModel:
    """Attach review prompts while preserving every script field verbatim."""

    return script.model_copy(
        update={
            "quality_risks": scan_script_quality(
                script, profile, creative_context=creative_context
            )
        }
    )
