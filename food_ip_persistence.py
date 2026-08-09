#!/usr/bin/env python3
"""
Food-IP Atomic Persistence Layer v1.0
=====================================
P0-7: Per-source atomic persistence with crash recovery.

Design:
  - Source truth: atomic/by_source/SRCxxxx/ (per-source directory)
  - Atomic writes: write .tmp → os.replace() (atomic on same filesystem)
  - Source state machine: pending → processing → done | failed
  - Stale recovery: status=processing with a dead/missing owner pid → reset to
    pending and reprocess; a processing marker with a LIVE owner is never preempted
  - Global JSONL files are rebuildable indices, NOT the source of truth

Usage:
  from food_ip_persistence import SourcePersistence, rebuild_global_indices
  sp = SourcePersistence(source_id)
  sp.start_processing(run_id)
  sp.save_chunks(chunks)
  sp.save_knowledge_cards(cards)
  sp.mark_done()
"""

import os
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

# Reuse config paths
import sys
sys.path.insert(0, str(Path(__file__).parent))
from food_ip_config import (
    ATOMIC_BY_SOURCE_DIR, PER_SOURCE_MANIFESTS_DIR,
    ATOMIC_DIR, GRAPH_DIR, SYNTHESIS_DIR,
    MANIFESTS_DIR, is_process_alive, ensure_dirs,
)

from pydantic import ValidationError
from food_ip_models import (
    SourceState, SourceStatus,
    SemanticChunk, KnowledgeCard, CaseCard, AntiPattern, CreativeFormat,
)


# Per-source refine artifacts mapped to their official persisted Pydantic
# models. A refine source is only treated as genuinely completed when every
# artifact exists AND every non-empty line:
#   1. parses as JSON,
#   2. validates against its persisted model (extra="forbid"),
#   3. belongs to THIS source (chunks carry source_id directly; cards nest it
#      under SourceRef.source_id).
#
#   chunks.jsonl            → SemanticChunk
#   knowledge_cards.jsonl   → KnowledgeCard
#   case_cards.jsonl        → CaseCard
#   anti_patterns.jsonl     → AntiPattern
#   creative_formats.jsonl  → CreativeFormat
#
# Empty files are allowed — a card type may legitimately have zero items. A
# missing file, corrupt line, schema/extra-field violation, or cross-source
# item all mean the completed marker has no trustworthy backing data and the
# source must be reprocessed — never silently skipped, never auto-repaired.
REFINE_ARTIFACTS = {
    "chunks.jsonl": (SemanticChunk, lambda m: m.source_id),
    "knowledge_cards.jsonl": (KnowledgeCard, lambda m: m.source.source_id),
    "case_cards.jsonl": (CaseCard, lambda m: m.source.source_id),
    "anti_patterns.jsonl": (AntiPattern, lambda m: m.source.source_id),
    "creative_formats.jsonl": (CreativeFormat, lambda m: m.source.source_id),
}


class StateOwnershipError(Exception):
    """The persisted state file does not belong to the current pipeline.

    Raised when the state file fails strict SourceState validation, or carries a
    source_id/stage that differs from the SourcePersistence reading it. This is
    state corruption / wrong ownership — fail fast rather than skip, reset, or
    fabricate state to keep running.
    """


def _default_pending_state(source_id: str, stage: str) -> dict:
    """Default pending state dict for a Source that has never been started.

    Used by the lenient ``load_state`` getter and by ``_load_validated_state``
    for the missing-file case (a Source is only 'pending' when its state file
    does not exist yet — never when the file exists but is corrupt).
    """
    return {
        "source_id": source_id,
        "stage": stage,
        "status": "pending",
        "run_id": "",
        "pid": 0,
        "started_at": "",
        "completed_at": "",
        "error": "",
        "stats": {},
    }


class SourcePersistence:
    """
    Atomic per-source persistence manager.

    Per-source directory structure:
      atomic/by_source/SRCxxxx/
        source_state.json             (generic / legacy state)
        source_state_refine.json      (refine stage — Round 2B-1)
        segments.json
        chunks.json
        knowledge_cards.json
        case_cards.json
        anti_patterns.json
        creative_formats.json
    """

    def __init__(self, source_id: str, stage: str = ""):
        self.source_id = source_id
        self.stage = stage
        self.source_dir = ATOMIC_BY_SOURCE_DIR / source_id
        self.source_dir.mkdir(parents=True, exist_ok=True)
        # Stage-scoped state file: independent pipeline stages (refine vs a
        # future transcribe) never read/write each other's lifecycle state.
        suffix = f"_{stage}" if stage else ""
        self._state_path = self.source_dir / f"source_state{suffix}.json"

    # ── State machine ──

    def load_state(self) -> dict:
        """Load current source state, or return default pending state.

        Lenient by design for state WRITERS (mark_done / mark_failed /
        reset_to_pending) and for a missing file (a Source never started). An
        existing but UNPARSEABLE file is only tolerated here — strict ownership
        and completion decisions go through ``_load_validated_state``, which
        treats an unparseable file as corruption and fails fast instead of
        silently treating it as pending.
        """
        if self._state_path.exists():
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return _default_pending_state(self.source_id, self.stage)

    def _save_state(self, state: dict):
        """Atomically save state file."""
        tmp = self.source_dir / f".{self._state_path.name}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(self._state_path)

    def _load_validated_state(self) -> SourceState:
        """Load and strictly validate the state file against SourceState,
        asserting it belongs to THIS SourcePersistence (source_id + stage).

        Raises StateOwnershipError on ANY of:
          - the file exists but cannot be parsed as JSON (corruption),
          - the file fails strict SourceState validation (extra="forbid"),
          - source_id / stage differ from this SourcePersistence.

        A done marker from a different source/stage — or a corrupt state file
        — must never be trusted, skipped, or silently reset to pending. Only a
        MISSING state file is treated as default pending (a Source that was
        never started). Corrupt state is never auto-deleted or auto-repaired;
        it fails fast so the corruption is visible.
        """
        if not self._state_path.exists():
            state = _default_pending_state(self.source_id, self.stage)
        else:
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, IOError) as exc:
                raise StateOwnershipError(
                    f"{self._state_path.name} exists but cannot be parsed "
                    f"as JSON: {exc}"
                ) from exc

        try:
            validated = SourceState.model_validate(state)
        except ValidationError as exc:
            raise StateOwnershipError(
                f"{self._state_path.name} fails strict SourceState validation: {exc}"
            ) from exc
        if validated.source_id != self.source_id:
            raise StateOwnershipError(
                f"{self._state_path.name} belongs to source "
                f"{validated.source_id!r}, expected {self.source_id!r}"
            )
        if validated.stage != self.stage:
            raise StateOwnershipError(
                f"{self._state_path.name} is for stage {validated.stage!r}, "
                f"expected {self.stage!r}"
            )
        return validated

    def start_processing(self, run_id: str) -> bool:
        """
        Transition to 'processing' state.
        If already 'done', returns False (skip).
        If previous 'processing' has stale pid, resets and returns True.

        Fail-fast: if the state file belongs to a different source/stage or
        fails strict SourceState validation, raises StateOwnershipError instead
        of silently skipping or overwriting foreign state.
        """
        self._load_validated_state()  # fail-fast on corrupt / foreign state
        state = self.load_state()

        if state["status"] == "done":
            return False  # Already completed

        if state["status"] == "processing":
            # P0-7 stale recovery: reclaim a processing marker whose owner is
            # gone. A dead recorded pid — or no recorded owner at all
            # (pid<=0) — means the previous run crashed, so reset and reprocess.
            # An old processing marker must never permanently block a Source.
            prev_pid = int(state.get("pid", 0) or 0)
            prev_run_id = state.get("run_id", "")
            if not is_process_alive(prev_pid):
                print(f"  [persist] Stale processing state detected (pid={prev_pid}, "
                      f"run={prev_run_id}) → resetting to pending")
                state["status"] = "pending"
                state["error"] = f"Reset from stale processing (pid={prev_pid} not alive)"
            else:
                # Process still running — never preempt another live run.
                print(f"  [persist] Source {self.source_id} is being processed by "
                      f"run={prev_run_id} (pid={prev_pid}) — refusing to preempt")
                return False  # Don't process

        state["status"] = "processing"
        state["stage"] = self.stage
        state["run_id"] = run_id
        state["pid"] = os.getpid()
        state["started_at"] = datetime.now().isoformat()
        self._save_state(state)
        return True

    def mark_done(self, stats: dict = None):
        """Transition to 'done' state."""
        state = self.load_state()
        state["status"] = "done"
        state["stage"] = self.stage
        state["completed_at"] = datetime.now().isoformat()
        if stats:
            state["stats"] = stats
        self._save_state(state)

    def mark_failed(self, error: str):
        """Transition to 'failed' state."""
        state = self.load_state()
        state["status"] = "failed"
        state["stage"] = self.stage
        state["completed_at"] = datetime.now().isoformat()
        state["error"] = error
        self._save_state(state)

    def reset_to_pending(self, error: str = ""):
        """Reset an invalid/stale state back to pending for reprocessing.

        Used when a completed marker exists but the required per-source data is
        missing — never trust a bare status field alone.
        """
        state = self.load_state()
        state["status"] = "pending"
        state["stage"] = self.stage
        state["run_id"] = ""
        state["pid"] = 0
        state["completed_at"] = ""
        if error:
            state["error"] = error
        self._save_state(state)

    def is_completed(self) -> bool:
        """True only when the stage-owned state is 'done' AND belongs to this
        source/stage.

        A mismatched or schema-invalid state raises StateOwnershipError instead
        of being (wrongly) treated as completed and skipped.
        """
        validated = self._load_validated_state()
        return validated.status == SourceStatus.DONE

    def refine_artifacts_complete(self) -> bool:
        """True only when every refine artifact exists, parses as JSON, validates
        against its persisted Pydantic model (extra="forbid"), and belongs to
        this source.

        Not a trust-the-flag check: a bare status=done marker is not enough. A
        missing file, corrupt line, schema/extra-field violation, or cross-source
        item means the completed state has no trustworthy backing data and the
        source must be reprocessed. Empty files are valid — a card type may
        legitimately have zero items.
        """
        return all(
            self._artifact_is_valid(name, model, owner)
            for name, (model, owner) in REFINE_ARTIFACTS.items()
        )

    def _artifact_is_valid(self, filename: str, model, owner) -> bool:
        """File exists and every non-empty line is a schema-valid, this-source item.

        Deliberately stricter than _load_jsonl (which skips corrupt lines for
        index rebuilds). For completion validation each line must:
          1. parse as JSON,
          2. validate against the artifact's persisted Pydantic model (extra="forbid"),
          3. carry THIS source's source_id.
        Any failure means we must not skip a source whose artifacts are damaged.
        """
        path = self.source_dir / filename
        if not path.is_file():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    item = model.model_validate(obj)
                    if owner(item) != self.source_id:
                        return False
        except (OSError, json.JSONDecodeError, ValidationError):
            return False
        return True

    # ── Data persistence (all atomic) ──

    def _save_atomic(self, filename: str, data: Any):
        """Generic atomic save: write to .tmp then os.replace()."""
        tmp_path = self.source_dir / f".{filename}.tmp"
        target_path = self.source_dir / filename

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        tmp_path.replace(target_path)

    def _save_jsonl_atomic(self, filename: str, items: list[dict]):
        """Atomic JSONL save."""
        tmp_path = self.source_dir / f".{filename}.tmp"
        target_path = self.source_dir / filename

        with open(tmp_path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        tmp_path.replace(target_path)

    def save_segments(self, segments: list[dict]):
        self._save_atomic("segments.json", {
            "source_id": self.source_id,
            "count": len(segments),
            "segments": segments,
        })

    def save_chunks(self, chunks: list[dict]):
        self._save_jsonl_atomic("chunks.jsonl", chunks)

    def save_knowledge_cards(self, cards: list[dict]):
        self._save_jsonl_atomic("knowledge_cards.jsonl", cards)

    def save_case_cards(self, cards: list[dict]):
        self._save_jsonl_atomic("case_cards.jsonl", cards)

    def save_anti_patterns(self, items: list[dict]):
        self._save_jsonl_atomic("anti_patterns.jsonl", items)

    def save_creative_formats(self, items: list[dict]):
        self._save_jsonl_atomic("creative_formats.jsonl", items)

    # ── Loaders ──

    def load_chunks(self) -> list[dict]:
        return self._load_jsonl("chunks.jsonl")

    def load_knowledge_cards(self) -> list[dict]:
        return self._load_jsonl("knowledge_cards.jsonl")

    def _load_jsonl(self, filename: str) -> list[dict]:
        path = self.source_dir / filename
        if not path.exists():
            return []
        items = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return items


# ============================================================================
# Global index rebuild
# ============================================================================

def rebuild_global_indices():
    """
    Rebuild all global JSONL files from per-source data.
    Called after each source completes, and at pipeline end.

    P0-Round2: also rebuilds a global chunks.jsonl so the per-source chunks
    written by food_ip_refine are reflected in the global index.
    """
    ensure_dirs()

    # Collect all per-source data
    all_chunks = []
    all_knowledge = []
    all_cases = []
    all_anti = []
    all_formats = []

    if ATOMIC_BY_SOURCE_DIR.exists():
        for src_dir in sorted(ATOMIC_BY_SOURCE_DIR.iterdir()):
            if not src_dir.is_dir():
                continue
            sp = SourcePersistence(src_dir.name)
            all_chunks.extend(sp.load_chunks())
            all_knowledge.extend(sp.load_knowledge_cards())
            all_cases.extend(sp._load_jsonl("case_cards.jsonl"))
            all_anti.extend(sp._load_jsonl("anti_patterns.jsonl"))
            all_formats.extend(sp._load_jsonl("creative_formats.jsonl"))

    # Atomic write for each global index
    def _write_jsonl(path, items):
        tmp = Path(str(path) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        tmp.replace(path)

    ATOMIC_DIR.mkdir(parents=True, exist_ok=True)
    _write_jsonl(ATOMIC_DIR / "chunks.jsonl", all_chunks)
    _write_jsonl(ATOMIC_DIR / "knowledge_cards.jsonl", all_knowledge)
    _write_jsonl(ATOMIC_DIR / "case_cards.jsonl", all_cases)
    _write_jsonl(ATOMIC_DIR / "anti_patterns.jsonl", all_anti)
    _write_jsonl(ATOMIC_DIR / "creative_formats.jsonl", all_formats)

    return {
        "chunks": len(all_chunks),
        "knowledge_cards": len(all_knowledge),
        "case_cards": len(all_cases),
        "anti_patterns": len(all_anti),
        "creative_formats": len(all_formats),
    }


def rebuild_sources_index():
    """Rebuild global sources.jsonl from per-source manifests."""
    ensure_dirs()
    sources = []

    if PER_SOURCE_MANIFESTS_DIR.exists():
        for mf in sorted(PER_SOURCE_MANIFESTS_DIR.glob("*.json")):
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    sources.append(json.load(f))
            except (json.JSONDecodeError, IOError):
                pass

    tmp = MANIFESTS_DIR / ".sources.jsonl.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for s in sources:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    tmp.replace(MANIFESTS_DIR / "sources.jsonl")
    return len(sources)


if __name__ == "__main__":
    ensure_dirs()
    print("food_ip_persistence module OK")
    print(f"  Per-source dir: {ATOMIC_BY_SOURCE_DIR}")
    print(f"  Global index: {ATOMIC_DIR}")
    print("  Write pattern: .tmp -> os.replace (atomic)")
    print("  Stale recovery: check pid alive before reset")
