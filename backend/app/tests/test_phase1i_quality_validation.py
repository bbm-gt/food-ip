from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.phase1i_quality_validation import (
    OwnerContextPacket,
    StudyRecord,
    blind_label,
    export_blind_bundle,
    main,
    owner_context_sha256,
    study_summary,
)


CREATED_AT = "2026-08-17T01:00:00.000Z"
DUE_AT = "2026-08-24T01:00:00.000Z"
RECORDED_AT = "2026-08-19T01:00:00.000Z"
MODEL = {"provider": "deepseek", "model": "deepseek-v4-flash"}


def context_packet(participant_id: str, cycle_number: int) -> OwnerContextPacket:
    suffix = f"{participant_id}_{cycle_number}"
    evidence_id = f"E_{suffix}"
    return OwnerContextPacket.model_validate(
        {
            "format_version": 1,
            "participant_id": participant_id,
            "cycle_number": cycle_number,
            "prepared_at": CREATED_AT,
            "evidence_sources": [
                {
                    "evidence_id": evidence_id,
                    "source_type": "OWNER_INTERVIEW",
                    "source_ref": f"interview-{suffix}",
                    "verbatim_excerpt": "这道菜是我每天在店里现做的。",
                }
            ],
            "confirmed_facts": [
                {
                    "item_id": f"F_{suffix}",
                    "statement": "这道菜每天在店里现做。",
                    "evidence_ids": [evidence_id],
                }
            ],
            "voice_samples": [
                {
                    "sample_id": f"V_{suffix}",
                    "verbatim_text": "我不说虚的，你到店里看。",
                    "evidence_ids": [evidence_id],
                }
            ],
            "recent_context": [],
            "content_goal": {
                "item_id": f"G_{suffix}",
                "statement": "让附近顾客理解现场制作过程。",
                "evidence_ids": [evidence_id],
            },
            "available_scenes": [
                {
                    "item_id": f"S_{suffix}",
                    "statement": "可以拍摄后厨制作过程。",
                    "evidence_ids": [evidence_id],
                }
            ],
            "constraints": [],
            "observed_preferences": [],
            "operator_declaration": {
                "operator_id": "operator-01",
                "no_inference_promoted_to_fact": True,
                "no_copywriting_added": True,
            },
        }
    )


def blind_review(study_id: str, participant_id: str, cycle_number: int) -> dict:
    dimension = {"outcome": "PASS", "note": "未发现影响本维度的问题。"}
    return {
        "format_version": 1,
        "blind_label": blind_label(study_id, participant_id, cycle_number),
        "reviewer_id": "reviewer-01",
        "reviewed_at": RECORDED_AT,
        "authenticity": dimension,
        "clarity": dimension,
        "evidence_strength": dimension,
        "watchability": dimension,
        "shootability": dimension,
        "overall_outcome": "PASS",
        "root_cause": None,
        "overall_note": "整体通过。",
    }


def feedback(
    participant_id: str,
    cycle_number: int,
    *,
    recorded: bool,
    unconfirmed_facts: int = 0,
) -> dict:
    issues = []
    if unconfirmed_facts:
        issues.append(
            {
                "statement": "每天卖五百份。",
                "issue_type": "NO_OWNER_EVIDENCE",
                "detail": "老板没有提供该销量事实。",
            }
        )
    return {
        "format_version": 1,
        "participant_id": participant_id,
        "cycle_number": cycle_number,
        "captured_at": RECORDED_AT,
        "direction_decision": "ACCEPTED" if recorded else "REJECTED",
        "output_disposition": "ACCEPTED" if recorded else "REJECTED",
        "final_edit_scope": "NONE",
        "local_edits": [],
        "recording_outcome": "RECORDED" if recorded else "NOT_RECORDED",
        "recording_due_at": DUE_AT,
        "recorded_at": RECORDED_AT if recorded else None,
        "not_recorded_reason": None if recorded else "DIRECTION",
        "owner_feedback": "愿意照这个版本拍。" if recorded else "这个方向现在不想拍。",
        "fact_audit": {
            "audited_at": RECORDED_AT,
            "auditor_id": "auditor-01",
            "unconfirmed_fact_count": unconfirmed_facts,
            "issues": issues,
        },
    }


def concierge_cycle(study_id: str, participant_id: str, cycle_number: int) -> dict:
    packet = context_packet(participant_id, cycle_number)
    suffix = f"{participant_id}_{cycle_number}"
    return {
        "cycle_number": cycle_number,
        "mode": "CONCIERGE",
        "started_at": CREATED_AT,
        "model_identity": MODEL,
        "owner_context_packet": packet.model_dump(mode="json"),
        "baseline_output": None,
        "shoot_ready_package": {
            "format_version": 1,
            "participant_id": participant_id,
            "cycle_number": cycle_number,
            "generated_at": CREATED_AT,
            "owner_context_sha256": owner_context_sha256(packet),
            "title": "把每天现做拍给顾客看",
            "core_proposition": "现场制作值得被顾客看见。",
            "recommendation_reason": "这是老板已确认且今天能够拍摄的真实细节。",
            "hook": {
                "spoken_line": "我不说虚的，你看这道菜怎么做。",
                "opening_shot": "老板入镜，镜头转向后厨。",
            },
            "segments": [
                {
                    "sequence": 1,
                    "duration_seconds": 10,
                    "spoken_line": "我不说虚的，你看这道菜怎么做。",
                    "shot_and_action": "老板入镜后带镜头进入后厨。",
                    "context_item_ids_used": [f"S_{suffix}"],
                },
                {
                    "sequence": 2,
                    "duration_seconds": 10,
                    "spoken_line": "这道菜每天都在店里现做，你到店里也能看到。",
                    "shot_and_action": "拍摄实际制作过程和成菜。",
                    "context_item_ids_used": [f"F_{suffix}", f"S_{suffix}"],
                },
            ],
            "shooting_order": [2, 1],
            "pending_fact_risks": [],
        },
        "feedback": feedback(participant_id, cycle_number, recorded=True),
        "blind_review": blind_review(study_id, participant_id, cycle_number),
        "operator_interventions": [
            {
                "intervention_id": f"I_{suffix}",
                "occurred_at": CREATED_AT,
                "operator_id": "operator-01",
                "intervention_type": "FACT_ORGANIZATION",
                "content_changed": False,
                "detail": "只整理老板原话与证据引用。",
            }
        ],
    }


def participant(study_id: str, participant_id: str, category: str) -> dict:
    return {
        "participant_id": participant_id,
        "restaurant_category": category,
        "consent": {
            "participation_confirmed": True,
            "model_processing_confirmed": True,
            "recording_followup_confirmed": True,
            "confirmed_at": CREATED_AT,
            "consent_record_ref": f"consent-{participant_id}",
        },
        "cycles": [
            {
                "cycle_number": 1,
                "mode": "BASELINE_CURRENT_CHAT",
                "started_at": CREATED_AT,
                "model_identity": MODEL,
                "owner_context_packet": None,
                "baseline_output": {
                    "format_version": 1,
                    "generated_at": CREATED_AT,
                    "ready_content_id": None,
                    "title": "当前聊天基线",
                    "script_text": "这是当前聊天生成的基线内容。",
                    "shooting_notes": ["老板在门店内口播。"],
                },
                "shoot_ready_package": None,
                "feedback": feedback(participant_id, 1, recorded=False),
                "blind_review": blind_review(study_id, participant_id, 1),
                "operator_interventions": [],
            },
            concierge_cycle(study_id, participant_id, 2),
            concierge_cycle(study_id, participant_id, 3),
        ],
    }


def complete_study_dict() -> dict:
    study_id = "phase1i-field-study"
    categories = ["火锅", "面馆", "烘焙", "火锅", "面馆"]
    return {
        "format_version": 1,
        "protocol_version": 1,
        "study_id": study_id,
        "created_at": CREATED_AT,
        "approved_model": MODEL,
        "recording_window_days": 7,
        "participant_target_min": 5,
        "participant_target_max": 10,
        "concierge_cycles_per_participant": 2,
        "participants": [
            participant(study_id, f"P{index:03d}", category)
            for index, category in enumerate(categories, start=1)
        ],
    }


def test_complete_study_reaches_phase_1j_gate() -> None:
    summary = study_summary(StudyRecord.model_validate(complete_study_dict()))

    assert summary["decision"] == "READY_FOR_PHASE_1J"
    assert summary["fact_gate"]["passed"] is True
    assert summary["behavior_gate"]["recording_rate"] == 1.0
    assert summary["baseline_comparison"]["concierge_recording_rate_improved"] is True


def test_accepted_unconfirmed_fact_blocks_progression() -> None:
    value = complete_study_dict()
    value["participants"][0]["cycles"][1]["feedback"] = feedback(
        "P001", 2, recorded=True, unconfirmed_facts=1
    )

    summary = study_summary(StudyRecord.model_validate(value))

    assert summary["decision"] == "BLOCKED_FACT_BOUNDARY"
    assert summary["fact_gate"]["unconfirmed_fact_count"] == 1


def test_package_cannot_reference_unknown_context_item() -> None:
    value = complete_study_dict()
    value["participants"][0]["cycles"][1]["shoot_ready_package"]["segments"][0][
        "context_item_ids_used"
    ] = ["F_UNKNOWN"]

    with pytest.raises(ValidationError, match="unknown context items"):
        StudyRecord.model_validate(value)


def test_recording_due_date_is_exactly_seven_days_after_output() -> None:
    value = complete_study_dict()
    value["participants"][0]["cycles"][1]["feedback"]["recording_due_at"] = (
        "2026-08-25T01:00:00.000Z"
    )

    with pytest.raises(ValidationError, match="exactly seven days"):
        StudyRecord.model_validate(value)


def test_init_creates_valid_incomplete_study(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "study.json"

    assert main(
        [
            "init",
            str(output),
            "--study-id",
            "phase1i-local",
            "--provider",
            "deepseek",
            "--model",
            "deepseek-v4-flash",
        ]
    ) == 0
    assert main(["validate", str(output)]) == 0
    assert study_summary(StudyRecord.model_validate_json(output.read_text(encoding="utf-8")))[
        "decision"
    ] == "NOT_READY"
    assert "valid: phase1i-local" in capsys.readouterr().out


def test_schema_command_exports_disposable_study_schema(tmp_path: Path) -> None:
    output = tmp_path / "study-record.schema.json"

    assert main(["schema", str(output)]) == 0

    schema = json.loads(output.read_text(encoding="utf-8"))
    assert schema["title"] == "StudyRecord"
    assert "participants" in schema["properties"]


def test_blind_bundle_normalizes_modes_and_keeps_mapping_separate(tmp_path: Path) -> None:
    study = StudyRecord.model_validate(complete_study_dict())
    review_output = tmp_path / "reviewer"
    mapping_output = tmp_path / "coordinator" / "mapping.json"

    count = export_blind_bundle(
        study,
        review_output,
        mapping_output,
        overwrite=False,
    )

    assert count == 15
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in review_output.iterdir()]
    assert all(set(item) == {"format_version", "blind_label", "title", "spoken_script", "shooting_notes"} for item in payloads)
    mapping = json.loads(mapping_output.read_text(encoding="utf-8"))
    assert {item["mode"] for item in mapping["mapping"]} == {
        "BASELINE_CURRENT_CHAT",
        "CONCIERGE",
    }
