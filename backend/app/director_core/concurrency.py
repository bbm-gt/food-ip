"""Small process-local concurrency primitives for Director Core."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import threading
import unicodedata
from collections.abc import Iterator


def normalize_session_lock_key(session_id: str) -> str:
    """Return the stable in-process key used for a Director Session lock."""

    if not isinstance(session_id, str):
        raise TypeError("session_id must be a string")
    key = unicodedata.normalize("NFC", session_id).strip().casefold()
    if not key:
        raise ValueError("session_id must not be blank")
    return key


@dataclass
class _LockEntry:
    owner_thread_id: int | None = None
    depth: int = 0
    references: int = 0
    waiters: int = 0


class SessionLockManager:
    """A re-entrant, reference-counted mutex per normalized Session ID.

    The manager intentionally has no persistence or lease semantics.  Entries
    remain present while either a holder or a waiter references them and are
    removed as soon as the last reference is released.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._entries: dict[str, _LockEntry] = {}

    @contextmanager
    def lock(self, session_id: str) -> Iterator[None]:
        key = normalize_session_lock_key(session_id)
        thread_id = threading.get_ident()
        with self._condition:
            entry = self._entries.setdefault(key, _LockEntry())
            entry.references += 1
            if entry.owner_thread_id == thread_id:
                entry.depth += 1
            else:
                entry.waiters += 1
                try:
                    while entry.owner_thread_id is not None:
                        self._condition.wait()
                    entry.owner_thread_id = thread_id
                    entry.depth = 1
                except BaseException:
                    entry.waiters -= 1
                    entry.references -= 1
                    self._remove_if_unused(key, entry)
                    raise
                else:
                    entry.waiters -= 1
        try:
            yield
        finally:
            with self._condition:
                entry = self._entries.get(key)
                if entry is None or entry.owner_thread_id != thread_id or entry.depth <= 0:
                    raise RuntimeError("session lock ownership was corrupted")
                entry.depth -= 1
                entry.references -= 1
                if entry.depth == 0:
                    entry.owner_thread_id = None
                    self._condition.notify_all()
                self._remove_if_unused(key, entry)

    def _remove_if_unused(self, key: str, entry: _LockEntry) -> None:
        if entry.references == 0 and entry.waiters == 0 and entry.depth == 0:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        with self._condition:
            return len(self._entries)

    def has_entry(self, session_id: str) -> bool:
        key = normalize_session_lock_key(session_id)
        with self._condition:
            return key in self._entries


shared_session_lock_manager = SessionLockManager()


__all__ = [
    "SessionLockManager",
    "normalize_session_lock_key",
    "shared_session_lock_manager",
]
