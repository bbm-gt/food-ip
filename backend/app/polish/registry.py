"""Provider registry for the optional second-phase polish capability."""

from __future__ import annotations

from typing import Protocol

from .contract import PolishRequest, PolishResult


class PolishProvider(Protocol):
    async def polish(self, req: PolishRequest) -> PolishResult: ...


REGISTRY: dict[str, PolishProvider] = {}


def register(name: str):
    """Register a provider class under a stable capability name."""

    def decorator(provider_class: type[PolishProvider]) -> type[PolishProvider]:
        REGISTRY[name] = provider_class()
        return provider_class

    return decorator


def get(name: str) -> PolishProvider:
    try:
        return REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"润色 provider 未注册：{name}") from exc
