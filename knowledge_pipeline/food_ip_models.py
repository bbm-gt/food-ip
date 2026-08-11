#!/usr/bin/env python3
"""
Food-IP Pydantic Models v2.0
=============================
Runtime source of truth for all Food-IP data types.
Pydantic v2 with extra="forbid" on all LLM-output models.

Design:
  - Deterministic identity: chunk_id / knowledge_id from content hash, not sequence
  - display_id (K000001 etc.) is human-readable only, NEVER used for dedup
  - Semantic validators enforce origin/pollution/timestamp rules
  - JSON Schema files are generated exports from these models

Usage:
  from food_ip_models import KnowledgeCard, CaseCard, ...
  card = KnowledgeCard.model_validate(parsed_dict)
"""

import hashlib
import re
from datetime import datetime
from enum import Enum
from typing import Optional, Any
from pydantic import (
    BaseModel, ConfigDict, Field, field_validator, model_validator,
)


# ============================================================================
# Deterministic Identity
# ============================================================================

def deterministic_id(*seed_parts: str, length: int = 12) -> str:
    """Stable content-based identity: sha256 hex digest prefix."""
    joined = "|".join(seed_parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def make_chunk_id(source_id: str, segment_id_min: str, segment_id_max: str) -> str:
    """Deterministic chunk ID from source + segment span."""
    return "CHK_" + deterministic_id(source_id, segment_id_min, segment_id_max)


def make_knowledge_id(source_id: str, chunk_id: str, knowledge_type: str,
                      core_idea: str) -> str:
    """Deterministic knowledge ID from source + chunk + type + normalized content."""
    normalized = re.sub(r'\s+', '', core_idea)[:200]
    return "KID_" + deterministic_id(source_id, chunk_id, knowledge_type, normalized)


def make_case_id(source_id: str, chunk_id: str, title: str) -> str:
    """Deterministic case ID."""
    normalized = re.sub(r'\s+', '', title)[:200]
    return "CID_" + deterministic_id(source_id, chunk_id, "case", normalized)


def make_anti_pattern_id(source_id: str, chunk_id: str, title: str) -> str:
    """Deterministic anti-pattern ID."""
    normalized = re.sub(r'\s+', '', title)[:200]
    return "AID_" + deterministic_id(source_id, chunk_id, "antipattern", normalized)


def make_format_id(source_id: str, chunk_id: str, name: str) -> str:
    """Deterministic creative format ID."""
    normalized = re.sub(r'\s+', '', name)[:200]
    return "FID_" + deterministic_id(source_id, chunk_id, "format", normalized)


def make_segment_id(source_id: str, idx: int) -> str:
    """Stable segment ID."""
    return f"{source_id}-SEG{idx:04d}"


# ============================================================================
# Enums
# ============================================================================

class KnowledgeType(str, Enum):
    PRINCIPLE = "principle"
    TECHNIQUE = "technique"
    CASE = "case"
    ANTI_PATTERN = "anti_pattern"
    CREATIVE_FORMAT = "creative_format"
    OPERATION = "operation"


class ContentStage(str, Enum):
    PLANNING = "planning"
    WRITING = "writing"
    SHOOTING = "shooting"
    REVIEW = "review"
    OPERATION = "operation"


class CTAType(str, Enum):
    NONE = "none"
    COMMENT = "comment"
    FOLLOW = "follow"
    VISIT = "visit"
    GROUP_BUY = "group_buy"
    OTHER = "other"


class RelationType(str, Enum):
    SAME = "same"
    SIMILAR = "similar"
    COMPLEMENTARY = "complementary"
    CONFLICTING = "conflicting"
    EXCEPTION = "exception"


class QualityStatus(str, Enum):
    GOOD = "good"
    POOR = "poor"
    VERY_POOR = "very_poor"
    UNKNOWN = "unknown"


class Origin(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    SYNTHESIZED = "synthesized"


class KnowledgeScope(str, Enum):
    METHODOLOGY = "methodology"
    SOURCE_CASE_FACT = "source_case_fact"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MatchMode(str, Enum):
    EXACT_PHRASE = "exact_phrase"
    REGEX = "regex"
    CONTEXT_REQUIRED = "context_required"


class SourceStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class ConflictResolution(str, Enum):
    AGREEMENT = "agreement"
    CONDITIONAL_DIFFERENCE = "conditional_difference"
    EXCEPTION = "exception"
    UNRESOLVED = "unresolved"


# ============================================================================
# Shared sub-models
# ============================================================================

class SourceRef(BaseModel):
    """Traceable source reference."""
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., pattern=r'^SRC\d{4}$')
    video_title: str = ""
    start_time: str = ""   # MM:SS or HH:MM:SS, derived from segments
    end_time: str = ""
    start_sec: float = 0.0
    end_sec: float = 0.0

    @field_validator("start_sec", "end_sec", mode="before")
    @classmethod
    def coerce_float(cls, v):
        return float(v) if v is not None else 0.0


# ============================================================================
# P0-3: ASR Segment Models
# ============================================================================

class WhisperSegment(BaseModel):
    """Raw segment from faster-whisper — NEVER modified after creation.
    Timestamps are machine-authoritative."""
    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(..., description="e.g. SRC0001-SEG0012")
    source_id: str = Field(..., pattern=r'^SRC\d{4}$')
    start_sec: float = Field(..., ge=0.0)
    end_sec: float = Field(..., ge=0.0)
    raw_text: str = Field(..., description="Whisper original text, never modified")

    @field_validator("start_sec", "end_sec", mode="before")
    @classmethod
    def coerce_float(cls, v):
        return float(v) if v is not None else 0.0

    @model_validator(mode="after")
    def start_before_end(self):
        if self.start_sec > self.end_sec:
            # Swap if reversed (should not happen from Whisper, but safe)
            self.start_sec, self.end_sec = self.end_sec, self.start_sec
        return self


class ASRSegment(WhisperSegment):
    """Segment with corrected text (glossary Layer 1 applied).
    raw_text is preserved from WhisperSegment. corrected_text is the fixed version.
    Glossary audit fields are part of the persisted runtime contract.
    """
    corrected_text: str = ""
    asr_fix_count: int = Field(default=0, ge=0)
    asr_fixes_applied: list[dict[str, Any]] = Field(default_factory=list)


# ============================================================================
# P0-5: Semantic Chunk
# ============================================================================

class SemanticChunk(BaseModel):
    """An independent knowledge unit covering one or more ASR segments.
    Timestamps are PROGRAMMATICALLY computed from segments, NEVER from LLM."""
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(..., description="Deterministic hash-based ID")
    source_id: str = Field(..., pattern=r'^SRC\d{4}$')
    segment_ids: list[str] = Field(..., min_length=1)
    knowledge_type_hint: KnowledgeType = KnowledgeType.TECHNIQUE
    brief: str = Field(default="", max_length=40)
    chunk_text: str = Field(..., min_length=10)
    start_sec: float = 0.0
    end_sec: float = 0.0
    start_time: str = ""
    end_time: str = ""
    retrieval_context: str = Field(default="", description="Reserved for future, can be empty")

    @field_validator("start_sec", "end_sec", mode="before")
    @classmethod
    def coerce_float(cls, v):
        return float(v) if v is not None else 0.0


# ============================================================================
# P0-8 Task 3: LLM Output Models (what the LLM actually outputs)
# ============================================================================
# LLM prompt does NOT ask for: knowledge_id, source, chunk_id,
# evidence_segment_ids, created_by_run_id, display_id
# These are programmatically assigned AFTER LLM output validation.
#
# Flow: LLM JSON → XxxLLMOutput (extra="forbid") → semantic validation
#       → programmatic ID/source/evidence assignment → Persisted Xxx → persist

class KnowledgeCardLLMOutput(BaseModel):
    """What the LLM outputs for a knowledge card. Does NOT include
    programmatically-assigned identity/provenance/audit fields."""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=120)
    knowledge_type: KnowledgeType = KnowledgeType.TECHNIQUE
    question_ids: list[str] = Field(default_factory=list)
    core_idea: str = Field(..., min_length=5)
    why_it_works: str = ""
    applicable_when: list[str] = Field(default_factory=list)
    not_applicable_when: list[str] = Field(default_factory=list)
    method: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    stages: list[ContentStage] = Field(default_factory=list)
    content_format: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    origin: Origin = Origin.INFERRED
    inference_basis: str = ""
    knowledge_scope: KnowledgeScope = KnowledgeScope.METHODOLOGY

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return 0.5
        return float(v)


class CaseCardLLMOutput(BaseModel):
    """What the LLM outputs for a case card."""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=120)
    restaurant_context: Optional[str] = None
    owner_context: Optional[str] = None
    original_problem: str = ""
    raw_content_opportunity: str = ""
    planner_insight: str = ""
    why_worth_watching: str = ""
    content_format: str = ""
    opening_mechanism: str = ""
    progression: list[str] = Field(default_factory=list)
    ip_expression: str = ""
    facts_used: list[str] = Field(default_factory=list)
    facts_not_used: list[str] = Field(default_factory=list)
    cta_type: CTAType = CTAType.NONE
    why_effective: list[str] = Field(default_factory=list)
    transferable_principles: list[str] = Field(default_factory=list)
    non_transferable_details: list[str] = Field(default_factory=list)
    related_question_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    origin: Origin = Origin.EXPLICIT
    knowledge_scope: KnowledgeScope = KnowledgeScope.SOURCE_CASE_FACT

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return 0.5
        return float(v)


class AntiPatternLLMOutput(BaseModel):
    """What the LLM outputs for an anti-pattern."""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=120)
    knowledge_type: KnowledgeType = KnowledgeType.ANTI_PATTERN
    question_ids: list[str] = Field(default_factory=list)
    core_idea: str = Field(..., min_length=5)
    why_it_works: str = ""
    anti_patterns: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    origin: Origin = Origin.INFERRED
    inference_basis: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return 0.5
        return float(v)


class CreativeFormatLLMOutput(BaseModel):
    """What the LLM outputs for a creative format."""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=120)
    knowledge_type: KnowledgeType = KnowledgeType.CREATIVE_FORMAT
    format_name: str = ""
    name: str = ""
    question_ids: list[str] = Field(default_factory=list)
    core_idea: str = ""
    best_for: list[str] = Field(default_factory=list)
    weak_for: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    origin: Origin = Origin.INFERRED

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return 0.5
        return float(v)


class QuestionSynthesisLLMOutput(BaseModel):
    """What the LLM outputs for question synthesis."""
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=10)
    decision_logic: list[str] = Field(default_factory=list)
    conditions: list[dict[str, str]] = Field(default_factory=list)
    common_mistakes: list[str] = Field(default_factory=list)
    related_cases: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)
    conflict_resolution: ConflictResolution = ConflictResolution.AGREEMENT
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return 0.5
        return float(v)


# ============================================================================
# P0-8: Persisted Models (include identity/provenance/audit fields)
# ============================================================================

class KnowledgeCard(BaseModel):
    """Atomic expert knowledge card. extra="forbid" to catch LLM hallucination."""
    model_config = ConfigDict(extra="forbid")

    knowledge_id: str = Field(..., description="Deterministic hash ID (KID_...)")
    display_id: str = Field(default="", description="Human-readable K000001")
    knowledge_type: KnowledgeType = KnowledgeType.TECHNIQUE
    title: str = Field(..., max_length=120)
    question_ids: list[str] = Field(default_factory=list)
    core_idea: str = Field(..., min_length=5)
    why_it_works: str = ""
    applicable_when: list[str] = Field(default_factory=list)
    not_applicable_when: list[str] = Field(default_factory=list)
    method: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    stages: list[ContentStage] = Field(default_factory=list)
    content_format: list[str] = Field(default_factory=list)
    source: SourceRef
    confidence: float = Field(..., ge=0.0, le=1.0)
    visual_evidence: list[dict[str, str]] = Field(default_factory=list)

    # P0-14: Epistemic status
    origin: Origin = Origin.INFERRED
    evidence_segment_ids: list[str] = Field(default_factory=list)
    inference_basis: str = ""
    source_knowledge_ids: list[str] = Field(default_factory=list)

    # P0-17: Knowledge scope
    knowledge_scope: KnowledgeScope = KnowledgeScope.METHODOLOGY

    # P0-18: Audit
    created_by_run_id: str = ""
    chunk_id: str = ""
    retrieval_context: str = Field(default="", description="Reserved, can be empty")

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return 0.5
        return float(v)

    @model_validator(mode="after")
    def validate_origin_evidence(self):
        """Semantic validation: origin must match evidence fields."""
        if self.origin == Origin.EXPLICIT:
            if not self.evidence_segment_ids:
                raise ValueError("origin='explicit' requires evidence_segment_ids")
        if self.origin == Origin.INFERRED:
            if not self.inference_basis:
                raise ValueError("origin='inferred' requires inference_basis")
            if not self.evidence_segment_ids:
                raise ValueError("origin='inferred' requires evidence_segment_ids")
        if self.origin == Origin.SYNTHESIZED:
            if not self.source_knowledge_ids:
                raise ValueError("origin='synthesized' requires source_knowledge_ids")
        return self

    @model_validator(mode="after")
    def validate_source_timestamps(self):
        if self.source.start_sec > self.source.end_sec and self.source.end_sec > 0:
            raise ValueError(
                f"source.start_sec ({self.source.start_sec}) > "
                f"source.end_sec ({self.source.end_sec})"
            )
        return self


# ============================================================================
# P0-8: CaseCard
# ============================================================================

class CaseCard(BaseModel):
    """Real restaurant case study card — preserves full context, not compressed."""
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., description="Deterministic hash ID (CID_...)")
    display_id: str = Field(default="", description="Human-readable C000001")
    title: str = Field(..., max_length=120)
    restaurant_context: Optional[str] = Field(default=None)
    owner_context: Optional[str] = Field(default=None)
    original_problem: str = ""
    raw_content_opportunity: str = ""
    planner_insight: str = ""
    why_worth_watching: str = ""
    content_format: str = ""
    opening_mechanism: str = ""
    progression: list[str] = Field(default_factory=list)
    ip_expression: str = ""
    facts_used: list[str] = Field(default_factory=list)
    facts_not_used: list[str] = Field(default_factory=list)
    cta_type: CTAType = CTAType.NONE
    why_effective: list[str] = Field(default_factory=list)
    transferable_principles: list[str] = Field(default_factory=list)
    non_transferable_details: list[str] = Field(default_factory=list)
    related_question_ids: list[str] = Field(default_factory=list)
    source: SourceRef
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # P0-17: Pollution boundary
    knowledge_scope: KnowledgeScope = KnowledgeScope.SOURCE_CASE_FACT
    transferable: bool = Field(default=False, description="Case facts are NOT transferable by default")

    # P0-14: Origin
    origin: Origin = Origin.EXPLICIT
    evidence_segment_ids: list[str] = Field(default_factory=list)

    # P0-18: Audit
    created_by_run_id: str = ""
    chunk_id: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return 0.5
        return float(v)

    @model_validator(mode="after")
    def validate_pollution_boundary(self):
        """Case facts must not be marked as transferable methodology."""
        if self.knowledge_scope == KnowledgeScope.SOURCE_CASE_FACT and self.transferable:
            raise ValueError(
                "Case facts with knowledge_scope='source_case_fact' must have transferable=false. "
                "Only abstracted methodology can be transferable."
            )
        return self

    @model_validator(mode="after")
    def validate_origin_evidence(self):
        if self.origin == Origin.EXPLICIT and not self.evidence_segment_ids:
            raise ValueError("origin='explicit' requires evidence_segment_ids")
        return self


# ============================================================================
# P0-8: AntiPattern
# ============================================================================

class AntiPattern(BaseModel):
    """Error mode / failure pattern with symptoms and repair direction."""
    model_config = ConfigDict(extra="forbid")

    anti_pattern_id: str = Field(..., description="Deterministic hash ID (AID_...)")
    display_id: str = Field(default="", description="Human-readable A000001")
    title: str = Field(..., max_length=120)
    symptoms: list[str] = Field(..., min_length=1)
    why_bad: str = Field(..., min_length=5)
    repair_direction: str = ""
    applicable_question_ids: list[str] = Field(default_factory=list)
    related_knowledge_ids: list[str] = Field(default_factory=list)
    source: SourceRef
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # P0-14
    origin: Origin = Origin.INFERRED
    evidence_segment_ids: list[str] = Field(default_factory=list)

    # P0-18
    created_by_run_id: str = ""
    chunk_id: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return 0.5
        return float(v)


# ============================================================================
# P0-8: CreativeFormat
# ============================================================================

class CreativeFormat(BaseModel):
    """Content expression format with planning/writing/shooting guidance."""
    model_config = ConfigDict(extra="forbid")

    format_id: str = Field(..., description="Deterministic hash ID (FID_...)")
    display_id: str = Field(default="", description="Human-readable F000001")
    name: str = Field(..., max_length=40)
    best_for: list[str] = Field(default_factory=list)
    weak_for: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    planning_guidance: str = ""
    writing_guidance: str = ""
    shooting_guidance: str = ""
    related_question_ids: list[str] = Field(default_factory=list)
    source: SourceRef = Field(default_factory=lambda: SourceRef(source_id="SRC0000"))
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # P0-14
    origin: Origin = Origin.INFERRED
    evidence_segment_ids: list[str] = Field(default_factory=list)

    # P0-18
    created_by_run_id: str = ""
    chunk_id: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return 0.5
        return float(v)


# ============================================================================
# Graph Models
# ============================================================================

class QuestionLink(BaseModel):
    """Link between a knowledge item and a question."""
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(..., pattern=r'^Q\d{3}$')
    knowledge_id: str
    relation: str = "answers"
    strength: float = Field(default=0.5, ge=0.0, le=1.0)


class KnowledgeRelation(BaseModel):
    """Relation between two knowledge items."""
    model_config = ConfigDict(extra="forbid")

    from_id: str
    to_id: str
    relation: RelationType
    note: str = ""
    can_merge: bool = False


class Conflict(BaseModel):
    """Conflict or exception between knowledge items. Never silently merged."""
    model_config = ConfigDict(extra="forbid")

    knowledge_a: str
    knowledge_b: str
    type: str  # "conflicting" or "exception"
    note: str = ""
    resolution: ConflictResolution = ConflictResolution.UNRESOLVED


# ============================================================================
# Synthesis Models
# ============================================================================

class QuestionSynthesis(BaseModel):
    """Synthesized answer from multiple knowledge sources."""
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(..., pattern=r'^Q\d{3}$')
    question: str = ""
    summary: str = Field(default="", min_length=10)
    decision_logic: list[str] = Field(default_factory=list)
    conditions: list[dict[str, str]] = Field(default_factory=list)
    common_mistakes: list[str] = Field(default_factory=list)
    related_cases: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    conflict_resolution: ConflictResolution = ConflictResolution.UNRESOLVED
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    origin: Origin = Origin.SYNTHESIZED
    source_knowledge_ids: list[str] = Field(default_factory=list)
    created_by_run_id: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return 0.5
        return float(v)

    @model_validator(mode="after")
    def validate_synthesized_origin(self):
        if self.origin == Origin.SYNTHESIZED and not self.source_knowledge_ids:
            raise ValueError("synthesized requires source_knowledge_ids")
        return self


class NewQuestionCandidate(BaseModel):
    """A new question discovered that doesn't exist in question_tree.json."""
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = ""
    question: str = Field(..., min_length=5)
    category: str = ""
    trigger_knowledge_id: str = ""
    trigger_chunk_id: str = ""
    reason: str = Field(default="", description="Why existing QIDs didn't match")
    suggested_category: str = ""
    created_by_run_id: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ============================================================================
# Config Models
# ============================================================================

class GlossaryEntry(BaseModel):
    """A single ASR correction rule."""
    model_config = ConfigDict(extra="forbid")

    wrong: str = Field(..., min_length=1)
    right: str = Field(..., min_length=1)
    category: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    match_mode: MatchMode = MatchMode.EXACT_PHRASE
    notes: str = ""

    @model_validator(mode="after")
    def wrong_not_equal_right(self):
        if self.wrong == self.right:
            raise ValueError(f"Glossary entry wrong==right ('{self.wrong}') — remove identity entry")
        return self


class QuestionEntry(BaseModel):
    """A question in the question tree."""
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(..., pattern=r'^Q\d{3}$')
    category: str = Field(..., min_length=1)
    question: str = Field(..., min_length=5)


# ============================================================================
# Manifest / Persistence Models
# ============================================================================

class SourceManifestEntry(BaseModel):
    """Per-source transcription manifest entry.

    This model is the runtime contract for manifests/by_source/SRCxxxx.json.
    ASR segments are the authoritative downstream segment source.
    """
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., pattern=r'^SRC\d{4}$')
    content_hash: str = Field(..., min_length=8, description="sha256 of video file")
    source_file: str = ""
    file_size: int = 0
    duration_sec: float = 0.0
    duration_str: str = ""
    title: str = ""
    pipeline_version: str = ""
    quality_status: QualityStatus = QualityStatus.UNKNOWN
    transcript: str = ""
    raw_transcript: str = ""
    keyframes_dir: Optional[str] = None
    keyframe_count: int = 0
    whisper_segments_path: str = ""
    asr_segments_path: str = ""
    segment_count: int = Field(default=0, ge=0)
    transcribed_at: str = ""
    processing_time_sec: int = Field(default=0, ge=0)


class SourceState(BaseModel):
    """Per-source processing state for crash recovery."""
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., pattern=r'^SRC\d{4}$')
    stage: str = Field(default="",
                       description="Pipeline stage owning this state (e.g. 'refine', 'transcribe')")
    status: SourceStatus = SourceStatus.PENDING
    run_id: str = ""
    pid: int = 0
    started_at: str = ""
    completed_at: str = ""
    error: str = ""
    stats: dict[str, int] = Field(default_factory=dict)


class RunAudit(BaseModel):
    """Run-level audit trail."""
    model_config = ConfigDict(extra="forbid")

    run_id: str
    pipeline_version: str
    model: str
    prompt_version: str = "3.0"
    question_tree_version: str = ""
    glossary_version: str = ""
    domain: str = "food-ip"
    started_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = ""
    sources_total: int = 0
    sources_completed: int = 0
    sources_failed: int = 0
    deferred_items: list[str] = Field(default_factory=list)
