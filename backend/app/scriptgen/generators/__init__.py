"""Script generator protocol and registry."""

from collections.abc import Callable
from typing import Protocol, TypeVar

from ..models import BossInfo, ScriptModel


class ScriptGenerator(Protocol):
    def generate(self, boss_info: BossInfo) -> ScriptModel:
        """Generate a structured script from questionnaire data."""


GeneratorType = TypeVar("GeneratorType", bound=type[ScriptGenerator])
REGISTRY: dict[str, ScriptGenerator] = {}


def register(name: str) -> Callable[[GeneratorType], GeneratorType]:
    """Register a generator class under a stable public name."""

    def decorator(generator_type: GeneratorType) -> GeneratorType:
        if name in REGISTRY:
            raise ValueError(f"生成器已注册：{name}")
        REGISTRY[name] = generator_type()
        return generator_type

    return decorator


def get(name: str) -> ScriptGenerator:
    """Return a registered generator instance."""

    try:
        return REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"生成器未配置：{name}") from exc


# Import modules after defining the registry so their decorators can register.
from . import codex as _codex  # noqa: E402,F401
from . import template as _template  # noqa: E402,F401
