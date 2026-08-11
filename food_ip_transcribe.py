#!/usr/bin/env python3
"""
Food-IP 转录包装器 v4.1
=======================
P0 hardened: content-hash source identity, per-source manifest, whisper adapter.

P0-2: Stable Source Identity
  - content_hash from video bytes (NOT filename/mtime)
  - Same hash + different name → same source
  - Different hash → new source
  - Per-source manifest: manifests/by_source/SRCxxxx.json
  - Global sources.jsonl rebuilt from per-source manifests

P0-11: Food-IP Whisper Prompt
  - Uses food_ip_whisper_adapter to inject FOOD_IP_PROMPT
  - Verified via mock/spy that initial_prompt reaches model.transcribe()

用法:
  python food_ip_transcribe.py --limit 5
  python food_ip_transcribe.py --extract-keyframes --no-interactive
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from food_ip_config import *
from food_ip_config import (
    ensure_dirs, compute_content_hash, generate_run_id,
    load_glossary, today_str, fmt_timestamp, PIPELINE_VERSION,
    PER_SOURCE_MANIFESTS_DIR, WHISPER_SEGMENTS_DIR,
)
from food_ip_persistence import rebuild_sources_index

# ============================================================================
# Config
# ============================================================================

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm",
                    ".m4v", ".mpg", ".mpeg", ".ts", ".m3u8",
                    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma"}


def scan_videos(input_dir):
    """扫描视频文件"""
    input_dir = Path(input_dir)
    all_files = []
    for ext in VIDEO_EXTENSIONS:
        all_files.extend(input_dir.rglob(f"*{ext}"))
        all_files.extend(input_dir.rglob(f"*{ext.upper()}"))
    return sorted(set(all_files), key=lambda p: p.name)


def transcode_to_food_ip(source_md_path, source_id, title, source_file,
                         duration_sec, quality_status):
    """转 transcribe_batch.py 输出为 Food-IP 格式"""
    md_path = Path(source_md_path)
    if not md_path.exists():
        print(f"  [warn] 转录文件不存在: {md_path}")
        return None

    raw_content = md_path.read_text(encoding="utf-8")

    # Save raw backup
    raw_copy = RAW_TRANSCRIPTS_DIR / f"{source_id}.md"
    raw_copy.write_text(raw_content, encoding="utf-8")

    # Food-IP frontmatter
    food_ip_header = f"""---
source_id: {source_id}
title: {title}
source_file: {source_file}
transcription_model: whisper-large-v3
language: zh
duration_seconds: {duration_sec}
transcribed_at: {today_str()}
quality_status: {quality_status}
pipeline_version: {PIPELINE_VERSION}
---

"""

    import re
    content = re.sub(r'^---\s*\n.*?\n(?:---|\.\.\.)', '', raw_content, flags=re.DOTALL)
    content = re.sub(r'^> 主题:.*\n', '', content, flags=re.MULTILINE)
    content = re.sub(r'^> 日期:.*\n', '', content, flags=re.MULTILINE)
    content = content.strip()

    if not content.startswith("# "):
        content = f"# {title}\n\n{content}"

    final = food_ip_header + content
    output_path = TRANSCRIPTS_DIR / f"{source_id}.md"
    output_path.write_text(final, encoding="utf-8")
    print(f"  [out] {output_path}")
    print(f"  [raw] {raw_copy}")
    return output_path


def run_transcription(video_path, output_dir, source_id=None, relaxed=False,
                     preprocess=False, use_direct=True):
    """
    P0-3/P0-11: Transcribe a SINGLE video file.

    PRIMARY PATH (use_direct=True): Uses food_ip_direct_transcribe which:
      - Passes FOOD_IP_PROMPT directly to model.transcribe(initial_prompt=...)
      - Captures Whisper native segments (start_sec/end_sec from Whisper)
      - Saves whisper_segments.json immediately
      - One video file → one transcription (no directory scanning)

    FALLBACK PATH (use_direct=False): Legacy subprocess mode calling
    transcribe_batch.py. Does NOT capture native segments.

    Returns: (markdown_path | None, segments_result | None)
    """
    output_dir = Path(output_dir)
    stem = video_path.stem
    sid = source_id or stem
    expected_md = output_dir / f"{stem}.md"

    if use_direct:
        try:
            from food_ip_direct_transcribe import transcribe_single_video
            result = transcribe_single_video(
                video_path, output_dir, sid,
            )
            if result and Path(result["markdown_path"]).exists():
                print(f"  [direct] Segments: {result['segment_count']} | Markdown: {result['markdown_path']}")
                return Path(result["markdown_path"]), result
            # P0: NO automatic fallback to legacy subprocess.
            # No native Whisper segments → transcription FAILED.
            # Legacy subprocess can be used explicitly via use_direct=False.
            print(f"  [direct] Direct transcription FAILED — NO native Whisper segments")
            print(f"  [direct] Source will NOT be marked successful")
            return None, None
        except ImportError as e:
            print(f"  [direct] food_ip_direct_transcribe not available: {e}")
            print(f"  [direct] FAILED — cannot proceed without native segment capture")
            return None, None
        except Exception as e:
            print(f"  [direct] Error: {e}")
            print(f"  [direct] FAILED — NO fallback to legacy subprocess")
            return None, None

    # ── FALLBACK: Legacy subprocess mode ──
    # Task 2 fix: Pass the SPECIFIC video file, NOT video_path.parent
    cmd_parts = [
        str(Path(sys.executable).parent / "python.exe"),
        str(TRANSCRIBE_BATCH_PATH),
        "--input", str(video_path),     # ← Task 2: SPECIFIC file, not parent dir
        "--output", str(output_dir),
        "--no-interactive",
    ]
    if relaxed:
        cmd_parts.append("--relaxed")
    if preprocess:
        cmd_parts.append("--preprocess")

    cmd_str = " ".join(f'"{p}"' if " " in str(p) else str(p) for p in cmd_parts)
    print(f"  [cmd] {cmd_str[:120]}...")
    result = subprocess.run(cmd_str, shell=True,
                            cwd=str(TRANSCRIBE_BATCH_PATH.parent),
                            capture_output=True, text=True, timeout=7200)

    if result.returncode != 0:
        print(f"  [error] exit {result.returncode}")
        if result.stderr:
            print(f"  [stderr] {result.stderr[-500:]}")
        return None, None

    if expected_md.exists():
        return expected_md, None

    for f in output_dir.glob("*.md"):
        if f.stem in stem or stem in f.stem:
            return f, None
    return None, None


def assess_quality(md_path, duration_sec):
    """质量评估"""
    try:
        content = Path(md_path).read_text(encoding="utf-8")
        body = content.split("---\n", 2)[-1] if "---" in content else content
        ts_lines = [l for l in body.split("\n") if "]]" in l and "[[" in l]
        content_bytes = len(body.encode("utf-8"))
        density = content_bytes / max(duration_sec, 1)
        ts_density = len(ts_lines) / max(duration_sec / 60, 1)
        if density < 40 or ts_density < 2:
            return "very_poor"
        elif density < 80 or ts_density < 4:
            return "poor"
        else:
            return "good"
    except Exception:
        return "unknown"


# ============================================================================
# P0-2: Stable Source Identity
# ============================================================================

def find_or_create_source_id(video_path, manifest_index):
    """
    P0-2: Stable source identity based on content hash.

    Args:
        video_path: Path to video file
        manifest_index: {content_hash: source_info} from existing manifests

    Returns:
        (source_id, content_hash, is_new) tuple
    """
    content_hash = compute_content_hash(video_path)

    if content_hash in manifest_index:
        existing = manifest_index[content_hash]
        return existing["source_id"], content_hash, False

    # New source — generate next ID
    next_num = len(manifest_index) + 1
    # Ensure uniqueness even with gaps
    existing_nums = set()
    for info in manifest_index.values():
        sid = info["source_id"]
        try:
            existing_nums.add(int(sid.replace("SRC", "")))
        except ValueError:
            pass
    while next_num in existing_nums:
        next_num += 1

    source_id = f"SRC{next_num:04d}"
    return source_id, content_hash, True


def load_manifest_index():
    """
    P0-2: Load all per-source manifests into a content_hash → source_info index.
    """
    index = {}
    if PER_SOURCE_MANIFESTS_DIR.exists():
        for mf in PER_SOURCE_MANIFESTS_DIR.glob("*.json"):
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                ch = entry.get("content_hash", "")
                if ch:
                    index[ch] = entry
            except (json.JSONDecodeError, IOError):
                pass
    return index


def save_per_source_manifest(entry: dict):
    """P0-2: Validate and atomically save the per-source manifest contract."""
    from food_ip_models import SourceManifestEntry

    validated = SourceManifestEntry.model_validate(entry).model_dump(mode="json")
    PER_SOURCE_MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    source_id = validated["source_id"]
    tmp = PER_SOURCE_MANIFESTS_DIR / f".{source_id}.json.tmp"
    target = PER_SOURCE_MANIFESTS_DIR / f"{source_id}.json"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(validated, f, ensure_ascii=False, indent=2)
    tmp.replace(target)


# ============================================================================
# Main
# ============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Food-IP 转录管线 v4.1")
    parser.add_argument("--input", type=str, default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--output", type=str, default=str(FOOD_IP_SOURCES_DIR))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-interactive", action="store_true", default=True)
    parser.add_argument("--extract-keyframes", action="store_true", default=False)
    parser.add_argument("--relaxed", action="store_true", default=False)
    parser.add_argument("--preprocess", action="store_true", default=False)
    parser.add_argument("--resume", action="store_true", default=True)
    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.is_dir():
        print(f"[error] 源目录不存在: {input_dir}")
        sys.exit(1)

    # P0-1: Fail-fast config validation
    try:
        validate_all_config()
    except SystemExit:
        return

    ensure_dirs()
    glossary = load_glossary()
    print(f"Food-IP 转录管线 v{PIPELINE_VERSION}")
    print(f"  输入: {input_dir}")
    print(f"  输出: {FOOD_IP_SOURCES_DIR}")
    print(f"  术语表: {len(glossary)} 条安全自动替换")

    videos = scan_videos(input_dir)
    print(f"\n  发现 {len(videos)} 个视频")

    if args.limit:
        videos = videos[:args.limit]
        print(f"  限制: 前 {args.limit} 个")

    # P0-2: Load content-hash manifest index
    manifest_index = load_manifest_index()
    print(f"  Manifest: {len(manifest_index)} 已有 source")

    # Temp output dir
    temp_out = FOOD_IP_SOURCES_DIR / "_temp_transcripts"
    temp_out.mkdir(parents=True, exist_ok=True)

    stats = {"success": 0, "skipped": 0, "error": 0, "keyframes": 0}
    log_path = LOGS_DIR / f"transcribe_{today_str()}.log"

    with open(log_path, "w", encoding="utf-8") as log_f:
        log_f.write(f"Food-IP Transcription Log — {today_str()} v{PIPELINE_VERSION}\n\n")

    for idx, video_path in enumerate(videos, 1):
        stem = video_path.stem
        print(f"\n[{idx}/{len(videos)}] {stem[:60]}")

        try:
            t_start = time.time()

            # P0-2: Stable source identity
            source_id, content_hash, is_new = find_or_create_source_id(
                video_path, manifest_index
            )

            if not is_new and args.resume:
                existing = manifest_index[content_hash]
                print(f"  [P0-2] 已知 source {source_id} (hash={content_hash[:12]}...) — 跳过")
                print(f"    原文件: {existing.get('source_file', '?')}")
                stats["skipped"] += 1
                continue

            print(f"  [P0-2] 新 source: {source_id} (hash={content_hash[:12]}...)")

            # Step 1: Transcribe (Task 1: direct path with segment capture)
            print(f"  转录中...")
            source_md, segments_result = run_transcription(
                video_path, temp_out, source_id=source_id,
                relaxed=args.relaxed,
                preprocess=args.preprocess,
            )
            if source_md is None or not segments_result:
                print(f"  FAIL: 转录失败或缺少原生 Segment 结果")
                stats["error"] += 1
                continue
            asr_segments_path = segments_result.get("asr_segments_path", "")
            if not asr_segments_path or not Path(asr_segments_path).is_file():
                print(f"  FAIL: 缺少 authoritative ASRSegment 文件")
                stats["error"] += 1
                continue

            # Step 2: Duration
            duration_sec = _get_duration(video_path)
            quality = assess_quality(source_md, duration_sec)

            # Step 3: Transcode
            title = video_path.stem
            import re as _re
            clean_title = _re.sub(r'^\d+\.\s*', '', title)
            clean_title = _re.sub(r'-平滑填充$', '', clean_title).strip()
            if clean_title:
                title = clean_title

            food_ip_md = transcode_to_food_ip(
                source_md, source_id, title,
                video_path.name, duration_sec, quality
            )

            # Step 4: Keyframes
            keyframes = []
            if args.extract_keyframes:
                try:
                    from extract_keyframes import extract_keyframes, save_keyframe_manifest
                    print(f"  提取关键帧...")
                    keyframes = extract_keyframes(
                        str(video_path), str(KEYFRAMES_DIR),
                        source_id=source_id, verbose=True
                    )
                    if keyframes:
                        save_keyframe_manifest(keyframes, MANIFESTS_DIR, source_id)
                        stats["keyframes"] += len(keyframes)
                except ImportError:
                    print(f"  [warn] opencv-python 未安装")
                except Exception as e:
                    print(f"  [warn] 关键帧失败: {e}")

            # Step 5: Quality flag
            flag_path = FLAGS_DIR / f"{source_id}.flag"
            flag_path.write_text(json.dumps({
                "source_id": source_id, "quality": quality,
                "timestamp": today_str(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            # Step 6: P0-2 Per-source manifest (source truth)
            elapsed = time.time() - t_start
            manifest_entry = {
                "source_id": source_id,
                "content_hash": content_hash,
                "title": title,
                "source_file": video_path.name,
                "file_size": video_path.stat().st_size,
                "duration_sec": duration_sec,
                "duration_str": fmt_timestamp(duration_sec),
                "transcript": f"transcripts/{source_id}.md",
                "raw_transcript": f"raw_transcripts/{source_id}.md",
                "keyframes_dir": f"keyframes/{source_id}" if keyframes else None,
                "keyframe_count": len(keyframes),
                "quality_status": quality,
                "transcribed_at": today_str(),
                "processing_time_sec": int(elapsed),
                "pipeline_version": PIPELINE_VERSION,
                "whisper_segments_path": f"whisper_segments/{source_id}_whisper_segments.json",
                "asr_segments_path": f"whisper_segments/{source_id}_asr_whisper_segments.json",
                "segment_count": segments_result["segment_count"],
            }
            # Runtime manifest contract must match the Pydantic source of truth.
            from food_ip_models import SourceManifestEntry
            manifest_entry = SourceManifestEntry.model_validate(manifest_entry).model_dump(mode="json")
            save_per_source_manifest(manifest_entry)

            # Update in-memory index
            manifest_index[content_hash] = manifest_entry

            # Rebuild global index
            rebuild_sources_index()

            stats["success"] += 1
            print(f"  OK {source_id} | {fmt_timestamp(duration_sec)} | {quality} | {elapsed:.0f}s")

        except Exception as e:
            print(f"  FAIL: {e}")
            stats["error"] += 1
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"ERROR: {video_path.name} — {e}\n")

    # Report
    print(f"\n{'='*60}")
    print(f"转录完成 v{PIPELINE_VERSION}")
    print(f"  成功: {stats['success']} | 跳过: {stats['skipped']} | 失败: {stats['error']}")
    if args.extract_keyframes:
        print(f"  关键帧: {stats['keyframes']} 张")
    print(f"  日志: {log_path}")
    print(f"  输出: {FOOD_IP_SOURCES_DIR}")


def _get_duration(video_path):
    try:
        ffprobe = Path(r"E:\ffmpeg\bin\ffprobe.exe")
        if not ffprobe.exists():
            ffprobe = "ffprobe"
        cmd = [str(ffprobe), "-v", "error",
               "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except Exception:
        return 0


if __name__ == "__main__":
    main()
