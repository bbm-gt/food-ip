"""Strict provider-neutral Stage model output boundary for Director Core."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import ConfigDict, StrictInt, StrictStr, ValidationError, field_validator

from .models import (
    GateResult,
    RunControl,
    Stage,
    StrictModel,
    TraceReview,
    WorkingState,
    validate_uuid4,
)
from .stage_contract import outcome_spec, validate_outcome_envelope


class StageModelOutputError(ValueError):
    """Base error for rejected model output before authoritative writes."""


class StageModelOutputTypeError(StageModelOutputError):
    """The provider result was not one structured object."""


class StageModelOutputSchemaError(StageModelOutputError):
    """The structured object failed strict StageModelOutputV1 validation."""


class StageContractViolationError(StageModelOutputError):
    """A schema-valid output violated the current Stage business contract."""


class StageModelProposalError(StageModelOutputError):
    """The model proposal failed the pre-resolution application boundary."""


class StageModelProposalSchemaError(StageModelOutputSchemaError, StageModelProposalError):
    """The raw model proposal failed its strict top-level schema."""


class IdentityResolutionError(StageModelProposalError):
    """A model identity or identity reference cannot be accepted."""


class InvalidTemporaryReferenceError(IdentityResolutionError):
    """A temporary reference is not in the approved ASCII format."""


class DuplicateTemporaryDefinitionError(IdentityResolutionError):
    """One temporary reference defines more than one new object."""


class UndefinedTemporaryReferenceError(IdentityResolutionError):
    """A temporary reference was used without a definition in this output."""


class TemporaryReferenceNamespaceError(IdentityResolutionError):
    """A temporary reference was used in the wrong identity namespace."""


class TemporaryReferenceForbiddenError(IdentityResolutionError):
    """A temporary reference appeared in a non-identity field."""


class ForgedUUIDError(IdentityResolutionError):
    """A real UUID was not present in the current Working State."""


class ExistingObjectMutationError(IdentityResolutionError):
    """An existing object ID was used while changing its semantic content."""


class ContentIdentityError(IdentityResolutionError):
    """A new identity was allocated for unchanged semantic content."""


class DraftIdentityError(IdentityResolutionError):
    """Draft content and draft identity changed inconsistently."""


class ReviewIdentityError(IdentityResolutionError):
    """A REVIEW reused an existing review identity."""


class RejectedItemIdentityError(IdentityResolutionError):
    """A rejected item does not close over one pre-state effective object."""


class AuthoritativeObjectDeletionError(IdentityResolutionError):
    """A pre-state authoritative object disappeared without rejection."""


class DuplicateItemIdentityError(IdentityResolutionError):
    """One item identity appeared in more than one Working State location."""


class StageModelProposalV1(StrictModel):
    """Strict raw model result; ``post_state`` is resolved by the application."""

    model_config = ConfigDict(extra="forbid", strict=True)

    output_format_version: StrictInt
    run_control: RunControl
    target_stage: Stage
    transition_reason_code: Literal[
        "OWNER_INPUT_REQUIRED", "DIRECTION_CONFIRMED", "DIRECTION_INVALID",
        "MATERIAL_GAP", "MATERIAL_SUFFICIENT", "DRAFT_CREATED",
        "WRITING_REPAIR", "REVIEW_PASSED",
    ]
    director_message: StrictStr | None
    gate: GateResult | None
    review: TraceReview | None
    post_state: dict[str, Any]

    @field_validator("output_format_version")
    @classmethod
    def version_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only output_format_version 1 is supported")
        return value

    @field_validator("post_state")
    @classmethod
    def state_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if type(value) is not dict:
            raise ValueError("post_state must be one structured object")
        return value


class StageModelOutputV1(StrictModel):
    """The complete and only accepted Phase 1E model result shape."""

    model_config = ConfigDict(extra="forbid", strict=True)

    output_format_version: StrictInt
    run_control: RunControl
    target_stage: Stage
    transition_reason_code: Literal[
        "OWNER_INPUT_REQUIRED", "DIRECTION_CONFIRMED", "DIRECTION_INVALID",
        "MATERIAL_GAP", "MATERIAL_SUFFICIENT", "DRAFT_CREATED",
        "WRITING_REPAIR", "REVIEW_PASSED",
    ]
    director_message: StrictStr | None
    gate: GateResult | None
    review: TraceReview | None
    post_state: WorkingState

    @field_validator("output_format_version")
    @classmethod
    def version_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only output_format_version 1 is supported")
        return value


_TEMPORARY_REFERENCE_PATTERN = re.compile(r"new:(item|draft|review):([a-z][a-z0-9_]{0,63})")
_TEMPORARY_REFERENCE_PREFIX = "new:"
_ITEM_LISTS = {
    "owner_facts", "ai_judgments", "unconfirmed_inferences", "rejected_items",
    "owner_constraints",
}
_FORBIDDEN_TEMPORARY_ANCESTORS = {
    "evidence_refs", "rejected_by_evidence_refs", "inherited_from",
    "session_id", "target_session_id", "source_session_id", "message_id",
    "turn_id", "owner_message_id", "director_message_id", "ready_content_id",
    "based_on_ready_content_id",
}
_IDENTITY_OBJECT_KINDS = (
    "owner_facts", "owner_constraints", "ai_judgments", "unconfirmed_inferences",
    "rejected_items", "direction", "required_confirmations",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _without(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = deepcopy(value)
    result.pop(key, None)
    return result


def _context_working_state(context: Any) -> dict[str, Any]:
    to_dict = getattr(context, "to_dict", None)
    raw_state = to_dict()["working_state"] if callable(to_dict) else deepcopy(context.working_state)
    try:
        return WorkingState.model_validate(raw_state).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise IdentityResolutionError("current Working State is invalid before identity resolution") from exc


def _identity_spec(path: tuple[Any, ...]) -> tuple[str, bool] | None:
    """Return (namespace, is_definition) for one explicitly allowed identity field."""

    if not path:
        return None
    key = path[-1]
    parent = path[:-1]
    if key == "item_id":
        if len(parent) == 2 and parent[0] in _ITEM_LISTS and isinstance(parent[1], int):
            return "item", True
        if parent == ("direction",):
            return "item", True
        if len(parent) == 3 and parent[0] == "material_state" and parent[1] == "required_confirmations":
            return "item", True
    if key == "draft_id" and parent == ("draft",):
        return "draft", True
    if key == "review_id" and parent == ("review",):
        return "review", True
    if key == "superseded_by_item_id" and len(parent) == 2 and parent[0] == "rejected_items":
        return "item", False
    if key == "against_draft_id" and parent == ("review",):
        return "draft", False
    if key == "supersedes_item_ids" and len(parent) == 2 and parent[0] == "owner_facts":
        return "item", False
    if len(parent) >= 3 and parent[-1] == "supersedes_item_ids" and parent[-3] == "owner_facts":
        return "item", False
    return None


def _temporary_parts(value: str, path: tuple[Any, ...]) -> tuple[str, str]:
    match = _TEMPORARY_REFERENCE_PATTERN.fullmatch(value)
    if match is None:
        raise InvalidTemporaryReferenceError(
            f"invalid temporary reference format at {'.'.join(map(str, path))}"
        )
    return match.group(1), match.group(2)


def _walk_identity_values(value: Any) -> list[tuple[tuple[Any, ...], str, str, bool]]:
    occurrences: list[tuple[tuple[Any, ...], str, str, bool]] = []

    def visit(current: Any, path: tuple[Any, ...]) -> None:
        if isinstance(current, dict):
            for key, child in current.items():
                child_path = path + (key,)
                spec = _identity_spec(child_path)
                if isinstance(child, str) and child.startswith(_TEMPORARY_REFERENCE_PREFIX):
                    if spec is None:
                        if any(part in _FORBIDDEN_TEMPORARY_ANCESTORS for part in child_path):
                            raise TemporaryReferenceForbiddenError(
                                f"temporary reference is forbidden at {'.'.join(map(str, child_path))}"
                            )
                        raise TemporaryReferenceForbiddenError(
                            f"temporary reference is only allowed in identity fields: {'.'.join(map(str, child_path))}"
                        )
                    namespace, local_key = _temporary_parts(child, child_path)
                    expected, is_definition = spec
                    if namespace != expected:
                        raise TemporaryReferenceNamespaceError(
                            f"{child} uses namespace {namespace!r}, expected {expected!r} at {'.'.join(map(str, child_path))}"
                        )
                    occurrences.append((child_path, child, local_key, is_definition))
                else:
                    visit(child, child_path)
        elif isinstance(current, list):
            for index, child in enumerate(current):
                visit(child, path + (index,))
        elif isinstance(current, str) and current.startswith(_TEMPORARY_REFERENCE_PREFIX):
            spec = _identity_spec(path)
            if spec is None:
                if any(part in _FORBIDDEN_TEMPORARY_ANCESTORS for part in path):
                    raise TemporaryReferenceForbiddenError(
                        f"temporary reference is forbidden at {'.'.join(map(str, path))}"
                    )
                raise TemporaryReferenceForbiddenError(
                    f"temporary reference is only allowed in identity fields: {'.'.join(map(str, path))}"
                )
            namespace, local_key = _temporary_parts(current, path)
            expected, is_definition = spec
            if namespace != expected:
                raise TemporaryReferenceNamespaceError(
                    f"{current} uses namespace {namespace!r}, expected {expected!r} at {'.'.join(map(str, path))}"
                )
            occurrences.append((path, current, local_key, is_definition))

    visit(value, ())
    return occurrences


def _pre_identity_index(state: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    existing_ids_by_namespace: dict[str, set[str]] = {
        "item": set(),
        "draft": set(),
        "review": set(),
    }
    by_kind: dict[str, set[str]] = {kind: set() for kind in _IDENTITY_OBJECT_KINDS}
    for kind in ("owner_facts", "owner_constraints", "ai_judgments", "unconfirmed_inferences", "rejected_items"):
        for item in state[kind]:
            existing_ids_by_namespace["item"].add(item["item_id"])
            by_kind[kind].add(item["item_id"])
    if state["direction"] is not None:
        existing_ids_by_namespace["item"].add(state["direction"]["item_id"])
        by_kind["direction"].add(state["direction"]["item_id"])
    for item in state["material_state"]["required_confirmations"]:
        existing_ids_by_namespace["item"].add(item["item_id"])
        by_kind["required_confirmations"].add(item["item_id"])
    if state["draft"] is not None and state["draft"]["draft_id"] is not None:
        existing_ids_by_namespace["draft"].add(state["draft"]["draft_id"])
    if state["review"] is not None:
        existing_ids_by_namespace["review"].add(state["review"]["review_id"])
    return existing_ids_by_namespace, by_kind


def _working_state_item_occurrences(state: dict[str, Any]) -> list[tuple[str, str]]:
    occurrences: list[tuple[str, str]] = []
    for kind in ("owner_facts", "owner_constraints", "ai_judgments", "unconfirmed_inferences", "rejected_items"):
        occurrences.extend((kind, item["item_id"]) for item in state[kind])
    if state["direction"] is not None:
        occurrences.append(("direction", state["direction"]["item_id"]))
    occurrences.extend(
        ("required_confirmations", item["item_id"])
        for item in state["material_state"]["required_confirmations"]
    )
    return occurrences


def _validate_global_item_uniqueness(state: dict[str, Any]) -> None:
    seen: dict[str, str] = {}
    for kind, item_id in _working_state_item_occurrences(state):
        previous_kind = seen.get(item_id)
        if previous_kind is not None:
            raise DuplicateItemIdentityError(
                f"item_id {item_id} appears in both {previous_kind} and {kind}"
            )
        seen[item_id] = kind


_REJECTED_SOURCE_KINDS = {
    "OWNER_FACT": "owner_facts",
    "OWNER_CONSTRAINT": "owner_constraints",
    "DIRECTION": "direction",
    "AI_JUDGMENT": "ai_judgments",
    "UNCONFIRMED_INFERENCE": "unconfirmed_inferences",
}


def _source_items(state: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if kind == "direction":
        return [] if state["direction"] is None else [state["direction"]]
    return state[kind]


def _rejected_source_payload(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": source["item_id"],
        "statement": source["statement"],
        "evidence_refs": deepcopy(source.get("evidence_refs", [])),
        "inherited_from": deepcopy(source.get("inherited_from")),
    }


def _validate_rejected_item_closure(pre_state: dict[str, Any], post_state: dict[str, Any]) -> None:
    pre_rejected = {item["item_id"]: item for item in pre_state["rejected_items"]}
    post_rejected = {item["item_id"]: item for item in post_state["rejected_items"]}
    for item_id in pre_rejected:
        if item_id not in post_rejected:
            raise RejectedItemIdentityError(
                f"existing rejected item {item_id} cannot be deleted"
            )
    pre_active: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for kind in ("owner_facts", "owner_constraints", "direction", "ai_judgments", "unconfirmed_inferences"):
        for item in _source_items(pre_state, kind):
            pre_active.setdefault(item["item_id"], []).append((kind, item))

    for rejected in post_state["rejected_items"]:
        item_id = rejected["item_id"]
        if item_id in pre_rejected:
            if rejected != pre_rejected[item_id]:
                raise RejectedItemIdentityError(
                    f"existing rejected item {item_id} changed after rejection"
                )
            continue
        matches = pre_active.get(item_id, [])
        if len(matches) != 1:
            raise RejectedItemIdentityError(
                f"rejected item {item_id} must correspond to exactly one pre-state effective object"
            )
        source_kind, source = matches[0]
        expected_item_kind = next(
            item_kind for item_kind, mapped_kind in _REJECTED_SOURCE_KINDS.items()
            if mapped_kind == source_kind
        )
        if rejected["item_kind"] != expected_item_kind:
            raise RejectedItemIdentityError(
                f"rejected item {item_id} has item_kind {rejected['item_kind']!r}, expected {expected_item_kind!r}"
            )
        expected_payload = _rejected_source_payload(source)
        actual_payload = {
            "item_id": rejected["item_id"],
            "statement": rejected["statement"],
            "evidence_refs": rejected["evidence_refs"],
            "inherited_from": rejected["inherited_from"],
        }
        if actual_payload != expected_payload:
            raise RejectedItemIdentityError(
                f"rejected item {item_id} must retain the source identity, statement, evidence, and inheritance"
            )
        if any(
            active_item["item_id"] == item_id
            for kind in ("owner_facts", "owner_constraints", "ai_judgments", "unconfirmed_inferences")
            for active_item in post_state[kind]
        ) or (
            post_state["direction"] is not None
            and post_state["direction"]["item_id"] == item_id
        ):
            raise RejectedItemIdentityError(
                f"rejected item {item_id} must be removed from its effective location"
            )


def _validate_authoritative_object_retention(
    pre_state: dict[str, Any], post_state: dict[str, Any]
) -> None:
    post_rejected_ids = {item["item_id"] for item in post_state["rejected_items"]}
    for kind in ("owner_facts", "owner_constraints"):
        post_ids = {item["item_id"] for item in post_state[kind]}
        for item in pre_state[kind]:
            if item["item_id"] not in post_ids and item["item_id"] not in post_rejected_ids:
                raise AuthoritativeObjectDeletionError(
                    f"pre-state {kind} object {item['item_id']} disappeared without entering rejected_items"
                )

    before_direction = pre_state["direction"]
    if before_direction is not None:
        post_direction = post_state["direction"]
        retained = post_direction is not None and post_direction["item_id"] == before_direction["item_id"]
        rejected = before_direction["item_id"] in post_rejected_ids
        if not retained and not rejected:
            raise AuthoritativeObjectDeletionError(
                f"pre-state Direction {before_direction['item_id']} disappeared without entering rejected_items"
            )


def _resolve_identity_references(proposal_state: dict[str, Any], context: Any) -> dict[str, Any]:
    """Resolve model-local identities without allowing model-generated UUIDs."""

    pre_state = _context_working_state(context)
    occurrences = _walk_identity_values(proposal_state)
    definitions: dict[str, tuple[str, tuple[Any, ...]]] = {}
    for path, reference, _local_key, is_definition in occurrences:
        if not is_definition:
            continue
        if reference in definitions:
            raise DuplicateTemporaryDefinitionError(
                f"temporary reference is defined more than once: {reference}"
            )
        namespace = reference.split(":", 2)[1]
        definitions[reference] = (namespace, path)
    for path, reference, _local_key, is_definition in occurrences:
        if not is_definition and reference not in definitions:
            raise UndefinedTemporaryReferenceError(
                f"temporary reference is not defined in this model output: {reference}"
            )

    entered_stage = getattr(context, "stage", None)
    if entered_stage is None:
        entered_stage = context.stage_contract["stage"]
    if entered_stage != "REVIEW" and any(
        reference.split(":", 2)[1] == "review" and is_definition
        for _path, reference, _local_key, is_definition in occurrences
    ):
        raise ReviewIdentityError("only the REVIEW stage may create a new review")

    existing_ids_by_namespace, ids_by_kind = _pre_identity_index(pre_state)
    used_ids = set().union(*existing_ids_by_namespace.values())
    generated: dict[str, str] = {}
    generated_by_namespace: dict[str, set[str]] = {"item": set(), "draft": set(), "review": set()}
    for _path, reference, _local_key, is_definition in occurrences:
        if not is_definition or reference in generated:
            continue
        namespace = reference.split(":", 2)[1]
        while True:
            candidate = str(uuid4())
            if candidate not in used_ids:
                break
        generated[reference] = candidate
        generated_by_namespace[namespace].add(candidate)
        used_ids.add(candidate)

    def replace(current: Any, path: tuple[Any, ...]) -> Any:
        if isinstance(current, dict):
            return {key: replace(child, path + (key,)) for key, child in current.items()}
        if isinstance(current, list):
            return [replace(child, path + (index,)) for index, child in enumerate(current)]
        if isinstance(current, str):
            spec = _identity_spec(path)
            if spec is not None and current.startswith(_TEMPORARY_REFERENCE_PREFIX):
                return generated[current]
            return current
        return current

    resolved = replace(deepcopy(proposal_state), ())

    def check_real_identity(value: Any, path: tuple[Any, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                check_real_identity(child, path + (key,))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check_real_identity(child, path + (index,))
        elif isinstance(value, str):
            spec = _identity_spec(path)
            if spec is None or value.startswith(_TEMPORARY_REFERENCE_PREFIX):
                return
            expected, _is_definition = spec
            try:
                validate_uuid4(value)
            except (TypeError, ValueError) as exc:
                raise IdentityResolutionError(
                    f"invalid {expected} identity at {'.'.join(map(str, path))}"
                ) from exc
            if value in existing_ids_by_namespace[expected] or value in generated_by_namespace[expected]:
                return
            if expected == "item":
                raise ForgedUUIDError(
                    f"model supplied an item UUID not present in the current Working State: {value}"
                )
            if expected == "draft":
                raise ForgedUUIDError(
                    f"model supplied a draft UUID not present in the current Working State: {value}"
                )
            if expected == "review":
                raise ForgedUUIDError(
                    f"model supplied a review UUID not present in the current Working State: {value}"
                )

    check_real_identity(resolved, ())
    _validate_identity_stability(
        pre_state, resolved, context, ids_by_kind, generated_by_namespace
    )
    _validate_global_item_uniqueness(resolved)
    _validate_authoritative_object_retention(pre_state, resolved)
    _validate_rejected_item_closure(pre_state, resolved)
    return resolved


def _validate_identity_stability(
    pre_state: dict[str, Any],
    post_state: dict[str, Any],
    context: Any,
    ids_by_kind: dict[str, set[str]],
    generated_by_namespace: dict[str, set[str]],
) -> None:
    kind_fields = (
        ("owner_facts", "item_id"), ("owner_constraints", "item_id"),
        ("ai_judgments", "item_id"), ("unconfirmed_inferences", "item_id"),
    )
    for kind, identity_field in kind_fields:
        before = {item[identity_field]: item for item in pre_state[kind]}
        after = {item[identity_field]: item for item in post_state[kind]}
        for item_id, item in after.items():
            prior_kinds = {
                prior_kind for prior_kind, prior_ids in ids_by_kind.items() if item_id in prior_ids
            }
            if item_id in before:
                prior_kinds.discard(kind)
            if prior_kinds:
                raise ExistingObjectMutationError(
                    f"{kind} object {item_id} changes object type; upgraded objects require a new item ID"
                )
            if item_id in before and _canonical(_without(item, identity_field)) != _canonical(_without(before[item_id], identity_field)):
                raise ExistingObjectMutationError(
                    f"existing {kind} object {item_id} changed semantic content while retaining its ID"
                )
            if item_id not in before and _canonical(_without(item, identity_field)) in {
                _canonical(_without(candidate, identity_field)) for candidate in before.values()
            }:
                raise ContentIdentityError(
                    f"new {kind} object has unchanged content but a new ID"
                )

    before_direction = pre_state.get("direction")
    after_direction = post_state.get("direction")
    if after_direction is not None:
        prior_kinds = {
            prior_kind for prior_kind, prior_ids in ids_by_kind.items()
            if after_direction["item_id"] in prior_ids
        }
        if before_direction is not None and after_direction["item_id"] == before_direction["item_id"]:
            prior_kinds.discard("direction")
        if prior_kinds:
            raise ExistingObjectMutationError(
                "active Direction changes object type; upgraded objects require a new item ID"
            )
    if before_direction is not None and after_direction is not None:
        if after_direction["item_id"] == before_direction["item_id"]:
            if _canonical(_without(after_direction, "item_id")) != _canonical(_without(before_direction, "item_id")):
                raise ExistingObjectMutationError("existing direction changed semantic content while retaining its ID")
        elif _canonical(_without(after_direction, "item_id")) == _canonical(_without(before_direction, "item_id")):
            raise ContentIdentityError("direction content is unchanged but uses a new ID")

    before_confirmations = {item["item_id"]: item for item in pre_state["material_state"]["required_confirmations"]}
    after_confirmations = {item["item_id"]: item for item in post_state["material_state"]["required_confirmations"]}
    for item_id, item in after_confirmations.items():
        prior_kinds = {
            prior_kind for prior_kind, prior_ids in ids_by_kind.items() if item_id in prior_ids
        }
        if item_id in before_confirmations:
            prior_kinds.discard("required_confirmations")
        if prior_kinds:
            raise ExistingObjectMutationError(
                "RequiredConfirmation changes object type; it must use a new item ID"
            )
        if item_id in before_confirmations and _canonical(_without(item, "item_id")) != _canonical(_without(before_confirmations[item_id], "item_id")):
            raise ExistingObjectMutationError("existing required confirmation changed semantic content while retaining its ID")
        if item_id not in before_confirmations and _canonical(_without(item, "item_id")) in {
            _canonical(_without(candidate, "item_id")) for candidate in before_confirmations.values()
        }:
            raise ContentIdentityError("required confirmation content is unchanged but uses a new ID")

    before_draft = pre_state.get("draft")
    after_draft = post_state.get("draft")
    if before_draft is not None and after_draft is not None:
        before_content = _canonical(before_draft["content"])
        after_content = _canonical(after_draft["content"])
        before_id = before_draft.get("draft_id")
        after_id = after_draft.get("draft_id")
        if before_content == after_content:
            if before_id != after_id:
                raise DraftIdentityError("unchanged Draft content must retain its draft_id")
        else:
            if after_id is None:
                raise DraftIdentityError("changed Draft content must receive a new draft_id")
            if before_id is not None and before_id == after_id:
                raise DraftIdentityError("changed Draft content must receive a new draft_id")

    entered_stage = getattr(context, "stage", None)
    if entered_stage is None:
        entered_stage = context.stage_contract["stage"]
    if entered_stage == "REVIEW":
        after_review = post_state.get("review")
        if after_review is not None and after_review.get("review_id") not in generated_by_namespace["review"]:
            raise ReviewIdentityError("each REVIEW must generate a new review_id")
    elif pre_state.get("review") is not None and post_state.get("review") is not None:
        if post_state["review"] != pre_state["review"]:
            raise ReviewIdentityError(
                "a non-REVIEW stage may only preserve an existing Review unchanged"
            )



def _legal_owner_evidence_references(context: Any) -> set[tuple[str, str, str]]:
    references = getattr(context, "owner_evidence_references", ())
    return {
        (reference["evidence_type"], reference["target_id"], reference["target_session_id"])
        for reference in references
    }


def _require_confirmed_direction(state: WorkingState, context: Any) -> None:
    direction = state.direction
    if direction is None or direction.owner_confirmed is not True or not direction.evidence_refs:
        raise StageContractViolationError("progression requires an owner-confirmed Direction")


def _require_authorized_evidence(state: WorkingState, context: Any) -> None:
    legal_references = _legal_owner_evidence_references(context)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "evidence_type" in value:
                marker = (
                    value.get("evidence_type"),
                    value.get("target_id"),
                    value.get("target_session_id"),
                )
                if marker not in legal_references:
                    raise StageContractViolationError(
                        "Evidence must exactly match an authorized OWNER Evidence Reference in Model Context"
                    )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(state.model_dump(mode="json"))


def _require_review_matches_state(state: WorkingState, trace_review: TraceReview | None) -> None:
    state_review = state.review
    if state_review is None or trace_review is None:
        raise StageContractViolationError("REVIEW output requires a current Working State review")
    if (
        state_review.outcome != trace_review.outcome
        or state_review.root_cause != trace_review.root_cause
        or state.draft is None
        or state_review.against_draft_id != state.draft.draft_id
    ):
        raise StageContractViolationError(
            "trace review must match Working State review and current Draft"
        )


def _normalized_context_state(context: Any) -> WorkingState:
    to_dict = getattr(context, "to_dict", None)
    raw_state = to_dict()["working_state"] if callable(to_dict) else deepcopy(context.working_state)
    return WorkingState.model_validate(raw_state)


def _validate_state_requirements(
    requirements: dict[str, str],
    *,
    state: WorkingState,
    pre_state: WorkingState,
    trace_review: TraceReview | None,
    context: Any,
) -> None:
    if requirements["active_direction"] == "REQUIRED" and state.direction is None:
        raise StageContractViolationError("outcome requires an active Direction")
    if requirements["active_direction"] == "ABSENT" and state.direction is not None:
        raise StageContractViolationError("outcome requires the active Direction to be cleared")
    if requirements["confirmed_direction"] == "REQUIRED":
        _require_confirmed_direction(state, context)
    if requirements["material_status"] != "ANY" and (
        state.material_state.status != requirements["material_status"]
    ):
        raise StageContractViolationError(
            f"outcome requires {requirements['material_status']} material"
        )
    confirmations = state.material_state.required_confirmations
    if requirements["required_confirmations"] == "NON_EMPTY" and not confirmations:
        raise StageContractViolationError("outcome requires material confirmations")
    if requirements["required_confirmations"] == "EMPTY" and confirmations:
        raise StageContractViolationError("outcome requires no pending material confirmations")
    if requirements["draft"] == "REQUIRED" and state.draft is None:
        raise StageContractViolationError("outcome requires a Draft")
    if requirements["draft"] == "FINAL_CANDIDATE" and (
        state.draft is None
        or state.draft.draft_id is None
        or state.draft.content_status != "FINAL_CANDIDATE"
    ):
        raise StageContractViolationError("outcome requires one UUIDv4 FINAL_CANDIDATE Draft")
    if requirements["review"] == "ABSENT" and state.review is not None:
        raise StageContractViolationError("outcome requires review to be absent")
    if requirements["review"] == "MATCH_TRACE_AND_DRAFT":
        _require_review_matches_state(state, trace_review)
    if requirements["state_change"] == "REQUIRED" and (
        state.model_dump(mode="json") == pre_state.model_dump(mode="json")
    ):
        raise StageContractViolationError("outcome requires a meaningful Working State change")


def validate_stage_model_proposal(raw_output: Any) -> StageModelProposalV1:
    """Validate only the provider-neutral raw proposal boundary."""

    if not isinstance(raw_output, (dict, StageModelOutputV1, StageModelProposalV1)):
        raise StageModelOutputTypeError(
            "Stage model output must be one structured object; text, Markdown, and arrays are forbidden"
        )
    candidate = (
        raw_output.model_dump(mode="python", warnings=False)
        if isinstance(raw_output, (StageModelOutputV1, StageModelProposalV1))
        else deepcopy(raw_output)
    )
    try:
        return StageModelProposalV1.model_validate(candidate)
    except ValidationError as exc:
        raise StageModelProposalSchemaError("Stage model proposal failed strict v1 schema") from exc


def resolve_stage_model_proposal(
    proposal: StageModelProposalV1 | dict[str, Any], *, context: Any
) -> StageModelOutputV1:
    """Resolve identities and return the UUID-only internal Stage output."""

    validated = validate_stage_model_proposal(proposal)
    candidate = validated.model_dump(mode="python", warnings=False)
    try:
        candidate["post_state"] = _resolve_identity_references(validated.post_state, context)
    except IdentityResolutionError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise StageModelOutputSchemaError("Stage model output failed strict v1 schema") from exc
    try:
        return StageModelOutputV1.model_validate(candidate)
    except ValidationError as exc:
        raise StageModelOutputSchemaError("Stage model output failed strict v1 schema") from exc


def validate_stage_model_output(
    raw_output: Any,
    *,
    context: Any,
) -> StageModelOutputV1:
    """Resolve a raw proposal, then enforce the existing Stage business contract."""

    output = resolve_stage_model_proposal(raw_output, context=context)

    return validate_resolved_stage_model_output(output, context=context)


def validate_resolved_stage_model_output(
    output: StageModelOutputV1 | dict[str, Any],
    *,
    context: Any,
) -> StageModelOutputV1:
    """Validate an application-resolved output without re-running model IDs.

    ``semantic_only`` allocates formal identities in trusted application code.
    It still goes through the same envelope, evidence, state-requirement and
    Pydantic checks as the legacy model proposal path.
    """

    if not isinstance(output, StageModelOutputV1):
        try:
            output = StageModelOutputV1.model_validate(output)
        except ValidationError as exc:
            raise StageModelOutputSchemaError("resolved Stage output failed strict v1 schema") from exc

    entered_stage = getattr(context, "stage", None)
    if entered_stage is None:
        entered_stage = context.stage_contract["stage"]
    try:
        validate_outcome_envelope(
            entered_stage=entered_stage,
            run_control=output.run_control,
            target_stage=output.target_stage,
            transition_reason_code=output.transition_reason_code,
            director_message=output.director_message,
            gate=output.gate,
            review=output.review,
        )
        state = output.post_state
        _require_authorized_evidence(state, context)
        spec = outcome_spec(
            entered_stage,
            output.run_control,
            output.target_stage,
            output.transition_reason_code,
        )
        _validate_state_requirements(
            spec["state_requirements"],
            state=state,
            pre_state=_normalized_context_state(context),
            trace_review=output.review,
            context=context,
        )
    except StageContractViolationError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise StageContractViolationError("Stage output violates the business contract") from exc
    return output


__all__ = [
    "AuthoritativeObjectDeletionError",
    "ContentIdentityError",
    "DuplicateItemIdentityError",
    "DuplicateTemporaryDefinitionError",
    "DraftIdentityError",
    "ExistingObjectMutationError",
    "ForgedUUIDError",
    "IdentityResolutionError",
    "InvalidTemporaryReferenceError",
    "ReviewIdentityError",
    "RejectedItemIdentityError",
    "StageContractViolationError",
    "StageModelOutputError",
    "StageModelOutputSchemaError",
    "StageModelOutputTypeError",
    "StageModelProposalError",
    "StageModelProposalSchemaError",
    "StageModelProposalV1",
    "StageModelOutputV1",
    "TemporaryReferenceForbiddenError",
    "TemporaryReferenceNamespaceError",
    "UndefinedTemporaryReferenceError",
    "resolve_stage_model_proposal",
    "validate_resolved_stage_model_output",
    "validate_stage_model_output",
    "validate_stage_model_proposal",
]
