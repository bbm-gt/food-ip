"""Placeholder for the optional second-phase Codex generator."""

from ..models import BossInfo, ScriptModel
from . import register


@register("codex")
class CodexGenerator:
    def generate(self, boss_info: BossInfo) -> ScriptModel:
        del boss_info
        raise NotImplementedError("Codex AI 生成器二期接入")
