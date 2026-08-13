"""SQLite connection and repeatable migration helpers for Director Core."""

from __future__ import annotations

import sqlite3
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class ForeignKeysDisabledError(RuntimeError):
    pass


def enable_and_verify_foreign_keys(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    enabled = connection.execute("PRAGMA foreign_keys").fetchone()
    if enabled is None or enabled[0] != 1:
        raise ForeignKeysDisabledError("SQLite foreign_keys must be enabled")


def require_foreign_keys(connection: sqlite3.Connection) -> None:
    enabled = connection.execute("PRAGMA foreign_keys").fetchone()
    if enabled is None or enabled[0] != 1:
        raise ForeignKeysDisabledError("Director Core writes require SQLite foreign_keys")


def connect(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    enable_and_verify_foreign_keys(connection)
    return connection


def apply_migrations(connection: sqlite3.Connection) -> None:
    require_foreign_keys(connection)
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        connection.executescript(migration.read_text(encoding="utf-8"))
