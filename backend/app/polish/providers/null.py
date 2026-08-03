"""No-op provider used until an AI polish service is configured."""

from ..contract import PolishRequest, PolishResult
from ..registry import register


@register("null")
class NullPolishProvider:
    async def polish(self, req: PolishRequest) -> PolishResult:
        junction = req.segment.junction_id
        segment_id = (
            f"{req.segment.project_id}:junction:{junction}"
            if junction is not None
            else req.segment.project_id
        )
        return PolishResult(
            segment_id=segment_id,
            status="not_configured",
            message="AI 润色功能尚未接入（二期实现）",
        )
