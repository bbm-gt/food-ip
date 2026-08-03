"""Contracts shared by polish providers and HTTP routes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SegmentRef(BaseModel):
    project_id: str
    junction_id: int | None = None
    src_file: str = ""
    range_seconds: tuple[float, float] | None = None


class PolishRequest(BaseModel):
    segment: SegmentRef
    goal: Literal[
        "harmonize_junction", "stabilize", "relight", "fix_audio"
    ] = "harmonize_junction"
    params: dict[str, Any] = Field(default_factory=dict)


class PolishResult(BaseModel):
    segment_id: str
    output_file: str | None = None
    status: Literal["pending", "running", "done", "failed", "not_configured"]
    message: str | None = None
