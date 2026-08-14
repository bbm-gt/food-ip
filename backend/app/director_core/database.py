"""SQLite connection and repeatable migration helpers for Director Core."""

from __future__ import annotations

import sqlite3
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class ForeignKeysDisabledError(RuntimeError):
    pass


class InvalidBusyTimeoutError(ValueError):
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


def _validate_busy_timeout_ms(busy_timeout_ms: int) -> int:
    if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int):
        raise InvalidBusyTimeoutError("busy_timeout_ms must be a non-negative integer")
    if busy_timeout_ms < 0:
        raise InvalidBusyTimeoutError("busy_timeout_ms must be a non-negative integer")
    return busy_timeout_ms


def connect(
    path: str | Path,
    busy_timeout_ms: int = 0,
    *,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Create a Director Core connection with an explicit bounded busy wait.

    The compatibility default is zero milliseconds (fail-fast), not a hidden
    product retry policy.  Production callers should pass their environment's
    explicit value.
    """

    timeout_ms = _validate_busy_timeout_ms(busy_timeout_ms)
    connection = sqlite3.connect(
        path, timeout=timeout_ms / 1000, check_same_thread=check_same_thread
    )
    connection.row_factory = sqlite3.Row
    enable_and_verify_foreign_keys(connection)
    connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
    configured = connection.execute("PRAGMA busy_timeout").fetchone()
    if configured is None or configured[0] != timeout_ms:
        connection.close()
        raise InvalidBusyTimeoutError("SQLite busy_timeout was not configured")
    return connection


def apply_migrations(connection: sqlite3.Connection) -> None:
    require_foreign_keys(connection)
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        connection.executescript(migration.read_text(encoding="utf-8"))
