import sqlite3
from uuid import uuid4

import pytest

from backend.app.director_core.database import (
    ForeignKeysDisabledError,
    apply_migrations,
    connect,
)
from backend.app.director_core.repository import AuthorizationScope, DirectorRepository


TABLES = {
    "director_sessions",
    "director_messages",
    "director_working_state",
    "director_turns",
    "director_context_checkpoints",
    "director_ready_content",
}


def test_migration_creates_exactly_six_director_tables_and_is_repeatable() -> None:
    connection = connect(":memory:")
    apply_migrations(connection)
    apply_migrations(connection)
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'director_%'"
        )
    }
    assert names == TABLES
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_foreign_keys_must_be_enabled_before_director_write() -> None:
    connection = connect(":memory:")
    apply_migrations(connection)
    connection.execute("PRAGMA foreign_keys = OFF")
    repository = DirectorRepository(connection)
    with pytest.raises(ForeignKeysDisabledError):
        repository.create_session(AuthorizationScope("workspace", "project"))


def test_checks_unique_foreign_keys_and_immutable_guards() -> None:
    connection = connect(":memory:")
    apply_migrations(connection)
    repository = DirectorRepository(connection)
    scope = AuthorizationScope("workspace", "project")
    session = repository.create_session(scope)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO director_sessions
               (id, workspace_id, project_id, lifecycle_status, created_at)
               VALUES ('not-a-uuid', 'workspace', 'project', 'ACTIVE', '2026-01-01T00:00:00.000Z')"""
        )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO director_messages VALUES (?, ?, 1, 'OWNER', 'x', ?, '2026-01-01T00:00:00.000Z')",
            (str(uuid4()), session.id, str(uuid4())),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE director_sessions SET project_id = 'other' WHERE id = ?", (session.id,)
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE director_working_state SET stage = 'INVALID' WHERE session_id = ?", (session.id,)
        )
