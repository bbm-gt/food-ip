"""Semantic-only model boundary and deterministic state resolution.

The model describes the creative/business meaning of one stage.  This module
owns the translation back to the existing Director Core state envelope so the
model never has to copy state, manufacture identities, or choose infrastructure
fields.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal
from uuid import uuid4

from pydantic import ConfigDict, StrictStr, ValidationError, field_validator, model_validator

from .models import StrictModel, WorkingState


SEMANTIC_ONLY = "semantic_only"
LEGACY = "legacy"
SUPPORTED_STAGE_MODES = (LEGACY, SEMANTIC_ONLY)
ConstraintKind = Literal[
    "BUSINESS_OBJECTIVE", "CONTENT_REQUIREMENT", "PREFERENCE",
    "EXPRESSION", "SHOOTING", "PROHIBITION",
]


class SemanticOutputError(ValueError):
    """Base error for a rejected semantic-only model response."""


class SemanticOutputTypeError(SemanticOutputError):
    """The provider did not return one JSON object."""


class SemanticOutputSchemaError(SemanticOutputError):
    """The stage-specific semantic object failed strict validation."""


class SemanticConversionError(SemanticOutputError):
    """The trusted application conversion could not produce valid state."""


class _SemanticModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("reason", check_fields=False)
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value

    @field_validator("missing_material", check_fields=False)
    @classmethod
    def nonblank_material_list(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("semantic material items must not be blank")
        return value


class SemanticFactChange(StrictModel):
    action: Literal["ADD", "CORRECT", "REMOVE"]
    statement: StrictStr | None
    owner_quote: StrictStr
    replaces_statement: StrictStr | None

    @model_validator(mode="after")
    def valid_change(self) -> "SemanticFactChange":
        if self.action == "ADD":
            if self.statement is None or not self.statement.strip() or self.replaces_statement is not None:
                raise ValueError("ADD requires statement and no replaces_statement")
        elif self.replaces_statement is None or not self.replaces_statement.strip():
            raise ValueError("CORRECT and REMOVE require replaces_statement")
        if self.action == "CORRECT" and (self.statement is None or not self.statement.strip()):
            raise ValueError("CORRECT requires statement")
        if self.action == "REMOVE" and self.statement is not None:
            raise ValueError("REMOVE statement must be null")
        if not self.owner_quote.strip():
            raise ValueError("owner_quote must not be blank")
        return self


class SemanticConstraintChange(StrictModel):
    action: Literal["ADD", "CORRECT", "REMOVE"]
    statement: StrictStr | None
    owner_quote: StrictStr
    replaces_statement: StrictStr | None
    constraint_kind: ConstraintKind

    @model_validator(mode="after")
    def valid_change(self) -> "SemanticConstraintChange":
        if self.action == "ADD":
            if self.statement is None or not self.statement.strip() or self.replaces_statement is not None:
                raise ValueError("ADD requires statement and no replaces_statement")
        elif self.replaces_statement is None or not self.replaces_statement.strip():
            raise ValueError("CORRECT and REMOVE require replaces_statement")
        if self.action == "CORRECT" and (self.statement is None or not self.statement.strip()):
            raise ValueError("CORRECT requires statement")
        if self.action == "REMOVE" and self.statement is not None:
            raise ValueError("REMOVE statement must be null")
        if not self.owner_quote.strip():
            raise ValueError("owner_quote must not be blank")
        return self


class ExploreSemanticOutput(_SemanticModel):
    result: Literal["ASK_OWNER", "DIRECTION_CANDIDATE", "DIRECTION_READY"]
    message: StrictStr | None
    direction: StrictStr | None
    owner_quote: StrictStr | None
    new_facts: list[SemanticFactChange]
    new_constraints: list[SemanticConstraintChange]
    reason: StrictStr

    @model_validator(mode="after")
    def result_fields(self) -> "ExploreSemanticOutput":
        if self.result in {"ASK_OWNER", "DIRECTION_CANDIDATE"}:
            if self.message is None or not self.message.strip():
                raise ValueError("owner input result requires message")
            if self.result == "ASK_OWNER" and self.direction is not None:
                raise ValueError("ASK_OWNER must not establish direction")
            if self.result == "DIRECTION_CANDIDATE" and (self.direction is None or not self.direction.strip()):
                raise ValueError("DIRECTION_CANDIDATE requires direction")
            if self.owner_quote is not None:
                raise ValueError("unconfirmed direction must not include owner_quote")
        else:
            if self.direction is None or not self.direction.strip():
                raise ValueError("DIRECTION_READY requires direction")
            if self.owner_quote is None or not self.owner_quote.strip():
                raise ValueError("DIRECTION_READY requires owner_quote")
        return self


class DeepenSemanticOutput(_SemanticModel):
    result: Literal["ASK_OWNER", "MATERIAL_READY"]
    message: StrictStr | None
    new_facts: list[SemanticFactChange]
    new_constraints: list[SemanticConstraintChange]
    missing_material: list[StrictStr]
    reason: StrictStr

    @model_validator(mode="after")
    def result_fields(self) -> "DeepenSemanticOutput":
        if self.result == "ASK_OWNER":
            if self.message is None or not self.message.strip():
                raise ValueError("ASK_OWNER requires message")
            if not self.missing_material:
                raise ValueError("ASK_OWNER requires missing_material")
        elif self.missing_material:
            raise ValueError("MATERIAL_READY must not leave missing_material")
        return self


class CreateSemanticOutput(_SemanticModel):
    title: StrictStr | None
    script_text: StrictStr
    shooting_notes: list[StrictStr]

    @field_validator("script_text")
    @classmethod
    def nonblank_script(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("script_text must not be blank")
        return value

    @field_validator("shooting_notes")
    @classmethod
    def nonblank_notes(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("shooting_notes must not contain blanks")
        return value


class ReviewSemanticOutput(_SemanticModel):
    result: Literal["PASS", "REWRITE", "NEED_MATERIAL", "CHANGE_DIRECTION"]
    problem: StrictStr | None
    reason: StrictStr
    preserve: list[StrictStr]
    change: list[StrictStr]

    @model_validator(mode="after")
    def result_fields(self) -> "ReviewSemanticOutput":
        if self.result == "PASS":
            if self.problem is not None:
                raise ValueError("PASS must not include problem")
        elif self.problem is None or not self.problem.strip():
            raise ValueError("blocked review result requires problem")
        if self.result != "PASS" and not self.change:
            raise ValueError("blocked review result requires change")
        return self


SemanticStageOutput = (
    ExploreSemanticOutput
    | DeepenSemanticOutput
    | CreateSemanticOutput
    | ReviewSemanticOutput
)


def _model_for_stage(stage: str) -> type[_SemanticModel]:
    return {
        "EXPLORE": ExploreSemanticOutput,
        "DEEPEN": DeepenSemanticOutput,
        "CREATE": CreateSemanticOutput,
        "REVIEW": ReviewSemanticOutput,
    }.get(stage, _SemanticModel)


def validate_semantic_output(stage: str, raw_output: Any) -> SemanticStageOutput:
    if not isinstance(raw_output, dict):
        raise SemanticOutputTypeError("semantic model output must be one JSON object")
    model = _model_for_stage(stage)
    if model is _SemanticModel:
        raise SemanticOutputSchemaError(f"semantic_only does not support stage {stage}")
    try:
        return model.model_validate(deepcopy(raw_output))  # type: ignore[return-value]
    except ValidationError as exc:
        raise SemanticOutputSchemaError(
            f"{stage} semantic output failed its strict stage schema"
        ) from exc


def _statements(items: list[dict[str, Any]]) -> list[str]:
    return [item["statement"] for item in items]


def _recent_dialogue(payload: dict[str, Any]) -> list[dict[str, str]]:
    turns = payload.get("history_turns", [])
    return [
        {
            "owner": turn["owner"]["content"],
            "director": turn["director"]["content"],
        }
        for turn in turns[-6:]
    ]


def _feedback_for_model(feedback: Any) -> dict[str, Any]:
    """Turn the immutable context mapping back into JSON-shaped business data."""

    return {
        key: list(value) if isinstance(value, tuple) else deepcopy(value)
        for key, value in dict(feedback).items()
    }


def _owner_items(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"statement": item["statement"]} for item in items]


def _constraint_items(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"statement": item["statement"], "category": item["constraint_kind"]}
        for item in items
    ]


def semantic_model_input(context: Any) -> dict[str, Any]:
    """Build stage-relevant business context without technical state metadata."""

    payload = context.to_dict()
    stage = payload["stage_contract"]["stage"]
    state = payload["working_state"]
    base = {
        "stage": stage,
        "owner_message": payload["current_owner_message"]["content"],
        "direction": None if state["direction"] is None else state["direction"]["statement"],
        "facts": _owner_items(state["owner_facts"]),
        "constraints": _constraint_items(state["owner_constraints"]),
        "recent_dialogue": _recent_dialogue(payload),
    }
    if stage == "EXPLORE":
        base["candidate_directions"] = [
            item["statement"]
            for item in state["ai_judgments"]
            if item["judgment_kind"] == "DIRECTION_CANDIDATE"
        ]
        base["rejected_directions"] = [
            {"direction": item["statement"], "reason": item["rejection_code"]}
            for item in state["rejected_items"]
            if item["item_kind"] == "DIRECTION"
        ]
    if stage in {"DEEPEN", "CREATE", "REVIEW"}:
        base["material"] = {
            "status": state["material_state"]["status"],
            "missing": _statements(state["material_state"]["required_confirmations"]),
        }
    if stage in {"CREATE", "REVIEW"}:
        base["draft"] = None if state["draft"] is None else state["draft"]["content"]
    if stage in {"EXPLORE", "CREATE"} and payload.get("source_ready_content") is not None:
        base["source_ready_content"] = payload["source_ready_content"]["final_content"]
    if stage == "REVIEW":
        base["draft"] = None if state["draft"] is None else state["draft"]["content"]
    feedback = getattr(context, "business_feedback", None)
    if feedback is not None:
        feedback_target = feedback.get("target_stage")
        if feedback_target == stage or (
            stage == "REVIEW" and feedback.get("kind") == "review"
        ):
            base["modification_goal"] = _feedback_for_model(feedback)
    return base


def _evidence(owner_message_id: str, owner_session_id: str) -> dict[str, str]:
    return {
        "evidence_type": "owner_message",
        "target_id": owner_message_id,
        "target_session_id": owner_session_id,
    }


def _normalized_text(value: str) -> str:
    return "".join(
        char.casefold()
        for char in value
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


def _validate_owner_quote(owner_text: str, quote: str) -> None:
    if quote not in owner_text:
        raise SemanticConversionError("owner_quote is not a contiguous fragment of the current owner message")


def _statement_supported_by_quote(statement: str, quote: str) -> bool:
    normalized_statement = _normalized_text(statement)
    normalized_quote = _normalized_text(quote)
    return bool(normalized_statement) and normalized_statement in normalized_quote


def _append_unconfirmed(state: dict[str, Any], statement: str, reason: str) -> None:
    if any(item["statement"] == statement for item in state["unconfirmed_inferences"]):
        return
    state["unconfirmed_inferences"].append({
        "item_id": str(uuid4()),
        "statement": statement,
        "reason": reason,
    })


def _append_fact(
    state: dict[str, Any], statement: str, evidence: dict[str, str], *, supersedes: list[str] | None = None
) -> str:
    if any(item["statement"] == statement for item in state["owner_facts"]):
        return next(item["item_id"] for item in state["owner_facts"] if item["statement"] == statement)
    item_id = str(uuid4())
    state["owner_facts"].append({
        "item_id": item_id,
        "statement": statement,
        "evidence_refs": [deepcopy(evidence)],
        "supersedes_item_ids": list(supersedes or []),
        "inherited_from": None,
    })
    return item_id


def _append_constraint(
    state: dict[str, Any], statement: str, evidence: dict[str, str], *, constraint_kind: ConstraintKind,
) -> str:
    if any(item["statement"] == statement for item in state["owner_constraints"]):
        return next(item["item_id"] for item in state["owner_constraints"] if item["statement"] == statement)
    item_id = str(uuid4())
    state["owner_constraints"].append({
        "item_id": item_id,
        "statement": statement,
        "evidence_refs": [deepcopy(evidence)],
        "constraint_kind": constraint_kind,
        "inherited_from": None,
    })
    return item_id


def _append_owner_material(
    state: dict[str, Any], output: Any, evidence: dict[str, str], owner_text: str
) -> None:
    for change in output.new_facts:
        _apply_fact_change(state, change, evidence, owner_text)
    for change in output.new_constraints:
        _apply_constraint_change(state, change, evidence, owner_text)


def _reject_active_item(
    state: dict[str, Any], item: dict[str, Any], item_kind: str, rejection_code: str,
    evidence: dict[str, str], replacement: str | None,
) -> None:
    state["rejected_items"].append({
        "item_id": item["item_id"],
        "item_kind": item_kind,
        "statement": item["statement"],
        "rejection_code": rejection_code,
        "evidence_refs": deepcopy(item["evidence_refs"]),
        "rejected_by_evidence_refs": [deepcopy(evidence)],
        "superseded_by_item_id": replacement,
        "inherited_from": deepcopy(item["inherited_from"]),
    })


def _matching_active_items(state: dict[str, Any], key: str, statement: str) -> list[dict[str, Any]]:
    return [item for item in state[key] if item["statement"] == statement]


def _apply_fact_change(state: dict[str, Any], change: SemanticFactChange, evidence: dict[str, str], owner_text: str) -> None:
    _validate_owner_quote(owner_text, change.owner_quote)
    if change.action == "ADD" and not _statement_supported_by_quote(change.statement, change.owner_quote):
        _append_unconfirmed(state, change.statement, "AI 提炼超出老板原话，待老板确认。")
        return
    if change.action in {"CORRECT", "REMOVE"}:
        if change.statement is not None and not _statement_supported_by_quote(change.statement, change.owner_quote):
            raise SemanticConversionError("corrected fact statement is broader than owner_quote")
        matches = _matching_active_items(state, "owner_facts", change.replaces_statement)
        if len(matches) != 1:
            raise SemanticConversionError("fact correction must match exactly one active fact")
        old = matches[0]
        if change.action == "REMOVE":
            _reject_active_item(state, old, "OWNER_FACT", "OWNER_REJECTED", evidence, None)
            state["owner_facts"].remove(old)
        else:
            new_id = _append_fact(state, change.statement, evidence, supersedes=[old["item_id"]])
            _reject_active_item(state, old, "OWNER_FACT", "OWNER_CORRECTED", evidence, new_id)
            state["owner_facts"].remove(old)
        return
    _append_fact(state, change.statement, evidence)


def _apply_constraint_change(
    state: dict[str, Any], change: SemanticConstraintChange, evidence: dict[str, str], owner_text: str
) -> None:
    _validate_owner_quote(owner_text, change.owner_quote)
    if change.action == "ADD" and not _statement_supported_by_quote(change.statement, change.owner_quote):
        _append_unconfirmed(state, change.statement, "AI 提炼超出老板原话，待老板确认。")
        return
    if change.action in {"CORRECT", "REMOVE"}:
        if change.statement is not None and not _statement_supported_by_quote(change.statement, change.owner_quote):
            raise SemanticConversionError("corrected constraint statement is broader than owner_quote")
        matches = _matching_active_items(state, "owner_constraints", change.replaces_statement)
        if len(matches) != 1:
            raise SemanticConversionError("constraint correction must match exactly one active constraint")
        old = matches[0]
        if change.action == "REMOVE":
            _reject_active_item(state, old, "OWNER_CONSTRAINT", "OWNER_REJECTED", evidence, None)
            state["owner_constraints"].remove(old)
        else:
            new_id = _append_constraint(state, change.statement, evidence, constraint_kind=change.constraint_kind)
            _reject_active_item(state, old, "OWNER_CONSTRAINT", "OWNER_CORRECTED", evidence, new_id)
            state["owner_constraints"].remove(old)
        return
    _append_constraint(state, change.statement, evidence, constraint_kind=change.constraint_kind)


def _reject_direction(
    state: dict[str, Any], direction: dict[str, Any], evidence: dict[str, str], *, replacement: str | None
) -> None:
    state["rejected_items"].append({
        "item_id": direction["item_id"],
        "item_kind": "DIRECTION",
        "statement": direction["statement"],
        "rejection_code": "DIRECTION_REPLACED" if replacement else "OWNER_REJECTED",
        "evidence_refs": deepcopy(direction["evidence_refs"]),
        "rejected_by_evidence_refs": [deepcopy(evidence)],
        "superseded_by_item_id": replacement,
        "inherited_from": deepcopy(direction["inherited_from"]),
    })


def _set_direction(
    state: dict[str, Any], statement: str, evidence: dict[str, str], owner_text: str, owner_quote: str
) -> None:
    _validate_owner_quote(owner_text, owner_quote)
    if not _statement_supported_by_quote(statement, owner_quote):
        raise SemanticConversionError("confirmed direction is broader than owner_quote")
    old = state["direction"]
    if old is not None and old["statement"] == statement:
        return
    new_id = str(uuid4())
    if old is not None:
        _reject_direction(state, old, evidence, replacement=new_id)
    state["direction"] = {
        "item_id": new_id,
        "statement": statement,
        "owner_confirmed": True,
        "evidence_refs": [deepcopy(evidence)],
        "inherited_from": None,
    }
    # A new direction cannot safely carry a draft/review written for another one.
    state["draft"] = None
    state["review"] = None


def _append_direction_candidate(state: dict[str, Any], statement: str) -> None:
    if any(
        item["item_kind"] == "DIRECTION" and item["statement"] == statement
        for item in state["rejected_items"]
    ):
        raise SemanticConversionError("direction repeats a previously rejected direction")
    if any(
        item["judgment_kind"] == "DIRECTION_CANDIDATE" and item["statement"] == statement
        for item in state["ai_judgments"]
    ):
        return
    state["ai_judgments"].append({
        "item_id": str(uuid4()),
        "judgment_kind": "DIRECTION_CANDIDATE",
        "statement": statement,
    })


def _reconcile_missing_material(state: dict[str, Any], missing: list[str]) -> None:
    if len(set(missing)) != len(missing):
        raise SemanticConversionError("missing_material contains duplicate questions")
    current = state["material_state"]["required_confirmations"]
    by_statement = {item["statement"]: item for item in current}
    reconciled: list[dict[str, Any]] = []
    for statement in missing:
        if statement in by_statement:
            reconciled.append(by_statement[statement])
        else:
            reconciled.append({
                "item_id": str(uuid4()),
                "statement": statement,
                "reason": "这项真实材料会影响核心表达。",
                "evidence_refs": [],
                "inherited_from": None,
            })
    state["material_state"] = {
        "status": "INSUFFICIENT",
        "required_confirmations": reconciled,
    }


def _clear_material_gap(state: dict[str, Any]) -> None:
    state["material_state"] = {"status": "SUFFICIENT", "required_confirmations": []}


def _content(output: CreateSemanticOutput) -> dict[str, Any]:
    return {
        "title": output.title,
        "script_text": output.script_text,
        "shooting_notes": list(output.shooting_notes),
    }


def _envelope(
    *,
    run_control: str,
    target_stage: str,
    reason_code: str,
    message: str | None,
    gate: dict[str, str] | None,
    review: dict[str, Any] | None,
    state: dict[str, Any],
) -> dict[str, Any]:
    return {
        "output_format_version": 1,
        "run_control": run_control,
        "target_stage": target_stage,
        "transition_reason_code": reason_code,
        "director_message": message,
        "gate": gate,
        "review": review,
        "post_state": state,
    }


def build_business_feedback(
    stage: str, pre_state: dict[str, Any], semantic_output: Any
) -> dict[str, Any] | None:
    """Extract non-persistent creative feedback for the next internal stage."""

    output = validate_semantic_output(stage, semantic_output)
    if stage != "REVIEW" or not isinstance(output, ReviewSemanticOutput) or output.result == "PASS":
        return None
    target_stage = {
        "REWRITE": "CREATE",
        "NEED_MATERIAL": "DEEPEN",
        "CHANGE_DIRECTION": "EXPLORE",
    }[output.result]
    feedback: dict[str, Any] = {
        "kind": "review",
        "target_stage": target_stage,
        "problem": output.problem,
        "reason": output.reason,
        "preserve": list(output.preserve),
        "change": list(output.change),
    }
    if output.result == "CHANGE_DIRECTION" and pre_state.get("direction") is not None:
        feedback["rejected_direction"] = pre_state["direction"]["statement"]
    return feedback


def convert_semantic_output(
    stage: str,
    pre_state: dict[str, Any],
    *,
    owner_text: str,
    owner_message_id: str,
    owner_session_id: str,
    semantic_output: Any,
) -> dict[str, Any]:
    """Turn one small semantic result into a complete internal proposal."""

    state = deepcopy(pre_state)
    try:
        if not isinstance(owner_text, str) or not owner_text.strip():
            raise SemanticConversionError("current owner message must be non-blank")
        state_model = WorkingState.model_validate(state)
        state = state_model.model_dump(mode="json")
        output = validate_semantic_output(stage, semantic_output)
        evidence = _evidence(owner_message_id, owner_session_id)

        if stage == "EXPLORE":
            assert isinstance(output, ExploreSemanticOutput)
            _append_owner_material(state, output, evidence, owner_text)
            if output.result == "ASK_OWNER":
                return _envelope(
                    run_control="WAIT_FOR_OWNER", target_stage="EXPLORE",
                    reason_code="OWNER_INPUT_REQUIRED", message=output.message,
                    gate={"outcome": "BLOCKED", "gate_code": "DIRECTION_NOT_CONFIRMED", "explanation": output.reason},
                    review=None, state=state,
                )
            if output.result == "DIRECTION_CANDIDATE":
                _append_direction_candidate(state, output.direction)  # type: ignore[arg-type]
                return _envelope(
                    run_control="WAIT_FOR_OWNER", target_stage="EXPLORE",
                    reason_code="OWNER_INPUT_REQUIRED", message=output.message,
                    gate={"outcome": "BLOCKED", "gate_code": "DIRECTION_NOT_CONFIRMED", "explanation": output.reason},
                    review=None, state=state,
                )
            if any(
                item["item_kind"] == "DIRECTION" and item["statement"] == output.direction
                for item in state["rejected_items"]
            ):
                raise SemanticConversionError("direction repeats a previously rejected direction")
            _set_direction(state, output.direction, evidence, owner_text, output.owner_quote)  # type: ignore[arg-type]
            return _envelope(
                run_control="CONTINUE", target_stage="DEEPEN",
                reason_code="DIRECTION_CONFIRMED", message=None, gate=None,
                review=None, state=state,
            )

        if stage == "DEEPEN":
            assert isinstance(output, DeepenSemanticOutput)
            _append_owner_material(state, output, evidence, owner_text)
            if output.result == "ASK_OWNER":
                _reconcile_missing_material(state, output.missing_material)
                return _envelope(
                    run_control="WAIT_FOR_OWNER", target_stage="DEEPEN",
                    reason_code="OWNER_INPUT_REQUIRED", message=output.message,
                    gate={"outcome": "BLOCKED", "gate_code": "MATERIAL_INSUFFICIENT", "explanation": output.reason},
                    review=None, state=state,
                )
            _clear_material_gap(state)
            return _envelope(
                run_control="CONTINUE", target_stage="CREATE",
                reason_code="MATERIAL_SUFFICIENT", message=None, gate=None,
                review=None, state=state,
            )

        if stage == "CREATE":
            assert isinstance(output, CreateSemanticOutput)
            _clear_material_gap(state)
            previous = state["draft"]
            state["draft"] = {
                "draft_id": str(uuid4()),
                "content": _content(output),
                "content_status": "FINAL_CANDIDATE",
                "based_on_ready_content_id": (
                    None if previous is None else previous["based_on_ready_content_id"]
                ),
            }
            # Review identity/content is always tied to one exact Draft.
            state["review"] = None
            return _envelope(
                run_control="CONTINUE", target_stage="REVIEW",
                reason_code="DRAFT_CREATED", message=None, gate=None,
                review=None, state=state,
            )

        if stage == "REVIEW":
            assert isinstance(output, ReviewSemanticOutput)
            draft = state.get("draft")
            if not isinstance(draft, dict) or not draft.get("draft_id"):
                raise SemanticConversionError("REVIEW requires a generated current Draft")
            blocked = output.result != "PASS"
            root = {
                "REWRITE": "WRITING_PROBLEM",
                "NEED_MATERIAL": "MATERIAL_PROBLEM",
                "CHANGE_DIRECTION": "DIRECTION_PROBLEM",
            }.get(output.result)
            state["review"] = {
                "review_id": str(uuid4()),
                "outcome": "BLOCKED" if blocked else "PASSED",
                "root_cause": root,
                "against_draft_id": draft["draft_id"],
                "against_content": deepcopy(draft["content"]),
            }
            if output.result == "PASS":
                _clear_material_gap(state)
                return _envelope(
                    run_control="READY", target_stage="READY",
                    reason_code="REVIEW_PASSED", message="内容已经准备好，可以按这个版本拍摄了。",
                    gate={"outcome": "PASSED", "gate_code": "READINESS_PASSED", "explanation": output.reason},
                    review={"outcome": "PASSED", "root_cause": None}, state=state,
                )
            if output.result == "REWRITE":
                _clear_material_gap(state)
                target, code, gate_code = "CREATE", "WRITING_REPAIR", "CONTENT_INCOMPLETE"
            elif output.result == "NEED_MATERIAL":
                existing = [
                    item["statement"]
                    for item in state["material_state"]["required_confirmations"]
                    if item["statement"] != output.problem
                ]
                _reconcile_missing_material(state, [output.problem, *existing])  # type: ignore[list-item]
                target, code, gate_code = "DEEPEN", "MATERIAL_GAP", "MATERIAL_INSUFFICIENT"
            else:
                if state["direction"] is None:
                    raise SemanticConversionError("CHANGE_DIRECTION requires an active Direction")
                old_direction = state["direction"]
                state["rejected_items"].append({
                    "item_id": old_direction["item_id"],
                    "item_kind": "DIRECTION",
                    "statement": old_direction["statement"],
                    "rejection_code": "INCONSISTENT_WITH_CURRENT_STATE",
                    "evidence_refs": deepcopy(old_direction["evidence_refs"]),
                    "rejected_by_evidence_refs": [],
                    "superseded_by_item_id": None,
                    "inherited_from": deepcopy(old_direction["inherited_from"]),
                })
                state["direction"] = None
                target, code, gate_code = "EXPLORE", "DIRECTION_INVALID", "DIRECTION_NOT_CONFIRMED"
            return _envelope(
                run_control="CONTINUE", target_stage=target,
                reason_code=code, message=None,
                gate={"outcome": "BLOCKED", "gate_code": gate_code, "explanation": output.reason},
                review={"outcome": "BLOCKED", "root_cause": root}, state=state,
            )

        raise SemanticConversionError(f"semantic_only does not support stage {stage}")
    except SemanticOutputError:
        raise
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise SemanticConversionError("semantic output could not be converted to Working State") from exc


__all__ = [
    "CreateSemanticOutput",
    "DeepenSemanticOutput",
    "ExploreSemanticOutput",
    "LEGACY",
    "ReviewSemanticOutput",
    "SEMANTIC_ONLY",
    "SemanticConstraintChange",
    "SemanticConversionError",
    "SemanticFactChange",
    "SemanticOutputError",
    "SemanticOutputSchemaError",
    "SemanticOutputTypeError",
    "SUPPORTED_STAGE_MODES",
    "build_business_feedback",
    "convert_semantic_output",
    "semantic_model_input",
    "validate_semantic_output",
]
