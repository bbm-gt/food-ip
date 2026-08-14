"""Scoped SQLite repository for the Director Core Phase 1A foundation."""

from __future__ import annotations

import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .canonical import (
    canonical_sha256,
    canonical_text,
    checkpoint_sha256,
    normalize_text,
    parse_canonical_object,
    state_sha256,
    validate_normalized_request,
)
from .database import require_foreign_keys
from .models import (
    ContextCheckpoint,
    FirstResponse,
    ReadyContent,
    TurnExecutionTrace,
    TurnPostStateSnapshot,
    validate_utc_millis,
    validate_turn_execution_trace,
    validate_uuid4,
    validate_working_state,
)


class DirectorNotFoundError(LookupError):
    """The requested resource is not visible inside the authorization scope."""


class DirectorIntegrityError(RuntimeError):
    """Persisted Director Core data failed canonical or contract validation."""


@dataclass(frozen=True)
class AuthorizationScope:
    workspace_id: str
    project_id: str

    def __post_init__(self) -> None:
        if not self.workspace_id or not self.project_id:
            raise ValueError("workspace_id and project_id are required")


@dataclass(frozen=True)
class SessionRecord:
    id: str
    workspace_id: str
    project_id: str
    source_ready_content_id: str | None
    lifecycle_status: str
    created_at: str
    ready_at: str | None


@dataclass(frozen=True)
class WorkingStateRecord:
    session_id: str
    state_version: int
    stage: str
    state_json: dict[str, Any]
    state_sha256: str
    latest_successful_turn_id: str | None
    updated_at: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _uuid4() -> str:
    return str(uuid4())


def _empty_state() -> dict[str, Any]:
    return {
        "format_version": 1,
        "owner_facts": [],
        "ai_judgments": [],
        "unconfirmed_inferences": [],
        "rejected_items": [],
        "owner_constraints": [],
        "direction": None,
        "material_state": {"status": "UNKNOWN", "required_confirmations": []},
        "draft": None,
        "review": None,
    }


class DirectorRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    def _write_ready(self) -> None:
        require_foreign_keys(self.connection)

    def create_session(self, scope: AuthorizationScope) -> SessionRecord:
        return self._create_session(scope, source_ready_content_id=None)

    def create_revision_session(
        self, scope: AuthorizationScope, source_ready_content_id: str
    ) -> SessionRecord:
        validate_uuid4(source_ready_content_id)
        return self._create_session(scope, source_ready_content_id=source_ready_content_id)

    def _create_session(
        self, scope: AuthorizationScope, source_ready_content_id: str | None
    ) -> SessionRecord:
        self._write_ready()
        session_id = _uuid4()
        created_at = _utc_now()
        state = _empty_state()

        if source_ready_content_id is not None:
            source = self._source_for_revision(scope, source_ready_content_id)
            source_state = source["state"].state_json
            inherited_from = {
                "source_ready_content_id": source_ready_content_id,
                "source_session_id": source["session"].id,
            }
            state["owner_facts"] = self._inherit_items(source_state["owner_facts"], inherited_from)
            state["owner_constraints"] = self._inherit_items(source_state["owner_constraints"], inherited_from)
            if source_state["direction"] is not None:
                state["direction"] = deepcopy(source_state["direction"])
                state["direction"]["inherited_from"] = deepcopy(inherited_from)
            state["draft"] = {
                "draft_id": None,
                "content": deepcopy(source["ready_content"]["final_content_json"]),
                "content_status": "WORKING",
                "based_on_ready_content_id": source_ready_content_id,
            }

        validated = validate_working_state(
            state,
            stage="EXPLORE",
            state_version=0,
            source_ready_content_id=source_ready_content_id,
        ).model_dump(mode="json")
        state_text = canonical_text(validated)
        digest = state_sha256(0, "EXPLORE", validated)

        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO director_sessions
                        (id, workspace_id, project_id, source_ready_content_id,
                         lifecycle_status, created_at, ready_at)
                    VALUES (?, ?, ?, ?, 'ACTIVE', ?, NULL)
                    """,
                    (session_id, scope.workspace_id, scope.project_id, source_ready_content_id, created_at),
                )
                self.connection.execute(
                    """
                    INSERT INTO director_working_state
                        (session_id, state_version, stage, state_json, state_sha256,
                         latest_successful_turn_id, updated_at)
                    VALUES (?, 0, 'EXPLORE', ?, ?, NULL, ?)
                    """,
                    (session_id, state_text, digest, created_at),
                )
        except sqlite3.DatabaseError:
            raise
        return self.get_session(scope, session_id)

    @staticmethod
    def _inherit_items(items: list[dict[str, Any]], inherited_from: dict[str, str]) -> list[dict[str, Any]]:
        result = deepcopy(items)
        for item in result:
            item["inherited_from"] = deepcopy(inherited_from)
        return result

    def _source_for_revision(
        self, scope: AuthorizationScope, ready_content_id: str
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT rc.*, s.id AS source_session_id
            FROM director_ready_content rc
            JOIN director_sessions s ON s.id = rc.session_id
            WHERE rc.id = ? AND s.workspace_id = ? AND s.project_id = ?
              AND s.lifecycle_status = 'READY'
            """,
            (ready_content_id, scope.workspace_id, scope.project_id),
        ).fetchone()
        if row is None:
            raise DirectorNotFoundError("ReadyContent is not visible in this scope")
        session = self.get_session(scope, row["source_session_id"])
        state = self.get_working_state(scope, session.id)
        content = self._validated_ready_content_row(row, expected_session_id=session.id)
        return {"session": session, "state": state, "ready_content": content}

    def get_session(self, scope: AuthorizationScope, session_id: str) -> SessionRecord:
        validate_uuid4(session_id)
        row = self.connection.execute(
            """SELECT * FROM director_sessions
               WHERE id = ? AND workspace_id = ? AND project_id = ?""",
            (session_id, scope.workspace_id, scope.project_id),
        ).fetchone()
        if row is None:
            raise DirectorNotFoundError("Director Session is not visible in this scope")
        validate_utc_millis(row["created_at"])
        if row["ready_at"] is not None:
            validate_utc_millis(row["ready_at"])
        return SessionRecord(**dict(row))

    def get_working_state(
        self, scope: AuthorizationScope, session_id: str
    ) -> WorkingStateRecord:
        session = self.get_session(scope, session_id)
        row = self.connection.execute(
            """
            SELECT ws.* FROM director_working_state ws
            JOIN director_sessions s ON s.id = ws.session_id
            WHERE ws.session_id = ? AND s.workspace_id = ? AND s.project_id = ?
            """,
            (session_id, scope.workspace_id, scope.project_id),
        ).fetchone()
        if row is None:
            raise DirectorIntegrityError("Director Session has no Working State")
        try:
            state = parse_canonical_object(row["state_json"])
            validated = validate_working_state(
                state,
                stage=row["stage"],
                state_version=row["state_version"],
                source_ready_content_id=session.source_ready_content_id,
            ).model_dump(mode="json")
            digest = state_sha256(row["state_version"], row["stage"], validated)
            if digest != row["state_sha256"]:
                raise DirectorIntegrityError("Working State hash mismatch")
            self._validate_state_turn_link(row)
            self._validate_evidence_closure(session, validated)
            self._validate_session_lifecycle(session, row)
            validate_utc_millis(row["updated_at"])
        except (ValueError, TypeError) as exc:
            if isinstance(exc, DirectorIntegrityError):
                raise
            raise DirectorIntegrityError("invalid Working State") from exc
        return WorkingStateRecord(
            session_id=row["session_id"],
            state_version=row["state_version"],
            stage=row["stage"],
            state_json=validated,
            state_sha256=row["state_sha256"],
            latest_successful_turn_id=row["latest_successful_turn_id"],
            updated_at=row["updated_at"],
        )

    def _validate_session_lifecycle(self, session: SessionRecord, state_row: sqlite3.Row) -> None:
        """Enforce the READY terminal state across the four authoritative records."""
        ready_row = self.connection.execute(
            "SELECT id FROM director_ready_content WHERE session_id = ?", (session.id,)
        ).fetchone()
        if state_row["stage"] == "READY":
            if session.lifecycle_status != "READY" or ready_row is None:
                raise DirectorIntegrityError("READY Working State requires READY Session and ReadyContent")
        elif session.lifecycle_status == "READY" or ready_row is not None:
            raise DirectorIntegrityError("READY Session lifecycle requires READY Working State")

    def _validate_state_turn_link(self, row: sqlite3.Row) -> None:
        if row["state_version"] == 0:
            if row["latest_successful_turn_id"] is not None:
                raise DirectorIntegrityError("version 0 cannot reference a successful Turn")
            return
        turn = self.connection.execute(
            """SELECT 1 FROM director_turns WHERE id = ? AND session_id = ?
               AND post_state_version = ? AND target_stage = ? AND post_state_sha256 = ?""",
            (
                row["latest_successful_turn_id"], row["session_id"], row["state_version"],
                row["stage"], row["state_sha256"],
            ),
        ).fetchone()
        if turn is None:
            raise DirectorIntegrityError("Working State latest Turn link is invalid")

    def _validate_turn_ready_closure(
        self, row: sqlite3.Row, response: dict[str, Any]
    ) -> None:
        session_row = self.connection.execute(
            """SELECT s.lifecycle_status, ws.stage, ws.latest_successful_turn_id,
                      ws.state_version
               FROM director_sessions s
               JOIN director_working_state ws ON ws.session_id = s.id
               WHERE s.id = ?""",
            (row["session_id"],),
        ).fetchone()
        if session_row is None:
            raise DirectorIntegrityError("Turn Session or Working State is missing")
        ready_content = self.connection.execute(
            """SELECT id, session_id, created_by_turn_id
               FROM director_ready_content
               WHERE id = ?""",
            (response.get("ready_content_id"),),
        ).fetchone() if response.get("ready_content_id") is not None else None
        if row["target_stage"] == "READY" or row["final_run_control"] == "READY":
            if row["target_stage"] != "READY" or row["final_run_control"] != "READY":
                raise DirectorIntegrityError("READY Turn top-level fields are inconsistent")
            if response["stage"] != "READY" or response["ready_content_id"] is None:
                raise DirectorIntegrityError("READY Turn response is incomplete")
            if ready_content is None:
                raise DirectorIntegrityError("READY Turn ReadyContent is missing")
            if ready_content["session_id"] != row["session_id"] or ready_content["created_by_turn_id"] != row["id"]:
                raise DirectorIntegrityError("READY Turn ReadyContent relationship is invalid")
            if not (
                session_row["lifecycle_status"] == "READY"
                and session_row["stage"] == "READY"
                and session_row["latest_successful_turn_id"] == row["id"]
                and session_row["state_version"] == row["post_state_version"]
            ):
                raise DirectorIntegrityError("READY Turn lifecycle is not closed")
        else:
            if response["ready_content_id"] is not None:
                raise DirectorIntegrityError("non-READY Turn cannot carry ReadyContent")
            current_turn_ready = self.connection.execute(
                "SELECT 1 FROM director_ready_content WHERE session_id = ? AND created_by_turn_id = ?",
                (row["session_id"], row["id"]),
            ).fetchone()
            if current_turn_ready is not None:
                raise DirectorIntegrityError("non-READY Turn cannot create ReadyContent")
            if session_row["latest_successful_turn_id"] == row["id"] and session_row["stage"] == "READY":
                raise DirectorIntegrityError("non-READY Turn cannot move its Working State to READY")

    def _validate_evidence_closure(
        self, session: SessionRecord, state: dict[str, Any]
    ) -> None:
        objects: list[tuple[str, dict[str, Any]]] = []
        objects.extend(("owner_facts", item) for item in state["owner_facts"])
        objects.extend(("owner_constraints", item) for item in state["owner_constraints"])
        if state["direction"] is not None:
            objects.append(("direction", state["direction"]))
        objects.extend(("rejected_items", item) for item in state["rejected_items"])
        objects.extend(("required_confirmations", item) for item in state["material_state"]["required_confirmations"])
        source_state: dict[str, Any] | None = None
        source_session_id: str | None = None
        for object_kind, item in objects:
            original_refs = list(item.get("evidence_refs", []))
            rejected_refs = list(item.get("rejected_by_evidence_refs", []))
            inherited = item.get("inherited_from")
            if inherited is not None:
                if session.source_ready_content_id is None:
                    raise DirectorIntegrityError("ordinary Session cannot contain inherited objects")
                if object_kind == "rejected_items":
                    source_kind = {
                        "OWNER_FACT": "owner_facts",
                        "OWNER_CONSTRAINT": "owner_constraints",
                        "DIRECTION": "direction",
                    }.get(item.get("item_kind"))
                    if source_kind is None:
                        raise DirectorIntegrityError("only rejected inherited Owner Fact, Constraint, or Direction is allowed")
                elif object_kind == "required_confirmations":
                    source_kind = None
                elif object_kind not in {"owner_facts", "owner_constraints", "direction"}:
                    raise DirectorIntegrityError("only active facts, constraints, and direction may be inherited")
                else:
                    source_kind = object_kind
                if inherited["source_ready_content_id"] != session.source_ready_content_id:
                    raise DirectorIntegrityError("inherited object does not name the direct ReadyContent")
                if source_state is None:
                    source_row = self.connection.execute(
                        """SELECT rc.session_id, ws.state_version, ws.stage, ws.state_json,
                                  ws.state_sha256, source.source_ready_content_id
                           FROM director_ready_content rc
                           JOIN director_sessions source ON source.id = rc.session_id
                           JOIN director_working_state ws ON ws.session_id = source.id
                           WHERE rc.id = ? AND source.lifecycle_status = 'READY'
                             AND source.workspace_id = ? AND source.project_id = ?""",
                        (session.source_ready_content_id, session.workspace_id, session.project_id),
                    ).fetchone()
                    if source_row is None:
                        raise DirectorIntegrityError("revision source ReadyContent is missing")
                    source_session_id = source_row["session_id"]
                    source_state = validate_working_state(
                        parse_canonical_object(source_row["state_json"]),
                        stage=source_row["stage"], state_version=source_row["state_version"],
                        source_ready_content_id=source_row["source_ready_content_id"],
                    ).model_dump(mode="json")
                    if source_row["stage"] != "READY" or state_sha256(
                        source_row["state_version"], source_row["stage"], source_state
                    ) != source_row["state_sha256"]:
                        raise DirectorIntegrityError("direct source final Working State is invalid")
                if inherited["source_session_id"] != source_session_id:
                    raise DirectorIntegrityError("inherited object does not name the producing Session")
                if object_kind == "required_confirmations":
                    candidates = []
                    for candidate_kind in ("owner_facts", "owner_constraints"):
                        candidates.extend(
                            (candidate_kind, candidate)
                            for candidate in source_state[candidate_kind]
                            if candidate["item_id"] == item["item_id"]
                        )
                    if source_state["direction"] is not None and source_state["direction"]["item_id"] == item["item_id"]:
                        candidates.append(("direction", source_state["direction"]))
                    if len(candidates) != 1:
                        raise DirectorIntegrityError("required confirmation must name one direct source Owner object")
                    _, source_item = candidates[0]
                    if not item["evidence_refs"] or item["statement"] != source_item["statement"]:
                        raise DirectorIntegrityError("inherited required confirmation differs from its direct source state")
                    if item["evidence_refs"] != source_item["evidence_refs"]:
                        raise DirectorIntegrityError("inherited required confirmation evidence differs from its direct source state")
                else:
                    source_items = [source_state[source_kind]] if source_kind == "direction" else source_state[source_kind]
                    if object_kind == "rejected_items":
                        matches = any(
                            source_item["item_id"] == item["item_id"]
                            and source_item["statement"] == item["statement"]
                            and source_item["evidence_refs"] == item["evidence_refs"]
                            for source_item in source_items
                        )
                    else:
                        candidate = deepcopy(item)
                        candidate.pop("inherited_from")
                        matches = any(
                            (lambda copy: (copy.pop("inherited_from", None), copy)[1])(deepcopy(source_item)) == candidate
                            for source_item in source_items
                        )
                    if not matches:
                        raise DirectorIntegrityError("inherited object differs from its direct source state")
            for reference in original_refs:
                target = self._validate_evidence_reference(
                    session, reference, allow_cross_session=inherited is not None
                )
                if inherited is None and target["session_id"] != session.id:
                    raise DirectorIntegrityError("non-inherited Evidence crosses Session")
            for reference in rejected_refs:
                target = self._validate_evidence_reference(session, reference, allow_cross_session=False)
                if target["session_id"] != session.id:
                    raise DirectorIntegrityError("rejection Evidence must belong to current Session")

    def _validate_evidence_reference(
        self,
        session: SessionRecord,
        reference: dict[str, Any],
        *,
        allow_cross_session: bool,
    ) -> sqlite3.Row:
        target = self.connection.execute(
            """
            SELECT m.*, s.workspace_id, s.project_id
            FROM director_messages m JOIN director_sessions s ON s.id = m.session_id
            WHERE m.id = ? AND m.session_id = ?
            """,
            (reference["target_id"], reference["target_session_id"]),
        ).fetchone()
        if target is None or target["visible_role"] != "OWNER":
            raise DirectorIntegrityError("Evidence does not resolve to an OWNER Message")
        if target["workspace_id"] != session.workspace_id or target["project_id"] != session.project_id:
            raise DirectorIntegrityError("Evidence crosses authorization scope")
        if not allow_cross_session and target["session_id"] != session.id:
            raise DirectorIntegrityError("Evidence crosses Session")
        self._validate_evidence_turn_pair(target)
        return target

    def _validate_evidence_turn_pair(self, owner_message: sqlite3.Row) -> None:
        """Validate the complete successful Turn visible to an Evidence reference.

        This deliberately does not call ``_validated_turn_row`` so Working State
        evidence validation cannot recurse through Turn validation.
        """
        turn = self.connection.execute(
            "SELECT * FROM director_turns WHERE id = ? AND session_id = ?",
            (owner_message["turn_id"], owner_message["session_id"]),
        ).fetchone()
        if turn is None:
            raise DirectorIntegrityError("Evidence Message does not belong to a Turn")
        try:
            validate_uuid4(turn["id"])
            validate_uuid4(turn["session_id"])
        except (TypeError, ValueError) as exc:
            raise DirectorIntegrityError("Evidence Turn identity is invalid") from exc
        if turn["post_state_version"] != turn["pre_state_version"] + 1:
            raise DirectorIntegrityError("Evidence Turn version chain is invalid")
        if turn["execution_format_version"] != 1 or turn["response_format_version"] != 1 or turn["snapshot_format_version"] != 1:
            raise DirectorIntegrityError("Evidence Turn format version is invalid")
        try:
            normalized_request = parse_canonical_object(turn["normalized_request_json"])
            validate_normalized_request(normalized_request)
            if canonical_sha256(normalized_request) != turn["request_sha256"]:
                raise DirectorIntegrityError("Evidence Turn request hash mismatch")
            pre_stage = "EXPLORE" if turn["pre_state_version"] == 0 else self.connection.execute(
                "SELECT target_stage FROM director_turns WHERE session_id = ? AND post_state_version = ?",
                (turn["session_id"], turn["pre_state_version"]),
            ).fetchone()
            if pre_stage is None:
                raise DirectorIntegrityError("Evidence Turn pre-state is missing")
            if not isinstance(pre_stage, str):
                pre_stage = pre_stage["target_stage"]
            validate_turn_execution_trace(
                parse_canonical_object(turn["execution_trace_json"]),
                pre_stage=pre_stage,
                final_run_control=turn["final_run_control"], target_stage=turn["target_stage"],
                transition_reason_code=turn["transition_reason_code"], gate_outcome=turn["gate_outcome"],
                review_root_cause=turn["review_root_cause"],
            )
            response = FirstResponse.model_validate(parse_canonical_object(turn["first_response_json"])).model_dump(mode="json")
            self._validate_turn_ready_closure(turn, response)
            snapshot = TurnPostStateSnapshot.model_validate(parse_canonical_object(turn["post_state_snapshot_json"])).model_dump(mode="json")
            if state_sha256(snapshot["state_version"], snapshot["stage"], snapshot["state_json"]) != turn["post_state_sha256"]:
                raise DirectorIntegrityError("Evidence Turn snapshot hash mismatch")
            if snapshot["state_version"] != turn["post_state_version"] or snapshot["stage"] != turn["target_stage"]:
                raise DirectorIntegrityError("Evidence Turn snapshot columns mismatch")
            if response["session_id"] != turn["session_id"] or response["turn_id"] != turn["id"]:
                raise DirectorIntegrityError("Evidence Turn response identity mismatch")
            if response["state_version"] != turn["post_state_version"] or response["stage"] != turn["target_stage"] or response["run_control"] != turn["final_run_control"]:
                raise DirectorIntegrityError("Evidence Turn response state mismatch")
        except (ValueError, TypeError) as exc:
            if isinstance(exc, DirectorIntegrityError):
                raise
            raise DirectorIntegrityError("invalid Evidence Turn") from exc
        messages = self.connection.execute(
            """SELECT id, visible_role, content, message_seq, turn_id
               FROM director_messages WHERE session_id = ? AND turn_id = ? ORDER BY message_seq""",
            (turn["session_id"], turn["id"]),
        ).fetchall()
        if len(messages) != 2 or messages[0]["visible_role"] != "OWNER" or messages[1]["visible_role"] != "DIRECTOR":
            raise DirectorIntegrityError("Evidence Message Turn is not a complete visible pair")
        if messages[0]["id"] != owner_message["id"] or messages[0]["turn_id"] != turn["id"]:
            raise DirectorIntegrityError("Evidence OWNER Message pairing is invalid")
        if messages[0]["message_seq"] != 2 * turn["post_state_version"] - 1 or messages[1]["message_seq"] != 2 * turn["post_state_version"]:
            raise DirectorIntegrityError("Evidence Message sequence mismatch")
        if response["owner_message_id"] != messages[0]["id"] or response["director_message_id"] != messages[1]["id"]:
            raise DirectorIntegrityError("Evidence Turn response Message identity mismatch")
        if response["director_message"] != messages[1]["content"]:
            raise DirectorIntegrityError("Evidence Turn response differs from DIRECTOR Message")
        if normalize_text(messages[0]["content"]) != normalized_request["owner_text"]:
            raise DirectorIntegrityError("Evidence OWNER Message differs from normalized request text")

    def find_successful_turn(
        self, scope: AuthorizationScope, session_id: str, client_message_id: str
    ) -> dict[str, Any] | None:
        self.get_session(scope, session_id)
        row = self.connection.execute(
            """SELECT t.* FROM director_turns t
               JOIN director_sessions s ON s.id = t.session_id
               WHERE t.session_id = ? AND t.client_message_id = ?
                 AND s.workspace_id = ? AND s.project_id = ?""",
            (session_id, client_message_id, scope.workspace_id, scope.project_id),
        ).fetchone()
        return None if row is None else self._validated_turn_row(row)

    def get_recent_successful_turns(
        self, scope: AuthorizationScope, session_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        self.get_session(scope, session_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = self.connection.execute(
            """SELECT t.* FROM director_turns t
               JOIN director_sessions s ON s.id = t.session_id
               WHERE t.session_id = ? AND s.workspace_id = ? AND s.project_id = ?
               ORDER BY t.post_state_version DESC LIMIT ?""",
            (session_id, scope.workspace_id, scope.project_id, limit),
        ).fetchall()
        return [self._validated_turn_row(row) for row in rows]

    def _validated_turn_row(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            normalized_request = parse_canonical_object(row["normalized_request_json"])
            if row["request_format_version"] != 1:
                raise DirectorIntegrityError("unsupported request format version")
            validate_normalized_request(normalized_request)
            if canonical_sha256(normalized_request) != row["request_sha256"]:
                raise DirectorIntegrityError("Turn request hash mismatch")
            if (
                row["execution_format_version"] != 1
                or row["response_format_version"] != 1
                or row["snapshot_format_version"] != 1
            ):
                raise DirectorIntegrityError("unsupported Turn JSON format version")
            pre_stage = "EXPLORE" if row["pre_state_version"] == 0 else self.connection.execute(
                "SELECT target_stage FROM director_turns WHERE session_id = ? AND post_state_version = ?",
                (row["session_id"], row["pre_state_version"]),
            ).fetchone()
            if pre_stage is None:
                raise DirectorIntegrityError("Turn pre-state is missing")
            if not isinstance(pre_stage, str):
                pre_stage = pre_stage["target_stage"]
            trace = validate_turn_execution_trace(
                parse_canonical_object(row["execution_trace_json"]),
                pre_stage=pre_stage,
                final_run_control=row["final_run_control"], target_stage=row["target_stage"],
                transition_reason_code=row["transition_reason_code"], gate_outcome=row["gate_outcome"],
                review_root_cause=row["review_root_cause"],
            ).model_dump(mode="json")
            response = FirstResponse.model_validate(
                parse_canonical_object(row["first_response_json"])
            ).model_dump(mode="json")
            self._validate_turn_ready_closure(row, response)
            snapshot = TurnPostStateSnapshot.model_validate(
                parse_canonical_object(row["post_state_snapshot_json"])
            ).model_dump(mode="json")
            digest = state_sha256(snapshot["state_version"], snapshot["stage"], snapshot["state_json"])
            if digest != row["post_state_sha256"]:
                raise DirectorIntegrityError("Turn post-state hash mismatch")
            if snapshot["state_version"] != row["post_state_version"] or snapshot["stage"] != row["target_stage"]:
                raise DirectorIntegrityError("Turn snapshot columns mismatch")
            if response["session_id"] != row["session_id"] or response["turn_id"] != row["id"]:
                raise DirectorIntegrityError("Turn response identity mismatch")
            if response["state_version"] != row["post_state_version"] or response["stage"] != row["target_stage"]:
                raise DirectorIntegrityError("Turn response state mismatch")
            if response["run_control"] != row["final_run_control"]:
                raise DirectorIntegrityError("Turn response run control mismatch")
            if trace["steps"][-1]["run_control"] != row["final_run_control"]:
                raise DirectorIntegrityError("Turn trace final control mismatch")
            messages = self.connection.execute(
                """SELECT id, visible_role, content, message_seq FROM director_messages
                   WHERE session_id = ? AND turn_id = ? ORDER BY message_seq""",
                (row["session_id"], row["id"]),
            ).fetchall()
            if len(messages) != 2 or messages[0]["visible_role"] != "OWNER" or messages[1]["visible_role"] != "DIRECTOR":
                raise DirectorIntegrityError("Turn does not have one complete visible message pair")
            if messages[0]["message_seq"] != 2 * row["post_state_version"] - 1 or messages[1]["message_seq"] != 2 * row["post_state_version"]:
                raise DirectorIntegrityError("Turn message sequence mismatch")
            if response["owner_message_id"] != messages[0]["id"] or response["director_message_id"] != messages[1]["id"]:
                raise DirectorIntegrityError("Turn response Message identity mismatch")
            if response["director_message"] != messages[1]["content"]:
                raise DirectorIntegrityError("Turn response differs from DIRECTOR Message")
            if normalize_text(messages[0]["content"]) != normalized_request["owner_text"]:
                raise DirectorIntegrityError("OWNER Message differs from normalized request text")
        except (ValueError, TypeError) as exc:
            if isinstance(exc, DirectorIntegrityError):
                raise
            raise DirectorIntegrityError("invalid successful Turn") from exc
        result = dict(row)
        result.update(
            normalized_request_json=normalized_request,
            execution_trace_json=trace,
            first_response_json=response,
            post_state_snapshot_json=snapshot,
        )
        return result

    def get_complete_message_turns(
        self, scope: AuthorizationScope, session_id: str
    ) -> list[dict[str, Any]]:
        self.get_session(scope, session_id)
        rows = self.connection.execute(
            """
            SELECT m.* FROM director_messages m
            JOIN director_sessions s ON s.id = m.session_id
            WHERE m.session_id = ? AND s.workspace_id = ? AND s.project_id = ?
            ORDER BY m.message_seq
            """,
            (session_id, scope.workspace_id, scope.project_id),
        ).fetchall()
        messages = [dict(row) for row in rows]
        if len(messages) % 2:
            raise DirectorIntegrityError("Raw Transcript contains a partial Turn")
        for index in range(0, len(messages), 2):
            owner, director = messages[index : index + 2]
            if owner["visible_role"] != "OWNER" or director["visible_role"] != "DIRECTOR":
                raise DirectorIntegrityError("Raw Transcript role order is invalid")
            if owner["turn_id"] != director["turn_id"]:
                raise DirectorIntegrityError("Raw Transcript Turn pairing is invalid")
        return [
            {"owner": messages[index], "director": messages[index + 1]}
            for index in range(0, len(messages), 2)
        ]

    def get_latest_valid_checkpoint(
        self, scope: AuthorizationScope, session_id: str
    ) -> dict[str, Any] | None:
        self.get_session(scope, session_id)
        rows = self.connection.execute(
            """
            SELECT cp.* FROM director_context_checkpoints cp
            JOIN director_sessions s ON s.id = cp.session_id
            WHERE cp.session_id = ? AND cp.status = 'VALID'
              AND s.workspace_id = ? AND s.project_id = ?
            ORDER BY cp.covered_through_seq DESC, cp.created_at DESC, cp.id DESC
            """,
            (session_id, scope.workspace_id, scope.project_id),
        ).fetchall()
        for row in rows:
            try:
                if row["format_version"] != 1:
                    continue
                payload = ContextCheckpoint.model_validate(
                    parse_canonical_object(row["checkpoint_json"])
                ).model_dump(mode="json")
                digest = checkpoint_sha256(
                    session_id, row["covered_through_seq"], payload, format_version=row["format_version"]
                )
                if digest != row["integrity_sha256"]:
                    continue
                if not self._checkpoint_references_are_valid(
                    session_id, row["covered_through_seq"], payload
                ):
                    continue
                result = dict(row)
                result["checkpoint_json"] = payload
                return result
            except (ValueError, TypeError):
                continue
        return None

    def _checkpoint_references_are_valid(
        self, session_id: str, covered_through_seq: int, payload: dict[str, Any]
    ) -> bool:
        boundary = self.connection.execute(
            """SELECT visible_role FROM director_messages
               WHERE session_id = ? AND message_seq = ?""",
            (session_id, covered_through_seq),
        ).fetchone()
        count = self.connection.execute(
            """SELECT count(*) FROM director_messages
               WHERE session_id = ? AND message_seq <= ?""",
            (session_id, covered_through_seq),
        ).fetchone()[0]
        if boundary is None or boundary["visible_role"] != "DIRECTOR" or count != covered_through_seq:
            return False
        for group_name in ("confirmed_owner_positions", "open_threads", "abandoned_directions"):
            for entry in payload[group_name]:
                for message_id in entry["message_refs"]:
                    row = self.connection.execute(
                        """SELECT visible_role, message_seq FROM director_messages
                           WHERE id = ? AND session_id = ?""",
                        (message_id, session_id),
                    ).fetchone()
                    if row is None or row["message_seq"] > covered_through_seq:
                        return False
                    if group_name == "confirmed_owner_positions" and row["visible_role"] != "OWNER":
                        return False
        return True

    def get_ready_content(
        self, scope: AuthorizationScope, ready_content_id: str
    ) -> dict[str, Any]:
        validate_uuid4(ready_content_id)
        row = self.connection.execute(
            """
            SELECT rc.* FROM director_ready_content rc
            JOIN director_sessions s ON s.id = rc.session_id
            WHERE rc.id = ? AND s.workspace_id = ? AND s.project_id = ?
            """,
            (ready_content_id, scope.workspace_id, scope.project_id),
        ).fetchone()
        if row is None:
            raise DirectorNotFoundError("ReadyContent is not visible in this scope")
        return self._validated_ready_content_row(row)

    def _validated_ready_content_row(
        self, row: sqlite3.Row, *, expected_session_id: str | None = None
    ) -> dict[str, Any]:
        if expected_session_id is not None and row["session_id"] != expected_session_id:
            raise DirectorIntegrityError("ReadyContent Session mismatch")
        try:
            if row["content_format_version"] != 1:
                raise DirectorIntegrityError("unsupported ReadyContent format version")
            payload = ReadyContent.model_validate(
                parse_canonical_object(row["final_content_json"])
            ).model_dump(mode="json")
            validate_uuid4(row["id"])
            validate_uuid4(row["created_by_turn_id"])
            validate_utc_millis(row["created_at"])
            session_row = self.connection.execute(
                """
                SELECT s.lifecycle_status, s.ready_at, ws.stage, ws.state_version,
                       ws.latest_successful_turn_id, ws.state_json, ws.state_sha256,
                       t.post_state_version, t.post_state_sha256, t.final_run_control,
                       t.target_stage, t.gate_outcome
                FROM director_sessions s
                JOIN director_working_state ws ON ws.session_id = s.id
                JOIN director_turns t ON t.id = ? AND t.session_id = s.id
                WHERE s.id = ?
                """,
                (row["created_by_turn_id"], row["session_id"]),
            ).fetchone()
            if session_row is None or session_row["lifecycle_status"] != "READY" or session_row["ready_at"] != row["created_at"]:
                raise DirectorIntegrityError("ReadyContent lifecycle mismatch")
            if not (
                session_row["stage"] == "READY"
                and session_row["latest_successful_turn_id"] == row["created_by_turn_id"]
                and session_row["state_version"] == session_row["post_state_version"]
                and session_row["state_sha256"] == session_row["post_state_sha256"]
                and session_row["final_run_control"] == "READY"
                and session_row["target_stage"] == "READY"
                and session_row["gate_outcome"] == "PASSED"
            ):
                raise DirectorIntegrityError("ReadyContent production Turn mismatch")
            scope = self.connection.execute(
                "SELECT workspace_id, project_id FROM director_sessions WHERE id = ?", (row["session_id"],)
            ).fetchone()
            if scope is None:
                raise DirectorIntegrityError("ReadyContent Session is missing")
            working = self.get_working_state(
                AuthorizationScope(scope["workspace_id"], scope["project_id"]), row["session_id"]
            )
            if working.stage != "READY" or working.latest_successful_turn_id != row["created_by_turn_id"]:
                raise DirectorIntegrityError("ReadyContent does not match the final Working State")
            turn = self._validated_turn_row(self.connection.execute(
                "SELECT * FROM director_turns WHERE id = ? AND session_id = ?",
                (row["created_by_turn_id"], row["session_id"]),
            ).fetchone())
            if turn["first_response_json"]["ready_content_id"] != row["id"]:
                raise DirectorIntegrityError("ReadyContent response ID mismatch")
            draft = working.state_json.get("draft")
            if not isinstance(draft, dict) or draft.get("content") != payload:
                raise DirectorIntegrityError("ReadyContent differs from reviewed draft")
        except (ValueError, TypeError) as exc:
            raise DirectorIntegrityError("invalid ReadyContent") from exc
        result = dict(row)
        result["final_content_json"] = payload
        return result
