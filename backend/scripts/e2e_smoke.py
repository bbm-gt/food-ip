"""Run the P2 acceptance flow against a real local uvicorn HTTP server."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app import config  # noqa: E402
from backend.app.engine.media import probe_video  # noqa: E402
from backend.scripts.make_sample_shots import make_sample_shots  # noqa: E402


def _wait_until_ready(base_url: str, server: subprocess.Popen[str]) -> None:
    for _ in range(100):
        if server.poll() is not None:
            stdout, stderr = server.communicate()
            raise RuntimeError(stderr.strip() or stdout.strip() or "uvicorn 启动失败")
        try:
            response = httpx.get(f"{base_url}/health", timeout=1.0, trust_env=False)
            if response.status_code == 200 and response.json().get("ok") is True:
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError("uvicorn 未在预期时间内启动")


def run_smoke(port: int = 8765) -> dict:
    runtime = REPOSITORY_ROOT / "runtime"
    sample_dir = runtime / "e2e-smoke-samples"
    projects_root = runtime / "e2e-smoke-projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    samples = make_sample_shots(sample_dir)
    probed = [probe_video(sample) for sample in samples]
    manual_total = sum(float(item["duration"]) for item in probed)

    environment = os.environ.copy()
    environment["PROJECTS_ROOT"] = str(projects_root)
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    server = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creation_flags,
    )
    base_url = f"http://127.0.0.1:{port}/api"
    try:
        _wait_until_ready(base_url, server)
        with httpx.Client(timeout=30.0, trust_env=False) as client:
            created = client.post(
                f"{base_url}/projects", json={"name": "真实 HTTP 冒烟"}
            )
            created.raise_for_status()
            project_id = created.json()["id"]

            uploaded: list[dict] = []
            for index, sample in enumerate(samples):
                with sample.open("rb") as source:
                    response = client.post(
                        f"{base_url}/projects/{project_id}/materials",
                        data={"shot_index": str(index)},
                        files={"file": (sample.name, source, "video/mp4")},
                    )
                response.raise_for_status()
                uploaded.append(response.json())

            before_response = client.get(
                f"{base_url}/projects/{project_id}/timeline"
            )
            before_response.raise_for_status()
            before = before_response.json()

            edits_response = client.put(
                f"{base_url}/projects/{project_id}/edits",
                json={
                    "shots": [
                        {"trim_head": 0.0, "trim_tail": 0.5},
                        {"trim_head": 1.0, "trim_tail": 0.0},
                        {"trim_head": 0.0, "trim_tail": 0.0},
                    ],
                    "junctions": [
                        {"transition": "fade", "fade_seconds": 0.5},
                        {"transition": "fade", "fade_seconds": 0.5},
                    ],
                },
            )
            edits_response.raise_for_status()
            after = edits_response.json()["timeline"]
            persisted = client.get(
                f"{base_url}/projects/{project_id}/timeline"
            )
            persisted.raise_for_status()
            thumbnail = client.get(
                f"{base_url}/projects/{project_id}/materials/1/thumbnail"
            )

            if abs(float(before["total_duration"]) - manual_total) > 1e-6:
                raise AssertionError("初始 timeline 总时长与媒体探测手算不一致")
            if abs(float(after["total_duration"]) - (manual_total - 1.5)) > 1e-6:
                raise AssertionError("trim 后 timeline 总时长不一致")
            if persisted.json() != after:
                raise AssertionError("PUT 后 GET timeline 未持久化")
            if thumbnail.status_code != 200:
                raise AssertionError(f"缩略图状态码错误：{thumbnail.status_code}")
            if thumbnail.headers.get("content-type") != "image/jpeg":
                raise AssertionError("缩略图 Content-Type 不是 image/jpeg")

            return {
                "project_id": project_id,
                "probe_backend": (
                    "ffprobe" if config.FFPROBE_PATH else "ffmpeg-fallback"
                ),
                "uploaded_durations": [item["duration"] for item in uploaded],
                "manual_total_before": manual_total,
                "timeline_total_before": before["total_duration"],
                "timeline_total_after_trim": after["total_duration"],
                "thumbnail_status": thumbnail.status_code,
                "thumbnail_content_type": thumbnail.headers.get("content-type"),
            }
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    print(json.dumps(run_smoke(), ensure_ascii=False, indent=2))

