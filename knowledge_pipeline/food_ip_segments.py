#!/usr/bin/env python3
"""
ASR Segment Extractor v1.0
===========================
P0-3: Extract WhisperSegments from faster-whisper raw output.

CRITICAL DESIGN:
  Whisper Segment (native start/end/text from faster-whisper)
  → ASRSegment (raw_text preserved + corrected_text added)
  → Markdown (generated from segments, NOT the other way)

Timestamps ALWAYS from Whisper. LLM NEVER creates start/end times.
Segment ID is deterministic: {source_id}-SEG{idx:04d}

Usage:
  from food_ip_segments import extract_segments, save_segments
  segments = extract_segments(whisper_transcribe_result, source_id)
  save_segments(segments, output_dir)
"""

import json
from pathlib import Path
from typing import List
from datetime import datetime


def extract_segments(segments_raw, source_id: str) -> List[dict]:
    """
    Extract WhisperSegments from faster-whisper transcribe() result.

    Args:
        segments_raw: iterator of faster-whisper Segment objects
                      (each has .start, .end, .text attributes)
        source_id: e.g. "SRC0001"

    Returns:
        List of WhisperSegment dicts with fields:
          segment_id, source_id, start_sec, end_sec, raw_text
    """
    segments = []
    for idx, seg in enumerate(segments_raw, 1):
        segment = {
            "segment_id": f"{source_id}-SEG{idx:04d}",
            "source_id": source_id,
            "start_sec": round(seg.start, 2),
            "end_sec": round(seg.end, 2),
            "raw_text": seg.text.strip(),
        }
        segments.append(segment)
    return segments


def save_segments(segments: List[dict], output_dir: Path | str, source_id: str,
                  file_suffix: str = "") -> Path:
    """
    Save segment records to authoritative storage.

    ``source_id`` always remains the real SRCxxxx identity inside the payload.
    ``file_suffix`` only distinguishes the representation on disk (e.g. ``_asr``).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_stem = f"{source_id}{file_suffix}"
    output_path = output_dir / f"{file_stem}_whisper_segments.json"

    data = {
        "source_id": source_id,
        "segment_count": len(segments),
        "extracted_at": datetime.now().isoformat(),
        "segments": segments,
    }

    # Atomic write: .tmp then os.replace
    tmp_path = output_dir / f".{file_stem}_whisper_segments.json.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    tmp_path.replace(output_path)
    return output_path


def apply_glossary_to_segments(segments: List[dict],
                                glossary_fn) -> List[dict]:
    """
    Apply glossary corrections to produce ASRSegments.
    raw_text is PRESERVED; corrected_text is added as a new field.

    Args:
        segments: List of WhisperSegment dicts
        glossary_fn: Function that takes text and returns (corrected_text, fix_count, applied_list)

    Returns:
        List of ASRSegment dicts (original + corrected_text field)
    """
    corrected_segments = []
    for seg in segments:
        corrected_text, fix_count, applied = glossary_fn(seg["raw_text"])
        corrected_seg = dict(seg)  # Preserve all original fields
        corrected_seg["corrected_text"] = corrected_text
        corrected_seg["asr_fix_count"] = fix_count
        corrected_seg["asr_fixes_applied"] = applied
        corrected_segments.append(corrected_seg)
    return corrected_segments


def get_segment_time_range(segments: List[dict],
                            segment_ids: List[str]) -> tuple:
    """
    Compute time range for a set of segments.
    Used by SemanticChunker to derive chunk start/end from segment_ids.

    Returns:
        (min_start_sec, max_end_sec)
    """
    if not segment_ids or not segments:
        return (0.0, 0.0)

    seg_map = {s["segment_id"]: s for s in segments}
    starts = []
    ends = []
    for sid in segment_ids:
        if sid in seg_map:
            starts.append(seg_map[sid]["start_sec"])
            ends.append(seg_map[sid]["end_sec"])

    if not starts:
        return (0.0, 0.0)

    return (round(min(starts), 2), round(max(ends), 2))


def fmt_time_range(start_sec: float, end_sec: float) -> str:
    """Format time range as MM:SS-MM:SS or HH:MM:SS-HH:MM:SS."""
    def fmt(sec):
        sec = max(0, int(sec))
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
    return f"{fmt(start_sec)}-{fmt(end_sec)}"


if __name__ == "__main__":
    print("food_ip_segments module OK")
    print("  Segment ID format: SRCxxxx-SEGnnnn")
    print("  Timestamps: ALWAYS from Whisper, NEVER from LLM")
    print("  Authority chain: WhisperSegment → ASRSegment → Markdown")
