import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "generate_project_docs.py"
)
SPEC = importlib.util.spec_from_file_location("generate_project_docs", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
project_docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(project_docs)


def valid_status(instruction: str = "Use the status-file instruction exactly.") -> dict:
    return {
        "format_version": 1,
        "updated_at": "2026-08-16",
        "current": {
            "area": "Director Core",
            "milestone": "Test milestone",
            "status": "completed",
        },
        "completed": ["Test phase"],
        "next": {
            "status": "design_required",
            "objective": "Test the generator.",
            "instruction": instruction,
            "implementation_allowed": False,
        },
        "deferred": ["Test deferred item"],
    }


def load_status_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: dict) -> dict:
    status_path = tmp_path / "project-status.yaml"
    status_path.write_text(
        project_docs.yaml.safe_dump(status, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_docs, "STATUS_PATH", status_path)
    return project_docs.load_status()


def test_load_status_requires_next_instruction(tmp_path, monkeypatch):
    status = valid_status()
    del status["next"]["instruction"]

    with pytest.raises(ValueError, match="next.instruction"):
        load_status_from(tmp_path, monkeypatch, status)


def test_load_status_rejects_empty_next_instruction(tmp_path, monkeypatch):
    status = valid_status("   ")

    with pytest.raises(ValueError, match="next.instruction"):
        load_status_from(tmp_path, monkeypatch, status)


def test_render_uses_instruction_verbatim():
    instruction = "Only this instruction should be rendered."
    rendered = project_docs.render(valid_status(instruction))

    assert f"\n{instruction}\n" in rendered


def test_implementation_allowed_does_not_change_instruction_text():
    instruction = "Implementation is allowed after this exact review."
    status = valid_status(instruction)
    status["next"]["implementation_allowed"] = True

    rendered = project_docs.render(status)

    assert f"\n{instruction}\n" in rendered
    assert "下一阶段必须先完成最小设计并取得用户确认。" not in rendered
