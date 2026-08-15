"""Strict Food-IP Director Core JSON v1 models and cross-field validators."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from .canonical import is_blank_text


UUID4_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
UTC_MILLIS_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

Stage = Literal["EXPLORE", "DEEPEN", "CREATE", "REVIEW", "READY"]
RunControl = Literal["CONTINUE", "WAIT_FOR_OWNER", "READY"]

# One shared source of truth for the combinations exposed to a Stage handler
# and accepted by the persisted ExecutionStep validator.  CREATE may wait for
# owner input in the current execution contract; REVIEW only routes to a
# repair stage or completes READY.
STAGE_EXECUTION_COMBINATIONS: dict[Stage, tuple[tuple[RunControl, Stage], ...]] = {
    "EXPLORE": (
        ("WAIT_FOR_OWNER", "EXPLORE"),
        ("CONTINUE", "DEEPEN"),
    ),
    "DEEPEN": (
        ("WAIT_FOR_OWNER", "DEEPEN"),
        ("CONTINUE", "DEEPEN"),
        ("CONTINUE", "CREATE"),
    ),
    "CREATE": (
        ("WAIT_FOR_OWNER", "CREATE"),
        ("CONTINUE", "REVIEW"),
    ),
    "REVIEW": (
        ("CONTINUE", "CREATE"),
        ("CONTINUE", "DEEPEN"),
        ("CONTINUE", "EXPLORE"),
        ("READY", "READY"),
    ),
    "READY": (),
}


def stage_execution_contract(stage: Stage) -> dict[str, object]:
    """Return the handler-visible legal control/target combinations."""

    combinations = STAGE_EXECUTION_COMBINATIONS[stage]
    contract: dict[str, object] = {
        "stage": stage,
        "allowed_combinations": [
            {"run_control": run_control, "target_stage": target_stage}
            for run_control, target_stage in combinations
        ],
        "run_controls": list(dict.fromkeys(run_control for run_control, _ in combinations)),
        "legal_target_stages": list(dict.fromkeys(target_stage for _, target_stage in combinations)),
    }
    if stage == "REVIEW":
        contract["review_routes"] = [
            {"root_cause": "WRITING_PROBLEM", "run_control": "CONTINUE", "target_stage": "CREATE"},
            {"root_cause": "MATERIAL_PROBLEM", "run_control": "CONTINUE", "target_stage": "DEEPEN"},
            {"root_cause": "DIRECTION_PROBLEM", "run_control": "CONTINUE", "target_stage": "EXPLORE"},
            {"outcome": "PASSED", "run_control": "READY", "target_stage": "READY"},
        ]
    return contract


def validate_uuid4(value: str) -> str:
    if UUID4_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a normalized UUIDv4 string")
    return value


def validate_utc_millis(value: str) -> str:
    if UTC_MILLIS_PATTERN.fullmatch(value) is None:
        raise ValueError("must use UTC fixed-millisecond format")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ValueError("must be a valid UTC fixed-millisecond timestamp") from exc
    return value


def validate_sha256(value: str) -> str:
    if SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a lowercase SHA-256 hex digest")
    return value


def _nonblank(value: str) -> str:
    if is_blank_text(value):
        raise ValueError("must not be blank")
    return value


UUID4Text = Annotated[StrictStr, AfterValidator(validate_uuid4)]
NonBlankText = Annotated[StrictStr, Field(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceReference(StrictModel):
    evidence_type: Literal["owner_message"]
    target_id: UUID4Text
    target_session_id: UUID4Text


class InheritedFrom(StrictModel):
    source_ready_content_id: UUID4Text
    source_session_id: UUID4Text


class OwnerFact(StrictModel):
    item_id: UUID4Text
    statement: NonBlankText
    evidence_refs: list[EvidenceReference] = Field(min_length=1)
    supersedes_item_ids: list[UUID4Text]
    inherited_from: InheritedFrom | None

    _statement = field_validator("statement")(_nonblank)

    @model_validator(mode="after")
    def unique_refs(self) -> "OwnerFact":
        _require_unique(self.evidence_refs, "evidence_refs")
        _require_unique(self.supersedes_item_ids, "supersedes_item_ids")
        return self


ConstraintKind = Literal[
    "BUSINESS_OBJECTIVE", "CONTENT_REQUIREMENT", "PREFERENCE",
    "EXPRESSION", "SHOOTING", "PROHIBITION",
]


class OwnerConstraint(StrictModel):
    item_id: UUID4Text
    statement: NonBlankText
    evidence_refs: list[EvidenceReference] = Field(min_length=1)
    constraint_kind: ConstraintKind
    inherited_from: InheritedFrom | None

    _statement = field_validator("statement")(_nonblank)

    @model_validator(mode="after")
    def unique_refs(self) -> "OwnerConstraint":
        _require_unique(self.evidence_refs, "evidence_refs")
        return self


class AIJudgment(StrictModel):
    item_id: UUID4Text
    judgment_kind: Literal["DIRECTION_CANDIDATE", "STRUCTURE", "EXPRESSION", "MATERIAL_ASSESSMENT"]
    statement: NonBlankText

    _statement = field_validator("statement")(_nonblank)


class UnconfirmedInference(StrictModel):
    item_id: UUID4Text
    statement: NonBlankText
    reason: NonBlankText

    _texts = field_validator("statement", "reason")(_nonblank)


class RejectedItem(StrictModel):
    item_id: UUID4Text
    item_kind: Literal["OWNER_FACT", "OWNER_CONSTRAINT", "DIRECTION", "AI_JUDGMENT", "UNCONFIRMED_INFERENCE"]
    statement: NonBlankText
    rejection_code: Literal[
        "OWNER_CORRECTED", "OWNER_REJECTED", "DIRECTION_REPLACED",
        "NO_LONGER_USED", "INCONSISTENT_WITH_CURRENT_STATE",
    ]
    evidence_refs: list[EvidenceReference]
    rejected_by_evidence_refs: list[EvidenceReference]
    superseded_by_item_id: UUID4Text | None
    inherited_from: InheritedFrom | None

    _statement = field_validator("statement")(_nonblank)

    @model_validator(mode="after")
    def evidence_and_code(self) -> "RejectedItem":
        allowed = {
            "OWNER_FACT": {"OWNER_CORRECTED", "OWNER_REJECTED", "NO_LONGER_USED", "INCONSISTENT_WITH_CURRENT_STATE"},
            "OWNER_CONSTRAINT": {"OWNER_CORRECTED", "OWNER_REJECTED", "NO_LONGER_USED", "INCONSISTENT_WITH_CURRENT_STATE"},
            "DIRECTION": {"OWNER_REJECTED", "DIRECTION_REPLACED", "NO_LONGER_USED", "INCONSISTENT_WITH_CURRENT_STATE"},
            "AI_JUDGMENT": {"NO_LONGER_USED", "INCONSISTENT_WITH_CURRENT_STATE"},
            "UNCONFIRMED_INFERENCE": {"NO_LONGER_USED", "INCONSISTENT_WITH_CURRENT_STATE"},
        }
        if self.rejection_code not in allowed[self.item_kind]:
            raise ValueError("illegal item_kind and rejection_code combination")
        if self.item_kind in {"OWNER_FACT", "OWNER_CONSTRAINT", "DIRECTION"} and not self.evidence_refs:
            raise ValueError("rejected owner-backed items must retain original evidence")
        explicit = self.rejection_code in {"OWNER_CORRECTED", "OWNER_REJECTED", "DIRECTION_REPLACED"}
        if explicit and not self.rejected_by_evidence_refs:
            raise ValueError("explicit owner rejection requires owner evidence")
        _require_unique(self.evidence_refs, "evidence_refs")
        _require_unique(self.rejected_by_evidence_refs, "rejected_by_evidence_refs")
        return self


class Direction(StrictModel):
    item_id: UUID4Text
    statement: NonBlankText
    owner_confirmed: Literal[True]
    evidence_refs: list[EvidenceReference] = Field(min_length=1)
    inherited_from: InheritedFrom | None

    _statement = field_validator("statement")(_nonblank)

    @model_validator(mode="after")
    def unique_refs(self) -> "Direction":
        _require_unique(self.evidence_refs, "evidence_refs")
        return self


class RequiredConfirmation(StrictModel):
    item_id: UUID4Text
    statement: NonBlankText
    reason: NonBlankText
    evidence_refs: list[EvidenceReference]
    inherited_from: InheritedFrom | None

    _texts = field_validator("statement", "reason")(_nonblank)

    @model_validator(mode="after")
    def unique_refs(self) -> "RequiredConfirmation":
        _require_unique(self.evidence_refs, "evidence_refs")
        return self


class MaterialState(StrictModel):
    status: Literal["UNKNOWN", "SUFFICIENT", "INSUFFICIENT"]
    required_confirmations: list[RequiredConfirmation]


class Content(StrictModel):
    title: NonBlankText | None
    script_text: NonBlankText
    shooting_notes: list[NonBlankText]

    _texts = field_validator("title", "script_text")(
        lambda value: value if value is None else _nonblank(value)
    )

    @field_validator("shooting_notes")
    @classmethod
    def nonblank_notes(cls, value: list[str]) -> list[str]:
        for item in value:
            _nonblank(item)
        return value


class Draft(StrictModel):
    draft_id: UUID4Text | None
    content: Content
    content_status: Literal["WORKING", "FINAL_CANDIDATE"]
    based_on_ready_content_id: UUID4Text | None


class Review(StrictModel):
    review_id: UUID4Text
    outcome: Literal["PASSED", "BLOCKED"]
    root_cause: Literal["WRITING_PROBLEM", "MATERIAL_PROBLEM", "DIRECTION_PROBLEM"] | None
    against_draft_id: UUID4Text
    against_content: Content

    @model_validator(mode="after")
    def outcome_root_cause(self) -> "Review":
        if (self.outcome == "PASSED") != (self.root_cause is None):
            raise ValueError("PASSED requires null root_cause; BLOCKED requires a root cause")
        return self


class WorkingState(StrictModel):
    format_version: Literal[1]
    owner_facts: list[OwnerFact]
    ai_judgments: list[AIJudgment]
    unconfirmed_inferences: list[UnconfirmedInference]
    rejected_items: list[RejectedItem]
    owner_constraints: list[OwnerConstraint]
    direction: Direction | None
    material_state: MaterialState
    draft: Draft | None
    review: Review | None

    @model_validator(mode="after")
    def identities_and_review(self) -> "WorkingState":
        for name in ("owner_facts", "ai_judgments", "unconfirmed_inferences", "rejected_items", "owner_constraints"):
            _require_unique([item.item_id for item in getattr(self, name)], name)
        if self.direction is not None and any(
            item.item_id == self.direction.item_id for item in self.ai_judgments
        ):
            raise ValueError("current direction cannot also exist as an AI Judgment copy")
        if self.review is not None:
            if self.draft is None or self.draft.draft_id is None:
                raise ValueError("review requires a non-null draft_id")
            if self.review.against_draft_id != self.draft.draft_id:
                raise ValueError("review must target the current draft")
            if self.review.against_content != self.draft.content:
                raise ValueError("review content must equal current draft content")
        if self.draft is not None and self.draft.draft_id is None and self.review is not None:
            raise ValueError("null draft_id requires null review")
        return self


def validate_working_state(
    value: dict,
    *,
    stage: Stage,
    state_version: int,
    source_ready_content_id: str | None = None,
) -> WorkingState:
    state = WorkingState.model_validate(value)
    if state.draft is not None and state.draft.draft_id is None:
        if state_version != 0 or source_ready_content_id is None:
            raise ValueError("draft_id may be null only for a revision Session at version 0")
        if state.draft.based_on_ready_content_id != source_ready_content_id:
            raise ValueError("revision draft baseline must match source ReadyContent")
    if stage == "READY":
        if state.direction is None or state.draft is None or state.draft.draft_id is None:
            raise ValueError("READY requires direction and a generated draft")
        if state.review is None or state.review.outcome != "PASSED":
            raise ValueError("READY requires a passed review")
        if state.material_state.status != "SUFFICIENT":
            raise ValueError("READY requires sufficient material")
    return state


class GateResult(StrictModel):
    outcome: Literal["PASSED", "BLOCKED"]
    gate_code: Literal[
        "DIRECTION_NOT_CONFIRMED", "MATERIAL_INSUFFICIENT", "CONTENT_INCOMPLETE",
        "FACT_BOUNDARY_UNCLEAR", "NOT_SHOOTABLE", "OWNER_VOICE_MISMATCH", "READINESS_PASSED",
    ]
    explanation: NonBlankText
    _explanation = field_validator("explanation")(_nonblank)


class TraceReview(StrictModel):
    outcome: Literal["PASSED", "BLOCKED"]
    root_cause: Literal["WRITING_PROBLEM", "MATERIAL_PROBLEM", "DIRECTION_PROBLEM"] | None

    @model_validator(mode="after")
    def valid_root(self) -> "TraceReview":
        if (self.outcome == "PASSED") != (self.root_cause is None):
            raise ValueError("invalid review root cause")
        return self


class ExecutionStep(StrictModel):
    step_no: StrictInt = Field(ge=1)
    entered_stage: Stage
    run_control: RunControl
    target_stage: Stage
    transition_reason_code: Literal[
        "OWNER_INPUT_REQUIRED", "DIRECTION_CONFIRMED", "DIRECTION_INVALID", "MATERIAL_GAP",
        "MATERIAL_SUFFICIENT", "DRAFT_CREATED", "WRITING_REPAIR", "REVIEW_PASSED",
    ]
    gate: GateResult | None
    review: TraceReview | None
    candidate_revision: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def legal_transition_and_review_route(self) -> "ExecutionStep":
        if (self.run_control, self.target_stage) not in STAGE_EXECUTION_COMBINATIONS[self.entered_stage]:
            raise ValueError("illegal Director Core stage transition")
        if (self.review is not None) != (self.entered_stage == "REVIEW"):
            raise ValueError("review is only allowed on REVIEW steps")
        if self.review is not None:
            expected = {
                "WRITING_PROBLEM": "CREATE",
                "MATERIAL_PROBLEM": "DEEPEN",
                "DIRECTION_PROBLEM": "EXPLORE",
                None: "READY",
            }[self.review.root_cause]
            if self.target_stage != expected:
                raise ValueError("review root cause does not match target stage")
            if self.review.outcome == "PASSED" and (
                self.run_control != "READY" or self.target_stage != "READY"
                or self.transition_reason_code != "REVIEW_PASSED"
                or self.gate is None or self.gate.outcome != "PASSED"
                or self.gate.gate_code != "READINESS_PASSED"
            ):
                raise ValueError("passed review requires a passed gate and READY control")
            if self.review.outcome == "BLOCKED" and self.run_control != "CONTINUE":
                raise ValueError("blocked review must continue to its repair stage")
        return self


class TurnExecutionTrace(StrictModel):
    format_version: Literal[1]
    steps: list[ExecutionStep] = Field(min_length=1)

    @model_validator(mode="after")
    def consecutive(self) -> "TurnExecutionTrace":
        for index, step in enumerate(self.steps, 1):
            if step.step_no != index or step.candidate_revision != index:
                raise ValueError("step_no and candidate_revision must be consecutive")
            if index < len(self.steps) and step.run_control != "CONTINUE":
                raise ValueError("only the final step may stop the loop")
            if index > 1 and step.entered_stage != self.steps[index - 2].target_stage:
                raise ValueError("trace stage chain is discontinuous")
            if index == len(self.steps) and step.run_control == "CONTINUE":
                raise ValueError("final step must WAIT_FOR_OWNER or READY")
        return self


def validate_turn_execution_trace(
    value: dict,
    *,
    pre_stage: Stage,
    final_run_control: Literal["WAIT_FOR_OWNER", "READY"],
    target_stage: Stage,
    transition_reason_code: str,
    gate_outcome: str | None,
    review_root_cause: str | None,
) -> TurnExecutionTrace:
    """Validate a persisted trace as one closed execution chain, not loose steps."""
    trace = TurnExecutionTrace.model_validate(value)
    steps = trace.steps
    if steps[0].entered_stage != pre_stage:
        raise ValueError("trace first stage does not match Turn pre-state")
    final = steps[-1]
    if (
        final.run_control != final_run_control
        or final.target_stage != target_stage
        or final.transition_reason_code != transition_reason_code
        or (final.gate.outcome if final.gate else None) != gate_outcome
        or (final.review.root_cause if final.review else None) != review_root_cause
    ):
        raise ValueError("Turn top-level fields do not close over the final trace step")

    reason_routes = {
        "OWNER_INPUT_REQUIRED": {("EXPLORE", "EXPLORE"), ("DEEPEN", "DEEPEN"), ("CREATE", "CREATE")},
        "DIRECTION_CONFIRMED": {("EXPLORE", "DEEPEN")},
        "DIRECTION_INVALID": {("REVIEW", "EXPLORE")},
        "MATERIAL_GAP": {("DEEPEN", "DEEPEN"), ("REVIEW", "DEEPEN")},
        "MATERIAL_SUFFICIENT": {("DEEPEN", "CREATE")},
        "DRAFT_CREATED": {("CREATE", "REVIEW")},
        "WRITING_REPAIR": {("REVIEW", "CREATE")},
        "REVIEW_PASSED": {("REVIEW", "READY")},
    }
    for step in steps:
        if (step.entered_stage, step.target_stage) not in reason_routes[step.transition_reason_code]:
            raise ValueError("transition reason code does not match its transition")
        if step.transition_reason_code == "OWNER_INPUT_REQUIRED" and step.run_control != "WAIT_FOR_OWNER":
            raise ValueError("OWNER_INPUT_REQUIRED requires WAIT_FOR_OWNER")
        if step.transition_reason_code == "REVIEW_PASSED":
            if step.run_control != "READY" or step.gate is None or step.gate.gate_code != "READINESS_PASSED" or step.gate.outcome != "PASSED" or step.review is None or step.review.outcome != "PASSED":
                raise ValueError("REVIEW_PASSED requires READY, a passed readiness gate, and passed review")
        review_reasons = {
            "DIRECTION_INVALID": "DIRECTION_PROBLEM",
            "WRITING_REPAIR": "WRITING_PROBLEM",
        }
        if step.entered_stage == "REVIEW" and step.transition_reason_code == "MATERIAL_GAP":
            review_reasons["MATERIAL_GAP"] = "MATERIAL_PROBLEM"
        expected_root = review_reasons.get(step.transition_reason_code)
        if expected_root is not None and (
            step.review is None
            or step.review.outcome != "BLOCKED"
            or step.review.root_cause != expected_root
        ):
            raise ValueError("review outcome and root cause do not match the repair transition")
        if step.gate is not None:
            gate_targets = {
                "READINESS_PASSED": ("PASSED", "READY"),
                "DIRECTION_NOT_CONFIRMED": ("BLOCKED", "EXPLORE"),
                "MATERIAL_INSUFFICIENT": ("BLOCKED", "DEEPEN"),
                "CONTENT_INCOMPLETE": ("BLOCKED", "CREATE"),
                "FACT_BOUNDARY_UNCLEAR": ("BLOCKED", "EXPLORE"),
                "NOT_SHOOTABLE": ("BLOCKED", "CREATE"),
                "OWNER_VOICE_MISMATCH": ("BLOCKED", "CREATE"),
            }
            if (step.gate.outcome, step.target_stage) != gate_targets[step.gate.gate_code]:
                raise ValueError("gate outcome/code does not match target stage")
        if step.review is not None and step.review.outcome == "BLOCKED":
            if step.run_control != "CONTINUE":
                raise ValueError("blocked review must continue to its repair stage")
    return trace


class TurnPostStateSnapshot(StrictModel):
    snapshot_format_version: Literal[1]
    state_version: StrictInt = Field(ge=0)
    stage: Stage
    state_json: WorkingState

    @model_validator(mode="after")
    def state_cross_fields(self) -> "TurnPostStateSnapshot":
        validate_working_state(
            self.state_json.model_dump(mode="json"),
            stage=self.stage,
            state_version=self.state_version,
        )
        return self


class CheckpointEntry(StrictModel):
    statement: NonBlankText
    message_refs: list[UUID4Text] = Field(min_length=1)
    _statement = field_validator("statement")(_nonblank)

    @model_validator(mode="after")
    def unique_refs(self) -> "CheckpointEntry":
        _require_unique(self.message_refs, "message_refs")
        return self


class ContextCheckpoint(StrictModel):
    conversation_summary: StrictStr
    confirmed_owner_positions: list[CheckpointEntry]
    open_threads: list[CheckpointEntry]
    abandoned_directions: list[CheckpointEntry]

    @model_validator(mode="after")
    def summary_boundary(self) -> "ContextCheckpoint":
        has_entries = bool(self.confirmed_owner_positions or self.open_threads or self.abandoned_directions)
        if has_entries:
            _nonblank(self.conversation_summary)
        elif self.conversation_summary != "":
            raise ValueError("summary must be empty when all checkpoint entries are empty")
        return self


class ReadyContent(Content):
    pass


class FirstResponse(StrictModel):
    session_id: UUID4Text
    turn_id: UUID4Text
    owner_message_id: UUID4Text
    director_message_id: UUID4Text
    state_version: StrictInt = Field(ge=1)
    stage: Stage
    run_control: Literal["WAIT_FOR_OWNER", "READY"]
    director_message: NonBlankText
    ready_content_id: UUID4Text | None

    _message = field_validator("director_message")(_nonblank)

    @model_validator(mode="after")
    def ready_id(self) -> "FirstResponse":
        if (self.run_control == "READY") != (self.ready_content_id is not None):
            raise ValueError("ready_content_id presence must match READY run control")
        if self.run_control == "READY" and self.stage != "READY":
            raise ValueError("READY response must target READY stage")
        if self.stage == "READY" and self.run_control != "READY":
            raise ValueError("READY stage requires READY run control")
        return self


def _require_unique(values: list, field_name: str) -> None:
    normalized = [item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in values]
    seen: set[str] = set()
    import json
    for item in normalized:
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if marker in seen:
            raise ValueError(f"{field_name} contains duplicates")
        seen.add(marker)
