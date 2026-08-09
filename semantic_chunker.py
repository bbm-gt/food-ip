#!/usr/bin/env python3
"""
语义知识点切分器 v2.0
=====================
P0-5: Segment-aware semantic chunking.

Changes from v1.0:
  - Input: corrected_text + ASRSegments list
  - LLM outputs segment_ids (NOT start_time/end_time)
  - Chunk time range computed PROGRAMMATICALLY from segments
  - Chunk ID: deterministic hash from source_id + segment span
  - LLM NEVER creates timestamps

Usage:
  from semantic_chunker import chunk_transcript
  chunks = chunk_transcript(corrected_text, source_id, source_title,
                            segments=segments_list)
"""

import os, sys, json, re, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from food_ip_config import (
    LLM_MODEL, LLM_BASE_URL, LLM_TEMPERATURE, LLM_MAX_TOKENS,
    KNOWLEDGE_TYPES, generate_deterministic_id,
)
from food_ip_segments import get_segment_time_range, fmt_time_range

CHUNKER_SYSTEM = "你是餐饮IP短视频创作领域的知识管理专家。任务是把长文本拆分为可独立理解的原子知识块。每个块包含一个完整思想（原则/方法/案例/反例/创作形式/运营操作）。"

CHUNKER_PROMPT = """请将以下餐饮IP短视频创作课程转录文本，按"完整语义"切分为独立知识块。

## 切分规则
1. 不要按固定字数切分。一个块 80-400 字都可。
2. 每个块是一个完整思想：原则/方法/案例/反例/创作形式判断/运营操作。
3. 在主题变化处切分，不在方法中间切断。
4. 保留原始信息，不压缩案例上下文。

## P0-5: 时间范围约束
<!-- segments list -->
每个文本行前有 [segment_id] 标记。请为每个知识块输出它覆盖的 segment_ids。
时间范围由程序从 segments 计算，你不要输出时间。

## 输出格式（每块一个JSON，一行一个）
{"chunk_text": "知识点完整原文", "knowledge_type": "principle|technique|case|anti_pattern|creative_format|operation", "segment_ids": ["SRCxxxx-SEG0001", "SRCxxxx-SEG0002"], "brief": "10字以内简述"}

## 输入文本
来源: __SOURCE_TITLE__ (__SOURCE_ID__)

__CONTENT__"""


def chunk_transcript(corrected_text, source_id, source_title,
                     api_key=None, base_url=None, model=None,
                     segments=None):
    """
    对修正后的转录文本做语义切分。返回 list of dict。

    Args:
        corrected_text: Cleaned transcript text (may include [segment_id] markers)
        source_id: SRC0001 format
        source_title: Human-readable title
        api_key: DeepSeek API key
        segments: List of ASRSegment dicts for time range computation (P0-5)
    """
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("[error] 未设置 DEEPSEEK_API_KEY")
        return []

    base_url = base_url or LLM_BASE_URL
    model = model or LLM_MODEL

    # If segments provided, prepend segment markers to text for LLM guidance
    text_with_markers = _prepend_segment_markers(corrected_text, segments) \
        if segments else corrected_text

    prompt = CHUNKER_PROMPT.replace("__SOURCE_TITLE__", source_title)
    prompt = prompt.replace("__SOURCE_ID__", source_id)

    text_limit = 18000
    if len(text_with_markers) > text_limit:
        raw_chunks = _chunk_large(text_with_markers, source_id, source_title,
                                  api_key, base_url, model, text_limit)
    else:
        prompt = prompt.replace("__CONTENT__", text_with_markers)
        result = _call_llm(CHUNKER_SYSTEM, prompt, api_key, base_url, model,
                           max_tokens=LLM_MAX_TOKENS, temperature=0.2)
        raw_chunks = _parse_chunks(result, source_id) if result else []

    # P0-5: Enrich chunks with programmatic time ranges and deterministic IDs
    return _enrich_chunks(raw_chunks, source_id, segments)


def _prepend_segment_markers(text: str, segments: list) -> str:
    """Prepend segment_id markers to help LLM reference segments."""
    if not segments:
        return text

    # Create a segment_id → text map
    # For each segment, prepend its ID before the corresponding text
    result_parts = []
    for seg in segments:
        result_parts.append(f"[{seg['segment_id']}] {seg.get('corrected_text', seg['raw_text'])}")
    return '\n'.join(result_parts)


def _enrich_chunks(raw_chunks: list, source_id: str, segments: list) -> list:
    """
    P0-5: Enrich each chunk with:
      - Deterministic chunk_id
      - Programmatic time range (from segments, NOT LLM)
      - segment_ids validation (ensure they exist in segments list)
    """
    if not segments:
        if raw_chunks:
            print(f"[chunker] REJECT: {source_id} has chunks but no authoritative ASRSegments")
        return []

    seg_id_set = {s.get("segment_id", "") for s in segments}
    enriched = []

    for i, chunk in enumerate(raw_chunks):
        seg_ids = chunk.get("segment_ids", [])

        # P0: evidence is strict. Empty IDs or ANY unknown segment ID rejects
        # the whole chunk. Never invent a source-level/sentinel segment.
        if not isinstance(seg_ids, list) or not seg_ids:
            print(f"[chunker] REJECT chunk {i}: missing segment_ids")
            continue
        unknown = [sid for sid in seg_ids if sid not in seg_id_set]
        if unknown:
            print(f"[chunker] REJECT chunk {i}: unknown segment_ids={unknown}")
            continue
        valid_seg_ids = list(seg_ids)

        # P0-5: Deterministic chunk ID
        seg_min = min(valid_seg_ids)
        seg_max = max(valid_seg_ids)
        chunk_id = "CHK_" + generate_deterministic_id(source_id, seg_min, seg_max)

        # P0-5: Programmatic time range
        start_sec, end_sec = get_segment_time_range(segments, valid_seg_ids)
        start_time = fmt_time_range(start_sec, end_sec).split("-")[0] \
            if start_sec > 0 or end_sec > 0 else ""
        end_time = fmt_time_range(start_sec, end_sec).split("-")[-1] \
            if start_sec > 0 or end_sec > 0 else ""

        candidate = {
            "chunk_id": chunk_id,
            "source_id": source_id,
            "segment_ids": valid_seg_ids,
            "knowledge_type_hint": chunk.get("knowledge_type", "technique"),
            "brief": chunk.get("brief", ""),
            "chunk_text": chunk.get("chunk_text", ""),
            "start_sec": start_sec,
            "end_sec": end_sec,
            "start_time": start_time,
            "end_time": end_time,
            "retrieval_context": "",
        }
        try:
            from food_ip_models import SemanticChunk
            candidate = SemanticChunk.model_validate(candidate).model_dump(mode="json")
        except Exception as e:
            print(f"[chunker] REJECT chunk {i}: semantic validation failed: {e}")
            continue
        enriched.append(candidate)

    return enriched


def _chunk_large(text, source_id, source_title, api_key, base_url, model, size):
    """大文本分段处理"""
    paragraphs = text.split('\n\n')
    chunks, current = [], ""
    for p in paragraphs:
        if len(current) + len(p) > size and current:
            chunks.append(current)
            current = p
        else:
            current = current + "\n\n" + p if current else p
    if current:
        chunks.append(current)

    all_results = []
    for i, chunk_text in enumerate(chunks):
        prompt = CHUNKER_PROMPT.replace("__SOURCE_TITLE__", source_title)
        prompt = prompt.replace("__SOURCE_ID__", source_id)
        prompt = prompt.replace("__CONTENT__", f"[Part {i+1}/{len(chunks)}]\n\n{chunk_text}")
        result = _call_llm(CHUNKER_SYSTEM, prompt, api_key, base_url, model,
                           max_tokens=LLM_MAX_TOKENS, temperature=0.2)
        if result:
            all_results.extend(_parse_chunks(result, source_id))
    return all_results


def _call_llm(system_prompt, user_prompt, api_key, base_url, model,
              max_tokens=None, temperature=None):
    """调用 LLM API，带重试"""
    import requests
    max_tokens = max_tokens or LLM_MAX_TOKENS
    temperature = temperature or LLM_TEMPERATURE

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    for attempt in range(3):
        try:
            resp = requests.post(base_url, json=payload, headers=headers, timeout=300)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            elif resp.status_code == 429:
                time.sleep([5, 15, 60][attempt])
            else:
                if attempt < 2:
                    time.sleep(5)
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
    return None


def _parse_chunks(llm_output, source_id):
    """从 LLM 输出解析 JSON 块"""
    chunks = []
    try:
        data = json.loads(llm_output)
        if isinstance(data, list):
            for item in data:
                item["source_id"] = source_id
                chunks.append(item)
            return chunks
        elif isinstance(data, dict):
            data["source_id"] = source_id
            return [data]
    except json.JSONDecodeError:
        pass

    for line in llm_output.strip().split('\n'):
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                obj = json.loads(line)
                obj["source_id"] = source_id
                chunks.append(obj)
            except json.JSONDecodeError:
                continue
    return chunks


if __name__ == "__main__":
    test = "今天我们讲情绪钩子。真实情绪比表演更有效。有个烤鱼老板被骂后对着镜头说了一句话，火了。反过来硬演愤怒，用户会看出来。"
    result = chunk_transcript(test, "SRC0001", "测试-情绪钩子")
    print(f"切分: {len(result)} 块")
    for c in result:
        print(f"  [{c.get('knowledge_type_hint','?')}] {c.get('brief','')}")
        print(f"    chunk_id: {c.get('chunk_id','')}")
        print(f"    time: {c.get('start_time','')}-{c.get('end_time','')}")
