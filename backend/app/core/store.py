"""Filesystem-backed project state store."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .. import config
from ..scriptgen.models import (
    BossInfo,
    CreativeConversation,
    IPProfile,
    ResearchProfile,
    ScriptBundle,
    ScriptModel,
    ScriptVersion,
)


PROJECT_ID_PATTERN = re.compile(r"[a-z0-9-]{8,}")


class ProjectNotFoundError(LookupError):
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"项目不存在：{project_id}")


class InvalidProjectIdError(ValueError):
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__("项目 ID 格式不合法")


class CreativeConversationNotFoundError(LookupError):
    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"共创对话不存在：{conversation_id}")


def _root() -> Path:
    return Path(config.PROJECTS_ROOT)


def _project_dir(project_id: str) -> Path:
    if PROJECT_ID_PATTERN.fullmatch(project_id) is None:
        raise InvalidProjectIdError(project_id)
    return _root() / project_id


def _write_json(path: Path, payload: dict | list[dict]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_project(project_id: str) -> dict:
    project_file = _project_dir(project_id) / "project.json"
    if not project_file.is_file():
        raise ProjectNotFoundError(project_id)
    return _read_json(project_file)


def _script_payload(project_id: str) -> dict | None:
    script_file = _project_dir(project_id) / "script.json"
    if not script_file.is_file():
        return None
    return ScriptModel.model_validate(_read_json(script_file)).model_dump(mode="json")


def _research_payload(project_id: str) -> dict:
    project = _read_project(project_id)
    saved = project.get("research")
    if isinstance(saved, dict) and saved:
        return ResearchProfile.model_validate(saved).model_dump(mode="json")
    legacy = BossInfo.model_validate(project.get("boss_info") or {})
    return ResearchProfile.from_boss_info(legacy).model_dump(mode="json")


def _bundle_payload(project_id: str) -> dict | None:
    bundle_file = _project_dir(project_id) / "script_bundle.json"
    if not bundle_file.is_file():
        return None
    return ScriptBundle.model_validate(_read_json(bundle_file)).model_dump(mode="json")


def create_project(name: str) -> dict:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    project_id = uuid4().hex[:12]
    project_dir = _project_dir(project_id)
    project_dir.mkdir()
    project = {
        "id": project_id,
        "name": name,
        "boss_info": {},
        "research": None,
        "script": None,
        "script_bundle": None,
        "materials": [],
        "edits": None,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _write_json(project_dir / "project.json", project)
    return project


def list_projects() -> list[dict]:
    root = _root()
    if not root.is_dir():
        return []
    projects: list[dict] = []
    for project_file in root.glob("*/project.json"):
        project_id = project_file.parent.name
        if PROJECT_ID_PATTERN.fullmatch(project_id) is None:
            continue
        project = _read_json(project_file)
        project["script"] = _script_payload(project_id)
        project["research"] = _research_payload(project_id)
        project["script_bundle"] = _bundle_payload(project_id)
        projects.append(project)
    return sorted(projects, key=lambda item: item.get("created_at", ""), reverse=True)


def get_project(project_id: str) -> dict:
    project = _read_project(project_id)
    project["script"] = _script_payload(project_id)
    project["research"] = _research_payload(project_id)
    project["script_bundle"] = _bundle_payload(project_id)
    return project


def update_project(project_id: str, **patch: object) -> dict:
    project = get_project(project_id)
    project.update(patch)
    _write_json(_project_dir(project_id) / "project.json", project)
    return project


def load_script(project_id: str) -> ScriptModel | None:
    _read_project(project_id)
    payload = _script_payload(project_id)
    return ScriptModel.model_validate(payload) if payload is not None else None


def _script_versions_file(project_id: str) -> Path:
    return _project_dir(project_id) / "script_versions.json"


def _write_script_versions(project_id: str, versions: list[ScriptVersion]) -> None:
    _write_json(
        _script_versions_file(project_id),
        [version.model_dump(mode="json") for version in versions],
    )


def load_script_versions(project_id: str) -> list[ScriptVersion]:
    project = _read_project(project_id)
    versions_file = _script_versions_file(project_id)
    if versions_file.is_file():
        payload = json.loads(versions_file.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("script_versions.json 必须是数组")
        return [ScriptVersion.model_validate(item) for item in payload]

    current = _script_payload(project_id)
    if current is None:
        return []
    baseline = ScriptVersion(
        id=uuid4().hex[:12],
        version_number=1,
        created_at=str(project.get("created_at") or datetime.now(UTC).isoformat()),
        source="legacy_import",
        script=ScriptModel.model_validate(current),
    )
    _write_script_versions(project_id, [baseline])
    return [baseline]


def save_script(
    project_id: str,
    script: ScriptModel,
    *,
    source: Literal[
        "legacy_import",
        "template_generation",
        "candidate_selection",
        "manual_save",
    ] = "manual_save",
) -> None:
    project = get_project(project_id)
    payload = script.model_dump(mode="json")
    current = _script_payload(project_id)
    versions = load_script_versions(project_id)
    if current == payload:
        return

    _write_json(_project_dir(project_id) / "script.json", payload)
    versions.append(
        ScriptVersion(
            id=uuid4().hex[:12],
            version_number=max(
                (version.version_number for version in versions), default=0
            )
            + 1,
            created_at=datetime.now(UTC).isoformat(),
            source=source,
            script=script,
        )
    )
    _write_script_versions(project_id, versions)
    project["script"] = payload
    _write_json(_project_dir(project_id) / "project.json", project)


def save_research(project_id: str, research: ResearchProfile) -> ResearchProfile:
    project = _read_project(project_id)
    project["research"] = research.model_dump(mode="json")
    project["boss_info"] = research.to_boss_info().model_dump(mode="json")
    _write_json(_project_dir(project_id) / "project.json", project)
    return research


def load_research(project_id: str) -> ResearchProfile:
    return ResearchProfile.model_validate(_research_payload(project_id))


def save_ip_profile(project_id: str, profile: IPProfile) -> IPProfile:
    _read_project(project_id)
    _write_json(
        _project_dir(project_id) / "ip_profile.json",
        profile.model_dump(mode="json"),
    )
    return profile


def load_ip_profile(project_id: str) -> IPProfile:
    _read_project(project_id)
    profile_file = _project_dir(project_id) / "ip_profile.json"
    if profile_file.is_file():
        return IPProfile.model_validate(_read_json(profile_file))

    from ..scriptgen.ip_profile import rule_ip_profile

    return rule_ip_profile(load_research(project_id))


def _creative_conversation_file(project_id: str, conversation_id: str) -> Path:
    if PROJECT_ID_PATTERN.fullmatch(conversation_id) is None:
        raise CreativeConversationNotFoundError(conversation_id)
    return _project_dir(project_id) / "creative_conversations" / f"{conversation_id}.json"


def save_creative_conversation(
    project_id: str, conversation: CreativeConversation
) -> CreativeConversation:
    _read_project(project_id)
    if conversation.project_id != project_id:
        raise ValueError("共创对话与项目不匹配")
    conversation_file = _creative_conversation_file(project_id, conversation.id)
    conversation_file.parent.mkdir(parents=True, exist_ok=True)
    _write_json(conversation_file, conversation.model_dump(mode="json"))
    return conversation


def load_creative_conversation(
    project_id: str, conversation_id: str
) -> CreativeConversation:
    _read_project(project_id)
    conversation_file = _creative_conversation_file(project_id, conversation_id)
    if not conversation_file.is_file():
        raise CreativeConversationNotFoundError(conversation_id)
    return CreativeConversation.model_validate(_read_json(conversation_file))


def list_creative_conversations(project_id: str) -> list[CreativeConversation]:
    _read_project(project_id)
    conversations_dir = _project_dir(project_id) / "creative_conversations"
    if not conversations_dir.is_dir():
        return []
    conversations = [
        CreativeConversation.model_validate(_read_json(path))
        for path in conversations_dir.glob("*.json")
    ]
    return sorted(conversations, key=lambda item: item.updated_at, reverse=True)


def save_script_bundle(project_id: str, bundle: ScriptBundle) -> ScriptBundle:
    _read_project(project_id)
    _write_json(
        _project_dir(project_id) / "script_bundle.json",
        bundle.model_dump(mode="json"),
    )
    return bundle


def load_script_bundle(project_id: str) -> ScriptBundle | None:
    _read_project(project_id)
    payload = _bundle_payload(project_id)
    return ScriptBundle.model_validate(payload) if payload is not None else None


def _validate_shot_index(shot_index: int) -> int:
    if isinstance(shot_index, bool) or not isinstance(shot_index, int) or shot_index < 0:
        raise ValueError("拍摄序号必须是非负整数")
    return shot_index


def material_path(project_id: str, shot_index: int) -> Path:
    """Return the canonical stored video path, creating its directory."""
    _read_project(project_id)
    index = _validate_shot_index(shot_index)
    shots_dir = _project_dir(project_id) / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    return shots_dir / f"shot_{index}.mp4"


def thumbnail_path(project_id: str, shot_index: int) -> Path:
    """Return the canonical thumbnail path, creating its directory."""
    _read_project(project_id)
    index = _validate_shot_index(shot_index)
    work_dir = _project_dir(project_id) / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir / f"thumb_{index}.jpg"


def bgm_path(project_id: str, filename: str | None = None) -> Path:
    """Return the single project BGM path, creating its directory."""
    project = _read_project(project_id)
    metadata = project.get("bgm") or {}
    stored_name = filename or metadata.get("filename") or "bgm.mp3"
    if not isinstance(stored_name, str) or Path(stored_name).name != stored_name:
        raise ValueError("BGM 文件名不合法")
    audio_dir = _project_dir(project_id) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir / stored_name


def get_bgm(project_id: str) -> dict | None:
    return _read_project(project_id).get("bgm")


def save_bgm(project_id: str, metadata: dict) -> dict:
    _read_project(project_id)
    update_project(project_id, bgm=metadata)
    return metadata


def delete_bgm(project_id: str) -> dict | None:
    project = _read_project(project_id)
    metadata = project.get("bgm")
    if not metadata:
        return None
    filename = metadata.get("filename") if isinstance(metadata, dict) else None
    if isinstance(filename, str):
        bgm_path(project_id, filename).unlink(missing_ok=True)
    update_project(project_id, bgm=None)
    return metadata


def list_materials(project_id: str) -> list[dict]:
    project = _read_project(project_id)
    materials = project.get("materials") or []
    return sorted(materials, key=lambda material: material["shot_index"])


def save_material(project_id: str, material: dict) -> dict:
    materials = [
        current
        for current in list_materials(project_id)
        if current["shot_index"] != material["shot_index"]
    ]
    materials.append(material)
    update_project(
        project_id,
        materials=sorted(materials, key=lambda current: current["shot_index"]),
    )
    return material


def delete_material(project_id: str, shot_index: int) -> bool:
    index = _validate_shot_index(shot_index)
    materials = list_materials(project_id)
    remaining = [item for item in materials if item["shot_index"] != index]
    if len(remaining) == len(materials):
        return False
    update_project(project_id, materials=remaining)
    return True


def get_edits(project_id: str) -> dict | None:
    return _read_project(project_id).get("edits")


def save_edits(project_id: str, edits: dict) -> dict:
    update_project(project_id, edits=edits)
    return edits
