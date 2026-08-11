#!/usr/bin/env python3
"""
知识图谱模块 v2.0
=================
P0-16: Semantic relation candidate generation.
Replaces "next 5 of same type" with multi-strategy candidate selection.

Candidate strategies (in priority order):
  1. Shared question_id → high priority
  2. Same stage overlap → medium priority
  3. Same content_format → medium priority
  4. Cross-type pairs: principle↔case, technique↔anti_pattern, creative_format↔case
  5. Lexical/BM25-like similarity on title+core_idea as fallback

DeepSeek makes final relation judgment: same/similar/complementary/conflicting/exception.
"""

import os
import sys
import json
import re
import time
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from food_ip_config import *
from food_ip_config import (
    ATOMIC_DIR, GRAPH_DIR, SYNTHESIS_DIR, REVIEW_QUEUE_DIR, REPORTS_DIR,
    RELATION_TYPES, LLM_MODEL, LLM_BASE_URL, LLM_TEMPERATURE,
)
from food_ip_models import KnowledgeRelation


# ============================================================================
# P0-16: Semantic Candidate Generation
# ============================================================================

# Cross-type pairs that make sense to compare
CROSS_TYPE_PAIRS = [
    ("principle", "case"),        # Principle illustrated by case
    ("technique", "anti_pattern"),# Technique vs its anti-pattern
    ("creative_format", "case"),  # Format used in a case
    ("principle", "technique"),   # Principle applied as technique
    ("technique", "case"),        # Technique demonstrated in case
]

MAX_CANDIDATES_PER_KNOWLEDGE = 5
MAX_TOTAL_CANDIDATES = 500


def _tokenize(text: str) -> set[str]:
    """Simple Chinese tokenizer for BM25-like comparison."""
    # Remove punctuation, split on whitespace, also extract bigrams
    text = re.sub(r'[，。、；：！？\s]+', ' ', text)
    words = set(text.split())
    # Add character bigrams for CJK
    clean = re.sub(r'[\s\W]+', '', text)
    for i in range(len(clean) - 1):
        words.add(clean[i:i+2])
    return words


def _lexical_similarity(text_a: str, text_b: str) -> float:
    """Jaccard similarity on token sets."""
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


def generate_candidates(knowledge_cards: list[dict]) -> list[tuple]:
    """
    P0-16: Generate candidate pairs for relation detection.

    Uses multiple semantic signals instead of naive "next N of same type".

    Returns: list of (card_a, card_b, strategy, priority) tuples
      priority: 1=highest, 5=lowest
    """
    if len(knowledge_cards) < 2:
        return []

    # Build indexes
    qid_index = defaultdict(list)
    stage_index = defaultdict(list)
    format_index = defaultdict(list)
    type_index = defaultdict(list)

    for i, card in enumerate(knowledge_cards):
        for qid in card.get("question_ids", []):
            qid_index[qid].append(i)
        for stage in card.get("stages", []):
            stage_index[stage].append(i)
        for fmt in card.get("content_format", []):
            format_index[fmt].append(i)
        type_index[card.get("knowledge_type", "technique")].append(i)

    candidates = set()  # (idx_a, idx_b) pairs

    # Strategy 1: Shared question_id (priority 1)
    for qid, indices in qid_index.items():
        for a in range(len(indices)):
            for b in range(a + 1, len(indices)):
                candidates.add((indices[a], indices[b], "shared_qid", 1))

    # Strategy 2: Same stage overlap (priority 2)
    for stage, indices in stage_index.items():
        for a in range(len(indices)):
            for b in range(a + 1, len(indices)):
                pair = (indices[a], indices[b])
                if pair not in {(p[0], p[1]) for p in candidates}:
                    candidates.add((indices[a], indices[b], "shared_stage", 2))

    # Strategy 3: Same content_format (priority 3)
    for fmt, indices in format_index.items():
        for a in range(len(indices)):
            for b in range(a + 1, len(indices)):
                pair = (indices[a], indices[b])
                if pair not in {(p[0], p[1]) for p in candidates}:
                    candidates.add((indices[a], indices[b], "shared_format", 3))

    # Strategy 4: Cross-type pairs (priority 3)
    for type_a, type_b in CROSS_TYPE_PAIRS:
        indices_a = type_index.get(type_a, [])
        indices_b = type_index.get(type_b, [])
        for ia in indices_a[:10]:  # Limit per type to avoid combinatorial explosion
            for ib in indices_b[:10]:
                pair = (ia, ib)
                if pair not in {(p[0], p[1]) for p in candidates} and \
                   (ib, ia) not in {(p[0], p[1]) for p in candidates}:
                    candidates.add((ia, ib, f"cross_{type_a}_{type_b}", 3))

    # If not enough candidates, fallback to lexical similarity
    existing_pairs = {(c[0], c[1]) for c in candidates}
    if len(candidates) < min(20, len(knowledge_cards) * 2):
        # Strategy 5: Lexical/BM25 similarity (priority 5)
        for i, card_a in enumerate(knowledge_cards):
            text_a = f"{card_a.get('title', '')} {card_a.get('core_idea', '')}"
            for j in range(i + 1, min(i + 20, len(knowledge_cards))):
                if (i, j) in existing_pairs:
                    continue
                card_b = knowledge_cards[j]
                text_b = f"{card_b.get('title', '')} {card_b.get('core_idea', '')}"
                sim = _lexical_similarity(text_a, text_b)
                if sim > 0.3:  # Minimum similarity threshold
                    candidates.add((i, j, "lexical", 5))
                    if len(candidates) >= MAX_TOTAL_CANDIDATES:
                        break
            if len(candidates) >= MAX_TOTAL_CANDIDATES:
                break

    # Convert to list, sort by priority, limit per knowledge card
    result = []
    per_card_count = defaultdict(int)
    sorted_candidates = sorted(candidates, key=lambda x: x[3])

    for idx_a, idx_b, strategy, priority in sorted_candidates:
        if per_card_count[idx_a] >= MAX_CANDIDATES_PER_KNOWLEDGE:
            continue
        if per_card_count[idx_b] >= MAX_CANDIDATES_PER_KNOWLEDGE:
            continue
        result.append((knowledge_cards[idx_a], knowledge_cards[idx_b], strategy, priority))
        per_card_count[idx_a] += 1
        per_card_count[idx_b] += 1

    return result


# ============================================================================
# Question-知识链接
# ============================================================================

def link_questions(knowledge_cards, question_tree):
    """
    为每张知识卡建立与问题树的链接。输出 question_links.jsonl。
    """
    links = []
    for card in knowledge_cards:
        qids = card.get("question_ids", [])
        for qid in qids:
            links.append({
                "question_id": qid,
                "knowledge_id": card["knowledge_id"],
                "relation": "answers",
                "strength": card.get("confidence", 0.5),
            })
    return links


# ============================================================================
# 关系检测
# ============================================================================

RELATION_SYSTEM = """你是餐饮IP短视频创作领域的知识管理专家。
你的任务是比较两条知识，判断它们之间的关系。"""

RELATION_PROMPT = """比较以下两条来自不同课程来源的知识，判断它们之间的关系。

## 关系类型
- **same**: 完全相同的观点/方法，表述不同但实质一致
- **similar**: 相似但有关键差异（如适用条件、侧重点不同）
- **complementary**: 互补，A+B 形成更完整的理解
- **conflicting**: 冲突/矛盾，对同一问题给出不同建议
- **exception**: A 是 B 的例外情况

## 知识 A
标题: __TITLE_A__
类型: __TYPE_A__
内容: __CONTENT_A__

## 知识 B
标题: __TITLE_B__
类型: __TYPE_B__
内容: __CONTENT_B__

请输出 JSON:
{
  "relation": "same|similar|complementary|conflicting|exception",
  "note": "一句话解释关系",
  "can_merge": true/false
}"""


def detect_relations(knowledge_cards, api_key=None):
    """
    P0-16: Detect relations using semantic candidate generation.
    Uses DeepSeek for final judgment.
    """
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("[warn] 未设置 DEEPSEEK_API_KEY，跳过关系检测")
        return []

    if len(knowledge_cards) < 2:
        return []

    # P0-16: Generate semantic candidates
    candidates = generate_candidates(knowledge_cards)
    print(f"  [P0-16] 候选对: {len(candidates)} (from {len(knowledge_cards)} cards)")

    relations = []
    for card_a, card_b, strategy, priority in candidates:
        rel = _compare_pair(card_a, card_b, api_key)
        if isinstance(rel, dict) and rel:
            # Candidate-generation metadata is an internal selection detail;
            # it is deliberately not part of the persisted Relation contract.
            # Validate the complete result before returning it so undeclared
            # fields from the LLM cannot reach persistence.
            # Endpoint fields are injected from the candidate cards and must
            # never be supplied by the LLM response.
            if "from_id" in rel or "to_id" in rel:
                continue
            try:
                validated = KnowledgeRelation.model_validate({
                    "from_id": card_a["knowledge_id"],
                    "to_id": card_b["knowledge_id"],
                    **rel,
                })
            except Exception:
                continue
            relations.append(validated.model_dump(mode="json"))

    return relations


def _compare_pair(card_a, card_b, api_key):
    """LLM 比较一对知识卡"""
    import requests

    prompt = RELATION_PROMPT
    prompt = prompt.replace("__TITLE_A__", card_a.get("title", ""))
    prompt = prompt.replace("__TYPE_A__", card_a.get("knowledge_type", ""))
    prompt = prompt.replace("__CONTENT_A__", card_a.get("core_idea", "")[:500])
    prompt = prompt.replace("__TITLE_B__", card_b.get("title", ""))
    prompt = prompt.replace("__TYPE_B__", card_b.get("knowledge_type", ""))
    prompt = prompt.replace("__CONTENT_B__", card_b.get("core_idea", "")[:500])

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": RELATION_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 500,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(LLM_BASE_URL, json=payload, headers=headers, timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return None
    except Exception:
        pass
    return None


# ============================================================================
# 冲突检测
# ============================================================================

def detect_conflicts(knowledge_cards, relations):
    """从关系中提取冲突和例外。输出 conflicts.jsonl。"""
    conflicts = []
    for raw_rel in relations:
        rel = KnowledgeRelation.model_validate(raw_rel)
        if rel.relation.value in ("conflicting", "exception"):
            conflict = {
                "knowledge_a": rel.from_id,
                "knowledge_b": rel.to_id,
                "type": rel.relation.value,
                "note": rel.note,
                "resolution": "conditional_difference" if rel.relation.value == "conflicting" else "exception",
            }
            conflicts.append(conflict)
    return conflicts


# ============================================================================
# Canonical Knowledge 生成
# ============================================================================

def generate_canonical(relations, knowledge_cards):
    """对标记为 same 且 can_merge=true 的关系，生成 canonical knowledge。"""
    canonicals = []
    merge_groups = {}

    for raw_rel in relations:
        rel = KnowledgeRelation.model_validate(raw_rel)
        if rel.relation.value == "same" and rel.can_merge:
            a, b = rel.from_id, rel.to_id
            group = None
            for g_id, members in merge_groups.items():
                if a in members or b in members:
                    group = g_id
                    break
            if group:
                merge_groups[group].add(a)
                merge_groups[group].add(b)
            else:
                merge_groups[a] = {a, b}

    for anchor, members in merge_groups.items():
        member_cards = [c for c in knowledge_cards if c["knowledge_id"] in members]
        if len(member_cards) < 2:
            continue
        canonical = {
            "canonical_id": f"CANONICAL_{anchor[:12]}",
            "merged_from": list(members),
            "title": member_cards[0].get("title", ""),
            "knowledge_type": member_cards[0].get("knowledge_type", ""),
            "source_count": len(member_cards),
            "sources": [c.get("source", {}).get("source_id") for c in member_cards],
        }
        canonicals.append(canonical)

    return canonicals


if __name__ == "__main__":
    print("Knowledge Graph Module v2.0 OK")
    print("  P0-16: Semantic candidate generation (5 strategies)")
    print(f"  Cross-type pairs: {CROSS_TYPE_PAIRS}")
