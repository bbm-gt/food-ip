"""HTTP API route modules."""

from .edits import router as edits_router
from .materials import router as materials_router
from .polish import router as polish_router
from .projects import router as projects_router
from .script import router as script_router

__all__ = [
    "edits_router",
    "materials_router",
    "polish_router",
    "projects_router",
    "script_router",
]
