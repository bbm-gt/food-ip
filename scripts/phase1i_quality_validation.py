#!/usr/bin/env python3
"""Non-production Phase 1I field-study records, validation, and summaries.

The objects in this module are disposable research contracts.  They are not
Director Core API, persistence, or ReadyContent contracts, and product runtime
code must not import them.
"""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)


NonBlank = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
UtcTimestamp = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$",
    ),
]
StudyId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9-]{2,63}$"),
]
ParticipantId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^P\d{3}$"),
]
RecordId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[A-Z][A-Z0-9_-]{1,63}$"),
]
Sha256Text = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$"),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ModelIdentity(StrictModel):
    provider: NonBlank
    model: NonBlank


class ConsentRecord(StrictModel):
    participation_confirmed: Literal[True]
    model_processing_confirmed: Literal[True]
    recording_followup_confirmed: Literal[True]
    confirmed_at: UtcTimestamp
    consent_record_ref: NonBlank


class EvidenceSource(StrictModel):
    evidence_id: RecordId
    source_type: Literal[
        "OWNER_INTERVIEW", "OWNER_MESSAGE", "OWNER_CONFIRMED_MATERIAL"
    ]
    source_ref: NonBlank
    verbatim_excerpt: NonBlank


class ConfirmedContextItem(StrictModel):
    item_id: RecordId
    statement: NonBlank
    evidence_ids: list[RecordId] = Field(min_length=1)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must be unique")
        return value


class VoiceSample(StrictModel):
    sample_id: RecordId
    verbatim_text: NonBlank
    evidence_ids: list[RecordId] = Field(min_length=1)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must be unique")
        return value


class ObservedPreference(StrictModel):
    preference_id: RecordId
    statement: NonBlank
    observed_from_cycle_numbers: list[StrictInt] = Field(min_length=1)
    status: Literal["OBSERVATION_ONLY"]

    @field_validator("observed_from_cycle_numbers")
    @classmethod
    def legal_cycles(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)) or any(item not in (1, 2, 3) for item in value):
            raise ValueError("observed cycle numbers must be unique values in 1..3")
        return value


class OperatorDeclaration(StrictModel):
    operator_id: NonBlank
    no_inference_promoted_to_fact: Literal[True]
    no_copywriting_added: Literal[True]


class OwnerContextPacket(StrictModel):
    format_version: Literal[1]
    participant_id: ParticipantId
    cycle_number: Literal[2, 3]
    prepared_at: UtcTimestamp
    evidence_sources: list[EvidenceSource] = Field(min_length=1)
    confirmed_facts: list[ConfirmedContextItem] = Field(min_length=1)
    voice_samples: list[VoiceSample]
    recent_context: list[ConfirmedContextItem]
    content_goal: ConfirmedContextItem
    available_scenes: list[ConfirmedContextItem]
    constraints: list[ConfirmedContextItem]
    observed_preferences: list[ObservedPreference]
    operator_declaration: OperatorDeclaration

    @model_validator(mode="after")
    def evidence_closure_and_unique_ids(self) -> "OwnerContextPacket":
        evidence_ids = [item.evidence_id for item in self.evidence_sources]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence source IDs must be unique")
        legal_evidence_ids = set(evidence_ids)

        context_items = [
            *self.confirmed_facts,
            *self.recent_context,
            self.content_goal,
            *self.available_scenes,
            *self.constraints,
        ]
        item_ids = [item.item_id for item in context_items]
        preference_ids = [item.preference_id for item in self.observed_preferences]
        voice_ids = [item.sample_id for item in self.voice_samples]
        all_ids = [*item_ids, *preference_ids, *voice_ids]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("OwnerContextPacket item IDs must be globally unique")

        for item in [*context_items, *self.voice_samples]:
            unknown = set(item.evidence_ids) - legal_evidence_ids
            if unknown:
                raise ValueError(f"item references unknown evidence IDs: {sorted(unknown)}")
        return self


class Hook(StrictModel):
    spoken_line: NonBlank
    opening_shot: NonBlank


class ShootSegment(StrictModel):
    sequence: StrictInt = Field(ge=1)
    duration_seconds: StrictInt = Field(ge=1, le=60)
    spoken_line: NonBlank
    shot_and_action: NonBlank
    context_item_ids_used: list[RecordId]

    @field_validator("context_item_ids_used")
    @classmethod
    def unique_context_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("context_item_ids_used must be unique")
        return value


class PendingFactRisk(StrictModel):
    statement: NonBlank
    treatment: Literal["EXCLUDED", "OWNER_CONFIRMATION_REQUIRED"]


class ShootReadyPackage(StrictModel):
    format_version: Literal[1]
    participant_id: ParticipantId
    cycle_number: Literal[2, 3]
    generated_at: UtcTimestamp
    owner_context_sha256: Sha256Text
    title: NonBlank
    core_proposition: NonBlank
    recommendation_reason: NonBlank
    hook: Hook
    segments: list[ShootSegment] = Field(min_length=2)
    shooting_order: list[StrictInt] = Field(min_length=2)
    pending_fact_risks: list[PendingFactRisk]

    @model_validator(mode="after")
    def shootable_shape(self) -> "ShootReadyPackage":
        sequences = [item.sequence for item in self.segments]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("segment sequence must be consecutive and ordered from 1")
        if len(self.shooting_order) != len(set(self.shooting_order)):
            raise ValueError("shooting_order must not repeat a segment")
        if set(self.shooting_order) != set(sequences):
            raise ValueError("shooting_order must contain every segment exactly once")
        total_seconds = sum(item.duration_seconds for item in self.segments)
        if not 20 <= total_seconds <= 60:
            raise ValueError("ShootReadyPackage must total 20..60 seconds")
        return self


class BaselineOutput(StrictModel):
    format_version: Literal[1]
    generated_at: UtcTimestamp
    ready_content_id: NonBlank | None
    title: NonBlank
    script_text: NonBlank
    shooting_notes: list[NonBlank]


class LocalEdit(StrictModel):
    edit_type: Literal[
        "HOOK", "TONE", "DURATION", "SHOT", "WORDING", "FACT_CORRECTION", "OTHER"
    ]
    description: NonBlank
    changes_core_direction: StrictBool
    changes_major_facts: StrictBool
    changes_overall_structure: StrictBool


class FactIssue(StrictModel):
    statement: NonBlank
    issue_type: Literal[
        "NO_OWNER_EVIDENCE", "EXCEEDS_EVIDENCE", "AI_INFERENCE_AS_FACT", "OTHER"
    ]
    detail: NonBlank


class FactAudit(StrictModel):
    audited_at: UtcTimestamp
    auditor_id: NonBlank
    unconfirmed_fact_count: StrictInt = Field(ge=0)
    issues: list[FactIssue]

    @model_validator(mode="after")
    def issue_count_matches(self) -> "FactAudit":
        if self.unconfirmed_fact_count != len(self.issues):
            raise ValueError("unconfirmed_fact_count must equal len(issues)")
        return self


class FeedbackRecord(StrictModel):
    format_version: Literal[1]
    participant_id: ParticipantId
    cycle_number: Literal[1, 2, 3]
    captured_at: UtcTimestamp
    direction_decision: Literal["ACCEPTED", "REPLACED", "REJECTED"]
    output_disposition: Literal["ACCEPTED", "REJECTED"]
    final_edit_scope: Literal["NONE", "LOCAL", "SUBSTANTIAL"]
    local_edits: list[LocalEdit]
    recording_outcome: Literal["RECORDED", "NOT_RECORDED", "PENDING"]
    recording_due_at: UtcTimestamp
    recorded_at: UtcTimestamp | None
    not_recorded_reason: Literal[
        "DIRECTION", "MATERIAL", "WRITING", "EXECUTION", "OTHER"
    ] | None
    owner_feedback: NonBlank
    fact_audit: FactAudit

    @model_validator(mode="after")
    def recording_and_edit_consistency(self) -> "FeedbackRecord":
        if self.final_edit_scope == "NONE" and self.local_edits:
            raise ValueError("NONE edit scope forbids local_edits")
        if self.final_edit_scope == "LOCAL" and not self.local_edits:
            raise ValueError("LOCAL edit scope requires local_edits")
        if self.final_edit_scope == "LOCAL" and any(
            edit.changes_core_direction
            or edit.changes_major_facts
            or edit.changes_overall_structure
            for edit in self.local_edits
        ):
            raise ValueError("LOCAL edits cannot change direction, major facts, or structure")

        if self.recording_outcome == "RECORDED":
            if self.recorded_at is None or self.not_recorded_reason is not None:
                raise ValueError("RECORDED requires recorded_at and forbids not_recorded_reason")
            if self.output_disposition != "ACCEPTED":
                raise ValueError("a recorded output must be accepted")
        elif self.recording_outcome == "NOT_RECORDED":
            if self.recorded_at is not None or self.not_recorded_reason is None:
                raise ValueError("NOT_RECORDED requires a reason and forbids recorded_at")
        else:
            if self.recorded_at is not None or self.not_recorded_reason is not None:
                raise ValueError("PENDING forbids recorded_at and not_recorded_reason")
        return self


class BlindDimensionDiagnosis(StrictModel):
    outcome: Literal["PASS", "CONCERN", "FAIL"]
    note: NonBlank


class BlindReviewRecord(StrictModel):
    format_version: Literal[1]
    blind_label: Annotated[
        str, StringConstraints(strict=True, pattern=r"^B-[0-9A-F]{10}$")
    ]
    reviewer_id: NonBlank
    reviewed_at: UtcTimestamp
    authenticity: BlindDimensionDiagnosis
    clarity: BlindDimensionDiagnosis
    evidence_strength: BlindDimensionDiagnosis
    watchability: BlindDimensionDiagnosis
    shootability: BlindDimensionDiagnosis
    overall_outcome: Literal["PASS", "FAIL"]
    root_cause: Literal["DIRECTION", "MATERIAL", "WRITING", "EXECUTION"] | None
    overall_note: NonBlank

    @model_validator(mode="after")
    def outcome_has_root_cause(self) -> "BlindReviewRecord":
        if (self.overall_outcome == "PASS") != (self.root_cause is None):
            raise ValueError("PASS requires null root_cause; FAIL requires a root_cause")
        return self


class OperatorIntervention(StrictModel):
    intervention_id: RecordId
    occurred_at: UtcTimestamp
    operator_id: NonBlank
    intervention_type: Literal[
        "FACT_ORGANIZATION",
        "QUESTION_RELAY",
        "RESPONSE_RELAY",
        "TECHNICAL_HELP",
        "CONTENT_EDIT",
    ]
    content_changed: StrictBool
    detail: NonBlank

    @model_validator(mode="after")
    def content_edit_marker(self) -> "OperatorIntervention":
        if (self.intervention_type == "CONTENT_EDIT") != self.content_changed:
            raise ValueError("only CONTENT_EDIT may set content_changed=true")
        return self


class ContentCycle(StrictModel):
    cycle_number: Literal[1, 2, 3]
    mode: Literal["BASELINE_CURRENT_CHAT", "CONCIERGE"]
    started_at: UtcTimestamp
    model_identity: ModelIdentity
    owner_context_packet: OwnerContextPacket | None
    baseline_output: BaselineOutput | None
    shoot_ready_package: ShootReadyPackage | None
    feedback: FeedbackRecord | None
    blind_review: BlindReviewRecord | None
    operator_interventions: list[OperatorIntervention]

    @model_validator(mode="after")
    def mode_specific_shape(self) -> "ContentCycle":
        if self.cycle_number == 1:
            if self.mode != "BASELINE_CURRENT_CHAT":
                raise ValueError("cycle 1 must be BASELINE_CURRENT_CHAT")
            if self.owner_context_packet is not None or self.shoot_ready_package is not None:
                raise ValueError("baseline cycle forbids Concierge artifacts")
        else:
            if self.mode != "CONCIERGE":
                raise ValueError("cycles 2 and 3 must be CONCIERGE")
            if self.baseline_output is not None:
                raise ValueError("Concierge cycle forbids baseline_output")

        if self.shoot_ready_package is not None and self.owner_context_packet is None:
            raise ValueError("ShootReadyPackage requires OwnerContextPacket")
        has_output = self.baseline_output is not None or self.shoot_ready_package is not None
        if (self.feedback is not None or self.blind_review is not None) and not has_output:
            raise ValueError("feedback and blind review require a generated output")
        if self.feedback is not None and self.feedback.cycle_number != self.cycle_number:
            raise ValueError("feedback cycle_number mismatch")
        return self


class ParticipantRecord(StrictModel):
    participant_id: ParticipantId
    restaurant_category: NonBlank
    consent: ConsentRecord
    cycles: list[ContentCycle] = Field(max_length=3)

    @model_validator(mode="after")
    def ordered_unique_cycles(self) -> "ParticipantRecord":
        cycle_numbers = [item.cycle_number for item in self.cycles]
        if cycle_numbers != sorted(cycle_numbers) or len(cycle_numbers) != len(set(cycle_numbers)):
            raise ValueError("participant cycles must be unique and ordered")
        for cycle in self.cycles:
            packet = cycle.owner_context_packet
            package = cycle.shoot_ready_package
            feedback = cycle.feedback
            if packet is not None and (
                packet.participant_id != self.participant_id
                or packet.cycle_number != cycle.cycle_number
            ):
                raise ValueError("OwnerContextPacket identity mismatch")
            if package is not None and (
                package.participant_id != self.participant_id
                or package.cycle_number != cycle.cycle_number
            ):
                raise ValueError("ShootReadyPackage identity mismatch")
            if feedback is not None and feedback.participant_id != self.participant_id:
                raise ValueError("FeedbackRecord participant mismatch")
        return self


class StudyRecord(StrictModel):
    format_version: Literal[1]
    protocol_version: Literal[1]
    study_id: StudyId
    created_at: UtcTimestamp
    approved_model: ModelIdentity
    recording_window_days: Literal[7]
    participant_target_min: Literal[5]
    participant_target_max: Literal[10]
    concierge_cycles_per_participant: Literal[2]
    participants: list[ParticipantRecord] = Field(max_length=10)

    @model_validator(mode="after")
    def study_closure(self) -> "StudyRecord":
        participant_ids = [item.participant_id for item in self.participants]
        if len(participant_ids) != len(set(participant_ids)):
            raise ValueError("participant IDs must be unique")
        blind_labels: list[str] = []
        for participant in self.participants:
            for cycle in participant.cycles:
                if cycle.model_identity != self.approved_model:
                    raise ValueError("all cycles must use the approved study model")
                packet = cycle.owner_context_packet
                package = cycle.shoot_ready_package
                generated_at = (
                    cycle.baseline_output.generated_at
                    if cycle.baseline_output is not None
                    else package.generated_at if package is not None else None
                )
                if cycle.feedback is not None and generated_at is not None:
                    from datetime import timedelta

                    expected_due_at = parse_timestamp(generated_at) + timedelta(
                        days=self.recording_window_days
                    )
                    if parse_timestamp(cycle.feedback.recording_due_at) != expected_due_at:
                        raise ValueError(
                            "FeedbackRecord recording_due_at must be exactly seven days after output generation"
                        )
                if packet is not None and package is not None:
                    expected_hash = owner_context_sha256(packet)
                    if package.owner_context_sha256 != expected_hash:
                        raise ValueError("ShootReadyPackage owner_context_sha256 mismatch")
                    legal_item_ids = {
                        item.item_id
                        for item in [
                            *packet.confirmed_facts,
                            *packet.recent_context,
                            packet.content_goal,
                            *packet.available_scenes,
                            *packet.constraints,
                        ]
                    }
                    used_ids = {
                        item_id
                        for segment in package.segments
                        for item_id in segment.context_item_ids_used
                    }
                    unknown = used_ids - legal_item_ids
                    if unknown:
                        raise ValueError(
                            f"ShootReadyPackage references unknown context items: {sorted(unknown)}"
                        )
                if cycle.blind_review is not None:
                    expected_label = blind_label(
                        self.study_id, participant.participant_id, cycle.cycle_number
                    )
                    if cycle.blind_review.blind_label != expected_label:
                        raise ValueError("BlindReviewRecord blind_label mismatch")
                    blind_labels.append(cycle.blind_review.blind_label)
        if len(blind_labels) != len(set(blind_labels)):
            raise ValueError("blind labels must be unique")
        return self


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def owner_context_sha256(packet: OwnerContextPacket) -> str:
    return sha256(canonical_json(packet).encode("utf-8")).hexdigest()


def blind_label(study_id: str, participant_id: str, cycle_number: int) -> str:
    digest = sha256(f"{study_id}:{participant_id}:{cycle_number}".encode("utf-8")).hexdigest()
    return f"B-{digest[:10].upper()}"


def parse_timestamp(value: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_study(path: Path) -> StudyRecord:
    try:
        return StudyRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    except ValidationError as error:
        raise ValueError(str(error)) from error


def is_protocol_eligible(cycle: ContentCycle) -> bool:
    return not any(item.content_changed for item in cycle.operator_interventions)


def recorded_within_window(cycle: ContentCycle) -> bool:
    if cycle.feedback is None or cycle.feedback.recording_outcome != "RECORDED":
        return False
    if cycle.feedback.recorded_at is None:
        return False
    return parse_timestamp(cycle.feedback.recorded_at) <= parse_timestamp(
        cycle.feedback.recording_due_at
    )


def study_summary(study: StudyRecord) -> dict[str, Any]:
    categories = {item.restaurant_category for item in study.participants}
    baseline_cycles: list[ContentCycle] = []
    concierge_cycles: list[ContentCycle] = []
    complete_participants = 0
    all_cycles: list[ContentCycle] = []

    for participant in study.participants:
        all_cycles.extend(participant.cycles)
        if len(participant.cycles) == 3 and all(
            cycle.feedback is not None and cycle.blind_review is not None
            for cycle in participant.cycles
        ):
            complete_participants += 1
        for cycle in participant.cycles:
            if cycle.mode == "BASELINE_CURRENT_CHAT":
                if cycle.baseline_output is not None and is_protocol_eligible(cycle):
                    baseline_cycles.append(cycle)
            elif (
                cycle.owner_context_packet is not None
                and cycle.shoot_ready_package is not None
                and is_protocol_eligible(cycle)
            ):
                concierge_cycles.append(cycle)

    baseline_recorded = sum(recorded_within_window(item) for item in baseline_cycles)
    concierge_recorded = sum(recorded_within_window(item) for item in concierge_cycles)
    baseline_rate = baseline_recorded / len(baseline_cycles) if baseline_cycles else None
    concierge_rate = concierge_recorded / len(concierge_cycles) if concierge_cycles else None

    accepted_or_recorded = [
        cycle
        for cycle in all_cycles
        if cycle.feedback is not None
        and (
            cycle.feedback.output_disposition == "ACCEPTED"
            or cycle.feedback.recording_outcome == "RECORDED"
        )
    ]
    unconfirmed_facts = sum(
        cycle.feedback.fact_audit.unconfirmed_fact_count
        for cycle in accepted_or_recorded
        if cycle.feedback is not None
    )
    fact_gate_pass = bool(accepted_or_recorded) and unconfirmed_facts == 0
    behavior_gate_pass = (
        len(concierge_cycles) >= 10
        and concierge_rate is not None
        and concierge_rate >= (1 / 3)
    )
    recording_improved = (
        baseline_rate is not None
        and concierge_rate is not None
        and concierge_rate > baseline_rate
    )
    sample_ready = (
        5 <= len(study.participants) <= 10
        and len(categories) >= 3
        and complete_participants == len(study.participants)
        and 10 <= len(concierge_cycles) <= 20
    )

    if unconfirmed_facts > 0:
        decision = "BLOCKED_FACT_BOUNDARY"
    elif not sample_ready:
        decision = "NOT_READY"
    elif fact_gate_pass and behavior_gate_pass and recording_improved:
        decision = "READY_FOR_PHASE_1J"
    else:
        decision = "ITERATE_PHASE_1I"

    failure_root_causes = Counter(
        cycle.feedback.not_recorded_reason
        for cycle in all_cycles
        if cycle.feedback is not None
        and cycle.feedback.not_recorded_reason is not None
    )
    blind_root_causes = Counter(
        cycle.blind_review.root_cause
        for cycle in all_cycles
        if cycle.blind_review is not None and cycle.blind_review.root_cause is not None
    )
    recorded_concierge = [item for item in concierge_cycles if recorded_within_window(item)]
    recorded_local_or_none = sum(
        item.feedback is not None and item.feedback.final_edit_scope in {"NONE", "LOCAL"}
        for item in recorded_concierge
    )

    return {
        "study_id": study.study_id,
        "decision": decision,
        "sample": {
            "participants": len(study.participants),
            "complete_participants": complete_participants,
            "restaurant_categories": len(categories),
            "sample_ready": sample_ready,
        },
        "fact_gate": {
            "accepted_or_recorded_outputs": len(accepted_or_recorded),
            "unconfirmed_fact_count": unconfirmed_facts,
            "passed": fact_gate_pass,
        },
        "behavior_gate": {
            "eligible_concierge_packages": len(concierge_cycles),
            "recorded_within_seven_days": concierge_recorded,
            "recording_rate": concierge_rate,
            "passed": behavior_gate_pass,
        },
        "baseline_comparison": {
            "eligible_baseline_outputs": len(baseline_cycles),
            "recorded_within_seven_days": baseline_recorded,
            "recording_rate": baseline_rate,
            "concierge_recording_rate_improved": recording_improved,
        },
        "edit_diagnostic": {
            "recorded_concierge_packages": len(recorded_concierge),
            "recorded_with_none_or_local_edits": recorded_local_or_none,
        },
        "root_cause_diagnostics": {
            "not_recorded": dict(sorted(failure_root_causes.items())),
            "blind_review_failures": dict(sorted(blind_root_causes.items())),
        },
        "protocol_deviations": sum(
            not is_protocol_eligible(cycle) for cycle in all_cycles
        ),
    }


def format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def summary_text(summary: dict[str, Any]) -> str:
    sample = summary["sample"]
    fact = summary["fact_gate"]
    behavior = summary["behavior_gate"]
    baseline = summary["baseline_comparison"]
    edits = summary["edit_diagnostic"]
    return "\n".join(
        [
            f"Study: {summary['study_id']}",
            f"Decision: {summary['decision']}",
            (
                "Sample: "
                f"{sample['participants']} participants, "
                f"{sample['complete_participants']} complete, "
                f"{sample['restaurant_categories']} categories"
            ),
            (
                "Fact gate: "
                f"{'PASS' if fact['passed'] else 'NOT PASS'} "
                f"({fact['unconfirmed_fact_count']} unconfirmed facts across "
                f"{fact['accepted_or_recorded_outputs']} accepted/recorded outputs)"
            ),
            (
                "Behavior gate: "
                f"{'PASS' if behavior['passed'] else 'NOT PASS'} "
                f"({behavior['recorded_within_seven_days']}/"
                f"{behavior['eligible_concierge_packages']} recorded, "
                f"{format_rate(behavior['recording_rate'])})"
            ),
            (
                "Baseline: "
                f"{baseline['recorded_within_seven_days']}/"
                f"{baseline['eligible_baseline_outputs']} recorded, "
                f"{format_rate(baseline['recording_rate'])}; "
                f"improved={baseline['concierge_recording_rate_improved']}"
            ),
            (
                "Recorded Concierge with none/local edits: "
                f"{edits['recorded_with_none_or_local_edits']}/"
                f"{edits['recorded_concierge_packages']}"
            ),
            f"Protocol deviations: {summary['protocol_deviations']}",
        ]
    )


def new_study(study_id: str, provider: str, model: str, created_at: str) -> StudyRecord:
    return StudyRecord(
        format_version=1,
        protocol_version=1,
        study_id=study_id,
        created_at=created_at,
        approved_model=ModelIdentity(provider=provider, model=model),
        recording_window_days=7,
        participant_target_min=5,
        participant_target_max=10,
        concierge_cycles_per_participant=2,
        participants=[],
    )


def write_json(path: Path, value: Any, *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise ValueError(f"refusing to overwrite existing path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def blind_artifact(cycle: ContentCycle) -> dict[str, Any] | None:
    if cycle.baseline_output is not None:
        output = cycle.baseline_output
        return {
            "title": output.title,
            "spoken_script": output.script_text,
            "shooting_notes": output.shooting_notes,
        }
    package = cycle.shoot_ready_package
    if package is None:
        return None
    return {
        "title": package.title,
        "spoken_script": "\n".join(item.spoken_line for item in package.segments),
        "shooting_notes": [
            f"{item.sequence}. {item.shot_and_action}" for item in package.segments
        ],
    }


def export_blind_bundle(
    study: StudyRecord,
    review_output: Path,
    mapping_output: Path,
    *,
    overwrite: bool,
) -> int:
    if review_output.resolve() == mapping_output.resolve():
        raise ValueError("review output and coordinator mapping must be separate paths")
    if review_output.exists() and any(review_output.iterdir()) and not overwrite:
        raise ValueError(f"refusing to write into non-empty directory: {review_output}")
    review_output.mkdir(parents=True, exist_ok=True)
    mapping: list[dict[str, Any]] = []
    count = 0
    for participant in study.participants:
        for cycle in participant.cycles:
            artifact = blind_artifact(cycle)
            if artifact is None:
                continue
            label = blind_label(study.study_id, participant.participant_id, cycle.cycle_number)
            payload = {"format_version": 1, "blind_label": label, **artifact}
            write_json(
                review_output / f"{label}.json",
                payload,
                overwrite=overwrite,
            )
            mapping.append(
                {
                    "blind_label": label,
                    "participant_id": participant.participant_id,
                    "cycle_number": cycle.cycle_number,
                    "mode": cycle.mode,
                }
            )
            count += 1
    write_json(
        mapping_output,
        {"format_version": 1, "study_id": study.study_id, "mapping": mapping},
        overwrite=overwrite,
    )
    return count


def utc_now_text() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def find_context(study: StudyRecord, participant_id: str, cycle_number: int) -> OwnerContextPacket:
    for participant in study.participants:
        if participant.participant_id != participant_id:
            continue
        for cycle in participant.cycles:
            if cycle.cycle_number == cycle_number and cycle.owner_context_packet is not None:
                return cycle.owner_context_packet
    raise ValueError("OwnerContextPacket not found for participant/cycle")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 1I non-production field-study toolkit."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create an empty study record")
    init_parser.add_argument("output", type=Path)
    init_parser.add_argument("--study-id", required=True)
    init_parser.add_argument("--provider", required=True)
    init_parser.add_argument("--model", required=True)
    init_parser.add_argument("--force", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="validate a study record")
    validate_parser.add_argument("study", type=Path)

    schema_parser = subparsers.add_parser(
        "schema", help="export the disposable StudyRecord JSON Schema"
    )
    schema_parser.add_argument("output", type=Path)
    schema_parser.add_argument("--force", action="store_true")

    summary_parser = subparsers.add_parser("summary", help="summarize gates and diagnostics")
    summary_parser.add_argument("study", type=Path)
    summary_parser.add_argument("--json", action="store_true", dest="as_json")

    hash_parser = subparsers.add_parser(
        "context-hash", help="compute the hash required by a ShootReadyPackage"
    )
    hash_parser.add_argument("study", type=Path)
    hash_parser.add_argument("--participant", required=True)
    hash_parser.add_argument("--cycle", required=True, type=int, choices=(2, 3))

    blind_parser = subparsers.add_parser(
        "blind-bundle", help="export normalized blind-review artifacts"
    )
    blind_parser.add_argument("study", type=Path)
    blind_parser.add_argument("--review-output", type=Path, required=True)
    blind_parser.add_argument("--mapping-output", type=Path, required=True)
    blind_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            study = new_study(
                args.study_id,
                args.provider,
                args.model,
                utc_now_text(),
            )
            write_json(args.output, study, overwrite=args.force)
            print(args.output)
        elif args.command == "validate":
            study = load_study(args.study)
            print(f"valid: {study.study_id}")
        elif args.command == "schema":
            write_json(
                args.output,
                StudyRecord.model_json_schema(),
                overwrite=args.force,
            )
            print(args.output)
        elif args.command == "summary":
            summary = study_summary(load_study(args.study))
            if args.as_json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                print(summary_text(summary))
        elif args.command == "context-hash":
            study = load_study(args.study)
            packet = find_context(study, args.participant, args.cycle)
            print(owner_context_sha256(packet))
        elif args.command == "blind-bundle":
            study = load_study(args.study)
            count = export_blind_bundle(
                study,
                args.review_output,
                args.mapping_output,
                overwrite=args.force,
            )
            print(f"exported {count} blind artifacts")
        else:  # pragma: no cover
            raise AssertionError("unreachable command")
    except (OSError, ValueError, ValidationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
