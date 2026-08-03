"""Optional AI polish contracts and provider registry."""

from . import providers as providers
from .contract import PolishRequest, PolishResult, SegmentRef
from .registry import REGISTRY, PolishProvider, get, register

__all__ = [
    "PolishProvider",
    "PolishRequest",
    "PolishResult",
    "REGISTRY",
    "SegmentRef",
    "get",
    "register",
]
