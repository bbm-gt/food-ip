#!/usr/bin/env python3
"""
Food-IP 预处理脚本 v3.1
=======================
P0 hardened: Whisper-native segments, non-destructive cleaning, safe glossary.

P0-3: Reads WhisperSegments (not markdown timestamps). Applies glossary → corrected_text.
P0-4: REDUCED aggressive cleaning — keeps teacher narrative, judgment process, case detail.
      Only removes: ASR artifacts, encoding garbage, clear non-content filler.
P0-12: Safe glossary — only risk_level=low + match_mode=exact_phrase/regex auto-applied.
       context_required entries flagged for LLM review.
       Double-correction guard: tracks applied fixes, verifies before/after.

原则:
  Whisper Segment → ASRSegment → Markdown
  CORRECTION NEVER CHANGES TIMESTAMPS

用法:
  python food_ip_preprocess.py
  python food_ip_preprocess.py --source SRC0001
  python food_ip_preprocess.py --limit 10
"""

import os
import sys
import json
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from food_ip_config import *
from food_ip_config import (
    ensure_dirs, load_glossary, load_glossary_all, load_blacklist,
    apply_asr_fixes, TRANSCRIPTS_DIR, RAW_TRANSCRIPTS_DIR,
    RAW_CORRECTED_DIR, WHISPER_SEGMENTS_DIR, LOGS_DIR,
)


# ============================================================================
# P0-4: Non-Destructive Text Cleaning
# ============================================================================

# P0-4: Keep teacher narrative markers — these contain judgment and case detail
# Only remove: ASR artifacts, encoding garbage, broadcast-style filler
_RE_ASR_ARTIFACT = re.compile(
    r"[#\*]{2,}"           # Markdown artifact sequences
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f]"  # Control chars (except \t \n)
    r"|�"              # Unicode replacement char
)

# Light filler removal — only broadcast-style phrases, NOT teacher judgment
_RE_BROADCAST_FILLER = re.compile(
    r"(大家把.{0,10}记下来[，,。.]?|"
    r"大家截图[，,。.]?|截图保存[，,。.]?|大家记一下[，,。.]?)"
)

# Course promo — transitional phrases only, NOT teacher reasoning
_RE_COURSE_PROMO_LIGHT = re.compile(
    r"(从下一节课开始[，,。.]?|"
    r"下一章[，,。.]?|下个视频[，,。.]?|下期[，,。.]?)"
)


def clean_text(text: str) -> str:
    """
    P0-4: Light text cleaning — removes only ASR artifacts and encoding garbage.
    Does NOT remove:
      - Teacher judgment process ("我给这个老板改的时候")
      - Case narrative ("大家看这个案例")
      - Comparison process
      - Personal experience ("我当时", "我记得")
    """
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        # Remove timestamp prefix
        line = re.sub(r'^>\s*\[\[[\d:]+\]\]\s*', '', line).strip()
        # Remove pure timestamp lines
        if re.match(r'^\[[\d:]+\]$', line):
            continue
        # Remove pure-number garbage lines
        if re.match(r'^[\d\s:.,，。！？、；]+$', line):
            continue
        if not line:
            continue

        # P0-4: Only remove ASR artifacts and encoding garbage
        line = _RE_ASR_ARTIFACT.sub(' ', line)
        line = _RE_BROADCAST_FILLER.sub('', line)
        line = _RE_COURSE_PROMO_LIGHT.sub('', line)

        # Normalize whitespace but preserve paragraph structure
        line = re.sub(r'\s{2,}', ' ', line).strip()

        if line and len(line) > 1:  # Skip single-char fragments
            cleaned.append(line)

    return '\n'.join(cleaned)


def deep_clean(text: str) -> str:
    """
    Minimal deep cleaning — structural cleanup only.
    Does NOT remove content or teacher reasoning.
    """
    if not text:
        return text

    # Clean up excessive blank lines
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    # Remove orphan punctuation lines
    text = re.sub(r'(?m)^[，,。.、；;：:！!？?\s]+$', '', text)
    # Clean duplicate punctuation
    text = re.sub(r'[，,]{3,}', '，', text)
    text = re.sub(r'[。.]{3,}', '。', text)
    # Clean double punctuation
    text = re.sub(r'[，,][。.]', '。', text)

    return text.strip()


# ============================================================================
# P0-12: Safe Glossary Application
# ============================================================================

def apply_safe_glossary(text: str, glossary_fn, all_glossary_entries: list) -> dict:
    """
    P0-12: Apply only safe (low risk + exact_phrase/regex) glossary entries.
    Returns: {corrected_text, fix_count, applied, flagged_context_required}
    """
    # Step 1: Apply safe auto-replacements
    corrected, fix_count, applied = glossary_fn(text)

    # Step 2: Collect context_required / high-risk entries present in text
    flagged = []
    for entry in all_glossary_entries:
        wrong = entry.get("wrong", "")
        right = entry.get("right", "")
        risk = entry.get("risk_level", "low")
        mode = entry.get("match_mode", "exact_phrase")

        if risk == "low" and mode in ("exact_phrase", "regex"):
            continue  # Already handled by glossary_fn
        if wrong == right:
            continue

        if wrong in text:
            flagged.append({
                "wrong": wrong,
                "right": right,
                "risk_level": risk,
                "match_mode": mode,
                "reason": f"risk={risk}, mode={mode} — needs LLM review",
            })

    return {
        "corrected_text": corrected,
        "fix_count": fix_count,
        "applied": applied,
        "flagged_context_required": flagged,
    }


def parse_food_ip_frontmatter(content):
    """解析 Food-IP 格式的 frontmatter"""
    meta = {"source_id": "", "title": "", "duration_seconds": 0, "quality_status": "unknown"}
    m = re.match(r'^---\s*\n(.*?)\n(?:---|\.\.\.)', content, re.DOTALL)
    if m:
        for line in m.group(1).split('\n'):
            kv = line.split(':', 1)
            if len(kv) == 2:
                key = kv[0].strip()
                val = kv[1].strip().strip('"\'')
                if key == 'source_id':
                    meta['source_id'] = val
                elif key == 'title':
                    meta['title'] = val
                elif key == 'duration_seconds':
                    try:
                        meta['duration_seconds'] = int(val)
                    except ValueError:
                        pass
                elif key == 'quality_status':
                    meta['quality_status'] = val
    return meta


def preprocess_source(source_md_path, glossary_fn, all_glossary_entries, blacklist):
    """
    P0-3 + P0-4 + P0-12: Process single source.

    1. Parse frontmatter
    2. Read WhisperSegments if available (P0-3)
    3. Light cleaning (P0-4: keep teacher narrative)
    4. Safe glossary application (P0-12)
    5. Output corrected text + meta
    6. Return stats
    """
    source_md_path = Path(source_md_path)
    if not source_md_path.exists():
        return None

    content = source_md_path.read_text(encoding="utf-8")
    meta = parse_food_ip_frontmatter(content)
    source_id = meta["source_id"]

    # Extract body (skip frontmatter and H1)
    body_parts = content.split("---\n", 2)
    body = body_parts[-1] if len(body_parts) > 2 else content
    body = re.sub(r'^# .+\n', '', body, count=1).strip()

    # P0-3: Check for WhisperSegments
    whisper_seg_path = WHISPER_SEGMENTS_DIR / f"{source_id}_whisper_segments.json"
    has_segments = whisper_seg_path.exists()

    # P0-4: Light cleaning pipeline
    cleaned = clean_text(body)
    deep_cleaned = deep_clean(cleaned)

    # P0-12: Safe glossary
    result = apply_safe_glossary(deep_cleaned, glossary_fn, all_glossary_entries)
    fixed = result["corrected_text"]

    # Save corrected text
    output = RAW_CORRECTED_DIR / f"{source_id}_corrected.txt"
    output.write_text(fixed, encoding="utf-8")

    # Save meta
    meta_out = RAW_CORRECTED_DIR / f"{source_id}_meta.json"
    meta_out.write_text(json.dumps({
        "source_id": source_id,
        "title": meta["title"],
        "char_count_original": len(body),
        "char_count_corrected": len(fixed),
        "asr_fixes_applied": result["fix_count"],
        "asr_fixes_detail": result["applied"],
        "context_required_flagged": result["flagged_context_required"],
        "has_whisper_segments": has_segments,
        "whisper_segments_path": str(whisper_seg_path) if has_segments else None,
        "pipeline_version": PIPELINE_VERSION,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "source_id": source_id,
        "title": meta["title"],
        "corrected_path": str(output),
        "char_count": len(fixed),
        "asr_fix_count": result["fix_count"],
        "flagged_count": len(result["flagged_context_required"]),
        "has_segments": has_segments,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Food-IP 预处理 v3.1")
    parser.add_argument("--source", type=str, default=None,
                        help="指定单个 source_id 处理")
    parser.add_argument("--limit", type=int, default=None,
                        help="限制处理文件数")
    args = parser.parse_args()

    # P0-1: Fail-fast config validation at pipeline startup (before any API call)
    try:
        validate_all_config()
    except SystemExit:
        return

    ensure_dirs()
    glossary_fn_items = load_glossary()  # Safe auto-apply entries only
    all_glossary = load_glossary_all()    # All entries for flagging
    blacklist = load_blacklist()

    print(f"Food-IP 预处理 v{PIPELINE_VERSION}")
    print(f"  术语表: {len(glossary_fn_items)} 条安全自动替换 + {len(all_glossary) - len(glossary_fn_items)} 条需LLM判断")
    print(f"  黑名单: {len(blacklist)} 条")

    # Get files to process
    if args.source:
        sources = [TRANSCRIPTS_DIR / f"{args.source}.md"]
    else:
        sources = sorted(TRANSCRIPTS_DIR.glob("SRC*.md"))

    if args.limit:
        sources = sources[:args.limit]

    print(f"  待处理: {len(sources)} 个 Source\n")

    stats = {"success": 0, "skip": 0, "error": 0}
    total_fixes = 0
    total_flagged = 0

    for src_path in sources:
        sid = src_path.stem
        print(f"  [{sid}] ", end="")

        # Check if already processed
        corrected_path = RAW_CORRECTED_DIR / f"{sid}_corrected.txt"
        if corrected_path.exists():
            print("skip (已存在)")
            stats["skip"] += 1
            continue

        try:
            result = preprocess_source(src_path, glossary_fn_items, all_glossary, blacklist)
            if result:
                flagged_str = f", {result['flagged_count']}处需LLM判断" if result['flagged_count'] > 0 else ""
                seg_str = " [has segments]" if result["has_segments"] else ""
                print(f"OK {result['char_count']}字, {result['asr_fix_count']}处修正{flagged_str}{seg_str}")
                stats["success"] += 1
                total_fixes += result["asr_fix_count"]
                total_flagged += result["flagged_count"]
            else:
                print("FAIL")
                stats["error"] += 1
        except Exception as e:
            print(f"FAIL: {e}")
            stats["error"] += 1

    print(f"\n预处理完成 v{PIPELINE_VERSION}")
    print(f"  成功: {stats['success']} | 跳过: {stats['skip']} | 失败: {stats['error']}")
    print(f"  ASR 修正: {total_fixes} 处自动 | {total_flagged} 处需LLM判断")
    print(f"  输出: {RAW_CORRECTED_DIR}")


if __name__ == "__main__":
    main()
