#!/usr/bin/env python3
"""
Food-IP Direct Transcription v1.0
==================================
P0-3 / P0-11: Direct in-process transcription using faster-whisper.

CRITICAL: This is the PRODUCTION entry point for transcription.
It guarantees:
  1. FOOD_IP_PROMPT reaches model.transcribe(initial_prompt=...)
  2. Whisper native segment.start/end → ASRSegment time authority
  3. Segments saved immediately (NEVER modified after save)
  4. One video file → one transcription (no directory scanning)

Authority chain: WhisperSegment → ASRSegment → Markdown (NOT reverse)

Usage:
  from food_ip_direct_transcribe import transcribe_single_video
  result = transcribe_single_video(video_path, output_dir, source_id)
  # → result["segments"] = [WhisperSegment dicts]
  # → result["markdown_path"] = Path to output .md
"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

# Ensure E:\ is importable for transcribe_batch modules
E_ROOT = Path("E:/")
if str(E_ROOT) not in sys.path:
    sys.path.insert(0, str(E_ROOT))

from food_ip_segments import extract_segments, save_segments, apply_glossary_to_segments
from food_ip_whisper_adapter import FOOD_IP_PROMPT, _DOMAIN_LABEL
from food_ip_config import (
    WHISPER_SEGMENTS_DIR, WHISPER_MODEL, MODEL_DOWNLOAD_ROOT,
    load_glossary, apply_asr_fixes,
)


# ============================================================================
# Public API
# ============================================================================

def transcribe_single_video(
    video_path: Path | str,
    output_dir: Path | str,
    source_id: str,
    *,
    model_size: str = WHISPER_MODEL,
    model_root: str = MODEL_DOWNLOAD_ROOT,
    device: str = "cuda",
    compute_type: str = "float16",
    language: str = "zh",
    beam_size: int = 5,
    vad_filter: bool = True,
) -> Optional[dict]:
    """
    Transcribe a SINGLE video file using faster-whisper in-process.

    CRITICAL: initial_prompt=FOOD_IP_PROMPT is passed DIRECTLY to
    model.transcribe() — not via global variable monkey-patch.

    Returns None on failure, or a dict:
      {
        "source_id": str,
        "video_path": str,
        "segment_count": int,
        "segments": [WhisperSegment dicts],
        "segments_path": Path,   # where segments were saved
        "markdown_path": Path,   # where transcript was saved
        "duration_sec": float,
        "model": str,
      }
    """
    video_path = Path(video_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # P0: Reject directory input — must be a single file
    if not video_path.is_file():
        print(f"[direct_transcribe] ERROR: not a regular file: {video_path}")
        return None

    print(f"[direct_transcribe] {source_id}: {video_path.name}")
    print(f"[direct_transcribe] initial_prompt: {FOOD_IP_PROMPT[:80]}...")

    t_start = time.time()

    # ── Load faster-whisper ──
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        print(f"[direct_transcribe] ERROR: faster-whisper not installed: {e}")
        return None

    # ── Create model ──
    try:
        model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=model_root,
        )
    except Exception as e:
        print(f"[direct_transcribe] ERROR: Cannot load WhisperModel({model_size}): {e}")
        return None

    # ── Transcribe with FOOD_IP_PROMPT ──
    # THIS is the call that mock/spy tests must verify.
    # initial_prompt=FOOD_IP_PROMPT is the authoritative injection point.
    print(f"[direct_transcribe] Calling model.transcribe(initial_prompt=FOOD_IP_PROMPT)...")
    try:
        segments_raw, info = model.transcribe(
            str(video_path),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            initial_prompt=FOOD_IP_PROMPT,   # <-- P0-11: AUTHORITATIVE INJECTION POINT
        )
    except Exception as e:
        print(f"[direct_transcribe] ERROR: model.transcribe() failed: {e}")
        return None

    duration_sec = info.duration if hasattr(info, 'duration') else 0.0
    print(f"[direct_transcribe] Audio duration: {duration_sec:.1f}s | Language: {info.language}")

    # ── P0-3: Extract WhisperSegments from native output ──
    # segment.start / segment.end are the AUTHORITATIVE time source.
    segments = extract_segments(segments_raw, source_id)

    if not segments:
        print(f"[direct_transcribe] ERROR: No segments extracted")
        return None

    print(f"[direct_transcribe] Extracted {len(segments)} WhisperSegments")

    # ── P0-3: Validate + save immutable WhisperSegments ──
    from food_ip_models import WhisperSegment, ASRSegment
    segments = [WhisperSegment.model_validate(seg).model_dump(mode="json") for seg in segments]
    segments_path = save_segments(segments, WHISPER_SEGMENTS_DIR, source_id)
    print(f"[direct_transcribe] WhisperSegments saved: {segments_path}")

    # ── P0-3: Create ASRSegments with glossary correction ──
    # raw_text = Whisper original (preserved forever)
    # corrected_text = after safe glossary application
    # start_sec / end_sec = immutable, from Whisper
    glossary = load_glossary()

    def _glossary_fn(text: str):
        return apply_asr_fixes(text, glossary)

    asr_segments = apply_glossary_to_segments(segments, _glossary_fn)
    # Runtime contract check BEFORE persistence. Unknown/malformed fields fail hard.
    asr_segments = [ASRSegment.model_validate(seg).model_dump(mode="json")
                    for seg in asr_segments]
    asr_segments_path = save_segments(
        asr_segments, WHISPER_SEGMENTS_DIR, source_id, file_suffix="_asr"
    )
    print(f"[direct_transcribe] ASRSegments saved: {asr_segments_path}")
    print(f"[direct_transcribe] ASR corrections applied: "
          f"{sum(s.get('asr_fix_count', 0) for s in asr_segments)} fixes")

    # ── Generate Markdown from ASRSegments (corrected_text preferred) ──
    markdown_path = _segments_to_markdown(asr_segments, output_dir, source_id, video_path.stem)

    elapsed = time.time() - t_start
    print(f"[direct_transcribe] Done in {elapsed:.0f}s")

    return {
        "source_id": source_id,
        "video_path": str(video_path),
        "segment_count": len(segments),
        "segments": segments,                    # immutable WhisperSegments
        "segments_path": str(segments_path),
        "asr_segments": asr_segments,            # ASRSegments with corrected_text
        "asr_segments_path": str(asr_segments_path),
        "markdown_path": str(markdown_path),
        "duration_sec": duration_sec,
        "model": model_size,
    }


# ============================================================================
# Markdown Generation (from segments — NOT reverse)
# ============================================================================

def _segments_to_markdown(
    segments: list[dict],
    output_dir: Path,
    source_id: str,
    title: str,
) -> Path:
    """
    Generate Markdown transcript FROM Whisper segments.
    Timestamps come from segment.start_sec/end_sec.

    This is the CORRECT direction of the authority chain:
      WhisperSegment → ASRSegment → Markdown
    """
    lines = [f"# {title}\n"]
    lines.append(f"> source_id: {source_id}")
    lines.append(f"> transcribed_at: {datetime.now().isoformat()}")
    lines.append(f"> segments: {len(segments)}\n")

    for seg in segments:
        start = seg["start_sec"]
        end = seg["end_sec"]
        # Prefer corrected_text (from ASRSegment), fall back to raw_text
        text = seg.get("corrected_text", seg.get("raw_text", ""))
        ts = _fmt_ts(start)
        lines.append(f"[[{ts}]] {text}\n")

    content = "\n".join(lines)
    output_path = output_dir / f"{source_id}.md"

    # Atomic write
    tmp = output_dir / f".{source_id}.md.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    tmp.replace(output_path)

    return output_path


def _fmt_ts(sec: float) -> str:
    """Format seconds as MM:SS"""
    sec = max(0, int(sec))
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"


# ============================================================================
# Self-test
# ============================================================================

if __name__ == "__main__":
    print("=== food_ip_direct_transcribe self-test ===\n")
    print("Authority chain: WhisperSegment → ASRSegment → Markdown")
    print("Prompt injection: model.transcribe(initial_prompt=FOOD_IP_PROMPT)")
    print("NOT: global variable monkey-patch only")
    print("\nModule OK. Use transcribe_single_video() for production.")
