"""Thread-safe in-memory export job registry."""

from __future__ import annotations

from threading import Lock
from typing import Any
from uuid import uuid4


_jobs: dict[str, dict[str, Any]] = {}
_lock = Lock()


def new_job() -> str:
    """Register a pending job and return its opaque identifier."""
    job_id = uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "status": "pending",
            "progress": 0,
            "message": "等待导出",
            "result": None,
        }
    return job_id


def update_job(job_id: str, **patch: Any) -> dict[str, Any] | None:
    """Atomically update a job, returning a detached snapshot when it exists."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job.update(patch)
        return dict(job)


def get_job(job_id: str) -> dict[str, Any] | None:
    """Return a detached job snapshot so callers cannot mutate the registry."""
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None
