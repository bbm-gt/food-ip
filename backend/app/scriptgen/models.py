"""Pydantic models shared by script generators and API routes."""

from pydantic import BaseModel, Field


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


class Shot(BaseModel):
    shot_index: int
    lines: str
    shooting_tips: str
    duration_hint_seconds: int
    location: str = ""
    angle: str = ""


class ScriptModel(BaseModel):
    title: str
    target_duration_seconds: int
    style: str
    opening_hook: str
    cta: str
    shots: list[Shot]
