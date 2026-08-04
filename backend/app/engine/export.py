"""Asynchronous final-video export orchestration."""

from __future__ import annotations

import shutil
from pathlib import Path
from threading import Thread

from .. import config
from ..core import jobs, store
from .build import RenderError, build_final, low_resolution_warnings
from .timeline import compute_timeline


def export_materials(project_id: str) -> list[dict]:
    """Validate and order the material set used by the final render."""
    materials = store.list_materials(project_id)
    script = store.load_script(project_id)
    if script is None:
        if len(materials) < 2:
            raise RenderError("at least two materials are required for export")
        return materials

    expected_indexes = [shot.shot_index for shot in script.shots]
    material_by_index = {int(item["shot_index"]): item for item in materials}
    missing = [index for index in expected_indexes if index not in material_by_index]
    if missing:
        joined = ", ".join(str(index) for index in missing)
        raise RenderError(f"missing materials for script shots: {joined}")
    unexpected = sorted(set(material_by_index) - set(expected_indexes))
    if unexpected:
        joined = ", ".join(str(index) for index in unexpected)
        raise RenderError(f"materials are not bound to script shots: {joined}")
    if len(expected_indexes) < 2:
        raise RenderError("at least two script shots are required for export")
    return [material_by_index[index] for index in expected_indexes]


def _render_export(job_id: str, project_id: str) -> None:
    try:
        jobs.update_job(job_id, status="running", progress=0, message="正在准备导出")
        materials = export_materials(project_id)
        timeline = compute_timeline(materials, store.get_edits(project_id))
        total_duration = float(timeline["total_duration"])
        if total_duration <= 0:
            raise RenderError("时间轴总时长无效，无法导出")

        def update_progress(out_time_seconds: float) -> None:
            progress = min(99, max(0, int(out_time_seconds / total_duration * 100)))
            jobs.update_job(job_id, progress=progress, message="正在渲染成片")

        rendered = build_final(project_id, timeline, on_progress=update_progress)
        exports_dir = Path(config.PROJECTS_ROOT) / project_id / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        destination = exports_dir / "final.mp4"
        shutil.copy2(rendered, destination)
        jobs.update_job(
            job_id,
            status="done",
            progress=100,
            message="导出完成",
            result={
                "output": "exports/final.mp4",
                "total_duration": total_duration,
                "warnings": low_resolution_warnings(materials),
            },
        )
    except (RenderError, OSError, ValueError) as exc:
        message = str(exc).strip()
        if len(message) > 800:
            message = message[-800:]
        jobs.update_job(
            job_id,
            status="failed",
            message=f"导出失败：{message or '未知渲染错误'}",
            result=None,
        )
    except Exception as exc:  # keep background failures observable through the job API
        jobs.update_job(
            job_id,
            status="failed",
            message=f"导出失败：{exc}",
            result=None,
        )


def start_export(project_id: str) -> str:
    """Create an export job and start its daemon worker."""
    store.get_project(project_id)
    export_materials(project_id)
    job_id = jobs.new_job()
    Thread(
        target=_render_export,
        args=(job_id, project_id),
        name=f"export-{job_id[:8]}",
        daemon=True,
    ).start()
    return job_id
