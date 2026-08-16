"""Runtime composition for the independent Director Core SQLite boundary."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from . import config
from .director_core.context import ContextBudget, ModelContextAssembler
from .director_core.database import apply_migrations, connect
from .director_core.orchestrator import DirectorOrchestrator, DirectorStageExecutor
from .director_core.providers.deepseek import DeepSeekStageHandler
from .director_core.repository import AuthorizationScope, DirectorRepository


DIRECTOR_WORKSPACE_ID = "local"
DIRECTOR_BUSY_TIMEOUT_MS = 1000


def open_director_connection():
    """Return one short-lived, configured connection for a single request."""

    config.DIRECTOR_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return connect(config.DIRECTOR_DB_PATH, busy_timeout_ms=DIRECTOR_BUSY_TIMEOUT_MS)


def initialize_director_database() -> None:
    """Apply the existing idempotent Director Core migrations at application start."""

    connection = open_director_connection()
    try:
        apply_migrations(connection)
    finally:
        connection.close()


@contextmanager
def director_repository() -> Iterator[DirectorRepository]:
    """Provide a request-local repository and close whichever connection it ends with."""

    repository = DirectorRepository(
        open_director_connection(),
        fresh_connection_factory=open_director_connection,
    )
    try:
        yield repository
    finally:
        repository.connection.close()


def director_scope(project_id: str) -> AuthorizationScope:
    return AuthorizationScope(DIRECTOR_WORKSPACE_ID, project_id)


def create_director_stage_handler() -> DeepSeekStageHandler:
    """Keep the selected production provider behind the StageHandler boundary."""

    return DeepSeekStageHandler.from_environment()


def create_director_orchestrator(
    repository: DirectorRepository, scope: AuthorizationScope
) -> DirectorOrchestrator:
    assembler = ModelContextAssembler(
        repository,
        scope,
        ContextBudget(config.DIRECTOR_CONTEXT_MAX_UNITS),
    )
    executor = DirectorStageExecutor(assembler, create_director_stage_handler())
    return DirectorOrchestrator(
        repository,
        scope,
        executor,
        max_internal_steps=config.DIRECTOR_MAX_INTERNAL_STEPS,
    )
