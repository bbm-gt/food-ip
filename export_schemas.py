#!/usr/bin/env python3
"""
Export JSON Schemas from Pydantic Models v1.0
=============================================
One-time utility. Generates JSON Schema files from food_ip_models.py.

Usage:
  python export_schemas.py
  → writes to food_ip_schemas/ directory
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from food_ip_models import (
    KnowledgeCard, CaseCard, AntiPattern, CreativeFormat,
    QuestionSynthesis, SemanticChunk, ASRSegment, WhisperSegment,
    QuestionLink, KnowledgeRelation, Conflict,
    NewQuestionCandidate, SourceManifestEntry, RunAudit, SourceState,
)


SCHEMAS_DIR = Path(__file__).parent / "food_ip_schemas"


def export_schema(model_cls, filename):
    """Export a Pydantic model as JSON Schema."""
    schema = model_cls.model_json_schema()
    path = SCHEMAS_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    print(f"  {filename} ({len(json.dumps(schema))} bytes)")


def main():
    print("Exporting JSON Schemas from Pydantic models...\n")

    export_schema(KnowledgeCard, "knowledge_card.schema.json")
    export_schema(CaseCard, "case_card.schema.json")
    export_schema(AntiPattern, "anti_pattern.schema.json")
    export_schema(CreativeFormat, "creative_format.schema.json")
    export_schema(QuestionSynthesis, "question_synthesis.schema.json")
    export_schema(SemanticChunk, "semantic_chunk.schema.json")
    export_schema(ASRSegment, "asr_segment.schema.json")
    export_schema(WhisperSegment, "whisper_segment.schema.json")
    export_schema(QuestionLink, "question_link.schema.json")
    export_schema(KnowledgeRelation, "knowledge_relation.schema.json")
    export_schema(Conflict, "conflict.schema.json")
    export_schema(NewQuestionCandidate, "new_question_candidate.schema.json")
    export_schema(SourceManifestEntry, "source_manifest_entry.schema.json")
    export_schema(RunAudit, "run_audit.schema.json")
    export_schema(SourceState, "source_state.schema.json")

    print(f"\nDone: {SCHEMAS_DIR}")


if __name__ == "__main__":
    main()
