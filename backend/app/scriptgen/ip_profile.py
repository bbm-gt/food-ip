from __future__ import annotations

import json

from . import ai
from .models import IPProfile, ResearchProfile


def rule_ip_profile(research: ResearchProfile) -> IPProfile:
    store, owner, audience = research.store, research.owner, research.audience
    name = owner.owner_name or store.restaurant_name or "餐饮老板"
    cuisine = store.cuisine_type or "餐饮"
    evidence = [*store.differentiators, *store.ingredient_proofs, *store.visible_processes]
    return IPProfile(
        persona_positioning=owner.owner_persona or f"认真做{cuisine}的{name}",
        core_audience=audience.core_audience or "附近想吃得放心的顾客",
        core_promise=f"持续讲清楚{name}如何把{cuisine}做得真实、值得来店体验",
        memory_points=[item for item in [store.restaurant_name, *store.signature_dishes, *store.differentiators] if item][:4],
        content_pillars=["招牌菜与做法", "真实食材与过程", "老板的经营日常"],
        recurring_series=["今天这道菜怎么做", "顾客常问", "店里的真实一天"],
        speaking_style=owner.speaking_style or owner.owner_persona or "实在真诚",
        evidence_assets=evidence[:6], avoided_topics=owner.avoided_topics,
        conversion_path=["认识老板", "相信真实做法", "记住招牌菜", "到店体验"],
    )


def generate_ip_profile(research: ResearchProfile) -> IPProfile:
    fallback = rule_ip_profile(research)
    try:
        response = ai._request_json([
            {"role": "system", "content": "你是餐饮老板IP定位顾问。只输出JSON，不得编造事实。"},
            {"role": "user", "content": json.dumps({"research": ai._safe_profile_payload(research), "fallback": fallback.model_dump(), "required_fields": list(IPProfile.model_fields)}, ensure_ascii=False)},
        ])
        return IPProfile.model_validate(response).model_copy(update={"confirmed": False})
    except (ai.AIScriptError, ValueError, TypeError):
        return fallback
