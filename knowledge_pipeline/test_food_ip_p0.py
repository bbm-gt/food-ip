#!/usr/bin/env python3
"""
Food-IP P0 Reliability Hardening — Test Suite
==============================================
60 automated tests covering all P0 categories.

Tasks 1-4 verification tests included:
  - Task 1: No legacy fallback when direct transcription fails
  - Task 2: ASRSegment production chain (raw_text + corrected_text)
  - Task 3: Directory rejection with WhisperModel.transcribe spy
  - Task 4: Fail-fast on Pydantic model import failure

Usage:
  cd knowledge_pipeline
  python test_food_ip_p0.py          # Run all tests
  python -m pytest test_food_ip_p0.py -v  # Verbose mode with pytest
"""

import json
import os
import sys
import tempfile
import hashlib
import unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, PropertyMock

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from food_ip_config import (
    compute_content_hash, validate_question_tree, validate_glossary,
    validate_all_config, generate_run_id, generate_deterministic_id,
    is_process_alive, PIPELINE_VERSION, DOMAIN, ensure_dirs,
    apply_asr_fixes, load_glossary, load_glossary_all, load_question_tree,
)
from robust_json_parser import parse_json
from food_ip_models import (
    KnowledgeCard, CaseCard, AntiPattern, CreativeFormat,
    KnowledgeRelation, QuestionSynthesis, SemanticChunk, ASRSegment, WhisperSegment,
    SourceRef, Conflict, NewQuestionCandidate,
    SourceManifestEntry, GlossaryEntry, QuestionEntry, SourceState,
    make_knowledge_id, make_chunk_id, make_segment_id,
    make_case_id, make_anti_pattern_id, make_format_id,
    deterministic_id,
    Origin, KnowledgeScope, KnowledgeType, SourceStatus, ConflictResolution,
)
from food_ip_segments import get_segment_time_range, fmt_time_range
from food_ip_persistence import SourcePersistence, StateOwnershipError


# ============================================================================
# Helpers
# ============================================================================

def _make_temp_file(content: bytes, suffix=".txt") -> Path:
    """Create a temp file with given content."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


# ============================================================================
# Config Tests (P0-1) — 3 tests
# ============================================================================

class TestConfigFailFast(unittest.TestCase):
    """P0-1: Config validation fails fast at pipeline startup."""

    def test_1_valid_question_tree(self):
        """Valid question tree passes validation."""
        errors = validate_question_tree()
        self.assertEqual(errors, [], f"Question tree has errors: {errors}")

    def test_2_valid_glossary(self):
        """Valid glossary passes validation."""
        errors = validate_glossary()
        self.assertEqual(errors, [], f"Glossary has errors: {errors}")

    def test_3_config_validation_does_not_raise_on_valid(self):
        """validate_all_config() succeeds on valid config."""
        # Should not raise SystemExit
        try:
            validate_all_config()
        except SystemExit:
            self.fail("validate_all_config() raised SystemExit on valid config")


class TestConfigFailFastInvalid(unittest.TestCase):
    """P0-1: Config validation FAILS FAST on invalid input.

    Covers the mandatory-test list items 1-3 from the task doc §13:
      - invalid JSON fail-fast
      - duplicate QID fail-fast
      - missing required field fail-fast

    These exercise the same validators as tests 1-3 but with BROKEN configs,
    asserting both the error list AND the SystemExit from validate_all_config().
    """

    def _write_question_tree(self, payload) -> str:
        """Write a throwaway question_tree.json and return its path."""
        f = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        if isinstance(payload, str):
            f.write(payload)
        else:
            json.dump(payload, f, ensure_ascii=False)
        f.close()
        return f.name

    def test_invalid_json_fails_fast(self):
        """Invalid JSON in question_tree.json → error + validate_all_config exits."""
        path = self._write_question_tree("{not valid json")
        try:
            with patch('food_ip_config.QUESTION_TREE_PATH', Path(path)):
                errors = validate_question_tree()
            self.assertTrue(errors, "broken JSON must produce validation errors")
            self.assertTrue(
                any("not valid JSON" in e for e in errors),
                f"expected JSON error, got: {errors}")
            with self.assertRaises(SystemExit):
                with patch('food_ip_config.QUESTION_TREE_PATH', Path(path)):
                    validate_all_config()
        finally:
            os.unlink(path)

    def test_duplicate_qid_fails_fast(self):
        """Duplicate question_id in question_tree.json → error + SystemExit."""
        path = self._write_question_tree({"questions": [
            {"question_id": "Q001", "question": "问题一怎么开头", "category": "开头"},
            {"question_id": "Q001", "question": "问题二怎么结尾", "category": "结尾"},
        ]})
        try:
            with patch('food_ip_config.QUESTION_TREE_PATH', Path(path)):
                errors = validate_question_tree()
            self.assertTrue(
                any("duplicate question_id" in e for e in errors),
                f"expected duplicate-QID error, got: {errors}")
            with self.assertRaises(SystemExit):
                with patch('food_ip_config.QUESTION_TREE_PATH', Path(path)):
                    validate_all_config()
        finally:
            os.unlink(path)

    def test_missing_required_field_fails_fast(self):
        """Missing required field (empty question / category) → error + SystemExit."""
        path = self._write_question_tree({"questions": [
            {"question_id": "Q001"},  # missing question + category
        ]})
        try:
            with patch('food_ip_config.QUESTION_TREE_PATH', Path(path)):
                errors = validate_question_tree()
            self.assertTrue(
                any("empty question text" in e for e in errors),
                f"expected missing-question error, got: {errors}")
            self.assertTrue(
                any("empty category" in e for e in errors),
                f"expected missing-category error, got: {errors}")
            with self.assertRaises(SystemExit):
                with patch('food_ip_config.QUESTION_TREE_PATH', Path(path)):
                    validate_all_config()
        finally:
            os.unlink(path)


# ============================================================================
# Source Identity Tests (P0-2) — 3 tests
# ============================================================================

class TestSourceIdentity(unittest.TestCase):
    """P0-2: Stable source identity via content hash."""

    def test_4_same_bytes_same_hash(self):
        """Identical content produces identical hash."""
        content = b"test video content"
        f1 = _make_temp_file(content)
        f2 = _make_temp_file(content)
        try:
            h1 = compute_content_hash(f1)
            h2 = compute_content_hash(f2)
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)  # SHA-256
        finally:
            f1.unlink(missing_ok=True)
            f2.unlink(missing_ok=True)

    def test_5_different_bytes_different_hash(self):
        """Different content produces different hash."""
        f1 = _make_temp_file(b"video A")
        f2 = _make_temp_file(b"video B")
        try:
            h1 = compute_content_hash(f1)
            h2 = compute_content_hash(f2)
            self.assertNotEqual(h1, h2)
        finally:
            f1.unlink(missing_ok=True)
            f2.unlink(missing_ok=True)

    def test_6_hash_independent_of_filename(self):
        """Hash depends on content, not filename."""
        content = b"same content"
        f1 = _make_temp_file(content)
        # Copy same content to different name
        f2 = Path(str(f1) + ".renamed")
        f2.write_bytes(content)
        try:
            h1 = compute_content_hash(f1)
            h2 = compute_content_hash(f2)
            self.assertEqual(h1, h2)
        finally:
            f1.unlink(missing_ok=True)
            f2.unlink(missing_ok=True)


# ============================================================================
# Provenance Tests (P0-3, P0-5) — 4 tests
# ============================================================================

class TestProvenance(unittest.TestCase):
    """P0-3/5: Segment provenance and time tracking."""

    def test_7_segment_time_range(self):
        """get_segment_time_range computes correct bounds."""
        segments = [
            {"segment_id": "SRC0001-SEG0001", "start_sec": 10.0, "end_sec": 20.0},
            {"segment_id": "SRC0001-SEG0002", "start_sec": 20.0, "end_sec": 35.0},
            {"segment_id": "SRC0001-SEG0003", "start_sec": 35.0, "end_sec": 50.0},
        ]
        start, end = get_segment_time_range(segments, ["SRC0001-SEG0001", "SRC0001-SEG0003"])
        self.assertEqual(start, 10.0)
        self.assertEqual(end, 50.0)

    def test_8_chunk_time_from_segments_not_llm(self):
        """Chunk timestamps derived from segments, not created by LLM."""
        segments = [
            {"segment_id": "SRC0001-SEG0001", "start_sec": 0.0, "end_sec": 15.5},
            {"segment_id": "SRC0001-SEG0002", "start_sec": 15.5, "end_sec": 30.0},
        ]
        # Simulate chunk referencing these segments
        start, end = get_segment_time_range(segments, ["SRC0001-SEG0001", "SRC0001-SEG0002"])
        self.assertEqual(start, 0.0)
        self.assertEqual(end, 30.0)
        # fmt_time_range produces MM:SS format
        time_range = fmt_time_range(start, end)
        self.assertIn("00:00", time_range)
        self.assertIn("00:30", time_range)

    def test_9_knowledge_traceable_to_source(self):
        """KnowledgeCard can trace back through chunk to source."""
        chunk_id = make_chunk_id("SRC0001", "SRC0001-SEG0001", "SRC0001-SEG0003")
        kid = make_knowledge_id("SRC0001", chunk_id, "technique", "test idea")
        # Same inputs should produce same IDs
        kid2 = make_knowledge_id("SRC0001", chunk_id, "technique", "test idea")
        self.assertEqual(kid, kid2)
        # Different content produces different ID
        kid3 = make_knowledge_id("SRC0001", chunk_id, "technique", "different idea")
        self.assertNotEqual(kid, kid3)

    def test_10_deterministic_id_stability(self):
        """Same inputs always produce same deterministic ID."""
        id1 = generate_deterministic_id("A", "B", "C")
        id2 = generate_deterministic_id("A", "B", "C")
        id3 = generate_deterministic_id("A", "B", "D")
        self.assertEqual(id1, id2)
        self.assertNotEqual(id1, id3)


# ============================================================================
# Resume/Crash Tests (P0-7) — 5 tests
# ============================================================================

class TestResumeCrash(unittest.TestCase):
    """P0-7: Crash recovery and resume."""

    def setUp(self):
        self.source_id = "SRC0999"
        self._temp_dir = tempfile.TemporaryDirectory()
        self._atomic_by_source = Path(self._temp_dir.name) / "atomic" / "by_source"
        self._patcher = patch(
            "food_ip_persistence.ATOMIC_BY_SOURCE_DIR", self._atomic_by_source
        )
        self._patcher.start()
        self.sp = SourcePersistence(self.source_id)

    def tearDown(self):
        # Cleanup test data
        import shutil
        self._patcher.stop()
        if self.sp.source_dir.exists():
            shutil.rmtree(self.sp.source_dir, ignore_errors=True)
        self._temp_dir.cleanup()

    def test_11_start_processing_transitions_state(self):
        """start_processing transitions pending→processing."""
        state_before = self.sp.load_state()
        self.assertEqual(state_before["status"], "pending")

        result = self.sp.start_processing("run_test_001")
        self.assertTrue(result)

        state_after = self.sp.load_state()
        self.assertEqual(state_after["status"], "processing")
        self.assertEqual(state_after["run_id"], "run_test_001")

    def test_12_mark_done_transitions_state(self):
        """mark_done transitions processing→done."""
        self.sp.start_processing("run_test_002")
        self.sp.mark_done({"chunks": 5})

        state = self.sp.load_state()
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["stats"]["chunks"], 5)

    def test_13_completed_source_not_rerun(self):
        """Completed source returns False on start_processing."""
        self.sp.start_processing("run_test_003")
        self.sp.mark_done()
        self.assertTrue(self.sp.is_completed())

        result = self.sp.start_processing("run_test_004")
        self.assertFalse(result)  # Should refuse to re-process

    def test_14_failed_source_can_be_recovered(self):
        """Failed source can be re-processed after reset."""
        self.sp.start_processing("run_test_005")
        self.sp.mark_failed("test error")
        self.assertFalse(self.sp.is_completed())

        # Manually reset to pending for recovery
        state = self.sp.load_state()
        state["status"] = "pending"
        self.sp._save_state(state)

        result = self.sp.start_processing("run_test_006")
        self.assertTrue(result)

    def test_15_no_duplicate_knowledge_same_run(self):
        """Same source re-processed in same run — deterministic IDs prevent duplicates."""
        chunk_id = make_chunk_id("SRC0999", "SRC0999-SEG0001", "SRC0999-SEG0002")
        kid1 = make_knowledge_id("SRC0999", chunk_id, "technique", "same idea")
        kid2 = make_knowledge_id("SRC0999", chunk_id, "technique", "same idea")
        self.assertEqual(kid1, kid2)  # Same inputs → same ID = dedup


# ============================================================================
# JSON/Validation Tests (P0-9, P0-8) — 7 tests
# ============================================================================

class TestJSONValidation(unittest.TestCase):
    """P0-9: Robust JSON parser. P0-8: Pydantic validation."""

    def test_16_nested_json(self):
        """Parse deeply nested JSON."""
        obj, err = parse_json('{"a": {"b": {"c": [1, 2, {"d": "e"}]}}}')
        self.assertIsNotNone(obj)
        self.assertIsNone(err)
        self.assertEqual(obj["a"]["b"]["c"][2]["d"], "e")

    def test_17_fenced_json(self):
        """Parse fenced ```json blocks."""
        obj, err = parse_json('```json\n{"x": 1, "y": 2}\n```')
        self.assertIsNotNone(obj)
        self.assertEqual(obj["x"], 1)

    def test_18_malformed_json(self):
        """Malformed JSON returns None + error."""
        obj, err = parse_json("this is not json")
        self.assertIsNone(obj)
        self.assertIsNotNone(err)

    def test_19_empty_content(self):
        """Empty input returns None + error."""
        obj, err = parse_json("")
        self.assertIsNone(obj)
        self.assertIsNotNone(err)

        obj, err = parse_json(None)
        self.assertIsNone(obj)
        self.assertIsNotNone(err)

    def test_20_wrong_type_validation(self):
        """Pydantic rejects wrong field types (extra="forbid")."""
        with self.assertRaises(Exception):
            KnowledgeCard.model_validate({
                "knowledge_id": "KID_test",
                "knowledge_type": "technique",
                "title": "Test",
                "core_idea": "A test idea",
                "source": {"source_id": "SRC0001"},
                "confidence": "not_a_number",  # Wrong type
            })

    def test_21_missing_required_field(self):
        """Pydantic rejects missing required fields."""
        with self.assertRaises(Exception):
            KnowledgeCard.model_validate({
                "title": "Test",
                # Missing: knowledge_id, core_idea, source, confidence
            })

    def test_22_braces_in_string(self):
        """P0-9 critical: braces inside JSON string values handled correctly."""
        obj, err = parse_json('{"text": "some {nested} braces here", "ok": true}')
        self.assertIsNotNone(obj)
        self.assertEqual(obj["text"], "some {nested} braces here")
        self.assertEqual(obj["ok"], True)

        # Multiple braces
        obj, err = parse_json('{"key": "a{b}c{d}e", "val": 1}')
        self.assertIsNotNone(obj)
        self.assertEqual(obj["key"], "a{b}c{d}e")

        # Closing brace in string
        obj, err = parse_json('{"msg": "this is } not close", "x": 1}')
        self.assertIsNotNone(obj)
        self.assertEqual(obj["msg"], "this is } not close")


# ============================================================================
# Glossary Tests (P0-12) — 4 tests
# ============================================================================

class TestSafeGlossary(unittest.TestCase):
    """P0-12: Safe glossary application."""

    def setUp(self):
        self.safe_glossary = load_glossary()
        self.all_glossary = load_glossary_all()

    def test_23_no_double_correction(self):
        """Already-correct text is not corrupted by glossary."""
        # "人物设定" should stay "人物设定"  — never become "人物设定定"
        # The glossary has "人物设" → "人物设定" but this is context_required, not auto
        text = "人物设定是老板IP的核心"
        corrected, count, applied = apply_asr_fixes(text, self.safe_glossary)
        self.assertIn("人物设定", corrected)
        self.assertNotIn("人物设定定", corrected)

    def test_24_context_required_not_auto_applied(self):
        """context_required entries not automatically applied."""
        context_entries = [e for e in self.all_glossary
                          if e.get("match_mode") == "context_required"]
        self.assertGreater(len(context_entries), 0,
                          "Should have at least one context_required entry")

        # Verify none of them are in the safe auto-apply list
        safe_wrongs = {w for w, r, e in self.safe_glossary}
        for entry in context_entries:
            self.assertNotIn(entry["wrong"], safe_wrongs,
                           f"context_required entry '{entry['wrong']}' found in safe list")

    def test_25_high_risk_not_auto_fixed(self):
        """High risk entries not in auto-apply list."""
        high_risk = [e for e in self.all_glossary if e.get("risk_level") == "high"]
        safe_wrongs = {w for w, r, e in self.safe_glossary}
        for entry in high_risk:
            self.assertNotIn(entry["wrong"], safe_wrongs)

    def test_26_low_risk_exact_phrase_applied(self):
        """Low risk exact_phrase entry correctly applied."""
        # Create a test with a known ASR error
        text = "这个视频的完播绿很重要"
        corrected, count, applied = apply_asr_fixes(text, self.safe_glossary)
        # 完播绿→完播率 should be applied
        self.assertIn("完播率", corrected)
        self.assertNotIn("完播绿", corrected)


# ============================================================================
# Origin Tests (P0-14) — 3 tests
# ============================================================================

class TestOriginValidation(unittest.TestCase):
    """P0-14: Epistemic origin semantic validation."""

    def test_27_explicit_origin_needs_evidence(self):
        """explicit origin requires evidence_segment_ids."""
        with self.assertRaises(Exception):
            KnowledgeCard.model_validate({
                "knowledge_id": "KID_test_001",
                "knowledge_type": "technique",
                "title": "Test",
                "core_idea": "A test idea",
                "source": {"source_id": "SRC0001"},
                "confidence": 0.9,
                "origin": "explicit",
                "evidence_segment_ids": [],  # Empty — should fail
            })

    def test_28_inferred_origin_needs_basis(self):
        """inferred origin requires inference_basis."""
        with self.assertRaises(Exception):
            KnowledgeCard.model_validate({
                "knowledge_id": "KID_test_002",
                "knowledge_type": "technique",
                "title": "Test",
                "core_idea": "A test idea",
                "source": {"source_id": "SRC0001"},
                "confidence": 0.7,
                "origin": "inferred",
                "evidence_segment_ids": ["SRC0001-SEG0001"],
                "inference_basis": "",  # Empty — should fail
            })

    def test_29_synthesized_origin_needs_sources(self):
        """synthesized origin requires source_knowledge_ids."""
        with self.assertRaises(Exception):
            QuestionSynthesis.model_validate({
                "question_id": "Q001",
                "summary": "A synthesis",
                "origin": "synthesized",
                "source_knowledge_ids": [],  # Empty — should fail
            })


# ============================================================================
# Conflict Tests (P0-13) — 2 tests
# ============================================================================

class TestConflictSynthesis(unittest.TestCase):
    """P0-13: Conflict handling in synthesis."""

    def test_30_conflict_preserved(self):
        """Conflicts are preserved, not silently merged."""
        conflict = Conflict(
            knowledge_a="KID_A",
            knowledge_b="KID_B",
            type="conflicting",
            note="Opposite advice on hook usage",
            resolution=ConflictResolution.UNRESOLVED,
        )
        self.assertEqual(conflict.resolution, ConflictResolution.UNRESOLVED)
        self.assertEqual(conflict.type, "conflicting")

    def test_31_unresolved_stays_unresolved(self):
        """Unresolved conflicts have resolution=unresolved."""
        conflict = Conflict(
            knowledge_a="KID_X",
            knowledge_b="KID_Y",
            type="exception",
            note="Different restaurant types",
            resolution=ConflictResolution.UNRESOLVED,
        )
        self.assertEqual(conflict.resolution, ConflictResolution.UNRESOLVED)
        # Not conditional_difference, not agreement
        self.assertNotEqual(conflict.resolution, ConflictResolution.AGREEMENT)


# ============================================================================
# Question Growth Tests (P0-15) — 2 tests
# ============================================================================

class TestQuestionGrowth(unittest.TestCase):
    """P0-15: Question tree growth."""

    def test_32_no_match_empty_question_ids(self):
        """When no QID matches, question_ids=[] is valid."""
        card = KnowledgeCard.model_validate({
            "knowledge_id": "KID_test_q",
            "knowledge_type": "technique",
            "title": "Test",
            "core_idea": "Something novel not in question tree",
            "source": {"source_id": "SRC0001"},
            "confidence": 0.85,
            "origin": "explicit",
            "evidence_segment_ids": ["SRC0001-SEG0005"],
            "question_ids": [],  # No match — valid
        })
        self.assertEqual(card.question_ids, [])

    def test_33_new_question_candidate_generated(self):
        """NewQuestionCandidate created for unmatched knowledge."""
        candidate = NewQuestionCandidate(
            question="How to make bubble tea videos?",
            category="",
            trigger_knowledge_id="KID_test",
            reason="Not covered by existing question tree",
        )
        self.assertIsNotNone(candidate.candidate_id)
        self.assertTrue(len(candidate.question) > 0)


# ============================================================================
# Pollution Boundary Tests (P0-17) — 2 tests
# ============================================================================

class TestPollutionBoundary(unittest.TestCase):
    """P0-17: Knowledge pollution boundary enforcement."""

    def test_34_case_fact_not_transferable(self):
        """source_case_fact with transferable=true should fail."""
        with self.assertRaises(Exception):
            CaseCard.model_validate({
                "case_id": "CID_test_001",
                "title": "Test case",
                "source": {"source_id": "SRC0001"},
                "knowledge_scope": "source_case_fact",
                "transferable": True,  # Should fail validation
            })

    def test_35_source_case_fact_defaults(self):
        """CaseCard defaults to source_case_fact with transferable=false."""
        card = CaseCard.model_validate({
            "case_id": "CID_test_002",
            "title": "Test case",
            "source": {"source_id": "SRC0001"},
            "origin": "explicit",
            "evidence_segment_ids": ["SRC0001-SEG0003"],
        })
        self.assertEqual(card.knowledge_scope, KnowledgeScope.SOURCE_CASE_FACT)
        self.assertFalse(card.transferable)


# ============================================================================
# Crash Atomicity Tests (P0-7) — 2 tests
# ============================================================================

class TestCrashAtomicity(unittest.TestCase):
    """P0-7: Atomic persistence."""

    def setUp(self):
        self.source_id = "SRC0998"
        self._temp_dir = tempfile.TemporaryDirectory()
        self._atomic_by_source = Path(self._temp_dir.name) / "atomic" / "by_source"
        self._patcher = patch(
            "food_ip_persistence.ATOMIC_BY_SOURCE_DIR", self._atomic_by_source
        )
        self._patcher.start()
        self.sp = SourcePersistence(self.source_id)

    def tearDown(self):
        import shutil
        self._patcher.stop()
        if self.sp.source_dir.exists():
            shutil.rmtree(self.sp.source_dir, ignore_errors=True)
        self._temp_dir.cleanup()

    def test_36_atomic_write_produces_valid_json(self):
        """Atomic write produces valid JSON."""
        test_data = [{"id": 1, "text": "test"}, {"id": 2, "text": "test2"}]
        self.sp.save_knowledge_cards(test_data)

        loaded = self.sp.load_knowledge_cards()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["id"], 1)

    def test_37_temp_file_not_left_as_target(self):
        """After atomic write, only target file exists, not .tmp."""
        self.sp.save_chunks([{"chunk_id": "CHK_test", "text": "hello"}])

        # Target exists
        target = self.sp.source_dir / "chunks.jsonl"
        self.assertTrue(target.exists())

        # No .tmp file left behind
        tmp_files = list(self.sp.source_dir.glob(".chunks.jsonl.tmp"))
        self.assertEqual(len(tmp_files), 0)

        # Content is valid JSONL
        with open(target, "r", encoding="utf-8") as f:
            line = f.readline()
            obj = json.loads(line)
            self.assertEqual(obj["chunk_id"], "CHK_test")


# ============================================================================
# Additional Tests
# ============================================================================

class TestPydanticExtraForbid(unittest.TestCase):
    """P0-8: extra='forbid' catches LLM hallucination."""

    def test_extra_fields_rejected(self):
        """Unknown fields in LLM output are rejected."""
        with self.assertRaises(Exception):
            KnowledgeCard.model_validate({
                "knowledge_id": "KID_test_extra",
                "knowledge_type": "technique",
                "title": "Test",
                "core_idea": "An idea",
                "source": {"source_id": "SRC0001"},
                "confidence": 0.9,
                "origin": "explicit",
                "evidence_segment_ids": ["SRC0001-SEG0001"],
                "hallucinated_field": "this should not be here",  # Extra field
            })

    def test_extra_fields_in_case_card_rejected(self):
        """CaseCard also rejects extra fields."""
        with self.assertRaises(Exception):
            CaseCard.model_validate({
                "case_id": "CID_test_extra",
                "title": "Test",
                "source": {"source_id": "SRC0001"},
                "origin": "explicit",
                "evidence_segment_ids": ["SRC0001-SEG0001"],
                "made_up_summary": "AI hallucinated this",  # Extra field
            })


# ============================================================================
# New Tests — Tasks 1-4 Verification
# ============================================================================

class TestWhisperPromptInjection(unittest.TestCase):
    """Task 1: Mock faster_whisper.WhisperModel, call transcribe_single_video(),
    assert initial_prompt == FOOD_IP_PROMPT, single-file input, native segment.start/end."""

    def setUp(self):
        self.temp_output_dir = tempfile.mkdtemp()
        self.temp_video = Path(self.temp_output_dir) / "test_video.mp4"
        self.temp_video.write_bytes(b"fake video bytes")

        # Inject fake faster_whisper module into sys.modules
        # (faster_whisper is NOT installed in this test environment)
        self._fake_faster_whisper = MagicMock()
        self._original_faster_whisper = sys.modules.get('faster_whisper')
        sys.modules['faster_whisper'] = self._fake_faster_whisper

    def tearDown(self):
        import shutil
        # Restore original faster_whisper module
        if self._original_faster_whisper is not None:
            sys.modules['faster_whisper'] = self._original_faster_whisper
        elif 'faster_whisper' in sys.modules:
            del sys.modules['faster_whisper']
        shutil.rmtree(self.temp_output_dir, ignore_errors=True)

    def test_mock_whisper_model_initial_prompt_reaches_transcribe(self):
        """Mock WhisperModel.transcribe, call transcribe_single_video(),
        assert initial_prompt=FOOD_IP_PROMPT and input is single file."""
        from food_ip_whisper_adapter import FOOD_IP_PROMPT
        from food_ip_direct_transcribe import transcribe_single_video

        # Build mock segments with Whisper-native .start/.end/.text
        class MockSegment:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        mock_segments = [
            MockSegment(0.0, 3.5, "餐饮短视频IP打造"),
            MockSegment(3.5, 8.2, "到店理由破解认知"),
            MockSegment(8.2, 15.0, "口播旁白实拍记录"),
        ]

        mock_info = MagicMock()
        mock_info.duration = 15.0
        mock_info.language = "zh"

        # The mock model captures transcribe() kwargs for assertion
        _last_kwargs = {}
        _last_audio_path = [None]

        class MockModel:
            def __init__(self, *args, **kwargs):
                self._init_kwargs = kwargs
            def transcribe(self, audio_path, **kwargs):
                _last_kwargs.clear()
                _last_kwargs.update(kwargs)
                _last_audio_path[0] = audio_path
                return iter(mock_segments), mock_info

        self._fake_faster_whisper.WhisperModel = MockModel

        # Patch WHISPER_SEGMENTS_DIR to use temp dir
        segments_dir = Path(self.temp_output_dir)
        segments_dir.mkdir(parents=True, exist_ok=True)
        with patch('food_ip_direct_transcribe.WHISPER_SEGMENTS_DIR', segments_dir):
            result = transcribe_single_video(
                self.temp_video,
                self.temp_output_dir,
                "SRC0001",
                device="cpu",
                compute_type="int8",
            )

        # ── Assertions ──
        self.assertIsNotNone(result, "transcribe_single_video must return a result dict")

        # 1. initial_prompt == FOOD_IP_PROMPT
        self.assertIn('initial_prompt', _last_kwargs,
                      "model.transcribe() must receive initial_prompt kwarg")
        self.assertEqual(_last_kwargs['initial_prompt'], FOOD_IP_PROMPT,
                         "initial_prompt must equal FOOD_IP_PROMPT")

        # 2. Input is a single file (str), NOT a directory
        self.assertIn(str(self.temp_video), _last_audio_path[0],
                      "model.transcribe() must receive single video file path, not directory")

        # 3. Native segment.start/end become ASRSegment time authority
        segments = result["segments"]
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0]["start_sec"], 0.0)
        self.assertEqual(segments[0]["end_sec"], 3.5)
        self.assertEqual(segments[0]["raw_text"], "餐饮短视频IP打造")
        self.assertEqual(segments[1]["start_sec"], 3.5)
        self.assertEqual(segments[1]["end_sec"], 8.2)
        self.assertEqual(segments[2]["start_sec"], 8.2)
        self.assertEqual(segments[2]["end_sec"], 15.0)
        self.assertEqual(segments[2]["raw_text"], "口播旁白实拍记录")

        # 4. Segment IDs are deterministic
        self.assertEqual(segments[0]["segment_id"], "SRC0001-SEG0001")
        self.assertEqual(segments[2]["segment_id"], "SRC0001-SEG0003")

        # 5. Result metadata
        self.assertEqual(result["source_id"], "SRC0001")
        self.assertEqual(result["segment_count"], 3)
        self.assertIn("markdown_path", result)

    def test_transcribe_single_video_rejects_directory_input(self):
        """transcribe_single_video must not accept a directory as input."""
        from food_ip_direct_transcribe import transcribe_single_video

        result = transcribe_single_video(
            Path(self.temp_output_dir),  # ← directory, not file
            self.temp_output_dir,
            "SRC0001",
        )
        self.assertIsNone(result,
                          "transcribe_single_video must return None for directory input")


class TestWhisperSegmentProvenance(unittest.TestCase):
    """Task 1: Whisper native segment.start/end → ASRSegment time authority."""

    def test_whisper_segment_timestamps_from_whisper(self):
        """ASRSegment times come from Whisper, not Markdown parsing."""
        # Simulate faster-whisper segments
        class MockSegment:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        mock_segments = [
            MockSegment(0.0, 5.2, "你好"),
            MockSegment(5.2, 12.8, "这是餐饮IP"),
            MockSegment(12.8, 20.1, "内容创作"),
        ]

        from food_ip_segments import extract_segments

        extracted = extract_segments(mock_segments, "SRC0001")
        self.assertEqual(len(extracted), 3)
        self.assertEqual(extracted[0]["start_sec"], 0.0)
        self.assertEqual(extracted[0]["end_sec"], 5.2)
        self.assertEqual(extracted[1]["start_sec"], 5.2)
        self.assertEqual(extracted[1]["end_sec"], 12.8)
        self.assertEqual(extracted[2]["start_sec"], 12.8)
        self.assertEqual(extracted[2]["end_sec"], 20.1)

        # Verify segment_id format
        self.assertEqual(extracted[0]["segment_id"], "SRC0001-SEG0001")
        self.assertEqual(extracted[1]["segment_id"], "SRC0001-SEG0002")
        self.assertEqual(extracted[2]["segment_id"], "SRC0001-SEG0003")

    def test_segment_timestamps_not_from_markdown(self):
        """Timestamps are NOT parsed from [[MM:SS]] markers."""
        from food_ip_segments import extract_segments

        # The extract_segments function ONLY accepts Segment objects with .start/.end
        # It does NOT accept markdown text. This is by design.
        # Attempting to pass markdown strings would fail
        with self.assertRaises(AttributeError):
            extract_segments(["[[00:05]] some text", "[[00:12]] more text"], "SRC0001")

    def test_asr_segment_preserves_raw_text(self):
        """ASRSegment.raw_text is Whisper original; corrected_text is after glossary."""
        seg = {
            "segment_id": "SRC0001-SEG0001",
            "source_id": "SRC0001",
            "start_sec": 10.0,
            "end_sec": 20.0,
            "raw_text": "原始文本",
        }
        from food_ip_segments import apply_glossary_to_segments

        def mock_glossary(text):
            return text.replace("原始", "修正"), 1, [{"wrong": "原始", "right": "修正"}]

        corrected = apply_glossary_to_segments([seg], mock_glossary)
        self.assertEqual(corrected[0]["raw_text"], "原始文本")  # PRESERVED
        self.assertEqual(corrected[0]["corrected_text"], "修正文本")  # CORRECTED
        self.assertEqual(corrected[0]["start_sec"], 10.0)  # TIMESTAMP PRESERVED
        self.assertEqual(corrected[0]["end_sec"], 20.0)


class TestLimitSingleVideo(unittest.TestCase):
    """Task 2: --limit 1 with 3 videos → run_transcription called exactly once with specific file."""

    def setUp(self):
        # Create temp dir with 3 fake video files
        self.temp_dir = tempfile.mkdtemp()
        self.video_paths = []
        for name in ["video_a.mp4", "video_b.mp4", "video_c.mp4"]:
            p = Path(self.temp_dir) / name
            p.write_bytes(b"fake video " + name.encode())
            self.video_paths.append(p)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_limit_1_calls_run_transcription_exactly_once(self):
        """Directory has 3 videos, --limit 1 → run_transcription called 1 time with specific file."""
        from food_ip_transcribe import scan_videos, main

        # Verify scan finds all 3
        scanned = scan_videos(self.temp_dir)
        self.assertEqual(len(scanned), 3, "scan_videos must find all 3 fake videos")

        limited = scanned[:1]
        self.assertEqual(len(limited), 1,
                         "--limit 1 must select exactly 1 video")
        self.assertEqual(limited[0], self.video_paths[0],
                         "--limit 1 must select the first video (sorted by name)")

        mock_calls = []

        def fake_run_transcription(video_path, output_dir, source_id=None,
                                   relaxed=False, preprocess=False, use_direct=True):
            mock_calls.append({
                "video_path": video_path,
                "source_id": source_id,
            })
            # Return a successful direct-transcription shape with a real ASR file.
            fake_md = Path(self.temp_dir) / f"{video_path.stem}.md"
            fake_md.write_text("# fake transcript", encoding="utf-8")
            fake_asr = Path(self.temp_dir) / f"{source_id}_asr_whisper_segments.json"
            fake_asr.write_text(json.dumps({
                "source_id": source_id,
                "segment_count": 1,
                "segments": [{
                    "segment_id": f"{source_id}-SEG0001",
                    "source_id": source_id,
                    "start_sec": 0.0,
                    "end_sec": 1.0,
                    "raw_text": "测试文本",
                    "corrected_text": "测试文本",
                    "asr_fix_count": 0,
                    "asr_fixes_applied": [],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            return fake_md, {
                "segment_count": 1,
                "asr_segments_path": str(fake_asr),
            }

        with patch('food_ip_transcribe.run_transcription',
                   side_effect=fake_run_transcription) as mock_rt:
            with patch('food_ip_transcribe.scan_videos', return_value=scanned):
                with patch('food_ip_transcribe.validate_all_config'):
                    with patch('food_ip_transcribe.ensure_dirs'):
                        with patch('food_ip_transcribe.load_glossary', return_value=[]):
                            with patch('food_ip_transcribe.compute_content_hash',
                                       side_effect=["hash_aaaa", "hash_bbbb", "hash_cccc"]):
                                with patch('food_ip_transcribe.load_manifest_index',
                                           return_value={}):
                                    with patch('food_ip_transcribe.find_or_create_source_id',
                                               side_effect=[
                                                   ("SRC0001", "hash_aaaa", True),
                                                   ("SRC0002", "hash_bbbb", True),
                                                   ("SRC0003", "hash_cccc", True),
                                               ]):
                                        with patch('food_ip_transcribe.assess_quality',
                                                   return_value="good"):
                                            with patch('food_ip_transcribe._get_duration',
                                                       return_value=60.0):
                                                with patch('food_ip_transcribe.transcode_to_food_ip',
                                                           return_value=Path(self.temp_dir) / "out.md"):
                                                    with patch('food_ip_transcribe.save_per_source_manifest') as mock_save_manifest:
                                                        with patch('food_ip_transcribe.rebuild_sources_index'):
                                                            with patch('food_ip_config.ensure_dirs'):
                                                                with patch('food_ip_transcribe.FLAGS_DIR',
                                                                           Path(self.temp_dir)):
                                                                    with patch('food_ip_transcribe.LOGS_DIR',
                                                                               Path(self.temp_dir)):
                                                                        with patch.object(
                                                                            sys, 'argv',
                                                                            ['food_ip_transcribe.py',
                                                                             '--input', str(self.temp_dir),
                                                                             '--limit', '1']
                                                                        ):
                                                                            main()

        # ── Assertions ──
        self.assertEqual(len(mock_calls), 1,
                         f"--limit 1 must call run_transcription exactly once, got {len(mock_calls)}")
        self.assertIn(str(self.video_paths[0]), str(mock_calls[0]["video_path"]),
                      "run_transcription must be called with the specific video file")
        self.assertNotEqual(str(mock_calls[0]["video_path"]), str(self.temp_dir),
                            "run_transcription must receive video file, not the parent directory")
        mock_save_manifest.assert_called_once()
        saved_manifest = mock_save_manifest.call_args.args[0]
        self.assertEqual(saved_manifest["asr_segments_path"],
                         "whisper_segments/SRC0001_asr_whisper_segments.json")
        self.assertEqual(saved_manifest["segment_count"], 1)

    def test_run_transcription_passes_specific_file_not_directory(self):
        """run_transcription --input line references video_path, NOT .parent."""
        import inspect
        from food_ip_transcribe import run_transcription

        source = inspect.getsource(run_transcription)
        input_lines = [l.strip() for l in source.split('\n') if '--input' in l]
        self.assertGreater(len(input_lines), 0,
                          "run_transcription must have a --input argument")
        for line in input_lines:
            self.assertIn("video_path", line)
            self.assertNotIn(".parent", line,
                             f"--input line must NOT use .parent: {line}")

    def test_direct_transcribe_accepts_single_file(self):
        """food_ip_direct_transcribe takes a single video file, not a directory."""
        import inspect
        from food_ip_direct_transcribe import transcribe_single_video

        source = inspect.getsource(transcribe_single_video)
        self.assertIn("video_path", source)
        self.assertIn("str(video_path)", source,
                      "transcribe_single_video must pass str(video_path) to model.transcribe()")


class TestLLMOutputModel(unittest.TestCase):
    """Task 3: LLM Output Model vs Persisted Model separation."""

    def test_normal_knowledge_card_json_passes_llm_output_model(self):
        """A normal LLM KnowledgeCard JSON passes LLMOutput model validation."""
        from food_ip_models import KnowledgeCardLLMOutput

        # This is what the LLM actually outputs (per the prompt template)
        llm_json = {
            "title": "钩子开头的三种方式",
            "knowledge_type": "technique",
            "question_ids": ["Q001", "Q005"],
            "core_idea": "开头3秒用冲突/反差/悬念抓住注意力",
            "why_it_works": "人的注意力机制对意外信息敏感",
            "applicable_when": ["短视频前3秒", "信息流广告"],
            "not_applicable_when": ["长视频深度内容"],
            "method": ["冲突开场", "反差开场", "数字悬念"],
            "examples": ["某餐饮号用'倒闭警告'开头获百万播放"],
            "anti_patterns": ["开头念菜单"],
            "stages": ["writing"],
            "content_format": ["口播", "短平快"],
            "confidence": 0.9,
            "origin": "explicit",
            "inference_basis": "",
            "knowledge_scope": "methodology",
        }

        # Must pass LLMOutput validation (no identity/provenance fields expected)
        validated = KnowledgeCardLLMOutput.model_validate(llm_json)
        self.assertEqual(validated.title, "钩子开头的三种方式")
        self.assertEqual(validated.knowledge_type, "technique")
        self.assertEqual(validated.core_idea, "开头3秒用冲突/反差/悬念抓住注意力")

    def test_llm_output_rejects_identity_fields(self):
        """LLMOutput model rejects fields the LLM shouldn't output."""
        from food_ip_models import KnowledgeCardLLMOutput

        # LLM should NOT output these fields — they're programmatic
        llm_json = {
            "title": "Test",
            "core_idea": "An idea",
            "knowledge_id": "KID_abc123",  # NOT in LLMOutput model → rejected
        }

        with self.assertRaises(Exception):
            KnowledgeCardLLMOutput.model_validate(llm_json)

    def test_programmatic_enrichment_passes_persisted_model(self):
        """After adding identity/provenance, the card passes full Persisted model."""
        from food_ip_models import (
            KnowledgeCardLLMOutput, KnowledgeCard,
            make_knowledge_id,
        )

        # Stage 1: LLM output
        llm_json = {
            "title": "钩子开头三种方式",
            "knowledge_type": "technique",
            "question_ids": ["Q001"],
            "core_idea": "开头用冲突抓住注意力",
            "why_it_works": "注意力机制",
            "confidence": 0.85,
            "origin": "explicit",
            "inference_basis": "",
            "knowledge_scope": "methodology",
        }
        llm_validated = KnowledgeCardLLMOutput.model_validate(llm_json)

        # Stage 2: Programmatic enrichment
        chunk_id = "CHK_test123"
        kid = make_knowledge_id("SRC0001", chunk_id, "technique", llm_json["core_idea"])
        display_id = "K000001"

        enriched = dict(llm_validated.model_dump())
        enriched["knowledge_id"] = kid
        enriched["display_id"] = display_id
        enriched["source"] = {
            "source_id": "SRC0001",
            "video_title": "Test Video",
            "start_sec": 10.0,
            "end_sec": 30.0,
        }
        enriched["chunk_id"] = chunk_id
        enriched["created_by_run_id"] = "run_test_001"
        enriched["evidence_segment_ids"] = ["SRC0001-SEG0001", "SRC0001-SEG0003"]
        enriched["retrieval_context"] = ""

        # Must pass full Persisted model
        persisted = KnowledgeCard.model_validate(enriched)
        self.assertEqual(persisted.knowledge_id, kid)
        self.assertEqual(persisted.display_id, display_id)
        self.assertEqual(persisted.source.source_id, "SRC0001")
        self.assertEqual(persisted.chunk_id, chunk_id)

    def test_case_card_llm_output_pass_through(self):
        """CaseCard LLM output → enrichment → persisted validation."""
        from food_ip_models import CaseCardLLMOutput, CaseCard, make_case_id

        llm_json = {
            "title": "某烧烤店IP打造案例",
            "original_problem": "没有到店理由",
            "content_format": "口播",
            "facts_used": ["老板东北人", "卖羊肉串15年"],
            "confidence": 0.85,
            "origin": "explicit",
            "knowledge_scope": "source_case_fact",
        }
        llm_validated = CaseCardLLMOutput.model_validate(llm_json)

        chunk_id = "CHK_case123"
        cid = make_case_id("SRC0001", chunk_id, llm_json["title"])

        enriched = dict(llm_validated.model_dump())
        enriched["case_id"] = cid
        enriched["display_id"] = "C000001"
        enriched["source"] = {
            "source_id": "SRC0001",
            "video_title": "Test",
        }
        enriched["chunk_id"] = chunk_id
        enriched["created_by_run_id"] = "run_test_001"
        enriched["evidence_segment_ids"] = ["SRC0001-SEG0001"]
        enriched["transferable"] = False

        persisted = CaseCard.model_validate(enriched)
        self.assertEqual(persisted.case_id, cid)
        self.assertFalse(persisted.transferable)


class TestExtraForbidNoBypass(unittest.TestCase):
    """Task 4: Unknown fields rejected, NOT silently stripped."""

    def test_hallucinated_field_rejected_by_llm_output_model(self):
        """LLM hallucinated field → rejected, NOT stripped."""
        from food_ip_models import KnowledgeCardLLMOutput

        llm_json = {
            "title": "钩子开头",
            "core_idea": "开头重要",
            "knowledge_type": "technique",
            "confidence": 0.9,
            "hallucinated_restaurant_fact": "老板来自东北",  # Hallucination!
        }

        # Must REJECT — extra="forbid" on LLMOutput model
        with self.assertRaises(Exception):
            KnowledgeCardLLMOutput.model_validate(llm_json)

    def test_no_strip_and_retry_in_parse_and_validate(self):
        """_parse_and_validate does NOT strip unknown fields and retry."""
        import inspect
        from food_ip_refine import FoodIPRefiner

        source = inspect.getsource(FoodIPRefiner._parse_and_validate)
        # Must NOT contain the old strip-and-retry pattern
        self.assertNotIn("cleaned = {k: v for k, v in obj.items()", source,
                         "_parse_and_validate must NOT strip unknown fields and retry")
        # Must contain the rejection message
        self.assertIn("REJECTED", source,
                      "_parse_and_validate must explicitly REJECT unknown fields")

    def test_case_card_hallucinated_field_rejected(self):
        """Case card with hallucinated field → rejected."""
        from food_ip_models import CaseCardLLMOutput

        llm_json = {
            "title": "Test Case",
            "confidence": 0.8,
            "hallucinated_competitor": "隔壁老王开了火锅店",  # Hallucination!
        }

        with self.assertRaises(Exception):
            CaseCardLLMOutput.model_validate(llm_json)


# ============================================================================
# Task 1: No Legacy Fallback — 1 test
# ============================================================================

class TestNoLegacyFallback(unittest.TestCase):
    """Task 1: Direct transcription failure must NOT auto-fallback to legacy subprocess."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_video = Path(self.temp_dir) / "test_video.mp4"
        self.temp_video.write_bytes(b"fake video bytes")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_direct_failure_does_not_call_legacy_subprocess(self):
        """When transcribe_single_video returns None, subprocess.run is NOT called."""
        from food_ip_transcribe import run_transcription

        subprocess_calls = []

        # transcribe_single_video is imported INSIDE run_transcription via
        #   from food_ip_direct_transcribe import transcribe_single_video
        # so we must patch the source module, not food_ip_transcribe
        with patch('food_ip_direct_transcribe.transcribe_single_video', return_value=None):
            with patch('subprocess.run', side_effect=lambda *a, **kw: subprocess_calls.append(1)) as mock_run:
                md_path, segments_result = run_transcription(
                    self.temp_video,
                    self.temp_dir,
                    source_id="SRC0001",
                    use_direct=True,
                )

        # ── Assertions ──
        self.assertIsNone(md_path,
                         "run_transcription must return None for failed direct transcription")
        self.assertIsNone(segments_result,
                         "segments_result must be None when direct transcription fails")
        self.assertEqual(len(subprocess_calls), 0,
                        f"subprocess.run must NOT be called; got {len(subprocess_calls)} call(s)")
        # Verify mock_run was never called (belt-and-suspenders)
        mock_run.assert_not_called()


# ============================================================================
# Task 2: ASRSegment Production Chain — 2 tests
# ============================================================================

class TestASRSegmentProductionChain(unittest.TestCase):
    """Task 2: ASRSegment production chain — raw_text preserved, corrected_text applied,
    timestamps immutable, same segment_id for downstream."""

    def setUp(self):
        self.temp_output_dir = tempfile.mkdtemp()
        self.temp_video = Path(self.temp_output_dir) / "test_video.mp4"
        self.temp_video.write_bytes(b"fake video bytes")

        # Inject fake faster_whisper module
        self._fake_faster_whisper = MagicMock()
        self._original_faster_whisper = sys.modules.get('faster_whisper')
        sys.modules['faster_whisper'] = self._fake_faster_whisper

    def tearDown(self):
        import shutil
        if self._original_faster_whisper is not None:
            sys.modules['faster_whisper'] = self._original_faster_whisper
        elif 'faster_whisper' in sys.modules:
            del sys.modules['faster_whisper']
        shutil.rmtree(self.temp_output_dir, ignore_errors=True)

    def test_asr_segment_raw_text_preserved_corrected_text_applied(self):
        """Whisper raw_text = A, glossary corrected = B →
        ASRSegment.raw_text == A, ASRSegment.corrected_text == B,
        start/end unchanged, segment_id consistent."""
        from food_ip_direct_transcribe import transcribe_single_video
        from food_ip_segments import apply_glossary_to_segments

        # Build mock segments with known ASR errors that the glossary would fix
        class MockSegment:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        mock_segments = [
            MockSegment(0.0, 5.0, "这个视频的完播绿很重要"),
            MockSegment(5.0, 10.0, "人物设要清晰"),
            MockSegment(10.0, 15.0, "到店理有很关键"),
        ]

        mock_info = MagicMock()
        mock_info.duration = 15.0
        mock_info.language = "zh"

        class MockModel:
            def __init__(self, *args, **kwargs):
                pass
            def transcribe(self, audio_path, **kwargs):
                return iter(mock_segments), mock_info

        self._fake_faster_whisper.WhisperModel = MockModel

        # Mock glossary that fixes known ASR errors
        mock_glossary_items = [
            ("完播绿", "完播率", {"risk_level": "low", "match_mode": "exact_phrase"}),
            ("人物设", "人物设定", {"risk_level": "low", "match_mode": "exact_phrase"}),
            ("到店理有", "到店理由", {"risk_level": "low", "match_mode": "exact_phrase"}),
        ]

        segments_dir = Path(self.temp_output_dir)
        segments_dir.mkdir(parents=True, exist_ok=True)
        with patch('food_ip_direct_transcribe.WHISPER_SEGMENTS_DIR', segments_dir):
            with patch('food_ip_direct_transcribe.load_glossary', return_value=mock_glossary_items):
                with patch('food_ip_direct_transcribe.apply_asr_fixes') as mock_apply:
                    # Simulate real glossary behavior
                    def fake_apply(text, glossary):
                        corrected = text
                        fixes = 0
                        applied = []
                        for wrong, right, meta in glossary:
                            if wrong in corrected:
                                count = corrected.count(wrong)
                                corrected = corrected.replace(wrong, right)
                                fixes += count
                                applied.append({"wrong": wrong, "right": right, "count": count})
                        return corrected, fixes, applied

                    mock_apply.side_effect = fake_apply

                    result = transcribe_single_video(
                        self.temp_video,
                        self.temp_output_dir,
                        "SRC0001",
                        device="cpu",
                        compute_type="int8",
                    )

        # ── Assertions ──
        self.assertIsNotNone(result, "transcribe_single_video must return a result")
        self.assertIn("asr_segments", result, "result must contain asr_segments")

        asr_segs = result["asr_segments"]
        whisper_segs = result["segments"]
        self.assertEqual(len(asr_segs), 3)
        self.assertEqual(len(whisper_segs), 3)

        # raw_text == Whisper original (preserved)
        self.assertEqual(asr_segs[0]["raw_text"], "这个视频的完播绿很重要")
        self.assertEqual(asr_segs[1]["raw_text"], "人物设要清晰")
        self.assertEqual(asr_segs[2]["raw_text"], "到店理有很关键")

        # corrected_text == glossary-corrected
        self.assertEqual(asr_segs[0]["corrected_text"], "这个视频的完播率很重要")
        self.assertEqual(asr_segs[1]["corrected_text"], "人物设定要清晰")
        self.assertEqual(asr_segs[2]["corrected_text"], "到店理由很关键")

        # start/end completely unchanged
        for i, (asr, wh) in enumerate(zip(asr_segs, whisper_segs)):
            self.assertEqual(asr["start_sec"], wh["start_sec"],
                           f"Segment {i}: start_sec changed from {wh['start_sec']} to {asr['start_sec']}")
            self.assertEqual(asr["end_sec"], wh["end_sec"],
                           f"Segment {i}: end_sec changed from {wh['end_sec']} to {asr['end_sec']}")
            # Same segment_id for downstream traceability
            self.assertEqual(asr["segment_id"], wh["segment_id"],
                           f"Segment {i}: segment_id mismatch")

        # Markdown was generated from segments
        self.assertIn("markdown_path", result)

    def test_asr_segments_saved_to_disk(self):
        """ASRSegments are saved to disk alongside WhisperSegments."""
        from food_ip_direct_transcribe import transcribe_single_video

        class MockSegment:
            def __init__(self, start, end, text):
                self.start = start; self.end = end; self.text = text

        mock_info = MagicMock()
        mock_info.duration = 5.0
        mock_info.language = "zh"

        class MockModel:
            def __init__(self, *args, **kwargs): pass
            def transcribe(self, audio_path, **kwargs):
                return iter([MockSegment(0.0, 5.0, "测试文本")]), mock_info

        self._fake_faster_whisper.WhisperModel = MockModel

        segments_dir = Path(self.temp_output_dir)
        segments_dir.mkdir(parents=True, exist_ok=True)
        with patch('food_ip_direct_transcribe.WHISPER_SEGMENTS_DIR', segments_dir):
            with patch('food_ip_direct_transcribe.load_glossary', return_value=[]):
                result = transcribe_single_video(
                    self.temp_video, self.temp_output_dir, "SRC0001",
                    device="cpu", compute_type="int8",
                )

        self.assertIsNotNone(result)
        # Check that asr_segments_path points to a real file
        asr_path = Path(result["asr_segments_path"])
        self.assertTrue(asr_path.exists(), f"ASRSegments file not found: {asr_path}")
        # Check it contains valid JSON with ASRSegment structure
        with open(asr_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Top-level source_id in ASR file uses the _asr suffix (for filename disambiguation)
        # Individual segments preserve the original source_id
        self.assertIn("SRC0001", data["source_id"],
                     f"ASR file source_id should reference SRC0001, got {data['source_id']}")
        self.assertGreater(len(data["segments"]), 0)
        seg = data["segments"][0]
        self.assertIn("raw_text", seg)
        self.assertIn("corrected_text", seg)
        self.assertIn("start_sec", seg)
        self.assertIn("end_sec", seg)
        self.assertIn("segment_id", seg)
        # Individual segment source_id is the original (not suffixed)
        self.assertEqual(seg["source_id"], "SRC0001")


# ============================================================================
# Task 3: Directory Rejection with Spy — 1 test
# ============================================================================

class TestDirectoryRejectionSpy(unittest.TestCase):
    """Task 3: transcribe_single_video rejects directory, WhisperModel.transcribe NEVER called."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Inject fake faster_whisper module
        self._fake_faster_whisper = MagicMock()
        self._original_faster_whisper = sys.modules.get('faster_whisper')
        sys.modules['faster_whisper'] = self._fake_faster_whisper

    def tearDown(self):
        import shutil
        if self._original_faster_whisper is not None:
            sys.modules['faster_whisper'] = self._original_faster_whisper
        elif 'faster_whisper' in sys.modules:
            del sys.modules['faster_whisper']
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_directory_input_never_calls_whisper_model_transcribe(self):
        """Passing a directory → transcribe call_count == 0 (spy verified)."""
        from food_ip_direct_transcribe import transcribe_single_video

        transcribe_call_count = [0]

        class MockModel:
            def __init__(self, *args, **kwargs):
                pass
            def transcribe(self, audio_path, **kwargs):
                transcribe_call_count[0] += 1
                mock_info = MagicMock()
                mock_info.duration = 0.0
                mock_info.language = "zh"
                return iter([]), mock_info

        self._fake_faster_whisper.WhisperModel = MockModel

        # Pass a directory (self.temp_dir), NOT a file
        result = transcribe_single_video(
            Path(self.temp_dir),       # ← DIRECTORY, not a file
            self.temp_dir,
            "SRC0001",
            device="cpu",
            compute_type="int8",
        )

        # ── Assertions ──
        self.assertIsNone(result,
                         "transcribe_single_video must return None for directory input")
        self.assertEqual(transcribe_call_count[0], 0,
                        f"WhisperModel.transcribe must NOT be called for directory; "
                        f"called {transcribe_call_count[0]} time(s)")


# ============================================================================
# Task 4: Fail-Fast Model Import — 2 tests
# ============================================================================

class TestFailFastModelImport(unittest.TestCase):
    """Task 4: Pydantic model import failure → RuntimeError, NOT silent degradation."""

    def test_model_import_failure_raises_runtime_error(self):
        """When food_ip_models cannot be imported, FoodIPRefiner raises RuntimeError."""
        import builtins

        _orig_import = builtins.__import__

        def _failing_import(name, *args, **kwargs):
            if name == 'food_ip_models':
                raise ImportError("Simulated model import failure for testing")
            return _orig_import(name, *args, **kwargs)

        # Remove food_ip_models from sys.modules so the import is re-attempted
        saved = sys.modules.pop('food_ip_models', None)
        try:
            with patch('builtins.__import__', _failing_import):
                from food_ip_refine import FoodIPRefiner
                with self.assertRaises(RuntimeError) as ctx:
                    FoodIPRefiner(api_key="fake_test_key")
                self.assertIn("food_ip_models", str(ctx.exception))
                self.assertIn("Pydantic", str(ctx.exception))
        finally:
            if saved is not None:
                sys.modules['food_ip_models'] = saved

    def test_has_models_false_not_present_in_init(self):
        """_has_models = False must NOT exist in FoodIPRefiner.__init__ (no silent degradation)."""
        import inspect
        from food_ip_refine import FoodIPRefiner

        source = inspect.getsource(FoodIPRefiner.__init__)
        # The old pattern that allowed silent degradation must be gone
        self.assertNotIn("_has_models = False", source,
                        "FoodIPRefiner.__init__ must NOT set _has_models = False")
        # The new pattern must raise on failure
        self.assertIn("RuntimeError", source,
                     "FoodIPRefiner.__init__ must raise RuntimeError on model import failure")
        self.assertIn("FATAL", source,
                     "FoodIPRefiner.__init__ must log FATAL on model import failure")


# ============================================================================
# Task 5: _load_segments() forces ASRSegment — 3 tests
# ============================================================================

class TestLoadSegmentsForceASR(unittest.TestCase):
    """Task 5: _load_segments() must force-read ASRSegments (corrected_text authority).

    ASRSegments are the PRODUCTION path. WhisperSegments are legacy fallback only.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.segments_dir = Path(self.temp_dir) / "whisper_segments"
        self.segments_dir.mkdir(parents=True, exist_ok=True)

        # Patch WHISPER_SEGMENTS_DIR to use temp dir
        self._seg_dir_patcher = patch(
            'food_ip_refine.WHISPER_SEGMENTS_DIR', self.segments_dir
        )
        self._seg_dir_patcher.start()

    def tearDown(self):
        import shutil
        self._seg_dir_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_asr_segments(self, source_id, segments):
        """Write ASRSegment file to temp dir."""
        path = self.segments_dir / f"{source_id}_asr_whisper_segments.json"
        data = {
            "source_id": source_id,
            "segment_count": len(segments),
            "extracted_at": "2026-08-08T00:00:00",
            "segments": segments,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def _write_whisper_segments(self, source_id, segments):
        """Write raw WhisperSegment file to temp dir (legacy)."""
        path = self.segments_dir / f"{source_id}_whisper_segments.json"
        data = {
            "source_id": source_id,
            "segment_count": len(segments),
            "extracted_at": "2026-08-08T00:00:00",
            "segments": segments,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def test_load_segments_reads_asr_with_corrected_text(self):
        """_load_segments returns ASRSegments with corrected_text when ASR file exists."""
        from food_ip_refine import FoodIPRefiner

        asr_segs = [
            {
                "segment_id": "SRC0100-SEG0001",
                "source_id": "SRC0100",
                "start_sec": 0.0,
                "end_sec": 5.2,
                "raw_text": "这个视频的完播绿很重要",
                "corrected_text": "这个视频的完播率很重要",
                "asr_fix_count": 1,
                "asr_fixes_applied": [{"wrong": "完播绿", "right": "完播率", "count": 1}],
            },
            {
                "segment_id": "SRC0100-SEG0002",
                "source_id": "SRC0100",
                "start_sec": 5.2,
                "end_sec": 10.0,
                "raw_text": "人物设要清晰",
                "corrected_text": "人物设定要清晰",
                "asr_fix_count": 1,
                "asr_fixes_applied": [{"wrong": "人物设", "right": "人物设定", "count": 1}],
            },
        ]
        self._write_asr_segments("SRC0100", asr_segs)

        # Create a minimal refiner instance (bypass real init)
        refiner = object.__new__(FoodIPRefiner)
        segments = refiner._load_segments("SRC0100")

        # ── Assertions ──
        self.assertEqual(len(segments), 2,
                        f"Must load 2 ASRSegments, got {len(segments)}")
        self.assertEqual(segments[0]["raw_text"], "这个视频的完播绿很重要")
        self.assertEqual(segments[0]["corrected_text"], "这个视频的完播率很重要")
        self.assertEqual(segments[0]["start_sec"], 0.0)
        self.assertEqual(segments[0]["end_sec"], 5.2)
        self.assertEqual(segments[0]["segment_id"], "SRC0100-SEG0001")
        self.assertEqual(segments[1]["raw_text"], "人物设要清晰")
        self.assertEqual(segments[1]["corrected_text"], "人物设定要清晰")

    def test_load_segments_rejects_whisper_fallback_when_asr_missing(self):
        """P0 production must reject raw Whisper fallback when ASR is missing."""
        from food_ip_refine import FoodIPRefiner

        whisper_segs = [
            {
                "segment_id": "SRC0200-SEG0001",
                "source_id": "SRC0200",
                "start_sec": 0.0,
                "end_sec": 8.0,
                "raw_text": "原始转录文本",
            },
        ]
        self._write_whisper_segments("SRC0200", whisper_segs)
        asr_path = self.segments_dir / "SRC0200_asr_whisper_segments.json"
        self.assertFalse(asr_path.exists())

        refiner = object.__new__(FoodIPRefiner)
        with self.assertRaises(RuntimeError):
            refiner._load_segments("SRC0200")

    def test_load_segments_raises_when_no_asr_file_exists(self):
        """Missing ASR authority must fail, never return an empty permissive value."""
        from food_ip_refine import FoodIPRefiner

        refiner = object.__new__(FoodIPRefiner)
        with self.assertRaises(RuntimeError):
            refiner._load_segments("SRC0300")

    def test_load_segments_prefers_asr_over_whisper_when_both_exist(self):
        """When BOTH ASR and Whisper files exist, ASRSegments win (production path)."""
        from food_ip_refine import FoodIPRefiner

        # Write ASRSegments (with corrected_text)
        asr_segs = [
            {
                "segment_id": "SRC0400-SEG0001",
                "source_id": "SRC0400",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "raw_text": "ASR版本",
                "corrected_text": "ASR修正版",
                "asr_fix_count": 1,
                "asr_fixes_applied": [],
            },
        ]
        self._write_asr_segments("SRC0400", asr_segs)

        # Write WhisperSegments (different content to prove ASR wins)
        whisper_segs = [
            {
                "segment_id": "SRC0400-SEG0001",
                "source_id": "SRC0400",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "raw_text": "Whisper原始版本",
            },
        ]
        self._write_whisper_segments("SRC0400", whisper_segs)

        # Both files exist
        self.assertTrue(asr_path := (self.segments_dir / "SRC0400_asr_whisper_segments.json").exists())
        self.assertTrue((self.segments_dir / "SRC0400_whisper_segments.json").exists())

        refiner = object.__new__(FoodIPRefiner)
        segments = refiner._load_segments("SRC0400")

        # ── Assertions: ASR wins ──
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["raw_text"], "ASR版本",
                        "Must return ASRSegment raw_text, not WhisperSegment's")
        self.assertEqual(segments[0]["corrected_text"], "ASR修正版",
                        "Must return ASRSegment corrected_text")
        self.assertIn("corrected_text", segments[0],
                     "ASRSegment must have corrected_text field")



# ============================================================================
# Round 1 Final Seal: strict ASR provenance + evidence + runtime contracts
# ============================================================================

class TestRound1FinalSeal(unittest.TestCase):
    def test_generated_asr_segment_matches_pydantic_runtime_contract(self):
        data = {
            "segment_id": "SRC0001-SEG0001",
            "source_id": "SRC0001",
            "start_sec": 1.2,
            "end_sec": 3.4,
            "raw_text": "完播绿很重要",
            "corrected_text": "完播率很重要",
            "asr_fix_count": 1,
            "asr_fixes_applied": [{"wrong": "完播绿", "right": "完播率", "count": 1}],
        }
        parsed = ASRSegment.model_validate(data)
        self.assertEqual(parsed.corrected_text, "完播率很重要")
        self.assertEqual(parsed.asr_fix_count, 1)
        self.assertEqual(parsed.asr_fixes_applied[0]["right"], "完播率")

    def test_source_manifest_runtime_contract_includes_asr_provenance(self):
        manifest = {
            "source_id": "SRC0001",
            "content_hash": "12345678abcdef",
            "source_file": "video.mp4",
            "file_size": 123,
            "duration_sec": 12.5,
            "duration_str": "00:12",
            "title": "测试",
            "pipeline_version": "v-test",
            "quality_status": "good",
            "transcript": "transcripts/SRC0001.md",
            "raw_transcript": "raw_transcripts/SRC0001.md",
            "keyframes_dir": None,
            "keyframe_count": 0,
            "whisper_segments_path": "whisper_segments/SRC0001_whisper_segments.json",
            "asr_segments_path": "whisper_segments/SRC0001_asr_whisper_segments.json",
            "segment_count": 2,
            "transcribed_at": "2026-08-08",
            "processing_time_sec": 1,
        }
        parsed = SourceManifestEntry.model_validate(manifest)
        self.assertEqual(parsed.asr_segments_path,
                         "whisper_segments/SRC0001_asr_whisper_segments.json")
        self.assertEqual(parsed.segment_count, 2)

    def test_semantic_chunk_rejects_unknown_segment_without_sentinel(self):
        from semantic_chunker import _enrich_chunks
        segments = [{
            "segment_id": "SRC0001-SEG0001",
            "source_id": "SRC0001",
            "start_sec": 0.0,
            "end_sec": 5.0,
            "raw_text": "原始文本",
            "corrected_text": "修正文本",
            "asr_fix_count": 0,
            "asr_fixes_applied": [],
        }]
        raw = [{
            "segment_ids": ["SRC0001-SEG9999"],
            "knowledge_type": "technique",
            "brief": "非法证据",
            "chunk_text": "这是一段长度足够的非法证据测试文本",
        }]
        out = _enrich_chunks(raw, "SRC0001", segments)
        self.assertEqual(out, [])
        self.assertNotIn("SRC0001-SEG0000", json.dumps(out, ensure_ascii=False))

    def test_semantic_chunk_rejects_mixed_valid_and_unknown_evidence(self):
        from semantic_chunker import _enrich_chunks
        segments = [{
            "segment_id": "SRC0001-SEG0001",
            "source_id": "SRC0001",
            "start_sec": 0.0,
            "end_sec": 5.0,
            "raw_text": "原始文本",
            "corrected_text": "修正文本",
            "asr_fix_count": 0,
            "asr_fixes_applied": [],
        }]
        raw = [{
            "segment_ids": ["SRC0001-SEG0001", "SRC0001-SEG9999"],
            "knowledge_type": "technique",
            "brief": "混合证据",
            "chunk_text": "这是一段长度足够的混合证据测试文本",
        }]
        self.assertEqual(_enrich_chunks(raw, "SRC0001", segments), [])

    def test_b1_chunk_text_rebuilt_from_authoritative_segments(self):
        """B1: the LLM's rewritten/hallucinated chunk_text is NOT persisted —
        the persisted chunk_text is rebuilt deterministically from the
        authoritative ASRSegments (corrected_text, else raw_text), joined in
        authoritative segment order."""
        from semantic_chunker import _enrich_chunks
        segments = [
            {
                "segment_id": "SRC0001-SEG0001",
                "source_id": "SRC0001",
                "start_sec": 0.0, "end_sec": 5.0,
                "raw_text": "原始第一段文本内容",
                "corrected_text": "修正第一段文本内容",
                "asr_fix_count": 1, "asr_fixes_applied": [],
            },
            {
                "segment_id": "SRC0001-SEG0002",
                "source_id": "SRC0001",
                "start_sec": 5.0, "end_sec": 10.0,
                "raw_text": "原始第二段文本内容",
                "corrected_text": "修正第二段文本内容",
                "asr_fix_count": 0, "asr_fixes_applied": [],
            },
        ]
        raw = [{
            "segment_ids": ["SRC0001-SEG0001", "SRC0001-SEG0002"],
            "knowledge_type": "technique",
            "brief": "重建正文",
            # LLM rewrote / hallucinated the text — must never be persisted
            "chunk_text": "这是LLM改写的一段完全不同的错误文本内容",
        }]
        out = _enrich_chunks(raw, "SRC0001", segments)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["chunk_text"],
                         "修正第一段文本内容\n修正第二段文本内容")
        self.assertNotIn("LLM改写", out[0]["chunk_text"])

    def test_b1_chunk_text_falls_back_to_raw_text_when_corrected_empty(self):
        """B1: per-segment text selection — non-empty corrected_text first,
        else raw_text — joined in AUTHORITATIVE segment order (not the order
        the LLM listed the segment_ids)."""
        from semantic_chunker import _enrich_chunks
        segments = [
            {
                "segment_id": "SRC0001-SEG0001",
                "source_id": "SRC0001",
                "start_sec": 0.0, "end_sec": 5.0,
                "raw_text": "第一条原始内容没有修正",
                "corrected_text": "",  # empty → fallback to raw_text
                "asr_fix_count": 0, "asr_fixes_applied": [],
            },
            {
                "segment_id": "SRC0001-SEG0002",
                "source_id": "SRC0001",
                "start_sec": 5.0, "end_sec": 10.0,
                "raw_text": "第二条原始内容",
                "corrected_text": "第二条修正内容",
                "asr_fix_count": 1, "asr_fixes_applied": [],
            },
        ]
        raw = [{
            "segment_ids": ["SRC0001-SEG0002", "SRC0001-SEG0001"],  # out of order
            "knowledge_type": "technique",
            "brief": "回退测试",
            "chunk_text": "忽略这段LLM文本",
        }]
        out = _enrich_chunks(raw, "SRC0001", segments)
        self.assertEqual(len(out), 1)
        # Authoritative order (SEG0001 then SEG0002), corrected where non-empty
        # else raw_text; the LLM's listed order and chunk_text are ignored.
        self.assertEqual(out[0]["chunk_text"],
                         "第一条原始内容没有修正\n第二条修正内容")
        self.assertNotIn("忽略这段LLM文本", out[0]["chunk_text"])

    def test_asr_corrected_text_reaches_semantic_chunk_llm_prompt(self):
        from food_ip_refine import FoodIPRefiner
        import food_ip_refine
        import semantic_chunker

        with tempfile.TemporaryDirectory() as td:
            seg_dir = Path(td)
            asr_path = seg_dir / "SRC0500_asr_whisper_segments.json"
            payload = {
                "source_id": "SRC0500",
                "segment_count": 1,
                "extracted_at": "2026-08-08T00:00:00",
                "segments": [{
                    "segment_id": "SRC0500-SEG0001",
                    "source_id": "SRC0500",
                    "start_sec": 2.0,
                    "end_sec": 7.0,
                    "raw_text": "完播绿非常重要",
                    "corrected_text": "完播率非常重要，这是完整知识块文本内容",
                    "asr_fix_count": 1,
                    "asr_fixes_applied": [{"wrong": "完播绿", "right": "完播率", "count": 1}],
                }],
            }
            asr_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            refiner = object.__new__(FoodIPRefiner)
            with patch.object(food_ip_refine, 'WHISPER_SEGMENTS_DIR', seg_dir):
                segments = refiner._load_segments("SRC0500")

            # B1: the LLM's chunk_text is a rewritten/hallucinated copy and must
            # NOT be persisted — the persisted chunk_text is rebuilt from the
            # authoritative ASRSegment (corrected_text).
            fake_response = json.dumps([{
                "segment_ids": ["SRC0500-SEG0001"],
                "knowledge_type": "technique",
                "brief": "完播率",
                "chunk_text": "这是LLM改写的一段完全不同错误文本",
            }], ensure_ascii=False)
            with patch('semantic_chunker._call_llm', return_value=fake_response) as llm:
                chunks = semantic_chunker.chunk_transcript(
                    "这里是不会成为正文的旧文本", "SRC0500", "测试",
                    api_key="fake-key", segments=segments
                )
            self.assertEqual(len(chunks), 1)
            prompt = llm.call_args.args[1]
            self.assertIn("完播率非常重要", prompt)
            self.assertNotIn("完播绿非常重要", prompt)
            self.assertEqual(chunks[0]["segment_ids"], ["SRC0500-SEG0001"])
            self.assertEqual(chunks[0]["start_sec"], 2.0)
            self.assertEqual(chunks[0]["end_sec"], 7.0)
            # B1: persisted chunk_text must be the authoritative segment text,
            # never the LLM's rewritten copy.
            self.assertEqual(chunks[0]["chunk_text"],
                             "完播率非常重要，这是完整知识块文本内容")
            self.assertNotIn("LLM改写", chunks[0]["chunk_text"])

    def test_process_source_does_not_call_chunker_without_asr(self):
        from food_ip_refine import FoodIPRefiner
        import food_ip_refine
        with tempfile.TemporaryDirectory() as td:
            refiner = object.__new__(FoodIPRefiner)
            refiner.api_key = "fake-key"
            with patch.object(food_ip_refine, 'WHISPER_SEGMENTS_DIR', Path(td)):
                with patch('semantic_chunker.chunk_transcript') as chunker:
                    with self.assertRaises(RuntimeError):
                        refiner.process_source("SRC0600", "测试", "文本")
                    chunker.assert_not_called()

    def test_manifest_persistence_boundary_validates_and_saves_asr_path(self):
        import food_ip_transcribe
        from food_ip_transcribe import save_per_source_manifest
        manifest = {
            "source_id": "SRC0700",
            "content_hash": "abcdef1234567890",
            "source_file": "video.mp4",
            "file_size": 123,
            "duration_sec": 12.5,
            "duration_str": "00:12",
            "title": "测试",
            "pipeline_version": "v-test",
            "quality_status": "good",
            "transcript": "transcripts/SRC0700.md",
            "raw_transcript": "raw_transcripts/SRC0700.md",
            "keyframes_dir": None,
            "keyframe_count": 0,
            "whisper_segments_path": "whisper_segments/SRC0700_whisper_segments.json",
            "asr_segments_path": "whisper_segments/SRC0700_asr_whisper_segments.json",
            "segment_count": 1,
            "transcribed_at": "2026-08-08",
            "processing_time_sec": 1,
        }
        with tempfile.TemporaryDirectory() as td:
            with patch.object(food_ip_transcribe, 'PER_SOURCE_MANIFESTS_DIR', Path(td)):
                save_per_source_manifest(manifest)
                saved = json.loads((Path(td) / "SRC0700.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["asr_segments_path"],
                         "whisper_segments/SRC0700_asr_whisper_segments.json")

    def test_manifest_persistence_boundary_rejects_unknown_field(self):
        import food_ip_transcribe
        from food_ip_transcribe import save_per_source_manifest
        bad = {
            "source_id": "SRC0701",
            "content_hash": "abcdef1234567890",
            "unexpected_field": "must fail",
        }
        with tempfile.TemporaryDirectory() as td:
            with patch.object(food_ip_transcribe, 'PER_SOURCE_MANIFESTS_DIR', Path(td)):
                with self.assertRaises(Exception):
                    save_per_source_manifest(bad)
                self.assertFalse((Path(td) / "SRC0701.json").exists())

# ============================================================================
# Zero-cost integration: transcription CLI → refine CLI (real data handoff)
# ============================================================================

class TestTranscribeToRefineIntegration(unittest.TestCase):
    """Transcribe → ASRSegment → Refine, run consecutively on real code paths.

    Zero-cost by design:
      - fake video file (no real media)
      - faster-whisper mocked (no GPU / no model download)
      - all LLM calls mocked (no paid API)
      - all E:\\ output dirs patched to temp (no writes outside the test)

    Phase 1 runs the REAL `food_ip_transcribe.main()` (--limit 1) which writes
    authoritative ASRSegments + a per-source manifest. Phase 2 runs the REAL
    `food_ip_refine.main()` (--source SRC0001) which now reads those ASR
    segments (no *_corrected.txt dependency) and produces a SemanticChunk-backed
    KnowledgeCard. This proves the two pipeline entry points run consecutively.
    """

    def setUp(self):
        self._fake_faster_whisper = MagicMock()
        self._orig_faster_whisper = sys.modules.get('faster_whisper')
        sys.modules['faster_whisper'] = self._fake_faster_whisper

        self.work = Path(tempfile.mkdtemp())
        self.input_dir = self.work / "videos"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.video = self.input_dir / "test_video.mp4"
        self.video.write_bytes(b"fake video bytes")

        for sub in ["transcripts", "raw_transcripts", "manifests/by_source",
                    "manifests", "flags", "logs", "whisper_segments",
                    "_temp_transcripts", "atomic", "atomic/by_source",
                    "graph", "synthesis", "review_queue", "reports"]:
            (self.work / sub).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self._orig_faster_whisper is not None:
            sys.modules['faster_whisper'] = self._orig_faster_whisper
        elif 'faster_whisper' in sys.modules:
            del sys.modules['faster_whisper']
        shutil.rmtree(self.work, ignore_errors=True)

    def _install_mock_whisper(self):
        """Make transcribe_single_video's `from faster_whisper import WhisperModel`
        resolve to a mock that returns native .start/.end/.text segments."""
        class MockSegment:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        segments = [
            MockSegment(0.0, 5.0, "这是第一条测试转录文本"),
            MockSegment(5.0, 10.0, "餐饮IP课程的核心方法内容"),
            MockSegment(10.0, 15.0, "到店理由与钩子开头的讲解"),
        ]

        class MockModel:
            def __init__(self, *args, **kwargs):
                pass
            def transcribe(self, audio_path, **kwargs):
                mock_info = MagicMock()
                mock_info.duration = 15.0
                mock_info.language = "zh"
                return iter(segments), mock_info

        self._fake_faster_whisper.WhisperModel = MockModel

    def test_transcribe_cli_to_refine_cli_consecutive_handoff(self):
        """food_ip_transcribe.main() then food_ip_refine.main() on real artifacts."""
        import food_ip_transcribe
        import food_ip_direct_transcribe
        import food_ip_persistence
        import food_ip_refine
        import semantic_chunker
        from food_ip_refine import FoodIPRefiner

        self._install_mock_whisper()

        # ── Phase 1: transcription CLI (real code, whisper mocked, dirs → temp) ──
        with ExitStack() as stack:
            for p in [
                patch('food_ip_transcribe.FOOD_IP_SOURCES_DIR', self.work),
                patch('food_ip_transcribe.TRANSCRIPTS_DIR', self.work / "transcripts"),
                patch('food_ip_transcribe.RAW_TRANSCRIPTS_DIR', self.work / "raw_transcripts"),
                patch('food_ip_transcribe.PER_SOURCE_MANIFESTS_DIR', self.work / "manifests/by_source"),
                patch('food_ip_transcribe.MANIFESTS_DIR', self.work / "manifests"),
                patch('food_ip_transcribe.FLAGS_DIR', self.work / "flags"),
                patch('food_ip_transcribe.LOGS_DIR', self.work / "logs"),
                patch('food_ip_transcribe.WHISPER_SEGMENTS_DIR', self.work / "whisper_segments"),
                patch('food_ip_direct_transcribe.WHISPER_SEGMENTS_DIR', self.work / "whisper_segments"),
                patch('food_ip_direct_transcribe.load_glossary', return_value=[]),
                # rebuild_sources_index lives in food_ip_persistence with its own bindings
                patch('food_ip_persistence.PER_SOURCE_MANIFESTS_DIR', self.work / "manifests/by_source"),
                patch('food_ip_persistence.MANIFESTS_DIR', self.work / "manifests"),
                patch('food_ip_transcribe._get_duration', return_value=15.0),
                patch('food_ip_transcribe.assess_quality', return_value="good"),
                patch('food_ip_transcribe.ensure_dirs'),  # avoid E:\ writes
            ]:
                stack.enter_context(p)
            with patch.object(sys, 'argv',
                              ['food_ip_transcribe.py', '--input', str(self.input_dir), '--limit', '1']):
                food_ip_transcribe.main()

        # Phase 1 assertions: transcription produced the authoritative artifacts
        asr_path = self.work / "whisper_segments" / "SRC0001_asr_whisper_segments.json"
        manifest_path = self.work / "manifests/by_source" / "SRC0001.json"
        self.assertTrue(asr_path.is_file(), "transcription must produce the ASRSegment file")
        self.assertTrue(manifest_path.is_file(), "transcription must produce the per-source manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["asr_segments_path"],
                         "whisper_segments/SRC0001_asr_whisper_segments.json")
        self.assertEqual(manifest["segment_count"], 3)

        # ── Phase 2: refine CLI (real entry, LLM mocked, dirs → temp) ──
        chunker_json = json.dumps([{
            "chunk_text": "这是一个足够长的完整知识块文本，验证转录到精炼的真实数据交接。",
            "knowledge_type": "technique",
            "segment_ids": ["SRC0001-SEG0001", "SRC0001-SEG0002"],
            "brief": "真实交接验证",
        }], ensure_ascii=False)
        card_json = json.dumps({
            "title": "真实交接验证",
            "knowledge_type": "technique",
            "question_ids": [],
            "core_idea": "转录到精炼的真实数据交接成功",
            "why_it_works": "端到端管线验证",
            "confidence": 0.9,
            "origin": "explicit",
            "inference_basis": "",
            "knowledge_scope": "methodology",
        }, ensure_ascii=False)

        with ExitStack() as stack:
            for p in [
                patch('food_ip_refine.WHISPER_SEGMENTS_DIR', self.work / "whisper_segments"),
                patch('food_ip_refine.PER_SOURCE_MANIFESTS_DIR', self.work / "manifests/by_source"),
                patch('food_ip_refine.ATOMIC_DIR', self.work / "atomic"),
                patch('food_ip_refine.ATOMIC_BY_SOURCE_DIR', self.work / "atomic/by_source"),
                patch('food_ip_refine.GRAPH_DIR', self.work / "graph"),
                patch('food_ip_refine.SYNTHESIS_DIR', self.work / "synthesis"),
                patch('food_ip_refine.REVIEW_QUEUE_DIR', self.work / "review_queue"),
                patch('food_ip_refine.REPORTS_DIR', self.work / "reports"),
                patch('food_ip_refine.MANIFESTS_DIR', self.work / "manifests"),
                patch('food_ip_refine.ensure_dirs'),  # avoid E:\ writes
                # P0-Round2: rebuild_global_indices / SourcePersistence resolve
                # their dirs from the food_ip_persistence module scope.
                patch('food_ip_persistence.ATOMIC_DIR', self.work / "atomic"),
                patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR', self.work / "atomic/by_source"),
                patch('food_ip_persistence.ensure_dirs'),
                patch('semantic_chunker._call_llm', return_value=chunker_json),
                patch.object(FoodIPRefiner, '_call_llm', side_effect=[card_json]),
            ]:
                stack.enter_context(p)
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
                with patch.object(sys, 'argv',
                                  ['food_ip_refine.py', '--source', 'SRC0001']):
                    food_ip_refine.main()

        # Phase 2 assertions: refine consumed ASR artifacts (no *_corrected.txt)
        cards_file = self.work / "atomic" / "knowledge_cards.jsonl"
        self.assertTrue(cards_file.is_file(), "refine must flush knowledge_cards.jsonl")
        lines = [l for l in cards_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, f"expected 1 knowledge card, got {len(lines)}")
        card = json.loads(lines[0])
        self.assertEqual(card["source"]["source_id"], "SRC0001")
        self.assertEqual(card["evidence_segment_ids"],
                         ["SRC0001-SEG0001", "SRC0001-SEG0002"])
        self.assertEqual(card["source"]["start_sec"], 0.0)
        self.assertEqual(card["source"]["end_sec"], 10.0)
        self.assertTrue(card["chunk_id"].startswith("CHK_"),
                        f"chunk_id must be programmatic, got {card['chunk_id']!r}")
        audits = list((self.work / "reports").glob("run_audit_*.json"))
        self.assertEqual(len(audits), 1, "refine must write a run audit report")

    def test_refine_does_not_require_corrected_txt(self):
        """pass0_asr_correction must NOT look for *_corrected.txt — it derives
        corrected text from ASRSegments and fails fast when ASR is missing."""
        import inspect
        import food_ip_refine
        from food_ip_refine import FoodIPRefiner

        refiner = FoodIPRefiner(api_key="fake-key")
        with patch('food_ip_refine.WHISPER_SEGMENTS_DIR', self.work / "whisper_segments"):
            # No ASR file for SRC9999 → returns None (clean abort), never reads a .txt
            self.assertIsNone(refiner.pass0_asr_correction("SRC9999"))
        # The code no longer reads the *_corrected.txt dir in pass0 / main loop.
        # (The docstring may mention the file name to explain the change; the
        # important contract is that RAW_CORRECTED_DIR is never referenced.)
        self.assertNotIn("RAW_CORRECTED_DIR",
                         inspect.getsource(refiner.pass0_asr_correction))
        self.assertNotIn("RAW_CORRECTED_DIR",
                         inspect.getsource(food_ip_refine.main))


# ============================================================================
# P0 Round 2: Refine per-source persistence (real path, LLM mocked)
# ============================================================================

class TestPerSourcePersistence(unittest.TestCase):
    """P0 Round 2: food_ip_refine persists each Source immediately, and the
    global index is REBUILT from per-source data — so an incremental
    `--source SRC0002` run never drops SRC0001's already-persisted knowledge.

    Real path: real FoodIPRefiner / main() entry, ASR artifacts on disk,
    LLM fully mocked, all E:\\ output dirs patched to a temp workspace.
    """

    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.whisper_segments = self.work / "whisper_segments"
        self.whisper_segments.mkdir(parents=True, exist_ok=True)
        self.manifests = self.work / "manifests" / "by_source"
        self.manifests.mkdir(parents=True, exist_ok=True)
        for sub in ["atomic", "atomic/by_source", "graph", "synthesis",
                    "review_queue", "reports"]:
            (self.work / sub).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.work, ignore_errors=True)

    def _write_asr(self, source_id):
        """Write authoritative ASRSegments + a per-source manifest for a source."""
        segs = [
            {"segment_id": f"{source_id}-SEG0001", "source_id": source_id,
             "start_sec": 0.0, "end_sec": 5.0, "raw_text": "第一条测试文本",
             "corrected_text": "第一条测试文本"},
            {"segment_id": f"{source_id}-SEG0002", "source_id": source_id,
             "start_sec": 5.0, "end_sec": 10.0, "raw_text": "第二条测试文本",
             "corrected_text": "第二条测试文本"},
        ]
        asr = self.whisper_segments / f"{source_id}_asr_whisper_segments.json"
        asr.write_text(json.dumps({
            "source_id": source_id, "segment_count": len(segs), "segments": segs,
        }, ensure_ascii=False), encoding="utf-8")
        (self.manifests / f"{source_id}.json").write_text(json.dumps({
            "source_id": source_id, "title": f"{source_id} title",
        }, ensure_ascii=False), encoding="utf-8")

    def _chunker_json(self, source_id):
        return json.dumps([{
            "chunk_text": f"{source_id} 的完整知识块文本，内容足够长以通过语义校验。",
            "knowledge_type": "technique",
            "segment_ids": [f"{source_id}-SEG0001", f"{source_id}-SEG0002"],
            "brief": f"{source_id}块",
        }], ensure_ascii=False)

    def _card_json(self, source_id):
        return json.dumps({
            "title": f"{source_id}卡片",
            "knowledge_type": "technique",
            "question_ids": [],
            "core_idea": f"{source_id} 的核心观点内容足够长",
            "why_it_works": "端到端验证",
            "confidence": 0.9,
            "origin": "explicit",
            "inference_basis": "",
            "knowledge_scope": "methodology",
        }, ensure_ascii=False)

    def _run_refine(self, source_id, card_llm=None, chunker_llm=None):
        """Drive the real food_ip_refine.main() entry for a single source.

        ``card_llm`` / ``chunker_llm`` may be provided to assert on call counts
        or to inject failures. Defaults return valid mocked output.
        """
        import food_ip_refine
        import food_ip_persistence
        import semantic_chunker
        from food_ip_refine import FoodIPRefiner

        card_llm = card_llm or MagicMock(side_effect=[self._card_json(source_id)])
        chunker_llm = chunker_llm or MagicMock(return_value=self._chunker_json(source_id))

        with ExitStack() as stack:
            for p in [
                patch('food_ip_refine.WHISPER_SEGMENTS_DIR', self.whisper_segments),
                patch('food_ip_refine.PER_SOURCE_MANIFESTS_DIR', self.manifests),
                patch('food_ip_refine.ATOMIC_DIR', self.work / "atomic"),
                patch('food_ip_refine.ATOMIC_BY_SOURCE_DIR', self.work / "atomic/by_source"),
                patch('food_ip_refine.GRAPH_DIR', self.work / "graph"),
                patch('food_ip_refine.SYNTHESIS_DIR', self.work / "synthesis"),
                patch('food_ip_refine.REVIEW_QUEUE_DIR', self.work / "review_queue"),
                patch('food_ip_refine.REPORTS_DIR', self.work / "reports"),
                patch('food_ip_refine.MANIFESTS_DIR', self.work / "manifests"),
                patch('food_ip_refine.ensure_dirs'),  # avoid E:\ writes
                # SourcePersistence / rebuild_global_indices use the
                # food_ip_persistence module scope for their dirs.
                patch('food_ip_persistence.ATOMIC_DIR', self.work / "atomic"),
                patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR', self.work / "atomic/by_source"),
                patch('food_ip_persistence.ensure_dirs'),
                patch('semantic_chunker._call_llm', chunker_llm),
                patch.object(FoodIPRefiner, '_call_llm', card_llm),
            ]:
                stack.enter_context(p)
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
                with patch.object(sys, 'argv',
                                  ['food_ip_refine.py', '--source', source_id]):
                    food_ip_refine.main()
        return card_llm

    def _mark_state(self, source_id, status, error=""):
        """Write a refine-stage state file with the given status, for building
        done/failed/processing eligibility fixtures. Returns the source dir."""
        with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                   self.work / "atomic/by_source"):
            sp = SourcePersistence(source_id, stage="refine")
            if status == "done":
                sp.mark_done()
            elif status == "failed":
                sp.mark_failed(error or "injected failure")
            else:  # processing
                sp.start_processing("run-eligibility-test")
        return sp.source_dir

    def _write_all_artifacts(self, source_id, overrides=None):
        """Write all five per-source artifact files (empty), then apply
        ``overrides`` {filename: content}. Returns the source dir."""
        src = self.work / "atomic/by_source" / source_id
        src.mkdir(parents=True, exist_ok=True)
        for name in ["chunks.jsonl", "knowledge_cards.jsonl", "case_cards.jsonl",
                     "anti_patterns.jsonl", "creative_formats.jsonl"]:
            (src / name).write_text("", encoding="utf-8")
        for name, content in (overrides or {}).items():
            (src / name).write_text(content, encoding="utf-8")
        return src

    def _valid_chunk_for(self, source_id, tag="elig"):
        """A schema-valid SemanticChunk JSON line belonging to ``source_id``."""
        return json.dumps({
            "chunk_id": f"CHK_{tag}_{source_id}",
            "source_id": source_id,
            "segment_ids": [f"{source_id}-SEG0001"],
            "knowledge_type_hint": "technique",
            "brief": f"{tag}块",
            "chunk_text": "这是一段用于资格过滤测试的足够长的知识块文本内容。",
            "start_sec": 0.0, "end_sec": 5.0, "start_time": "", "end_time": "",
        }, ensure_ascii=False) + "\n"

    def test_incremental_sources_both_in_global_after_rebuild(self):
        """First SRC0001, then only SRC0002 → global index keeps BOTH."""
        self._write_asr("SRC0001")
        self._write_asr("SRC0002")

        # ── First run: only SRC0001 ──
        self._run_refine("SRC0001")

        src1 = self.work / "atomic/by_source/SRC0001"
        self.assertTrue((src1 / "chunks.jsonl").is_file(),
                        "SRC0001 chunks must be persisted per-source")
        self.assertTrue((src1 / "knowledge_cards.jsonl").is_file(),
                        "SRC0001 knowledge cards must be persisted per-source")
        global_cards = (self.work / "atomic" / "knowledge_cards.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(global_cards), 1,
                         f"global index should have 1 card after SRC0001 only")

        # ── Second run: only SRC0002 (incremental) ──
        self._run_refine("SRC0002")

        src2 = self.work / "atomic/by_source/SRC0002"
        self.assertTrue((src2 / "chunks.jsonl").is_file(),
                        "SRC0002 chunks must be persisted per-source")
        self.assertTrue((src2 / "knowledge_cards.jsonl").is_file(),
                        "SRC0002 knowledge cards must be persisted per-source")

        # ── Rebuilt global index contains BOTH sources ──
        global_cards = (self.work / "atomic" / "knowledge_cards.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(global_cards), 2,
                         f"expected 2 global cards, got {len(global_cards)}: {global_cards}")
        card_sources = {json.loads(l)["source"]["source_id"]
                        for l in global_cards if l.strip()}
        self.assertEqual(card_sources, {"SRC0001", "SRC0002"},
                         "global cards must include BOTH SRC0001 and SRC0002")

        # Global chunks rebuilt from per-source too.
        global_chunks = (self.work / "atomic" / "chunks.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(global_chunks), 2,
                         f"expected 2 global chunks, got {len(global_chunks)}")
        chunk_sources = {json.loads(l)["source_id"]
                         for l in global_chunks if l.strip()}
        self.assertEqual(chunk_sources, {"SRC0001", "SRC0002"})

        # Each per-source knowledge card keeps real provenance.
        cards1 = [json.loads(l) for l in (src1 / "knowledge_cards.jsonl")
                  .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(cards1[0]["evidence_segment_ids"],
                         ["SRC0001-SEG0001", "SRC0001-SEG0002"])
        self.assertEqual(cards1[0]["source"]["start_sec"], 0.0)
        self.assertEqual(cards1[0]["source"]["end_sec"], 10.0)

    def test_b2_inferred_without_basis_rejected_not_fabricated(self):
        """B2: origin=inferred with an empty inference_basis is REJECTED by the
        persisted validator (fail-fast). The program must never fabricate a
        '推断自...' basis, the invalid card must not enter the per-source or
        global knowledge artifacts, and the source still completes cleanly."""
        self._write_asr("SRC0001")
        card_llm = MagicMock(return_value=json.dumps({
            "title": "SRC0001推断卡",
            "knowledge_type": "technique",
            "question_ids": [],
            "core_idea": "从案例推断的核心观点内容足够长",
            "why_it_works": "端到端验证",
            "confidence": 0.7,
            "origin": "inferred",
            "inference_basis": "",  # missing basis → must be rejected
            "knowledge_scope": "methodology",
        }, ensure_ascii=False))
        self._run_refine("SRC0001", card_llm=card_llm)

        src_dir = self.work / "atomic/by_source/SRC0001"
        card_lines = [l for l in (src_dir / "knowledge_cards.jsonl")
                      .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(card_lines), 0,
                         "inferred-without-basis card must NOT enter the "
                         "per-source knowledge artifacts")
        global_cards = (self.work / "atomic" / "knowledge_cards.jsonl") \
            .read_text(encoding="utf-8")
        self.assertNotIn("推断自", global_cards,
                         "the program must never fabricate a '推断自...' basis")
        self.assertNotIn("从案例推断", global_cards,
                         "the rejected card must not appear in the global index")
        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done",
                         "a rejected card must not fail the source run")

    # ── Round 2B-1: refine state machine + completed skip ──

    def test_completed_source_skipped_no_extra_llm(self):
        """First refine SRC0001 → LLM called + persisted + state=done.
        Second refine SRC0001 → completed skip, LLM call count unchanged."""
        self._write_asr("SRC0001")

        # ── First run: process + persist + complete ──
        card_llm = MagicMock(side_effect=[self._card_json("SRC0001")])
        self._run_refine("SRC0001", card_llm=card_llm)
        self.assertEqual(card_llm.call_count, 1,
                         "first refine must call the LLM once")

        src_dir = self.work / "atomic/by_source/SRC0001"
        self.assertTrue((src_dir / "knowledge_cards.jsonl").is_file(),
                        "first refine must persist per-source knowledge")
        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["stage"], "refine",
                         "refine state must be stage-scoped to refine")

        # ── Second run: completed → skip, NO extra LLM calls ──
        card_llm2 = MagicMock(side_effect=[self._card_json("SRC0001")])
        chunker_llm2 = MagicMock(
            side_effect=AssertionError("chunker must NOT run on a completed source"))
        self._run_refine("SRC0001", card_llm=card_llm2, chunker_llm=chunker_llm2)
        self.assertEqual(card_llm2.call_count, 0,
                         "completed source must not call the card LLM again")
        self.assertEqual(chunker_llm2.call_count, 0,
                         "completed source must not call the chunker LLM again")

        # Global index still holds the persisted card after the skip.
        global_cards = (self.work / "atomic" / "knowledge_cards.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(global_cards), 1)

    def test_failed_source_marked_failed_and_exception_propagates(self):
        """Refine exception → state=failed (with error) → exception re-raised."""
        self._write_asr("SRC0001")

        card_llm = MagicMock(side_effect=RuntimeError("simulated refine failure"))
        with self.assertRaises(RuntimeError) as ctx:
            self._run_refine("SRC0001", card_llm=card_llm)
        self.assertIn("simulated refine failure", str(ctx.exception),
                      "original exception must propagate")

        src_dir = self.work / "atomic/by_source/SRC0001"
        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["stage"], "refine")
        self.assertIn("simulated refine failure", state["error"])

    # ── Round 2B-1 acceptance: SourceState schema + artifact validation ──

    def test_source_state_refine_passes_source_state_model_validation(self):
        """The REAL source_state_refine.json written by the pipeline must pass
        SourceState.model_validate() — extra='forbid' stays strict, and the
        stage field is now a first-class part of the schema."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")

        src_dir = self.work / "atomic/by_source/SRC0001"
        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["stage"], "refine")

        # Raises ValidationError on any unexpected field; succeeds on the
        # stage-aware schema.
        validated = SourceState.model_validate(state)
        self.assertEqual(validated.source_id, "SRC0001")
        self.assertEqual(validated.stage, "refine")
        self.assertEqual(validated.status, SourceStatus.DONE)

    def test_completed_done_missing_artifact_does_not_skip_reprocesses(self):
        """state=done but knowledge_cards.jsonl is MISSING → not a valid skip:
        reset to pending and reprocess (LLM runs again), restoring the artifact."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")

        src_dir = self.work / "atomic/by_source/SRC0001"
        (src_dir / "knowledge_cards.jsonl").unlink()

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm)
        self.assertGreater(card_llm.call_count, 0,
                           "missing artifact must force reprocessing (LLM called)")

        # Reprocessing restores the artifact and returns to done.
        self.assertTrue((src_dir / "knowledge_cards.jsonl").is_file())
        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done")

    def test_completed_done_corrupt_jsonl_does_not_skip_reprocesses(self):
        """state=done but a required JSONL is CORRUPT → not a valid skip:
        reset to pending and reprocess, overwriting the damaged artifact."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")

        src_dir = self.work / "atomic/by_source/SRC0001"
        (src_dir / "case_cards.jsonl").write_text("{this is not valid json}\n",
                                                  encoding="utf-8")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm)
        self.assertGreater(card_llm.call_count, 0,
                           "corrupt JSONL must force reprocessing (LLM called)")

        # The reprocess rewrites a clean (empty here) case_cards.jsonl.
        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done")

    def test_completed_done_all_five_artifacts_valid_skips_no_llm(self):
        """state=done AND all five required artifacts exist + parse as JSONL
        (empty ones allowed) → genuine skip, zero LLM calls."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")

        src_dir = self.work / "atomic/by_source/SRC0001"
        for name in ["chunks.jsonl", "knowledge_cards.jsonl", "case_cards.jsonl",
                     "anti_patterns.jsonl", "creative_formats.jsonl"]:
            self.assertTrue((src_dir / name).is_file(),
                            f"required artifact missing after refine: {name}")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        chunker_llm = MagicMock(return_value=self._chunker_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm, chunker_llm=chunker_llm)
        self.assertEqual(card_llm.call_count, 0,
                         "all artifacts valid → skip, card LLM not called")
        self.assertEqual(chunker_llm.call_count, 0,
                         "all artifacts valid → skip, chunker LLM not called")

    def test_refine_artifacts_complete_allows_empty_files(self):
        """Completeness helper: empty JSONL files are VALID (a card type may
        have 0 items); only missing or corrupt files make completion invalid."""
        with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                   self.work / "atomic/by_source"):
            sp = SourcePersistence("SRC0001", stage="refine")
            self.assertFalse(sp.refine_artifacts_complete(),
                             "no artifacts yet → not complete")

            for name in ["chunks.jsonl", "knowledge_cards.jsonl",
                         "case_cards.jsonl", "anti_patterns.jsonl",
                         "creative_formats.jsonl"]:
                (sp.source_dir / name).write_text("", encoding="utf-8")
            self.assertTrue(sp.refine_artifacts_complete(),
                            "all five EMPTY files must count as complete")

            (sp.source_dir / "case_cards.jsonl").write_text(
                "{not json}\n", encoding="utf-8")
            self.assertFalse(sp.refine_artifacts_complete(),
                             "a corrupt line must invalidate the completed state")

    # ── Round 2B-1 acceptance: state ownership + semantic artifact validation ──

    def test_state_stage_mismatch_fails_fast_not_skipped(self):
        """source_state_refine.json stage='transcribe' → ownership violation:
        refine must NOT treat it as a completed skip. It fails fast
        (StateOwnershipError) and the tampered state is NOT overwritten."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")  # produces done state, stage=refine

        src_dir = self.work / "atomic/by_source/SRC0001"
        state_path = src_dir / "source_state_refine.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "transcribe"  # ← wrong-stage corruption
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        with self.assertRaises(StateOwnershipError) as ctx:
            self._run_refine("SRC0001", card_llm=card_llm)
        self.assertIn("transcribe", str(ctx.exception),
                      "error must name the mismatched stage")
        self.assertEqual(card_llm.call_count, 0,
                         "ownership mismatch must fail before any LLM call")

        # Fail-fast means the corrupt state was NOT overwritten to keep running.
        after = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(after["stage"], "transcribe",
                         "foreign/corrupt state must not be silently rewritten")

    def test_state_source_id_mismatch_fails_fast_not_skipped(self):
        """source_state_refine.json source_id differs from the Source directory
        → ownership violation: refine must NOT normally skip. Fails fast."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")

        src_dir = self.work / "atomic/by_source/SRC0001"
        state_path = src_dir / "source_state_refine.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["source_id"] = "SRC0999"  # ← wrong-owner corruption
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        with self.assertRaises(StateOwnershipError) as ctx:
            self._run_refine("SRC0001", card_llm=card_llm)
        self.assertIn("SRC0999", str(ctx.exception),
                      "error must name the mismatched source_id")
        self.assertEqual(card_llm.call_count, 0,
                         "ownership mismatch must fail before any LLM call")

        after = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(after["source_id"], "SRC0999",
                         "foreign/corrupt state must not be silently rewritten")

    def test_completed_done_schema_invalid_card_does_not_skip_reprocesses(self):
        """knowledge_cards.jsonl line '{}' is not a valid KnowledgeCard
        (missing required fields) → artifact incomplete → reset to pending →
        reprocess (LLM runs again) and the card is restored."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")

        src_dir = self.work / "atomic/by_source/SRC0001"
        (src_dir / "knowledge_cards.jsonl").write_text("{}\n", encoding="utf-8")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm)
        self.assertGreater(card_llm.call_count, 0,
                           "schema-invalid card must force reprocessing (LLM called)")

        lines = [l for l in (src_dir / "knowledge_cards.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 1,
                         "reprocess must restore a single valid card")
        self.assertEqual(json.loads(lines[0])["source"]["source_id"], "SRC0001")
        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done",
                         "reprocess must return the source to done")

    def test_completed_done_cross_source_card_does_not_skip_reprocesses(self):
        """A schema-valid KnowledgeCard owned by ANOTHER source in
        knowledge_cards.jsonl → ownership violation → artifact incomplete →
        reset to pending → reprocess (restores this source's own card)."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")

        src_dir = self.work / "atomic/by_source/SRC0001"
        foreign = {
            "knowledge_id": "KID_foreign",
            "display_id": "K000001",
            "knowledge_type": "technique",
            "title": "跨源卡片",
            "question_ids": [],
            "core_idea": "这是一个属于其他来源的合法知识卡片内容",
            "why_it_works": "验证跨源污染",
            "applicable_when": [],
            "not_applicable_when": [],
            "method": [],
            "examples": [],
            "anti_patterns": [],
            "stages": [],
            "content_format": [],
            "source": {"source_id": "SRC0002", "video_title": "别的视频",
                       "start_sec": 0.0, "end_sec": 5.0},
            "confidence": 0.9,
            "visual_evidence": [],
            "origin": "explicit",
            "evidence_segment_ids": ["SRC0002-SEG0001"],
            "inference_basis": "",
            "source_knowledge_ids": [],
            "knowledge_scope": "methodology",
            "created_by_run_id": "run_foreign",
            "chunk_id": "CHK_foreign",
            "retrieval_context": "",
        }
        # The card is schema-valid on its own — only its source is wrong.
        KnowledgeCard.model_validate(foreign)
        (src_dir / "knowledge_cards.jsonl").write_text(
            json.dumps(foreign, ensure_ascii=False) + "\n", encoding="utf-8")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm)
        self.assertGreater(card_llm.call_count, 0,
                           "cross-source card must force reprocessing (LLM called)")

        lines = [l for l in (src_dir / "knowledge_cards.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 1,
                         "reprocess must restore a single card")
        self.assertEqual(json.loads(lines[0])["source"]["source_id"], "SRC0001",
                         "reprocess must restore THIS source's card, not the foreign one")
        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done")

    # ── Round 2B-2: crash / stale recovery (integration through real main) ──

    def _write_stale_state(self, source_id, pid, run_id):
        """Write a status=processing state file with the given owner pid."""
        src_dir = self.work / "atomic/by_source" / source_id
        src_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "source_id": source_id,
            "stage": "refine",
            "status": "processing",
            "run_id": run_id,
            "pid": pid,
            "started_at": "2026-08-09T00:00:00",
            "completed_at": "",
            "error": "",
            "stats": {},
        }
        (src_dir / "source_state_refine.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8")
        return src_dir

    def test_stale_processing_reclaimed_reprocesses_to_done(self):
        """Test A: crash left status=processing + dead pid. The next real
        food_ip_refine.main() run reclaims the stale marker, reprocesses the
        source, writes a complete per-source artifact set, and reaches done —
        half-written artifacts from the crashed run are NOT trusted."""
        self._write_asr("SRC0001")
        src_dir = self._write_stale_state(
            "SRC0001", pid=99999999, run_id="run_crashed_A")
        # A crashed run may have left a PARTIAL artifact behind.
        (src_dir / "chunks.jsonl").write_text("", encoding="utf-8")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm)
        self.assertGreater(card_llm.call_count, 0,
                           "stale source must be reprocessed (LLM called)")

        # The complete per-source artifact set is regenerated.
        for name in ["chunks.jsonl", "knowledge_cards.jsonl", "case_cards.jsonl",
                     "anti_patterns.jsonl", "creative_formats.jsonl"]:
            self.assertTrue((src_dir / name).is_file(),
                            f"stale recovery must regenerate artifact: {name}")
        chunk_lines = [l for l in (src_dir / "chunks.jsonl")
                       .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(chunk_lines), 1,
                         "partial empty chunks.jsonl must be fully regenerated")
        self.assertEqual(json.loads(chunk_lines[0])["source_id"], "SRC0001")

        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["stage"], "refine")

    def test_after_stale_recovery_rerun_skips_no_llm(self):
        """Test B: full loop — crash → recover (LLM runs) → completed → rerun
        skip (chunker/card LLM 0 calls)."""
        self._write_asr("SRC0001")
        src_dir = self._write_stale_state(
            "SRC0001", pid=99999998, run_id="run_crashed_B")

        # ── Recovery run: stale reclaimed, reprocessed, completed ──
        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm)
        self.assertGreater(card_llm.call_count, 0,
                           "recovery run must reprocess (LLM called)")
        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done",
                         "recovery must end in the completed state")

        # ── Rerun after recovery: completed validation passes → skip, 0 LLM ──
        card_llm2 = MagicMock(return_value=self._card_json("SRC0001"))
        chunker_llm2 = MagicMock(
            side_effect=AssertionError("chunker must NOT run after recovery skip"))
        self._run_refine("SRC0001", card_llm=card_llm2, chunker_llm=chunker_llm2)
        self.assertEqual(card_llm2.call_count, 0,
                         "post-recovery rerun must skip (card LLM 0 calls)")
        self.assertEqual(chunker_llm2.call_count, 0,
                         "post-recovery rerun must skip (chunker LLM 0 calls)")

    def test_active_processing_not_preempted(self):
        """Test C: status=processing with a LIVE pid → refine must refuse:
        no LLM call, state NOT reclaimed or overwritten (still processing,
        same pid and run_id)."""
        self._write_asr("SRC0001")
        live_pid = os.getpid()  # this test process is alive
        src_dir = self._write_stale_state(
            "SRC0001", pid=live_pid, run_id="run_active_C")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        chunker_llm = MagicMock(return_value=self._chunker_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm, chunker_llm=chunker_llm)
        self.assertEqual(card_llm.call_count, 0,
                         "active processing must NOT be preempted (card LLM 0)")
        self.assertEqual(chunker_llm.call_count, 0,
                         "active processing must NOT be preempted (chunker LLM 0)")

        # State was neither reclaimed nor overwritten.
        after = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(after["status"], "processing")
        self.assertEqual(after["pid"], live_pid)
        self.assertEqual(after["run_id"], "run_active_C",
                         "live processing state must not be silently overwritten")

    def test_corrupt_state_json_fails_fast(self):
        """Test D: source_state_refine.json exists but is NOT valid JSON →
        fail-fast (StateOwnershipError), zero LLM calls, and the corrupt file
        is NOT auto-deleted or auto-repaired."""
        self._write_asr("SRC0001")
        src_dir = self.work / "atomic/by_source" / "SRC0001"
        src_dir.mkdir(parents=True, exist_ok=True)
        state_path = src_dir / "source_state_refine.json"
        corrupt = "{ this is not valid json !!!"
        state_path.write_text(corrupt, encoding="utf-8")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        with self.assertRaises(StateOwnershipError) as ctx:
            self._run_refine("SRC0001", card_llm=card_llm)
        self.assertIn("cannot be parsed", str(ctx.exception))
        self.assertEqual(card_llm.call_count, 0,
                         "corrupt state must fail before any LLM call")

        # Corruption is never auto-fixed by the pipeline.
        self.assertTrue(state_path.exists(), "corrupt state must not be deleted")
        self.assertEqual(state_path.read_text(encoding="utf-8"), corrupt,
                         "corrupt state must not be silently rewritten")

    # ── P0-FINAL acceptance: pid<=0 stale, rerun idempotency, crash-before-
    # mark_done, persistence failure (Cases C/F/G/H) ──

    def test_pid_zero_processing_treated_as_stale_reprocesses(self):
        """status=processing with pid=0 (no recorded owner) must be treated as
        stale: reclaimed and reprocessed to done — NOT skipped, NOT kept."""
        self._write_asr("SRC0001")
        src_dir = self._write_stale_state(
            "SRC0001", pid=0, run_id="run_pid0")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm)
        self.assertGreater(card_llm.call_count, 0,
                           "pid=0 processing must be reclaimed (LLM called)")

        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done",
                         "pid=0 stale marker must be reprocessed to done")

    def test_pid_negative_processing_treated_as_stale_reprocesses(self):
        """status=processing with pid<0 must be treated as stale: reclaimed
        and reprocessed to done."""
        self._write_asr("SRC0001")
        src_dir = self._write_stale_state(
            "SRC0001", pid=-42, run_id="run_pidneg")

        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm)
        self.assertGreater(card_llm.call_count, 0,
                           "pid<0 processing must be reclaimed (LLM called)")

        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done",
                         "pid<0 stale marker must be reprocessed to done")

    def test_reset_and_rerun_idempotent_no_duplicates(self):
        """Full lifecycle twice (explicit reset between runs): Source/Segment/
        Chunk/Knowledge identity is stable, and per-source + global data contain
        NO duplicate entities after the second run."""
        self._write_asr("SRC0001")

        # ── First full run ──
        self._run_refine("SRC0001")
        src_dir = self.work / "atomic/by_source/SRC0001"
        chunks1 = [json.loads(l) for l in (src_dir / "chunks.jsonl")
                   .read_text(encoding="utf-8").splitlines() if l.strip()]
        cards1 = [json.loads(l) for l in (src_dir / "knowledge_cards.jsonl")
                  .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(chunks1), 1)
        self.assertEqual(len(cards1), 1)
        chunk_id1 = chunks1[0]["chunk_id"]
        kid1 = cards1[0]["knowledge_id"]
        seg_ids1 = chunks1[0]["segment_ids"]

        # ── Explicit reset to pending (test-only recovery flow) ──
        # Must stay inside the patched dir scope — never touch real E:\ output.
        with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                   self.work / "atomic/by_source"):
            sp = SourcePersistence("SRC0001", stage="refine")
            sp.reset_to_pending("P0-FINAL rerun idempotency check")

        # ── Second full run: same deterministic identity, no duplicates ──
        self._run_refine("SRC0001")
        chunks2 = [json.loads(l) for l in (src_dir / "chunks.jsonl")
                   .read_text(encoding="utf-8").splitlines() if l.strip()]
        cards2 = [json.loads(l) for l in (src_dir / "knowledge_cards.jsonl")
                  .read_text(encoding="utf-8").splitlines() if l.strip()]

        self.assertEqual(len(chunks2), 1, "rerun must not duplicate chunks")
        self.assertEqual(len(cards2), 1, "rerun must not duplicate knowledge")
        self.assertEqual(chunks2[0]["chunk_id"], chunk_id1,
                         "chunk identity must be stable across reruns")
        self.assertEqual(chunks2[0]["segment_ids"], seg_ids1,
                         "chunk evidence segments must be stable across reruns")
        self.assertEqual(cards2[0]["knowledge_id"], kid1,
                         "knowledge identity must be stable across reruns")
        self.assertEqual(cards2[0]["source"]["source_id"], "SRC0001")

        # Global index rebuilt from per-source → still exactly one card.
        global_cards = [l for l in (self.work / "atomic" / "knowledge_cards.jsonl")
                        .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(global_cards), 1,
                         "global index must not accumulate duplicate cards")
        global_chunks = [l for l in (self.work / "atomic" / "chunks.jsonl")
                         .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(global_chunks), 1,
                         "global index must not accumulate duplicate chunks")

        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done",
                         "rerun must end in the completed state")

    def test_crash_after_persist_before_mark_done_no_duplicates(self):
        """Case G: complete per-source artifacts are on disk but the run crashed
        BEFORE mark_done (state=processing, dead pid). The next run reclaims the
        stale marker, reprocesses, and produces NO duplicate chunks/knowledge —
        deterministic IDs + overwrite persistence guarantee it."""
        self._write_asr("SRC0001")
        src_dir = self.work / "atomic/by_source/SRC0001"

        # Simulate a run that persisted successfully then crashed pre-mark_done:
        # run once to produce the complete artifact set, then flip the state to
        # a processing marker owned by a now-dead pid.
        self._run_refine("SRC0001")
        chunks_before = len([l for l in (src_dir / "chunks.jsonl")
                             .read_text(encoding="utf-8").splitlines() if l.strip()])
        cards_before = len([l for l in (src_dir / "knowledge_cards.jsonl")
                            .read_text(encoding="utf-8").splitlines() if l.strip()])
        self.assertEqual(chunks_before, 1)
        self.assertEqual(cards_before, 1)

        self._write_stale_state("SRC0001", pid=99999997, run_id="run_crashed_after_persist")

        # ── Recovery run ──
        card_llm = MagicMock(return_value=self._card_json("SRC0001"))
        self._run_refine("SRC0001", card_llm=card_llm)
        self.assertGreater(card_llm.call_count, 0,
                           "crash-before-mark_done must be reprocessed (LLM called)")

        # No duplicate entities: per-source files hold exactly one of each.
        chunks_after = [json.loads(l) for l in (src_dir / "chunks.jsonl")
                        .read_text(encoding="utf-8").splitlines() if l.strip()]
        cards_after = [json.loads(l) for l in (src_dir / "knowledge_cards.jsonl")
                       .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(chunks_after), 1,
                         "crash-before-mark_done must not duplicate chunks")
        self.assertEqual(len(cards_after), 1,
                         "crash-before-mark_done must not duplicate knowledge")
        self.assertEqual(chunks_after[0]["source_id"], "SRC0001")
        self.assertEqual(cards_after[0]["source"]["source_id"], "SRC0001")

        # Global index consistent.
        global_cards = [l for l in (self.work / "atomic" / "knowledge_cards.jsonl")
                        .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(global_cards), 1,
                         "crash-before-mark_done must not duplicate global cards")

        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done",
                         "recovery must end in the completed state")

    def test_persistence_failure_marks_failed_not_done(self):
        """Case H: a failure DURING per-source persistence must never leave a
        bare completed marker. The source is marked failed, the exception
        propagates, and the state is NOT done (no 'done but not saved')."""
        self._write_asr("SRC0001")
        src_dir = self.work / "atomic/by_source/SRC0001"

        from food_ip_persistence import SourcePersistence as _SP
        with patch.object(_SP, 'save_knowledge_cards',
                          side_effect=RuntimeError("disk full during persistence")):
            with self.assertRaises(RuntimeError) as ctx:
                self._run_refine("SRC0001")
            self.assertIn("disk full during persistence", str(ctx.exception),
                          "persistence failure must propagate")

        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed",
                         "persistence failure must mark the source failed, NOT done")
        self.assertIn("disk full during persistence", state["error"])
        # is_completed reads the state file — keep it inside the patched scope.
        with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                   self.work / "atomic/by_source"):
            sp = SourcePersistence("SRC0001", stage="refine")
            self.assertFalse(sp.is_completed(),
                             "a failed persistence must never look completed")

    # ── P0-FINAL acceptance: full Evidence / Provenance trace-back ──

    def test_evidence_chain_card_to_chunk_to_asr_segment_to_source(self):
        """Walk one persisted KnowledgeCard all the way back to its evidence:
        Card → chunk (chunk_id) → segment_ids → ASRSegments on disk →
        Whisper-native timestamps + raw_text → Source manifest. Proves the
        final knowledge is traceable to the real Source and evidence positions.
        Also asserts the teacher's original words (raw_text) are preserved
        separately from the corrected text."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")

        src_dir = self.work / "atomic/by_source/SRC0001"

        # ── Card (top of the chain) ──
        cards = [json.loads(l) for l in (src_dir / "knowledge_cards.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertTrue(card["knowledge_id"].startswith("KID_"),
                        "knowledge_id must be deterministic (KID_)")
        self.assertTrue(card["chunk_id"].startswith("CHK_"),
                        "card must carry its backing chunk_id")

        # ── Atomic → Chunk ──
        chunks = {c["chunk_id"]: c for c in
                  (json.loads(l) for l in (src_dir / "chunks.jsonl")
                   .read_text(encoding="utf-8").splitlines() if l.strip())}
        chunk = chunks.get(card["chunk_id"])
        self.assertIsNotNone(chunk,
                             "card.chunk_id must resolve to a persisted chunk")
        self.assertEqual(chunk["source_id"], "SRC0001")
        seg_ids = chunk["segment_ids"]
        self.assertEqual(seg_ids, ["SRC0001-SEG0001", "SRC0001-SEG0002"])

        # ── Chunk → ASR Segments on disk (Whisper-native authority) ──
        asr = json.loads((self.whisper_segments /
                          "SRC0001_asr_whisper_segments.json")
                         .read_text(encoding="utf-8"))
        seg_map = {s["segment_id"]: s for s in asr["segments"]}
        for sid in seg_ids:
            seg = seg_map.get(sid)
            self.assertIsNotNone(seg, f"evidence segment {sid} must exist in ASR file")
            self.assertEqual(seg["source_id"], "SRC0001")
            self.assertIn("start_sec", seg)
            self.assertIn("end_sec", seg)
            self.assertTrue(seg["raw_text"], f"{sid} must keep the teacher's raw_text")

        # ── Chunk → timestamp → Source (programmatic, from segments) ──
        self.assertEqual(chunk["start_sec"], 0.0)
        self.assertEqual(chunk["end_sec"], 10.0)
        self.assertEqual(card["source"]["source_id"], "SRC0001")
        self.assertEqual(card["source"]["start_sec"], 0.0)
        self.assertEqual(card["source"]["end_sec"], 10.0)

        # Evidence fields on the card must reference the SAME segments.
        self.assertEqual(card["evidence_segment_ids"], seg_ids)

        # ── raw_text (teacher words) vs corrected_text stay distinct ──
        seg1 = seg_map["SRC0001-SEG0001"]
        self.assertEqual(seg1["raw_text"], "第一条测试文本")
        self.assertEqual(seg1["corrected_text"], "第一条测试文本")
        self.assertEqual(seg1["segment_id"], "SRC0001-SEG0001")

        # Source manifest exists and anchors the Source identity. (The full
        # transcription manifest contract — asr_segments_path + segment_count —
        # is asserted by the real transcription CLI in
        # TestTranscribeToRefineIntegration; the minimal helper manifest used
        # here only carries what refine reads: source_id + title.)
        manifest = json.loads((self.manifests / "SRC0001.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_id"], "SRC0001")

    # ── P0-FINAL Issue 1: global index rebuild is STRICT (no silent corruption)
    #    and a rebuild failure blocks mark_done (source → failed, never a bare
    #    done marker backed by a stale/missing global index). ──

    def test_load_jsonl_raises_on_corrupt_authoritative_line(self):
        """Issue 1: a corrupt line in authoritative per-source data must RAISE,
        never be silently skipped (silent skipping makes the global index
        silently incomplete)."""
        with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                   self.work / "atomic/by_source"):
            sp = SourcePersistence("SRC0001", stage="refine")
            (sp.source_dir / "chunks.jsonl").write_text(
                '{"chunk_id": "CHK_a"}\n{this is not json}\n',
                encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                sp.load_chunks()
            self.assertIn("chunks.jsonl", str(ctx.exception),
                          "error must name the corrupt file")
            self.assertIn("SRC0001", str(ctx.exception),
                          "error must name the source")

    def test_rebuild_global_indices_raises_on_corrupt_per_source_line(self):
        """Issue 1: rebuild_global_indices must not swallow corrupt per-source
        data. A corrupt line in ANY source makes it RAISE, and the global files
        are left untouched — never a partially/incompletely rebuilt index."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")  # clean rebuild → global index exists
        global_cards = self.work / "atomic/knowledge_cards.jsonl"
        before = global_cards.read_text(encoding="utf-8")
        self.assertTrue(before.strip())

        # Corrupt a DIFFERENT, already-DONE source's authoritative per-source
        # data. Done sources are eligible for the snapshot, so the corrupt line
        # must RAISE (never silently dropped from the index).
        bad = self._mark_state("SRC0002", "done")
        (bad / "chunks.jsonl").write_text("{not json}\n", encoding="utf-8")

        from food_ip_persistence import rebuild_global_indices
        with patch('food_ip_persistence.ATOMIC_DIR', self.work / "atomic"):
            with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                       self.work / "atomic/by_source"):
                with patch('food_ip_persistence.ensure_dirs'):
                    with self.assertRaises(RuntimeError) as ctx:
                        rebuild_global_indices()
                    self.assertIn("SRC0002", str(ctx.exception),
                                  "error must name the corrupt source")
        self.assertEqual(global_cards.read_text(encoding="utf-8"), before,
                         "a failed rebuild must not rewrite the global index")

    def test_rebuild_global_indices_recovers_after_corruption_fixed(self):
        """Issue 1: recovery is deterministic — once the corrupt per-source data
        is fixed, rebuild succeeds and the global index matches per-source truth."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")
        src_dir = self.work / "atomic/by_source/SRC0001"
        valid_chunks = (src_dir / "chunks.jsonl").read_text(encoding="utf-8")

        # Corrupt SRC0001's own chunks → rebuild raises.
        (src_dir / "chunks.jsonl").write_text("{not json}\n", encoding="utf-8")

        from food_ip_persistence import rebuild_global_indices
        with patch('food_ip_persistence.ATOMIC_DIR', self.work / "atomic"):
            with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                       self.work / "atomic/by_source"):
                with patch('food_ip_persistence.ensure_dirs'):
                    with self.assertRaises(RuntimeError):
                        rebuild_global_indices()
                    # Fix the corruption → rebuild succeeds and equals truth.
                    (src_dir / "chunks.jsonl").write_text(
                        valid_chunks, encoding="utf-8")
                    counts = rebuild_global_indices()

        per_source = len([l for l in valid_chunks.splitlines() if l.strip()])
        self.assertEqual(counts["chunks"], per_source,
                         "after recovery the global index must equal per-source truth")
        global_chunks = self.work / "atomic/chunks.jsonl"
        self.assertEqual(len([l for l in global_chunks.read_text(encoding="utf-8")
                              .splitlines() if l.strip()]), per_source)

    def test_rebuild_failure_blocks_mark_done_marks_failed(self):
        """Issue 1 / Case H re-audit: a global-rebuild failure must NOT be a
        WARN-and-forget. The source that hits it is marked FAILED — never done
        with a stale/missing global index — the error is visible in state, and
        its already-saved per-source artifacts are preserved (valid data is
        never cleared)."""
        self._write_asr("SRC0001")
        # Corrupt authoritative data in ANOTHER, already-DONE source dir so the
        # rebuild (which reads ALL eligible per-source dirs) fails for SRC0001
        # as well — a done source's corrupt data must never be silently skipped.
        bad = self._mark_state("SRC0002", "done")
        (bad / "chunks.jsonl").write_text("{not json}\n", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            self._run_refine("SRC0001")
        self.assertIn("chunks.jsonl", str(ctx.exception),
                      "the rebuild failure must propagate, not be swallowed")

        src_dir = self.work / "atomic/by_source/SRC0001"
        state = json.loads((src_dir / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed",
                         "rebuild failure must mark the source failed, NOT done")
        self.assertIn("chunks.jsonl", state["error"],
                      "the failure must be visible in the source state")

        # Per-source artifacts were saved BEFORE the rebuild — preserved.
        self.assertTrue((src_dir / "chunks.jsonl").is_file())
        self.assertTrue((src_dir / "knowledge_cards.jsonl").is_file())
        # The global index must not claim success (no partial/empty write).
        self.assertFalse((self.work / "atomic/knowledge_cards.jsonl").exists(),
                         "a failed rebuild must not leave a 'looks-successful' "
                         "global index")

    def test_rebuild_sources_index_raises_on_corrupt_manifest(self):
        """Issue 1: rebuild_sources_index must not silently drop a corrupt
        per-source manifest from the global sources index."""
        (self.manifests / "SRC0009.json").write_text("{not json}", encoding="utf-8")
        from food_ip_persistence import rebuild_sources_index
        with patch('food_ip_persistence.PER_SOURCE_MANIFESTS_DIR', self.manifests):
            with patch('food_ip_persistence.MANIFESTS_DIR', self.work / "manifests"):
                with patch('food_ip_persistence.ensure_dirs'):
                    with self.assertRaises(RuntimeError) as ctx:
                        rebuild_sources_index()
                    self.assertIn("SRC0009.json", str(ctx.exception),
                                  "error must name the corrupt manifest")

    # ── P0-FINAL Global Snapshot Integrity (2026-08-09): the 5-file global
    #    index is committed as ONE snapshot (no mixed generation on a failed
    #    rebuild), only releasable sources (done+complete, or the explicitly
    #    committing source) may contribute, and every line is validated against
    #    its persisted Pydantic model + source ownership — not just JSON syntax. ──

    def test_rebuild_mid_commit_failure_preserves_previous_snapshot(self):
        """Issue A: a rebuild whose 2nd/3rd global-file swap raises must NOT
        leave a mixed-generation snapshot. The previous complete snapshot is
        rolled back onto disk (all five files identical), no .bak/.tmp staged
        leftovers survive, and a clean rerun rebuilds a coherent snapshot."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")
        atomic = self.work / "atomic"
        names = ["chunks.jsonl", "knowledge_cards.jsonl", "case_cards.jsonl",
                 "anti_patterns.jsonl", "creative_formats.jsonl"]
        before = {name: (atomic / name).read_text(encoding="utf-8") for name in names}
        self.assertTrue(before["chunks.jsonl"].strip(),
                        "previous snapshot must be non-empty")

        from food_ip_persistence import rebuild_global_indices
        real_replace = os.replace
        counter = {"n": 0}

        def flaky_replace(src, dst):
            counter["n"] += 1
            if counter["n"] == 7:  # chunks swapped (1st), knowledge_cards swap fails
                raise OSError("injected OSError on the knowledge_cards swap")
            return real_replace(src, dst)

        with patch('food_ip_persistence.os.replace', side_effect=flaky_replace):
            with patch('food_ip_persistence.ATOMIC_DIR', atomic):
                with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                           self.work / "atomic/by_source"):
                    with patch('food_ip_persistence.ensure_dirs'):
                        with self.assertRaises(OSError) as ctx:
                            rebuild_global_indices()
                        self.assertIn("knowledge_cards swap", str(ctx.exception))

        # No mixed generation: every canonical file equals the previous snapshot.
        for name in names:
            self.assertEqual((atomic / name).read_text(encoding="utf-8"),
                             before[name],
                             f"failed rebuild must roll back {name}")
        self.assertEqual(list(atomic.glob("*.bak")), [],
                         "no backup files may survive a rolled-back commit")
        self.assertEqual(list(atomic.glob("*.tmp")), [],
                         "no staged files may survive a rolled-back commit")

        # A clean rerun (no injection) commits a coherent snapshot.
        with patch('food_ip_persistence.ATOMIC_DIR', atomic):
            with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                       self.work / "atomic/by_source"):
                with patch('food_ip_persistence.ensure_dirs'):
                    counts = rebuild_global_indices()
        self.assertEqual(counts["chunks"], 1)
        self.assertEqual(counts["knowledge_cards"], 1)
        self.assertEqual(list(atomic.glob("*.bak")), [])
        self.assertEqual(list(atomic.glob("*.tmp")), [])

    def test_rebuild_excludes_failed_source_partial_artifacts(self):
        """Issue B / Test 2: a FAILED source with partial (schema-valid)
        artifacts contributes ZERO to the global snapshot; only the done
        source's data enters."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")
        bad = self._mark_state("SRC0002", "failed", error="disk full")
        (bad / "chunks.jsonl").write_text(
            self._valid_chunk_for("SRC0002", "fail"), encoding="utf-8")

        from food_ip_persistence import rebuild_global_indices
        with patch('food_ip_persistence.ATOMIC_DIR', self.work / "atomic"):
            with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                       self.work / "atomic/by_source"):
                with patch('food_ip_persistence.ensure_dirs'):
                    counts = rebuild_global_indices()
        self.assertEqual(counts["chunks"], 1,
                         "failed source must contribute ZERO chunks")
        self.assertEqual(counts["knowledge_cards"], 1)
        lines = [l for l in (self.work / "atomic/chunks.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["source_id"], "SRC0001",
                         "only the done source's chunk may enter the snapshot")

    def test_rebuild_excludes_processing_unrelated_source(self):
        """Issue B / Test 3: an unrelated source that is currently PROCESSING
        (partial artifacts) must NOT contaminate the global snapshot."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")
        bad = self._mark_state("SRC0002", "processing")
        (bad / "chunks.jsonl").write_text(
            self._valid_chunk_for("SRC0002", "proc"), encoding="utf-8")

        from food_ip_persistence import rebuild_global_indices
        with patch('food_ip_persistence.ATOMIC_DIR', self.work / "atomic"):
            with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                       self.work / "atomic/by_source"):
                with patch('food_ip_persistence.ensure_dirs'):
                    counts = rebuild_global_indices()
        self.assertEqual(counts["chunks"], 1,
                         "processing source must not contaminate the snapshot")

    def test_rebuild_fails_fast_on_done_source_missing_artifact(self):
        """Issue B / Test 4: a source that claims done but is missing a required
        artifact must fail-fast — never silently skipped from the snapshot."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")
        atomic = self.work / "atomic"
        before = (atomic / "knowledge_cards.jsonl").read_text(encoding="utf-8")
        self._mark_state("SRC0002", "done")  # done, but ZERO artifacts on disk

        from food_ip_persistence import rebuild_global_indices
        with patch('food_ip_persistence.ATOMIC_DIR', atomic):
            with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                       self.work / "atomic/by_source"):
                with patch('food_ip_persistence.ensure_dirs'):
                    with self.assertRaises(RuntimeError) as ctx:
                        rebuild_global_indices()
                    self.assertIn("SRC0002", str(ctx.exception))
                    self.assertIn("chunks.jsonl", str(ctx.exception))
        self.assertEqual((atomic / "knowledge_cards.jsonl")
                         .read_text(encoding="utf-8"), before,
                         "failed rebuild must not rewrite the previous snapshot")

    def test_rebuild_raises_on_valid_json_invalid_schema(self):
        """Issue C / Test 5: a line that is valid JSON but NOT a valid persisted
        model must fail-fast — JSON syntax validity is not artifact validity."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")
        atomic = self.work / "atomic"
        before = (atomic / "knowledge_cards.jsonl").read_text(encoding="utf-8")
        self._write_all_artifacts("SRC0002", overrides={
            "chunks.jsonl": '{"junk": "still valid json"}\n',
        })
        self._mark_state("SRC0002", "done")

        from food_ip_persistence import rebuild_global_indices
        with patch('food_ip_persistence.ATOMIC_DIR', atomic):
            with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                       self.work / "atomic/by_source"):
                with patch('food_ip_persistence.ensure_dirs'):
                    with self.assertRaises(RuntimeError) as ctx:
                        rebuild_global_indices()
                    self.assertIn("SRC0002", str(ctx.exception))
                    self.assertIn("SemanticChunk", str(ctx.exception),
                                  "error must cite the rejected persisted model")
        self.assertEqual((atomic / "knowledge_cards.jsonl")
                         .read_text(encoding="utf-8"), before,
                         "failed rebuild must not rewrite the previous snapshot")

    def test_rebuild_raises_on_cross_source_artifact(self):
        """Issue C / Test 6: a schema-valid artifact whose source_id belongs to a
        DIFFERENT source must fail-fast (cross-source contamination)."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")
        atomic = self.work / "atomic"
        before = (atomic / "knowledge_cards.jsonl").read_text(encoding="utf-8")
        # A VALID SemanticChunk owned by SRC0001, stored under SRC0002's dir.
        self._write_all_artifacts("SRC0002", overrides={
            "chunks.jsonl": self._valid_chunk_for("SRC0001", "xsrc"),
        })
        self._mark_state("SRC0002", "done")

        from food_ip_persistence import rebuild_global_indices
        with patch('food_ip_persistence.ATOMIC_DIR', atomic):
            with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                       self.work / "atomic/by_source"):
                with patch('food_ip_persistence.ensure_dirs'):
                    with self.assertRaises(RuntimeError) as ctx:
                        rebuild_global_indices()
                    self.assertIn("SRC0002", str(ctx.exception))
                    self.assertIn("SRC0001", str(ctx.exception))
                    self.assertIn("cross-source", str(ctx.exception))
        self.assertEqual((atomic / "knowledge_cards.jsonl")
                         .read_text(encoding="utf-8"), before,
                         "failed rebuild must not rewrite the previous snapshot")

    def test_rebuild_includes_current_committing_source(self):
        """Issue B / Test 7: the currently-committing source (passed explicitly,
        still status=processing because mark_done runs after the rebuild) DOES
        enter the resulting global snapshot; without the explicit commit arg, a
        processing source is excluded."""
        self._write_asr("SRC0001")
        self._run_refine("SRC0001")
        self._write_all_artifacts("SRC0002", overrides={
            "chunks.jsonl": self._valid_chunk_for("SRC0002", "commit"),
        })
        self._mark_state("SRC0002", "processing")

        from food_ip_persistence import rebuild_global_indices
        with patch('food_ip_persistence.ATOMIC_DIR', self.work / "atomic"):
            with patch('food_ip_persistence.ATOMIC_BY_SOURCE_DIR',
                       self.work / "atomic/by_source"):
                with patch('food_ip_persistence.ensure_dirs'):
                    # Plain rebuild: processing SRC0002 is NOT releasable.
                    counts = rebuild_global_indices()
                    self.assertEqual(counts["chunks"], 1)
                    # Explicit committing source: it IS included.
                    counts = rebuild_global_indices(commit_source_id="SRC0002")
        self.assertEqual(counts["chunks"], 2,
                         "the committing source must enter its own snapshot")
        self.assertEqual(counts["knowledge_cards"], 1)
        sids = sorted(json.loads(l)["source_id"]
                      for l in (self.work / "atomic/chunks.jsonl")
                      .read_text(encoding="utf-8").splitlines() if l.strip())
        self.assertEqual(sids, ["SRC0001", "SRC0002"])

    def test_rebuild_after_failure_repaired_runs_done_with_coherent_snapshot(self):
        """Issue B / Test 8: a done source's corruption blocks a NEW source's
        completion (rebuild fail-fast); once repaired, the rerun reaches done and
        the global snapshot is coherent across ALL eligible sources."""
        self._write_asr("SRC0001")
        self._write_asr("SRC0002")
        self._run_refine("SRC0001")
        src1 = self.work / "atomic/by_source/SRC0001"
        valid_chunks = (src1 / "chunks.jsonl").read_text(encoding="utf-8")
        (src1 / "chunks.jsonl").write_text("{not json}\n", encoding="utf-8")

        # SRC0002 cannot complete while SRC0001 (done) is corrupt → failed.
        with self.assertRaises(RuntimeError):
            self._run_refine("SRC0002")
        src2 = self.work / "atomic/by_source/SRC0002"
        state = json.loads((src2 / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed",
                         "a corrupt done source must block a new source's done")

        # Repair the corruption → rerun SRC0002 → done + coherent snapshot.
        (src1 / "chunks.jsonl").write_text(valid_chunks, encoding="utf-8")
        self._run_refine("SRC0002")
        state = json.loads((src2 / "source_state_refine.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done",
                         "after repair the rerun must reach done")

        lines = [l for l in (self.work / "atomic/chunks.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 2, "coherent snapshot: both sources present")
        self.assertEqual(sorted(json.loads(l)["source_id"] for l in lines),
                         ["SRC0001", "SRC0002"])

    # ── P0-FINAL Issue 2: Segment identity contract. Segment ID is
    #    {source_id}-SEG{ordinal} of the authoritative transcription — stable
    #    across reruns of the same transcription; evidence stays Whisper-native.
    #    (The rules in CLAUDE.md §10 document this exact contract.) ──

    def test_segment_identity_contract_stable_across_identical_retranscription(self):
        """Issue 2: identical Whisper output → identical Segment IDs, and the
        downstream chunk_id (hash(source, seg_min, seg_max)) is deterministic —
        so the index-based Segment identity does NOT drift across reruns of the
        same transcription (the P0 idempotency contract). Timestamps stay
        Whisper-native."""
        from food_ip_segments import extract_segments
        from food_ip_models import make_chunk_id

        class _Seg:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        run1 = [_Seg(0.0, 5.0, "a"), _Seg(5.0, 10.0, "b"), _Seg(10.0, 15.0, "c")]
        run2 = [_Seg(0.0, 5.0, "a"), _Seg(5.0, 10.0, "b"), _Seg(10.0, 15.0, "c")]
        out1 = extract_segments(run1, "SRC0001")
        out2 = extract_segments(run2, "SRC0001")

        ids = [s["segment_id"] for s in out1]
        self.assertEqual(ids,
                         ["SRC0001-SEG0001", "SRC0001-SEG0002", "SRC0001-SEG0003"],
                         "Segment ID = source_id + within-transcription ordinal")
        self.assertEqual(ids, [s["segment_id"] for s in out2],
                         "identical Whisper output must yield identical Segment IDs")

        self.assertEqual(make_chunk_id("SRC0001", "SRC0001-SEG0001", "SRC0001-SEG0002"),
                         make_chunk_id("SRC0001", "SRC0001-SEG0001", "SRC0001-SEG0002"),
                         "chunk_id must be deterministic from the segment span")
        self.assertEqual(out1[0]["start_sec"], 0.0)
        self.assertEqual(out1[2]["end_sec"], 15.0)


class TestPhase05ThinkingDisabledAndLoudFailure(unittest.TestCase):
    """Phase 0.5 extraction fixes:
      (1) reasoning models (deepseek-v4-*) must be called with
          thinking={"type": "disabled"} so internal reasoning does not consume
          the max_tokens budget and truncate/empty the actual content;
      (2) an LLM call that fails after retries must RAISE instead of returning
          None, so the Source is marked failed — never completed with 0 chunks.
    """

    def test_refiner_call_llm_sends_thinking_disabled(self):
        """Refiner._call_llm payload must carry thinking disabled."""
        import requests
        from food_ip_refine import FoodIPRefiner

        sent = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            sent["payload"] = json
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"content": "ok"}}]
            }
            return resp

        with patch.object(requests, "post", side_effect=fake_post):
            refiner = FoodIPRefiner(api_key="fake-key")
            out = refiner._call_llm("sys", "user", max_tokens=100)
        self.assertEqual(out, "ok")
        self.assertEqual(sent["payload"].get("thinking"),
                         {"type": "disabled"},
                         "Refiner LLM payload must disable thinking")

    def test_chunker_call_llm_sends_thinking_disabled(self):
        """semantic_chunker._call_llm payload must carry thinking disabled."""
        import requests
        from semantic_chunker import _call_llm

        sent = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            sent["payload"] = json
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"content": "ok"}}]
            }
            return resp

        with patch.object(requests, "post", side_effect=fake_post):
            out = _call_llm("sys", "user", api_key="k", base_url="http://x",
                            model="deepseek-v4-flash", max_tokens=100)
        self.assertEqual(out, "ok")
        self.assertEqual(sent["payload"].get("thinking"),
                         {"type": "disabled"},
                         "chunker LLM payload must disable thinking")

    def test_refiner_call_llm_raises_after_retries_not_none(self):
        """A persistently failing LLM call must RAISE, not return None.

        Returning None used to let the Source complete with 0 chunks. Now the
        failure propagates so run_source marks the Source failed.
        """
        import requests
        from food_ip_refine import FoodIPRefiner

        resp = MagicMock()
        resp.status_code = 500
        resp.text = "internal error"

        with patch.object(requests, "post", return_value=resp):
            refiner = FoodIPRefiner(api_key="fake-key")
            with self.assertRaises(RuntimeError) as ctx:
                refiner._call_llm("sys", "user", max_tokens=100)
        self.assertIn("500", str(ctx.exception))

    def test_chunker_call_llm_raises_after_retries_not_none(self):
        """Chunker LLM call failure must raise after retries, not return None."""
        import requests
        from semantic_chunker import _call_llm

        resp = MagicMock()
        resp.status_code = 503
        resp.text = "overloaded"

        with patch.object(requests, "post", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                _call_llm("sys", "user", api_key="k", base_url="http://x",
                          model="deepseek-v4-flash", max_tokens=100)
        self.assertIn("503", str(ctx.exception))


class TestKnowledgeGraphCandidates(unittest.TestCase):
    """Phase 0.5: knowledge_graph.generate_candidates must not crash with
    NameError (`_` was referenced but never defined) and must dedupe pairs."""

    def test_generate_candidates_no_nameerror_and_dedupes(self):
        from knowledge_graph import generate_candidates

        cards = [
            # Two cards sharing question Q001 (candidate via shared_qid).
            {"knowledge_id": "KID_a", "question_ids": ["Q001"],
             "stages": ["planning"], "content_format": [],
             "knowledge_type": "principle"},
            {"knowledge_id": "KID_b", "question_ids": ["Q001"],
             "stages": ["planning"], "content_format": [],
             "knowledge_type": "technique"},
        ]
        cands = generate_candidates(cards)
        self.assertIsInstance(cands, list)
        self.assertGreater(len(cands), 0, "shared question must produce candidates")
        # (a,b) appears at most once regardless of strategy overlap.
        pairs = [(c[0]["knowledge_id"], c[1]["knowledge_id"]) for c in cands]
        self.assertEqual(len(pairs), len(set(pairs)),
                         "candidate pairs must be deduplicated")

    def test_generate_candidates_single_card_returns_empty(self):
        from knowledge_graph import generate_candidates

        cards = [{"knowledge_id": "KID_a", "question_ids": ["Q001"],
                  "stages": [], "content_format": [], "knowledge_type": "principle"}]
        self.assertEqual(generate_candidates(cards), [])


class TestKnowledgeRelationContract(unittest.TestCase):
    """KnowledgeRelation is the single persisted relation contract."""

    def test_model_and_exported_schema_are_consistent(self):
        schema_path = Path(__file__).parent / "food_ip_schemas" / "knowledge_relation.schema.json"
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        self.assertEqual(
            set(schema["properties"]),
            {"from_id", "to_id", "relation", "note", "can_merge"},
        )
        self.assertEqual(set(schema["required"]), {"from_id", "to_id", "relation"})
        self.assertFalse(schema["additionalProperties"])
        valid = KnowledgeRelation(
            from_id="KID_a", to_id="KID_b", relation="complementary",
        )
        self.assertEqual(set(valid.model_dump()), set(schema["properties"]))
        with self.assertRaises(Exception):
            KnowledgeRelation.model_validate({
                "from_id": "KID_a", "to_id": "KID_b",
                "relation": "complementary", "from": "KID_a",
            })

    def test_relation_generation_returns_only_formal_fields(self):
        from knowledge_graph import detect_relations

        cards = [
            {"knowledge_id": "KID_a", "question_ids": ["Q001"],
             "stages": [], "content_format": [], "knowledge_type": "principle"},
            {"knowledge_id": "KID_b", "question_ids": ["Q001"],
             "stages": [], "content_format": [], "knowledge_type": "technique"},
        ]
        with patch("knowledge_graph._compare_pair", return_value={
            "relation": "complementary", "note": "test", "can_merge": False,
        }):
            relations = detect_relations(cards, api_key="test-key")

        self.assertGreaterEqual(len(relations), 1)
        self.assertEqual(
            set(relations[0]), {"from_id", "to_id", "relation", "note", "can_merge"}
        )
        self.assertNotIn("from", relations[0])
        self.assertNotIn("to", relations[0])
        self.assertNotIn("candidate_strategy", relations[0])
        self.assertNotIn("candidate_priority", relations[0])
        KnowledgeRelation.model_validate(relations[0])

        with patch("knowledge_graph._compare_pair", return_value={
            "relation": "complementary", "note": "test", "can_merge": False,
            "from_id": "KID_wrong",
        }):
            self.assertEqual(detect_relations(cards, api_key="test-key"), [])

    def test_relation_persistence_fails_before_replacing_target(self):
        from food_ip_refine import FoodIPRefiner

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "knowledge_relations.jsonl"
            path.write_text("sentinel\n", encoding="utf-8")
            refiner = object.__new__(FoodIPRefiner)
            invalid = [{
                "from_id": "KID_a", "to_id": "KID_b",
                "relation": "complementary", "unexpected": True,
            }]
            with self.assertRaises(Exception):
                refiner._write_jsonl(path, invalid, model=KnowledgeRelation)
            self.assertEqual(path.read_text(encoding="utf-8"), "sentinel\n")
            self.assertFalse(Path(str(path) + ".tmp").exists())

    def test_persisted_relations_validate_and_have_existing_endpoints(self):
        from food_ip_config import GRAPH_DIR, ATOMIC_DIR

        relation_path = GRAPH_DIR / "knowledge_relations.jsonl"
        cards_path = ATOMIC_DIR / "knowledge_cards.jsonl"
        if not relation_path.is_file() or not cards_path.is_file():
            self.skipTest("external knowledge snapshot is not available")

        with open(cards_path, "r", encoding="utf-8") as f:
            card_ids = {
                json.loads(line)["knowledge_id"]
                for line in f if line.strip()
            }
        with open(relation_path, "r", encoding="utf-8") as f:
            relations = [KnowledgeRelation.model_validate(json.loads(line))
                         for line in f if line.strip()]

        self.assertEqual(len(relations), 51)
        self.assertTrue(all(r.from_id in card_ids and r.to_id in card_ids for r in relations))
        self.assertTrue(all(r.from_id != r.to_id for r in relations))
        pairs = [(r.from_id, r.to_id) for r in relations]
        self.assertEqual(len(pairs), len(set(pairs)))


class TestQuestionSynthesisPromptSchemaConsistency(unittest.TestCase):
    """Phase 0.5: QUESTION_SYNTHESIS_PROMPT must match the persisted
    QuestionSynthesisLLMOutput schema. The conditions field is
    list[dict[str, str]] — the prompt must instruct dict objects, not strings.
    """

    def test_prompt_tells_conditions_are_dict_array_not_string(self):
        from food_ip_refine import QUESTION_SYNTHESIS_PROMPT
        # The prompt must describe conditions as a dict array (matching
        # list[dict[str, str]]), NOT lump conditions into the string arrays.
        self.assertIn("conditions 是字典数组", QUESTION_SYNTHESIS_PROMPT)
        self.assertIn('{"条件": "...", "影响": "..."}', QUESTION_SYNTHESIS_PROMPT)
        # And it must NOT claim conditions is a string array.
        self.assertNotIn(
            "decision_logic、conditions、common_mistakes、related_cases、"
            "evidence_sources 都是字符串数组",
            QUESTION_SYNTHESIS_PROMPT)

    def test_schema_accepts_dict_conditions_rejects_string_conditions(self):
        from food_ip_models import QuestionSynthesisLLMOutput
        # A synthesis whose conditions are dict objects must validate.
        ok = QuestionSynthesisLLMOutput(
            summary="这是一个足够长度的综合答案文本内容",
            conditions=[{"条件": "目标是营销门店型", "影响": "应强调到店理由"}],
            conflict_resolution="agreement",
        )
        self.assertEqual(ok.conditions[0]["条件"], "目标是营销门店型")
        # A string condition (what the old prompt produced) must be rejected.
        with self.assertRaises(Exception):
            QuestionSynthesisLLMOutput(
                summary="这是一个足够长度的综合答案文本内容",
                conditions=["条件是营销门店型时应当如何"],
            )

    def test_prompt_does_not_instruct_llm_to_output_origin(self):
        from food_ip_refine import QUESTION_SYNTHESIS_PROMPT
        # origin is a persisted-model field set by the program (synth["origin"]
        # = "synthesized"), NOT part of QuestionSynthesisLLMOutput. The prompt
        # must not tell the LLM to emit it, or extra_forbid rejects the output.
        self.assertNotIn('origin 固定为 "synthesized"', QUESTION_SYNTHESIS_PROMPT)
        self.assertIn("不要输出 origin 字段", QUESTION_SYNTHESIS_PROMPT)

    def test_schema_rejects_origin_extra_field(self):
        from food_ip_models import QuestionSynthesisLLMOutput
        # extra="forbid": an LLM output carrying origin must be rejected, which
        # is exactly why the prompt must not instruct the LLM to emit it.
        with self.assertRaises(Exception):
            QuestionSynthesisLLMOutput(
                summary="这是一个足够长度的综合答案文本内容",
                origin="synthesized",
            )


# ============================================================================
# Runner
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print(f"Food-IP P0 Reliability Hardening Test Suite")
    print(f"Pipeline: v{PIPELINE_VERSION} | Domain: {DOMAIN}")
    print("=" * 60)
    print()

    # Run with unittest
    unittest.main(verbosity=2)
