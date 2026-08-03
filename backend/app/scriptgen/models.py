"""Pydantic models shared by research, script generators and API routes."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


_LIST_SEPARATOR = re.compile(r"[\n、,，;；/\\]+")


def _normalize_multi_value(values: list[str]) -> list[str]:
    """Accept common separators while keeping the stored representation clean."""

    normalized: list[str] = []
    for value in values:
        for item in _LIST_SEPARATOR.split(value):
            clean = item.strip()
            if clean and clean not in normalized:
                normalized.append(clean)
    return normalized


class BossInfo(BaseModel):
    restaurant_name: str = ""
    cuisine_type: str = "家常菜"
    signature_dishes: list[str] = Field(default_factory=list)
    owner_persona: str = ""
    audience: str = ""
    video_style: str = "竖屏口播"
    target_duration_seconds: int = 60
    platform: str = "抖音"
    hook_preference: str = ""


class StoreProfile(BaseModel):
    restaurant_name: str = ""
    city: str = ""
    business_district: str = ""
    cuisine_type: str = "家常菜"
    years_in_business: int = Field(default=0, ge=0, le=200)
    price_per_person: int = Field(default=0, ge=0, le=10000)
    signature_dishes: list[str] = Field(default_factory=list)
    business_modes: list[str] = Field(default_factory=list)
    differentiators: list[str] = Field(default_factory=list)
    ingredient_proofs: list[str] = Field(default_factory=list)
    visible_processes: list[str] = Field(default_factory=list)
    customer_praises: list[str] = Field(default_factory=list)
    customer_misunderstandings: list[str] = Field(default_factory=list)

    @field_validator("signature_dishes", mode="after")
    @classmethod
    def normalize_signature_dishes(cls, values: list[str]) -> list[str]:
        return _normalize_multi_value(values)


class OwnerProfile(BaseModel):
    owner_name: str = ""
    hometown: str = ""
    owner_persona: str = ""
    origin_story: str = ""
    hardest_moment: str = ""
    proudest_moment: str = ""
    unique_experience: str = ""
    speaking_style: str = "实在真诚"
    appearance_mode: Literal["真人口播", "旁白", "只拍手部", "不出镜"] = "真人口播"
    language_style: str = "普通话"
    avoided_topics: list[str] = Field(default_factory=list)
    allow_personal_story: bool = False


class AudienceProfile(BaseModel):
    core_audience: str = ""
    dining_scenarios: list[str] = Field(default_factory=list)
    customer_needs: list[str] = Field(default_factory=list)
    customer_concerns: list[str] = Field(default_factory=list)
    current_business_problem: str = ""
    content_goal: Literal["吸引到店", "团购转化", "账号涨粉", "建立信任", "品牌认知"] = "吸引到店"


class ShootingProfile(BaseModel):
    platform: str = "抖音"
    video_style: str = "烟火气纪实"
    target_duration_seconds: int = Field(default=60, ge=15, le=180)
    available_locations: list[str] = Field(default_factory=list)
    unavailable_locations: list[str] = Field(default_factory=list)
    can_show_kitchen: bool = True
    can_show_customers: bool = False
    equipment: list[str] = Field(default_factory=list)
    daily_minutes: int = Field(default=20, ge=0, le=1440)
    update_frequency: str = "每周3条"
    hook_preference: str = ""


class ResearchProfile(BaseModel):
    schema_version: int = 1
    store: StoreProfile = Field(default_factory=StoreProfile)
    owner: OwnerProfile = Field(default_factory=OwnerProfile)
    audience: AudienceProfile = Field(default_factory=AudienceProfile)
    shooting: ShootingProfile = Field(default_factory=ShootingProfile)

    def to_boss_info(self) -> BossInfo:
        """Project the deep profile into the legacy single-template input."""

        return BossInfo(
            restaurant_name=self.store.restaurant_name,
            cuisine_type=self.store.cuisine_type,
            signature_dishes=self.store.signature_dishes,
            owner_persona=self.owner.owner_persona or self.owner.speaking_style,
            audience=self.audience.core_audience,
            video_style=self.shooting.video_style,
            target_duration_seconds=self.shooting.target_duration_seconds,
            platform=self.shooting.platform,
            hook_preference=self.shooting.hook_preference,
        )

    @classmethod
    def from_boss_info(cls, boss_info: BossInfo) -> "ResearchProfile":
        """Build a deep-profile draft from a legacy project."""

        return cls(
            store=StoreProfile(
                restaurant_name=boss_info.restaurant_name,
                cuisine_type=boss_info.cuisine_type,
                signature_dishes=boss_info.signature_dishes,
            ),
            owner=OwnerProfile(owner_persona=boss_info.owner_persona),
            audience=AudienceProfile(core_audience=boss_info.audience),
            shooting=ShootingProfile(
                platform=boss_info.platform,
                video_style=boss_info.video_style,
                target_duration_seconds=boss_info.target_duration_seconds,
                hook_preference=boss_info.hook_preference,
            ),
        )


class Shot(BaseModel):
    shot_index: int
    lines: str
    shooting_tips: str
    duration_hint_seconds: int
    location: str = ""
    angle: str = ""
    purpose: str = ""
    subject: str = ""
    action_steps: list[str] = Field(default_factory=list)
    phone_setup: str = ""
    camera_movement: str = ""
    audio: str = ""
    lighting: str = ""
    props: list[str] = Field(default_factory=list)
    subtitle: str = ""
    edit_note: str = ""
    common_mistakes: list[str] = Field(default_factory=list)
    retake_if: list[str] = Field(default_factory=list)


class ScriptModel(BaseModel):
    title: str
    target_duration_seconds: int
    style: str
    opening_hook: str
    cta: str
    shots: list[Shot]


class ScriptCandidate(BaseModel):
    id: str
    strategy: str
    strategy_name: str
    positioning: str
    score: int = Field(ge=0, le=100)
    reasons: list[str]
    difficulty: Literal["简单", "中等", "较难"]
    required_scenes: list[str]
    requires_owner: bool
    script: ScriptModel


class ScriptBundle(BaseModel):
    id: str
    generated_at: str
    research_summary: str
    candidates: list[ScriptCandidate]
    selected_script_id: str | None = None
    generator: Literal["template", "ai", "template_fallback"] = "template"
    model_name: str = ""
    warnings: list[str] = Field(default_factory=list)
