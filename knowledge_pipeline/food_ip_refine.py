#!/usr/bin/env python3
"""
Food-IP 知识精炼管线 v3.1
==========================
P0 hardened: Pydantic validation, deterministic identity, per-source persistence,
origin/epistemic tracking, pollution boundary, run audit.

P0 items addressed: P0-6, P0-8, P0-9, P0-10, P0-13, P0-14, P0-15, P0-17, P0-18

INPUT CONTRACT (connected to transcription):
  Per-source input is the transcription output, NOT a *_corrected.txt file:
    - whisper_segments/{sid}_asr_whisper_segments.json  → authoritative corrected text
    - manifests/by_source/{sid}.json                    → title / metadata
  Missing/invalid ASRSegments abort the source before any paid LLM work.

用法:
  set DEEPSEEK_API_KEY=sk-your-key
  python food_ip_refine.py --source SRC0001
  python food_ip_refine.py --all --limit 10
"""

import os
import sys
import json
import time
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from food_ip_config import *
from food_ip_config import (
    ensure_dirs, load_question_tree, load_glossary, load_blacklist,
    apply_asr_fixes, validate_all_config,
    generate_run_id, generate_deterministic_id,
    get_question_tree_version, get_glossary_version,
    RAW_CORRECTED_DIR, ATOMIC_DIR, GRAPH_DIR,
    SYNTHESIS_DIR, REVIEW_QUEUE_DIR, REPORTS_DIR, MANIFESTS_DIR,
    PER_SOURCE_MANIFESTS_DIR,
    TRANSCRIPTS_DIR, WHISPER_SEGMENTS_DIR,
    LLM_MODEL, LLM_BASE_URL, LLM_TEMPERATURE,
    LLM_MAX_TOKENS, LLM_MAX_TOKENS_LARGE,
    PIPELINE_VERSION, DOMAIN,
)
from food_ip_models import KnowledgeRelation


# ============================================================================
# Prompt 模板 (P0-17: pollution boundary warnings added)
# ============================================================================

SYSTEM_PROMPT = """你是餐饮IP短视频创作领域的资深专家和知识管理师。
你擅长从餐饮老板IP课程中提取可复用的专业判断知识，并以结构化JSON输出。

## 强制边界 (P0-17)
- knowledge_scope 必须区分: methodology（可迁移方法论）vs source_case_fact（案例中的事实）
- 案例事实默认不可迁移(transferable=false)，只有抽象出来的方法论才是 methodology
- 不能把案例中老板的餐厅事实变成当前用户的事实

## 核心原则
- 不把经验变成机械规则
- 保留适用条件和不适用条件
- 案例情境不丢失
- 区分老师明确说的(explicit)和AI推断的(inferred)
- 如果找不到合适的 question_id，留空[]，不要强行匹配"""

# ── KnowledgeCard Prompt ──
KNOWLEDGE_CARD_PROMPT = """请从以下餐饮IP课程的语义块中提取结构化知识卡。

## 输入
来源: __SOURCE_ID__ (__SOURCE_TITLE__)
语义块: __CHUNK_TEXT__

## 知识类型
- principle: 通用创作判断原则
- technique: 具体创作技巧/方法
- anti_pattern: 错误方式/失败模式
- creative_format: 内容表达形式判断
- operation: 账号运营/矩阵/团购等

## 输出 JSON（严格遵守以下字段，不要额外字段）
{
  "title": "简明标题（15字以内）",
  "knowledge_type": "principle|technique|anti_pattern|creative_format|operation",
  "question_ids": [],
  "core_idea": "核心观点，一句话",
  "why_it_works": "",
  "applicable_when": [],
  "not_applicable_when": [],
  "method": [],
  "examples": [],
  "anti_patterns": [],
  "stages": [],
  "content_format": [],
  "confidence": 0.85,
  "origin": "explicit|inferred",
  "inference_basis": "",
  "knowledge_scope": "methodology"
}

## 约束
- question_ids: 从提供的问题列表中选最匹配的 QID。没有匹配则留空 []。
- origin: 老师明确说的=explicit，AI从案例推断的=inferred。inferred必须有inference_basis。
- knowledge_scope: 如果是直接从案例事实推导的写methodology；如果是案例餐厅的具体背景facts则不生成这张卡。
- confidence: 表述清晰+有案例支撑→0.85+; 模糊推断→0.5-0.75
- stages: 只能取值 planning|writing|shooting|review|operation，多个用数组；不适用则留空 []，禁止填其他值。
- 不要制造硬规则，用条件判断
- 课程没讲的内容不要编造

## 问题列表
__QUESTIONS__"""

# ── CaseCard Prompt ──
CASE_CARD_PROMPT = """请从以下内容中提取真实案例。

## 输入
来源: __SOURCE_ID__ (__SOURCE_TITLE__)
内容: __CHUNK_TEXT__

## 输出 JSON（严格遵守以下字段）
{
  "title": "案例简述",
  "restaurant_context": null,
  "owner_context": null,
  "original_problem": "",
  "raw_content_opportunity": "",
  "planner_insight": "",
  "why_worth_watching": "",
  "content_format": "",
  "opening_mechanism": "",
  "progression": [],
  "ip_expression": "",
  "facts_used": [],
  "facts_not_used": [],
  "cta_type": "none",
  "why_effective": [],
  "transferable_principles": [],
  "non_transferable_details": [],
  "related_question_ids": [],
  "confidence": 0.8,
  "origin": "explicit",
  "knowledge_scope": "source_case_fact"
}

## 约束 (P0-17)
- knowledge_scope 必须是 "source_case_fact"
- 案例中的餐厅事实(老板东北人、卖羊肉串等)写在facts_used，但它们是 source_case_fact
- transferable_principles 只放从案例抽象出来的方法论，不要放案例具体事实
- 课程没讲的信息写 null 或 []，禁止编造
- 案例情境要完整保留"""

# ── Question Synthesis Prompt ──
QUESTION_SYNTHESIS_PROMPT = """请基于以下所有关联知识，生成一个"专家综合答案"。

## 问题
__QUESTION__ (__QUESTION_ID__)

## 关联知识
__KNOWLEDGE_CARDS__

## 关联案例
__CASES__

## 关联反例
__ANTI_PATTERNS__

## 关联冲突/例外
__CONFLICTS__

## 输出 JSON
{
  "summary": "综合回答",
  "decision_logic": [],
  "conditions": [],
  "common_mistakes": [],
  "related_cases": [],
  "evidence_sources": [],
  "conflict_resolution": "agreement",
  "confidence": 0.8
}

## 约束 (P0-13)
- conflict_resolution 必须选: agreement(观点一致) | conditional_difference(条件差异) | exception(例外情况) | unresolved(无法判断)
- 数组字段类型严格区分：
  - decision_logic、common_mistakes、related_cases、evidence_sources 都是字符串数组：每个元素必须是一段纯文本字符串，禁止输出对象/结构。
    - decision_logic: 每个元素是一句话描述一个决策步骤
    - common_mistakes: 每个元素是一句话描述一个常见错误
    - evidence_sources: 每个元素是一个知识ID或案例ID字符串（如 KID_xxx / CID_xxx）
  - conditions 是字典数组：每个元素必须是 {"条件": "...", "影响": "..."} 这样的键值对字典，禁止输出纯字符串。每一条条件写一个 {条件, 影响} 对。
- 冲突观点保留，标注条件差异，不要强行融合
- 合成答案必须是条件化判断，不能是硬规则
- 不要输出 origin 字段（该字段由系统自动设置），也不要输出 schema 未列出的任何额外字段
- 不要为了综合而综合—如果证据不足，如实标记 confidence 低"""


# ============================================================================
# FoodIPRefiner — P0 hardened
# ============================================================================

class FoodIPRefiner:
    def __init__(self, api_key=None):
        # P0-1: Fail-fast at pipeline startup
        try:
            validate_all_config()
        except SystemExit:
            raise

        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.questions = load_question_tree()
        self.glossary = load_glossary()
        self.blacklist = load_blacklist()

        # P0-18: Run audit
        self.run_id = generate_run_id()
        self.run_started_at = datetime.now().isoformat()
        print(f"\n[P0-18] Run ID: {self.run_id}")
        print(f"[P0-18] Pipeline: v{PIPELINE_VERSION} | Model: {LLM_MODEL} | Domain: {DOMAIN}")

        # Accumulators
        self.knowledge_cards = []
        self.case_cards = []
        self.anti_patterns = []
        self.creative_formats = []
        self.question_links = []
        self.relations = []
        self.conflicts = []
        self.new_question_candidates = []
        self.review_items = []
        self.stats = {"knowledge": 0, "case": 0, "anti": 0, "format": 0, "chunks": 0}

        # P0-10: Explicitly track what is deferred
        self.deferred = [
            "Layer 2 LLM ASR contextual correction (not implemented)",
            "Visual Understanding / OCR / 多模态 (DeepSeek Pro text-only)",
            "Retrieval Layer (only retrieval_context field reserved)",
            "Embedding / Vector DB / Reranker",
            "GraphRAG / RAPTOR / Theme Tree",
            "Content Engine V2 Integration",
        ]

        # Import Pydantic models lazily
        try:
            from food_ip_models import (
                # Task 3: LLM Output Models (what LLM actually outputs)
                KnowledgeCardLLMOutput, CaseCardLLMOutput,
                AntiPatternLLMOutput, CreativeFormatLLMOutput,
                QuestionSynthesisLLMOutput,
                # Task 3: Persisted Models (with identity/provenance/audit)
                KnowledgeCard, CaseCard, AntiPattern, CreativeFormat,
                QuestionSynthesis, NewQuestionCandidate,
                make_knowledge_id, make_case_id, make_anti_pattern_id, make_format_id,
                make_chunk_id,
                Origin, KnowledgeScope,
            )
            # Task 3: Separate LLM output models from persisted models
            self.llm_models = {
                "knowledge": KnowledgeCardLLMOutput,
                "case": CaseCardLLMOutput,
                "anti": AntiPatternLLMOutput,
                "format": CreativeFormatLLMOutput,
                "synthesis": QuestionSynthesisLLMOutput,
            }
            self.persisted_models = {
                "knowledge": KnowledgeCard,
                "case": CaseCard,
                "anti": AntiPattern,
                "format": CreativeFormat,
                "synthesis": QuestionSynthesis,
            }
            self.model_helpers = {
                "make_knowledge_id": make_knowledge_id,
                "make_case_id": make_case_id,
                "make_anti_pattern_id": make_anti_pattern_id,
                "make_format_id": make_format_id,
                "make_chunk_id": make_chunk_id,
            }
            self._has_models = True
        except ImportError as e:
            # P0: Model loading failure is FATAL — no silent degradation.
            # Without Pydantic models, validation is bypassed and LLM output
            # goes unchecked. This must fail before any paid API call.
            print(f"[FATAL] food_ip_models import failed: {e}")
            print(f"[FATAL] P0 production requires Pydantic validation — aborting")
            raise RuntimeError(
                f"Cannot load food_ip_models: {e}. "
                f"P0 pipeline requires Pydantic validation. "
                f"Fix the import error before running the pipeline."
            ) from e

    # ── P0-10: ASR Layer 2 Status ──

    def pass0_asr_correction(self, source_id):
        """
        Pass 0: build the authoritative corrected text from ASRSegments.

        Layer 1 (glossary) is applied at transcription time
        (food_ip_direct_transcribe), so raw_text/corrected_text already exist
        in the ASR file. Layer 2 (LLM contextual correction): DEFERRED — not
        implemented. Layer 3 (blacklist scan): run here.

        NOTE: No longer reads ``{source_id}_corrected.txt`` — the transcription
        pipeline never produces that file. Corrected text is derived from the
        authoritative ASRSegments
        (``whisper_segments/{source_id}_asr_whisper_segments.json``).
        """
        try:
            segments = self._load_segments(source_id)
        except RuntimeError as e:
            print(f"  [warn] {e}")
            return None

        if not segments:
            print(f"  [warn] 无 ASR 分段: {source_id}")
            return None

        text = "\n".join(
            seg.get("corrected_text") or seg.get("raw_text", "")
            for seg in segments
        )

        # Layer 3: Blacklist scan
        blacklist_hits = []
        for word in self.blacklist:
            if word in text:
                positions = [m.start() for m in re.finditer(re.escape(word), text)]
                blacklist_hits.append({"word": word, "count": len(positions)})

        if blacklist_hits:
            print(f"  [ASR L3] 黑名单残留: {len(blacklist_hits)} 个词")
            for hit in blacklist_hits[:5]:
                print(f"    - {hit['word']} x{hit['count']}")

        # P0-10: Truthfulness marker
        print(f"  [ASR L2] DEFERRED — LLM contextual correction not implemented")
        print(f"  [ASR L2] Only Layer 1 (glossary) + Layer 3 (blacklist) active")

        return text

    # ── Pass 1-6: Semantic chunking + card generation ──

    def process_source(self, source_id, source_title, corrected_text):
        """Process single source: chunk + generate cards"""
        if not self.api_key:
            print("[error] 未设置 DEEPSEEK_API_KEY")
            return

        from semantic_chunker import chunk_transcript

        # P0-3: ASRSegments are mandatory in production. _load_segments
        # raises before any paid chunking call if provenance is missing/invalid.
        segments = self._load_segments(source_id)

        print(f"  [Pass 1] 语义切分...")
        chunks = chunk_transcript(corrected_text, source_id, source_title,
                                  api_key=self.api_key, segments=segments)

        self.stats["chunks"] += len(chunks)
        print(f"  [Pass 1] {len(chunks)} 个知识块")

        # P0-Round2: snapshot accumulator positions so we can persist exactly
        # what THIS source produced the moment it finishes (per-source truth).
        mark_knowledge = len(self.knowledge_cards)
        mark_case = len(self.case_cards)
        mark_anti = len(self.anti_patterns)
        mark_format = len(self.creative_formats)

        for i, chunk in enumerate(chunks):
            chunk_type = chunk.get("knowledge_type_hint", "technique")
            print(f"    [{i+1}/{len(chunks)}] {chunk.get('brief','')[:30]} → {chunk_type}")

            if chunk_type == "case":
                self._generate_case_card(source_id, source_title, chunk)
            elif chunk_type == "anti_pattern":
                self._generate_anti_pattern(source_id, source_title, chunk)
            elif chunk_type == "creative_format":
                self._generate_creative_format(source_id, source_title, chunk)
            else:
                self._generate_knowledge_card(source_id, source_title, chunk)

        # P0-Round2: persist this source immediately — NOT deferred to a single
        # end-of-run flush. Global indexes are rebuilt from per-source data.
        # Persist even when chunks is empty so a completed refine state is
        # always backed by durable per-source artifacts (never a bare flag).
        self._persist_source(source_id, chunks,
                             mark_knowledge, mark_case, mark_anti, mark_format)

    def run_source(self, source_id, source_title, corrected_text):
        """
        Refine lifecycle for one source (Round 2B-1 / 2B-2).

        pending/no-state → processing → refine+persist → completed
        processing (crash) → stale reclaim → processing → refine+persist → completed
        processing (live owner) → refuse (never preempt another running process)
        processing → exception → failed (original exception re-raised)

        COMPLETION ("done") CONTRACT (P0-FINAL): a source is marked done ONLY
        after (a) its per-source refine artifacts are durably saved AND (b) the
        global indexes were successfully rebuilt from them (see
        _persist_source). If the global rebuild fails — e.g. corrupt per-source
        data in any source — the source is marked FAILED, never done: a bare
        done marker backed by a stale or missing global index must not form.

        Completed sources are skipped WITHOUT calling the LLM. Skip is not
        blind: state=done is only trusted when (a) the state file belongs to
        THIS source/stage and passes strict SourceState validation, and
        (b) the per-source refine artifacts exist on disk and validate against
        their persisted Pydantic models. A foreign/mismatched state raises
        StateOwnershipError (fail-fast, never skip); done-but-damaged artifacts
        reset to pending and the source is reprocessed.

        Crash / stale recovery: a status=processing marker whose recorded pid
        is no longer alive (or was never recorded) is reclaimed by
        SourcePersistence.start_processing and the source is fully reprocessed
        — a complete per-source artifact set is written before mark_done, so
        half-written artifacts from the crashed run are never treated as
        completed. A status=processing marker with a LIVE pid is refused: the
        source is never processed by two runs at once and the live state is not
        overwritten.
        """
        if not self.api_key:
            print("[error] 未设置 DEEPSEEK_API_KEY")
            return False

        from food_ip_persistence import SourcePersistence
        sp = SourcePersistence(source_id, stage="refine")

        # ── Completed skip (with artifact validation) ──
        if sp.is_completed():
            if sp.refine_artifacts_complete():
                print(f"  [refine] {source_id} already completed (stage=refine) — skip, no LLM")
                return False
            # state says done but a required refine artifact is missing or
            # corrupt → never trust a bare status field. Reset and reprocess
            # rather than fake completion.
            print(f"  [refine] {source_id} state=done but refine artifacts "
                  f"MISSING/CORRUPT — reset to pending and reprocess")
            sp.reset_to_pending("state=done but refine artifacts missing or corrupt")

        # ── processing ──
        if not sp.start_processing(self.run_id):
            print(f"  [refine] {source_id} cannot acquire processing state — skip")
            return False

        # ── refine + persist ──
        try:
            self.process_source(source_id, source_title, corrected_text)
            # mark completed ONLY after per-source data has been persisted.
            sp.mark_done({
                "chunks": len(sp.load_chunks()),
                "knowledge": len(sp.load_knowledge_cards()),
            })
            print(f"  [refine] {source_id} → completed (stage=refine)")
            return True
        except Exception as e:
            sp.mark_failed(f"{type(e).__name__}: {e}")
            print(f"  [refine] {source_id} → failed: {type(e).__name__}: {e}")
            raise  # original exception keeps propagating

    def _persist_source(self, source_id, chunks,
                        mark_knowledge, mark_case, mark_anti, mark_format):
        """P0-Round2: immediately persist one source's chunks + cards.

        Reuses food_ip_persistence.SourcePersistence so each Source is the
        primary durable unit (atomic/by_source/SRCxxxx/*.jsonl), then rebuilds
        the global indexes from per-source data so an incremental --source run
        never drops knowledge persisted by earlier runs.
        """
        from food_ip_persistence import SourcePersistence, rebuild_global_indices

        sp = SourcePersistence(source_id, stage="refine")
        sp.save_chunks(chunks)
        sp.save_knowledge_cards(self.knowledge_cards[mark_knowledge:])
        sp.save_case_cards(self.case_cards[mark_case:])
        sp.save_anti_patterns(self.anti_patterns[mark_anti:])
        sp.save_creative_formats(self.creative_formats[mark_format:])

        # Keep the global knowledge indexes consistent with per-source truth.
        # STRICT (P0-FINAL): rebuild failure (e.g. corrupt per-source data in
        # ANY source) propagates instead of being a WARN-and-forget. It must
        # never form a "looks-complete" state backed by a stale/missing global
        # index — run_source catches this and marks the source failed. The
        # per-source artifacts already saved above are preserved (valid data is
        # never cleared by a rebuild failure).
        #
        # THIS source is still status=processing here (mark_done happens after
        # the rebuild), so it is passed as the explicit committing source: only
        # done+complete historical sources and this in-flight source are allowed
        # into the snapshot, and its freshly-saved artifacts are validated
        # strictly (Pydantic model + source ownership) before they can enter.
        counts = rebuild_global_indices(commit_source_id=source_id)

        print(f"  [persist] {source_id} saved per-source "
              f"(chunks={len(chunks)}, knowledge={len(self.knowledge_cards)-mark_knowledge}, "
              f"case={len(self.case_cards)-mark_case}, "
              f"anti={len(self.anti_patterns)-mark_anti}, "
              f"format={len(self.creative_formats)-mark_format}) | "
              f"global knowledge={counts.get('knowledge_cards', 0)}")

    def _load_segments(self, source_id):
        """Load and validate authoritative ASRSegments only.

        P0 invariant: downstream refinement MUST NOT fall back to raw
        WhisperSegments. Missing, empty, malformed, cross-source, or otherwise
        invalid ASR data aborts the source before Semantic Chunking / paid LLM use.
        """
        from food_ip_models import ASRSegment

        asr_path = WHISPER_SEGMENTS_DIR / f"{source_id}_asr_whisper_segments.json"
        if not asr_path.is_file():
            raise RuntimeError(
                f"Authoritative ASRSegment file missing for {source_id}: {asr_path}"
            )

        try:
            with open(asr_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"Cannot load authoritative ASRSegments for {source_id}: {e}"
            ) from e

        if data.get("source_id") != source_id:
            raise RuntimeError(
                f"ASRSegment container source_id mismatch: expected {source_id}, "
                f"got {data.get('source_id')!r}"
            )

        raw_segments = data.get("segments")
        if not isinstance(raw_segments, list) or not raw_segments:
            raise RuntimeError(f"ASRSegment file is empty for {source_id}: {asr_path}")

        validated = []
        try:
            for raw in raw_segments:
                seg = ASRSegment.model_validate(raw)
                if seg.source_id != source_id:
                    raise ValueError(
                        f"segment {seg.segment_id} belongs to {seg.source_id}, not {source_id}"
                    )
                validated.append(seg.model_dump(mode="json"))
        except Exception as e:
            raise RuntimeError(
                f"Invalid ASRSegment data for {source_id}: {e}"
            ) from e

        return validated

    # ── Card Generation ──

    def _generate_knowledge_card(self, source_id, source_title, chunk):
        """Pass 3: KnowledgeCard with Pydantic validation."""
        q_summary = "\n".join([f"- {q['question_id']}: {q['question']}"
                               for q in self.questions[:40]])

        prompt = KNOWLEDGE_CARD_PROMPT
        prompt = prompt.replace("__SOURCE_ID__", source_id)
        prompt = prompt.replace("__SOURCE_TITLE__", source_title)
        prompt = prompt.replace("__CHUNK_TEXT__", chunk.get("chunk_text", ""))
        prompt = prompt.replace("__QUESTIONS__", q_summary)

        result = self._call_llm(SYSTEM_PROMPT, prompt, max_tokens=2000)
        if result is None:
            return

        # Task 3 Stage 1: JSON → LLMOutput model (extra="forbid")
        llm_output = self._parse_and_validate(result, "knowledge")
        if llm_output is None:
            return

        # ── Task 3: Programmatic enrichment ──
        chunk_id = chunk.get("chunk_id", "")
        kid = self.model_helpers["make_knowledge_id"](
            source_id, chunk_id,
            llm_output.get("knowledge_type", "technique"),
            llm_output.get("core_idea", "")
        ) if self._has_models else self._fallback_id("K", self.stats["knowledge"] + 1)

        display_id = f"K{self.stats['knowledge'] + 1:06d}"

        source_ref = {
            "source_id": source_id,
            "video_title": source_title,
            "start_time": chunk.get("start_time", ""),
            "end_time": chunk.get("end_time", ""),
            "start_sec": chunk.get("start_sec", 0.0),
            "end_sec": chunk.get("end_sec", 0.0),
        }

        segment_ids = chunk.get("segment_ids", [])

        # Build full card from LLM output + programmatic fields
        card = dict(llm_output)
        card["knowledge_id"] = kid
        card["display_id"] = display_id
        card["source"] = source_ref
        card["chunk_id"] = chunk_id
        card["created_by_run_id"] = self.run_id
        card["evidence_segment_ids"] = segment_ids
        card["retrieval_context"] = llm_output.get("retrieval_context", "")

        # P0-14: Origin validation
        self._validate_origin(card, chunk)

        # ── Task 3 Stage 2: Validate against Persisted model ──
        card = self._validate_persisted(card, "knowledge")
        if card is None:
            return

        self.knowledge_cards.append(card)
        self.stats["knowledge"] += 1

        # P0-15: Question growth
        qids = card.get("question_ids", [])
        if not qids:
            self._add_new_question_candidate(card, chunk)

        if card.get("confidence", 1.0) < 0.75:
            self._add_review("low_confidence", kid,
                           f"confidence={card['confidence']}: {card.get('title','')}")

    def _generate_case_card(self, source_id, source_title, chunk):
        """Pass 4: CaseCard with Pydantic validation."""
        prompt = CASE_CARD_PROMPT
        prompt = prompt.replace("__SOURCE_ID__", source_id)
        prompt = prompt.replace("__SOURCE_TITLE__", source_title)
        prompt = prompt.replace("__CHUNK_TEXT__", chunk.get("chunk_text", ""))

        result = self._call_llm(SYSTEM_PROMPT, prompt, max_tokens=2000)
        if result is None:
            return

        # Task 3 Stage 1: LLMOutput validation
        llm_output = self._parse_and_validate(result, "case")
        if llm_output is None:
            return

        # ── Programmatic enrichment ──
        chunk_id = chunk.get("chunk_id", "")
        cid = self.model_helpers["make_case_id"](
            source_id, chunk_id, llm_output.get("title", "")
        ) if self._has_models else self._fallback_id("C", self.stats["case"] + 1)

        display_id = f"C{self.stats['case'] + 1:06d}"

        card = dict(llm_output)
        card["case_id"] = cid
        card["display_id"] = display_id
        card["source"] = {
            "source_id": source_id, "video_title": source_title,
            "start_time": chunk.get("start_time", ""),
            "end_time": chunk.get("end_time", ""),
            "start_sec": chunk.get("start_sec", 0.0),
            "end_sec": chunk.get("end_sec", 0.0),
        }
        card["chunk_id"] = chunk_id
        card["created_by_run_id"] = self.run_id
        card["evidence_segment_ids"] = chunk.get("segment_ids", [])

        # P0-17: Enforce pollution boundary
        card["knowledge_scope"] = card.get("knowledge_scope", "source_case_fact")
        card["transferable"] = card.get("transferable", False)

        # ── Task 3 Stage 2: Persisted model validation ──
        card = self._validate_persisted(card, "case")
        if card is None:
            return

        self.case_cards.append(card)
        self.stats["case"] += 1

    def _generate_anti_pattern(self, source_id, source_title, chunk):
        """Pass 5: AntiPattern generation."""
        q_summary = "\n".join([f"- {q['question_id']}: {q['question']}"
                               for q in self.questions[:40]])
        prompt = KNOWLEDGE_CARD_PROMPT
        prompt = prompt.replace("__SOURCE_ID__", source_id)
        prompt = prompt.replace("__SOURCE_TITLE__", source_title)
        prompt = prompt.replace("__CHUNK_TEXT__", chunk.get("chunk_text", ""))
        prompt = prompt.replace("__QUESTIONS__", q_summary)

        result = self._call_llm(SYSTEM_PROMPT, prompt, max_tokens=2000)
        if result is None:
            return

        # Task 3 Stage 1: LLMOutput validation
        llm_output = self._parse_and_validate(result, "anti")
        if llm_output is None:
            return

        # ── Programmatic enrichment ──
        chunk_id = chunk.get("chunk_id", "")
        aid = self.model_helpers["make_anti_pattern_id"](
            source_id, chunk_id, llm_output.get("title", chunk.get("brief", ""))
        ) if self._has_models else self._fallback_id("A", self.stats["anti"] + 1)

        display_id = f"A{self.stats['anti'] + 1:06d}"

        card = {
            "anti_pattern_id": aid,
            "display_id": display_id,
            "title": llm_output.get("title", chunk.get("brief", "")),
            "symptoms": llm_output.get("anti_patterns", []),
            "why_bad": llm_output.get("why_it_works", ""),
            "repair_direction": llm_output.get("core_idea", ""),
            "applicable_question_ids": llm_output.get("question_ids", []),
            "source": {
                "source_id": source_id, "video_title": source_title,
                "start_time": chunk.get("start_time", ""),
                "end_time": chunk.get("end_time", ""),
                "start_sec": chunk.get("start_sec", 0.0),
                "end_sec": chunk.get("end_sec", 0.0),
            },
            "confidence": llm_output.get("confidence", 0.5),
            "origin": llm_output.get("origin", "inferred"),
            "evidence_segment_ids": chunk.get("segment_ids", []),
            "chunk_id": chunk_id,
            "created_by_run_id": self.run_id,
        }

        # ── Task 3 Stage 2: Persisted model validation ──
        card = self._validate_persisted(card, "anti")
        if card is None:
            return

        self.anti_patterns.append(card)
        self.stats["anti"] += 1

    def _generate_creative_format(self, source_id, source_title, chunk):
        """Pass 6: CreativeFormat generation."""
        q_summary = "\n".join([f"- {q['question_id']}: {q['question']}"
                               for q in self.questions[:40]])
        format_prompt = KNOWLEDGE_CARD_PROMPT + """
## 额外格式字段
如果是 creative_format 类型，额外输出:
{
  "format_name": "口播|旁白|短平快|实拍记录|摆拍还原|聊天观点|讲故事",
  "best_for": [],
  "weak_for": [],
  "strengths": [],
  "risks": []
}"""
        prompt = format_prompt
        prompt = prompt.replace("__SOURCE_ID__", source_id)
        prompt = prompt.replace("__SOURCE_TITLE__", source_title)
        prompt = prompt.replace("__CHUNK_TEXT__", chunk.get("chunk_text", ""))
        prompt = prompt.replace("__QUESTIONS__", q_summary)

        result = self._call_llm(SYSTEM_PROMPT, prompt, max_tokens=2000)
        if result is None:
            return

        # Task 3 Stage 1: LLMOutput validation
        llm_output = self._parse_and_validate(result, "format")
        if llm_output is None:
            return

        # ── Programmatic enrichment ──
        chunk_id = chunk.get("chunk_id", "")
        fid = self.model_helpers["make_format_id"](
            source_id, chunk_id, llm_output.get("format_name", llm_output.get("name", ""))
        ) if self._has_models else self._fallback_id("F", self.stats["format"] + 1)

        display_id = f"F{self.stats['format'] + 1:06d}"

        card = {
            "format_id": fid, "display_id": display_id,
            "name": llm_output.get("format_name", chunk.get("brief", "")),
            "best_for": llm_output.get("best_for", []),
            "weak_for": llm_output.get("weak_for", []),
            "strengths": llm_output.get("strengths", []),
            "risks": llm_output.get("risks", []),
            "planning_guidance": llm_output.get("core_idea", ""),
            "writing_guidance": "",
            "shooting_guidance": "",
            "related_question_ids": llm_output.get("question_ids", []),
            "source": {
                "source_id": source_id, "video_title": source_title,
                "start_time": chunk.get("start_time", ""),
                "end_time": chunk.get("end_time", ""),
                "start_sec": chunk.get("start_sec", 0.0),
                "end_sec": chunk.get("end_sec", 0.0),
            },
            "confidence": llm_output.get("confidence", 0.5),
            "origin": llm_output.get("origin", "inferred"),
            "evidence_segment_ids": chunk.get("segment_ids", []),
            "chunk_id": chunk_id,
            "created_by_run_id": self.run_id,
        }

        # ── Task 3 Stage 2: Persisted model validation ──
        card = self._validate_persisted(card, "format")
        if card is None:
            return

        self.creative_formats.append(card)
        self.stats["format"] += 1

    # ── P0-7: Question Linking ──

    def pass7_link_questions(self):
        from knowledge_graph import link_questions
        all_cards = self.knowledge_cards + self.case_cards + self.anti_patterns
        self.question_links = link_questions(all_cards, self.questions)
        print(f"  [Pass 7] 问题链接: {len(self.question_links)} 条")

    # ── P0-8/9: Relations ──

    def pass8_detect_relations(self):
        if len(self.knowledge_cards) < 2:
            print(f"  [Pass 8] 知识卡不足2张，跳过")
            return

        from knowledge_graph import detect_relations, detect_conflicts
        print(f"  [Pass 8] 关系检测 ({len(self.knowledge_cards)} 张卡)...")
        self.relations = detect_relations(self.knowledge_cards, api_key=self.api_key)
        print(f"  [Pass 8] {len(self.relations)} 条关系")

        self.conflicts = detect_conflicts(self.knowledge_cards, self.relations)
        if self.conflicts:
            print(f"  [Pass 9] 冲突/例外: {len(self.conflicts)} 条")
            for c in self.conflicts:
                self._add_review("conflict",
                               f"{c['knowledge_a']} vs {c['knowledge_b']}",
                               c.get("note", ""))

    # ── P0-13: Question Synthesis ──

    def pass10_synthesize_questions(self):
        if not self.knowledge_cards:
            return []

        q_knowledge = defaultdict(list)
        for link in self.question_links:
            q_knowledge[link["question_id"]].append(link["knowledge_id"])

        synthesized = []
        for question in self.questions:
            qid = question["question_id"]
            linked_kids = q_knowledge.get(qid, [])
            if len(linked_kids) < 2:
                continue

            linked_cards = [c for c in self.knowledge_cards
                          if c["knowledge_id"] in linked_kids]
            linked_cases = [c for c in self.case_cards
                          if set(c.get("related_question_ids", [])) & {qid}]
            linked_antis = [a for a in self.anti_patterns
                          if set(a.get("applicable_question_ids", [])) & {qid}]

            # P0-13: Include conflicts in synthesis input
            relevant_conflicts = [
                c for c in self.conflicts
                if c["knowledge_a"] in linked_kids or c["knowledge_b"] in linked_kids
            ]
            conflicts_text = "\n".join([
                f"- {c['knowledge_a']} vs {c['knowledge_b']}: {c.get('note','')} ({c.get('resolution','unresolved')})"
                for c in relevant_conflicts
            ]) if relevant_conflicts else "无"

            cards_text = "\n".join([f"- {c['knowledge_id']}: {c.get('core_idea','')[:200]}"
                                   for c in linked_cards])
            cases_text = "\n".join([f"- {c['case_id']}: {c.get('title','')}"
                                   for c in linked_cases])
            antis_text = "\n".join([f"- {a['anti_pattern_id']}: {a.get('title','')}"
                                   for a in linked_antis])

            prompt = QUESTION_SYNTHESIS_PROMPT
            prompt = prompt.replace("__QUESTION__", question["question"])
            prompt = prompt.replace("__QUESTION_ID__", qid)
            prompt = prompt.replace("__KNOWLEDGE_CARDS__", cards_text or "无")
            prompt = prompt.replace("__CASES__", cases_text or "无")
            prompt = prompt.replace("__ANTI_PATTERNS__", antis_text or "无")
            prompt = prompt.replace("__CONFLICTS__", conflicts_text)

            print(f"  [Pass 10] {qid} ({len(linked_cards)} sources, {len(relevant_conflicts)} conflicts)")
            result = self._call_llm(SYSTEM_PROMPT, prompt, max_tokens=2000)
            if result:
                # Task 3 Stage 1: LLMOutput validation
                llm_output = self._parse_and_validate(result, "synthesis")
                if llm_output:
                    # ── Programmatic enrichment ──
                    synth = dict(llm_output)
                    synth["question_id"] = qid
                    synth["question"] = question["question"]
                    synth["evidence_sources"] = linked_kids
                    synth["origin"] = "synthesized"
                    synth["source_knowledge_ids"] = linked_kids
                    synth["conflict_resolution"] = synth.get("conflict_resolution", "agreement")
                    synth["created_by_run_id"] = self.run_id

                    # ── Task 3 Stage 2: Persisted model validation ──
                    synth = self._validate_persisted(synth, "synthesis")
                    if synth:
                        synthesized.append(synth)

        return synthesized

    # ── Persistence ──

    def flush(self):
        """Write per-source + global files.

        P0-Round2: global knowledge indexes (knowledge/case/anti/format/chunks)
        are REBUILT from per-source data, NOT overwritten with this run's
        in-memory results. Previously-persisted sources survive incremental
        runs. Graph/review outputs remain run-level (not yet per-source).
        """
        ensure_dirs()

        # Global knowledge indexes: rebuild from per-source truth.
        # STRICT (P0-FINAL): a rebuild failure (e.g. corrupt per-source data)
        # propagates instead of printing a WARN and reporting a successful run
        # with a stale index. A run whose global index cannot be rebuilt must
        # fail loudly, never report DONE.
        from food_ip_persistence import rebuild_global_indices
        counts = rebuild_global_indices()

        self._write_jsonl(GRAPH_DIR / "question_links.jsonl", self.question_links)
        self._write_jsonl(
            GRAPH_DIR / "knowledge_relations.jsonl",
            self.relations,
            model=KnowledgeRelation,
        )
        self._write_jsonl(GRAPH_DIR / "conflicts.jsonl", self.conflicts)
        self._write_jsonl(GRAPH_DIR / "new_question_candidates.jsonl",
                         [c for c in self.new_question_candidates])
        self._write_jsonl(REVIEW_QUEUE_DIR / "review_queue.jsonl", self.review_items)

        print(f"\n  输出统计:")
        print(f"    Global KnowledgeCards: {counts.get('knowledge_cards', 0)}")
        print(f"    Global CaseCards: {counts.get('case_cards', 0)}")
        print(f"    Global AntiPatterns: {counts.get('anti_patterns', 0)}")
        print(f"    Global CreativeFormats: {counts.get('creative_formats', 0)}")
        print(f"    Global Chunks: {counts.get('chunks', 0)}")
        print(f"    QuestionLinks: {len(self.question_links)}")
        print(f"    Relations: {len(self.relations)}")
        print(f"    Conflicts: {len(self.conflicts)}")
        print(f"    NewQuestionCandidates: {len(self.new_question_candidates)}")
        print(f"    ReviewQueue: {len(self.review_items)}")
        print(f"\n  [P0-10] Deferred items: {len(self.deferred)}")
        for d in self.deferred:
            print(f"    - {d}")

    def _write_jsonl(self, path, data, model=None):
        # Validate the complete batch before creating or replacing the target.
        # In particular, Relation uses extra="forbid" so undeclared fields
        # cannot silently reach persistent storage.
        if model is not None:
            data = [model.model_validate(item).model_dump(mode="json") for item in data]
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: write .tmp then replace
        tmp = Path(str(path) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        tmp.replace(path)

    def _add_review(self, reason, item_id, description):
        self.review_items.append({
            "reason": reason, "item_id": item_id,
            "description": description,
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "run_id": self.run_id,
        })

    # ── P0-14: Origin Validation ──

    def _validate_origin(self, card, chunk):
        """P0-14 + B2: Deterministic provenance enrichment ONLY.

        The program may deterministically fill ``evidence_segment_ids`` from the
        chunk's real validated segment_ids (metadata enrichment). It must NEVER:
          - rewrite the LLM's ``origin`` (e.g. explicit → inferred) to dodge
            validation, or
          - fabricate ``inference_basis`` (or any other missing provenance).
        Missing provenance is rejected by the persisted KnowledgeCard validator
        in ``_validate_persisted`` (fail-fast), never auto-repaired.
        """
        card["evidence_segment_ids"] = chunk.get("segment_ids", [])

    # ── P0-15: Question Growth ──

    def _add_new_question_candidate(self, card, chunk):
        """P0-15: Knowledge doesn't match any existing QID → candidate."""
        candidate = {
            "candidate_id": f"QNEW{len(self.new_question_candidates) + 1:04d}",
            "question": f"从知识推断: {card.get('title', '')}",
            "category": "",
            "trigger_knowledge_id": card.get("knowledge_id", ""),
            "trigger_chunk_id": chunk.get("chunk_id", ""),
            "reason": f"现有 {len(self.questions)} 个问题中无匹配。核心观点: {card.get('core_idea', '')[:100]}",
            "suggested_category": card.get("knowledge_type", ""),
            "created_by_run_id": self.run_id,
            "created_at": datetime.now().isoformat(),
        }
        self.new_question_candidates.append(candidate)

    # ── LLM Call ──

    def _call_llm(self, system_prompt, user_prompt, max_tokens=None):
        import requests
        max_tokens = max_tokens or LLM_MAX_TOKENS

        payload = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": LLM_TEMPERATURE,
            "max_tokens": max_tokens,
            # Phase 0.5: disable thinking on reasoning models (e.g. deepseek-v4-*).
            # With thinking enabled, internal reasoning consumes the max_tokens
            # budget and the actual content can be truncated or empty.
            "thinking": {"type": "disabled"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        last_error = None
        for attempt in range(3):
            try:
                resp = requests.post(LLM_BASE_URL, json=payload, headers=headers, timeout=300)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
                elif resp.status_code == 429:
                    time.sleep([5, 15, 60][attempt])
                else:
                    last_error = RuntimeError(
                        f"LLM HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    if attempt < 2:
                        time.sleep(5)
            except Exception as e:
                last_error = e
                if attempt < 2:
                    time.sleep(5)
        # Phase 0.5: a failed extraction call must fail the Source loudly,
        # not silently return None and let the Source complete with 0 chunks.
        raise last_error if last_error else RuntimeError("LLM call failed after retries")

    # ── P0-8 + P0-9: JSON Parse + Pydantic Validation ──

    def _parse_and_validate(self, text: str, model_type: str) -> Optional[dict]:
        """
        Task 3 + Task 4: Two-stage validation chain.

        Stage 1: JSON → LLMOutput model (extra="forbid")
                 Catches hallucinated fields, wrong types, missing fields.
                 NO strip-and-retry. Unknown field → reject immediately.

        Stage 2: Programmatic enrichment (identity, source, evidence, run_id)
                 Then validate against full Persisted model.
                 Catches semantic rule violations (origin, pollution, timestamps).

        On ANY failure: return None. Caller handles retry/review/fail.
        """
        from robust_json_parser import parse_json

        obj, err = parse_json(text)
        if obj is None:
            print(f"    [JSON] Parse failed: {err}")
            return None

        if not isinstance(obj, dict):
            print(f"    [JSON] Expected dict, got {type(obj).__name__}")
            return None

        # ── Stage 1: LLMOutput model validation ──
        if self._has_models and model_type in self.llm_models:
            llm_model_cls = self.llm_models[model_type]
            try:
                validated = llm_model_cls.model_validate(obj)
                return validated.model_dump()
            except Exception as e:
                # Task 4: REJECT — no strip-and-retry
                # Unknown fields, wrong types, missing fields → immediate rejection
                print(f"    [LLMOutput] Validation FAILED: {e}")
                print(f"    [LLMOutput] Result REJECTED — will NOT attempt field stripping")
                return None

        # ── Fallback: no models loaded ──
        return obj  # Raw dict if no models

    def _validate_persisted(self, card: dict, model_type: str) -> Optional[dict]:
        """
        Task 3 Stage 2: Validate enriched card against full Persisted model.
        Called AFTER programmatic ID/source/evidence/run_id assignment.

        Returns validated dict or None (rejected).
        """
        if not self._has_models or model_type not in self.persisted_models:
            return card  # No models — trust caller

        persisted_cls = self.persisted_models[model_type]
        try:
            validated = persisted_cls.model_validate(card)
            return validated.model_dump()
        except Exception as e:
            print(f"    [Persisted] Final validation FAILED: {e}")
            print(f"    [Persisted] Card REJECTED — will NOT persist")
            return None

    def _fallback_id(self, prefix: str, seq: int) -> str:
        """Fallback sequential ID when models not available."""
        return f"{prefix}_{self.run_id[-8:]}_{seq:04d}"


# ============================================================================
# Main
# ============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Food-IP 知识精炼管线 v3.1")
    parser.add_argument("--source", type=str, default=None)
    parser.add_argument("--from-sources", type=str, default=None)
    parser.add_argument("--all", action="store_true", default=False)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-synthesis", action="store_true", default=False)
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: 未设置 DEEPSEEK_API_KEY 环境变量")
        return

    # P0-1: Config validation happens in FoodIPRefiner.__init__()
    ensure_dirs()

    source_list = []
    if args.source:
        source_list = [args.source]
    elif args.from_sources:
        source_list = [s.strip() for s in args.from_sources.split(",")]
    elif args.all:
        manifest_path = MANIFESTS_DIR / "sources.jsonl"
        if manifest_path.exists():
            with open(manifest_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            source_list.append(json.loads(line)["source_id"])
                        except (json.JSONDecodeError, KeyError):
                            pass
        if not source_list:
            for f in sorted(TRANSCRIPTS_DIR.glob("SRC*.md")):
                source_list.append(f.stem)
    else:
        print("请指定 --source, --from-sources, 或 --all")
        return

    if args.limit:
        source_list = source_list[:args.limit]

    if not source_list:
        print("没有找到任何 Source")
        return

    print(f"Food-IP 知识精炼 v{PIPELINE_VERSION}")
    print(f"  Run ID: {generate_run_id()}")
    print(f"  待处理: {len(source_list)} 个 Source")
    print(f"  问题树: {len(load_question_tree())} 个问题 (v{get_question_tree_version()})")

    try:
        refiner = FoodIPRefiner(api_key=api_key)
    except SystemExit:
        return

    for sid in source_list:
        print(f"\n{'='*50}")
        print(f"[{sid}]")

        # P0: Source title comes from the transcription per-source manifest
        # (manifests/by_source/{sid}.json), NOT from a *_corrected.txt /
        # *_meta.json pair that the transcription pipeline never writes.
        source_title = sid
        manifest_path = PER_SOURCE_MANIFESTS_DIR / f"{sid}.json"
        if manifest_path.exists():
            try:
                meta = json.loads(manifest_path.read_text(encoding="utf-8"))
                source_title = meta.get("title", sid) or sid
            except Exception:
                pass

        # P0-10: Pass 0 (ASR) — corrected text derives from authoritative
        # ASRSegments. Missing/invalid ASR aborts before any paid LLM work.
        corrected_text = refiner.pass0_asr_correction(sid)
        if corrected_text is None:
            print(f"  SKIP: 缺少 authoritative ASRSegments ({sid})")
            continue

        # Pass 1-6 + refine lifecycle (completed-skip / processing → completed)
        refiner.run_source(sid, source_title, corrected_text)

    # Pass 7
    print(f"\n{'='*50}")
    refiner.pass7_link_questions()

    # Pass 8-9
    refiner.pass8_detect_relations()

    # Pass 10
    if not args.skip_synthesis:
        print(f"\n{'='*50}")
        syntheses = refiner.pass10_synthesize_questions()
        if syntheses:
            refiner._write_jsonl(SYNTHESIS_DIR / "questions.jsonl", syntheses)
            print(f"  [Pass 10] {len(syntheses)} 条综合答案")
    else:
        print(f"\n  [Pass 10] 已跳过")

    # Flush + Report
    print(f"\n{'='*50}")
    refiner.flush()

    # P0-18: Run audit
    audit = {
        "run_id": refiner.run_id,
        "pipeline_version": PIPELINE_VERSION,
        "model": LLM_MODEL,
        "prompt_version": "3.1",
        "question_tree_version": get_question_tree_version(),
        "glossary_version": get_glossary_version(),
        "domain": DOMAIN,
        "started_at": refiner.run_started_at,
        "completed_at": datetime.now().isoformat(),
        "sources_total": len(source_list),
        "sources_completed": refiner.stats["chunks"],
        "stats": refiner.stats,
        "deferred_items": refiner.deferred,
    }
    audit_path = REPORTS_DIR / f"run_audit_{refiner.run_id}.json"
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)

    print(f"\nDONE. Report: {audit_path}")
    print(f"Output: {FOOD_IP_KNOWLEDGE_DIR}")


if __name__ == "__main__":
    main()
