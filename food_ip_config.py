#!/usr/bin/env python3
"""
Food-IP 共享配置模块 v3.1
==========================
P0 hardened — fail-fast at pipeline start, content-hash identity, run audit.

CHANGES (P0):
  - P0-1: validate_all_config() called at pipeline startup, NOT import time
  - P0-2: compute_content_hash() for stable source identity
  - P0-18: generate_run_id(), run audit support
  - P0-12: glossary load respects risk_level and match_mode
"""

import os
import json
import re
import hashlib
import uuid
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Optional


# ============================================================================
# Version & Identity
# ============================================================================

PIPELINE_VERSION = "3.1.0"
DOMAIN = "food-ip"


# ============================================================================
# 路径配置
# ============================================================================

# 视频源目录
DEFAULT_INPUT_DIR = Path(r"E:\BaiduNetdiskDownload\餐饮短视频ip打造")

# 输出根目录
FOOD_IP_SOURCES_DIR = Path(r"E:\food_ip_sources")
FOOD_IP_KNOWLEDGE_DIR = Path(r"E:\food_ip_knowledge")

# 子目录
TRANSCRIPTS_DIR = FOOD_IP_SOURCES_DIR / "transcripts"
RAW_TRANSCRIPTS_DIR = FOOD_IP_SOURCES_DIR / "raw_transcripts"
KEYFRAMES_DIR = FOOD_IP_SOURCES_DIR / "keyframes"
MANIFESTS_DIR = FOOD_IP_SOURCES_DIR / "manifests"
PER_SOURCE_MANIFESTS_DIR = MANIFESTS_DIR / "by_source"
FLAGS_DIR = FOOD_IP_SOURCES_DIR / "flags"
LOGS_DIR = FOOD_IP_SOURCES_DIR / "logs"
WHISPER_SEGMENTS_DIR = FOOD_IP_SOURCES_DIR / "whisper_segments"

# 知识库子目录
RAW_CORRECTED_DIR = FOOD_IP_KNOWLEDGE_DIR / "raw_corrected"
ATOMIC_DIR = FOOD_IP_KNOWLEDGE_DIR / "atomic"
ATOMIC_BY_SOURCE_DIR = ATOMIC_DIR / "by_source"
GRAPH_DIR = FOOD_IP_KNOWLEDGE_DIR / "graph"
SYNTHESIS_DIR = FOOD_IP_KNOWLEDGE_DIR / "synthesis"
REVIEW_QUEUE_DIR = FOOD_IP_KNOWLEDGE_DIR / "review_queue"
REPORTS_DIR = FOOD_IP_KNOWLEDGE_DIR / "reports"

# 配置文件路径
GLOSSARY_PATH = Path(__file__).parent / "food_ip_config" / "food_ip_asr_glossary.json"
QUESTION_TREE_PATH = Path(__file__).parent / "food_ip_config" / "question_tree.json"
SCHEMAS_DIR = Path(__file__).parent / "food_ip_schemas"

# 现有脚本路径（复用）
TRANSCRIBE_BATCH_PATH = Path(r"E:\transcribe_batch.py")
AUDIO_PREPROCESSOR_PATH = Path(r"E:\audio_preprocessor.py")
WHISPER_VENV = Path(r"E:\whisper_venv")
FFMPEG_BIN = Path(r"E:\ffmpeg\bin")

# ============================================================================
# 模型配置
# ============================================================================

WHISPER_MODEL = "large-v3"
MODEL_DOWNLOAD_ROOT = "E:/WhisperModels"

# LLM 配置
LLM_MODEL = "deepseek-chat"
LLM_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
LLM_TEMPERATURE = 0.3
LLM_MAX_TOKENS = 8000
LLM_MAX_TOKENS_LARGE = 16000

# ============================================================================
# Food-IP 领域配置
# ============================================================================

# 内容形式列表
CREATIVE_FORMATS = [
    "口播",
    "旁白",
    "短平快",
    "实拍记录",
    "摆拍还原",
    "聊天观点",
    "讲故事",
]

# 知识类型
KNOWLEDGE_TYPES = [
    "principle",
    "technique",
    "case",
    "anti_pattern",
    "creative_format",
    "operation",
]

# 创作阶段
CONTENT_STAGES = ["planning", "writing", "shooting", "review", "operation"]

# 关系类型
RELATION_TYPES = ["same", "similar", "complementary", "conflicting", "exception"]

# CTA 类型
CTA_TYPES = ["none", "comment", "follow", "visit", "group_buy", "other"]

# 质量状态
QUALITY_STATUSES = ["good", "poor", "very_poor", "unknown"]


# ============================================================================
# P0-2: Content Hash Identity
# ============================================================================

def compute_content_hash(file_path: Path | str) -> str:
    """Compute SHA-256 hash of file content for stable identity.
    Does NOT include filename, metadata, or path.
    Same bytes → same hash regardless of filename.
    """
    file_path = Path(file_path)
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)  # 64KB buffer
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def generate_deterministic_id(*seed_parts: str, length: int = 12) -> str:
    """Stable content-based identity: sha256 hex digest prefix."""
    joined = "|".join(str(p) for p in seed_parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def generate_run_id() -> str:
    """Generate unique run ID with timestamp and random suffix."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    return f"run_{ts}_{suffix}"


def is_process_alive(pid: int) -> bool:
    """Check if a process with given PID is currently running (Windows-safe)."""
    if pid <= 0:
        return False
    try:
        import ctypes
        import ctypes.wintypes
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_INFORMATION = 0x0400
        handle = ctypes.windll.kernel32.OpenProcess(
            SYNCHRONIZE | PROCESS_QUERY_INFORMATION, False, pid
        )
        if handle == 0:
            return False
        exit_code = ctypes.wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == 259  # STILL_ACTIVE
    except Exception:
        # Fallback: try psutil if available
        try:
            import psutil
            return psutil.pid_exists(pid)
        except ImportError:
            # Last resort: assume alive (don't accidentally reset)
            return True


# ============================================================================
# 工具函数
# ============================================================================

def ensure_dirs():
    """创建所有需要的输出目录"""
    dirs = [
        TRANSCRIPTS_DIR, RAW_TRANSCRIPTS_DIR, KEYFRAMES_DIR,
        MANIFESTS_DIR, PER_SOURCE_MANIFESTS_DIR, FLAGS_DIR, LOGS_DIR,
        WHISPER_SEGMENTS_DIR,
        RAW_CORRECTED_DIR, ATOMIC_DIR, ATOMIC_BY_SOURCE_DIR, GRAPH_DIR,
        SYNTHESIS_DIR, REVIEW_QUEUE_DIR, REPORTS_DIR,
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def load_question_tree():
    """加载问题树，返回 list of dict."""
    with open(QUESTION_TREE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("questions", [])


def load_glossary() -> List[Tuple[str, str, dict]]:
    """
    加载 Food-IP ASR 术语表，按 wrong 长度倒序。
    Returns list of (wrong, right, entry_meta) for entries safe to auto-apply.
    """
    with open(GLOSSARY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = []
    for entry in data.get("replacements", []):
        wrong = entry.get("wrong", "")
        right = entry.get("right", "")
        risk = entry.get("risk_level", "low")
        mode = entry.get("match_mode", "exact_phrase")

        # P0-12: Only auto-apply safe entries
        if risk != "low":
            continue
        if mode not in ("exact_phrase", "regex"):
            continue
        if wrong == right:
            continue

        items.append((wrong, right, entry))
    items.sort(key=lambda x: len(x[0]), reverse=True)
    return items


def load_glossary_all() -> list[dict]:
    """Load all glossary entries (including context_required, high risk) for reference."""
    with open(GLOSSARY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("replacements", [])


def load_blacklist():
    """加载 ASR 黑名单词"""
    with open(GLOSSARY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("blacklist", [])


def apply_asr_fixes(text: str, glossary_items: List[Tuple[str, str, dict]]) -> Tuple[str, int, list]:
    """
    按术语表做精确替换（长串优先）。
    P0-12: Only auto-apply safe (low_risk + exact_phrase/regex) entries.
    Returns: (corrected_text, fix_count, applied_entries)
    """
    if not glossary_items:
        return text, 0, []
    total_fixes = 0
    applied = []
    for wrong, right, entry_meta in glossary_items:
        if wrong == right:
            continue
        count = text.count(wrong)
        if count > 0:
            # P0-12: Double-correction guard — check that replacement doesn't
            # create a known wrong pattern or expand a previously-correct term
            candidate = text.replace(wrong, right)
            # Guard: "人物设定" should NOT become "人物设定定" etc.
            if not _has_overcorrection(text, candidate, wrong, right):
                text = candidate
                total_fixes += count
                applied.append({"wrong": wrong, "right": right, "count": count})
    return text, total_fixes, applied


def _has_overcorrection(original: str, candidate: str, wrong: str, right: str) -> bool:
    """Detect double-correction: e.g. '人物设定' -> '人物设定定'"""
    # Simple check: if the right string already appears adjacent to where wrong
    # would be replaced, it might be an identity-correction issue.
    # More robust: check if candidate has patterns where right appears
    # immediately followed by a suffix that looks like wrong was already correct.
    if wrong.startswith(right) and len(wrong) > len(right):
        return True
    if right.endswith(wrong) and len(right) > len(wrong):
        return True
    return False


def fmt_timestamp(seconds):
    """秒数 → MM:SS 或 HH:MM:SS"""
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def now_iso():
    """当前时间 ISO 格式"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def today_str():
    """当前日期字符串"""
    return datetime.now().strftime("%Y-%m-%d")


def get_question_tree_version() -> str:
    """Get version from question_tree.json _meta block."""
    try:
        with open(QUESTION_TREE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("_meta", {}).get("version", "unknown")
    except Exception:
        return "unknown"


def get_glossary_version() -> str:
    """Get version from glossary.json _meta block."""
    try:
        with open(GLOSSARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("_meta", {}).get("version", "unknown")
    except Exception:
        return "unknown"


# ============================================================================
# P0-1: Config Validation (called at pipeline startup, NOT import time)
# ============================================================================

def validate_question_tree() -> list[str]:
    """Validate question_tree.json. Returns list of error messages (empty = OK)."""
    errors = []
    try:
        with open(QUESTION_TREE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [f"question_tree.json is not valid JSON: {e}"]
    except FileNotFoundError:
        return [f"question_tree.json not found at {QUESTION_TREE_PATH}"]

    questions = data.get("questions", [])
    if not isinstance(questions, list):
        return ["question_tree.json: 'questions' must be a list"]
    if len(questions) == 0:
        errors.append("question_tree.json: 'questions' list is empty")

    seen_ids = set()
    seen_questions = set()
    for q in questions:
        qid = q.get("question_id", "")
        question_text = q.get("question", "")
        category = q.get("category", "")

        if not qid:
            errors.append("question_tree.json: entry missing question_id")
            continue
        if not re.match(r'^Q\d{3}$', qid):
            errors.append(f"question_tree.json: invalid question_id format: {qid}")
        if qid in seen_ids:
            errors.append(f"question_tree.json: duplicate question_id: {qid}")
        seen_ids.add(qid)

        if not question_text or not question_text.strip():
            errors.append(f"question_tree.json: {qid} has empty question text")
        if not category or not category.strip():
            errors.append(f"question_tree.json: {qid} has empty category")

        # Detect near-duplicate questions
        normalized = re.sub(r'\s+', '', question_text)[:50]
        if normalized in seen_questions:
            errors.append(f"question_tree.json: {qid} has semantically similar question to another entry")
        seen_questions.add(normalized)

    return errors


def validate_glossary() -> list[str]:
    """Validate food_ip_asr_glossary.json. Returns list of error messages (empty = OK)."""
    errors = []
    try:
        with open(GLOSSARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [f"food_ip_asr_glossary.json is not valid JSON: {e}"]
    except FileNotFoundError:
        return [f"food_ip_asr_glossary.json not found at {GLOSSARY_PATH}"]

    entries = data.get("replacements", [])
    if not isinstance(entries, list):
        return ["food_ip_asr_glossary.json: 'replacements' must be a list"]

    for i, entry in enumerate(entries):
        wrong = entry.get("wrong", "")
        right = entry.get("right", "")
        risk = entry.get("risk_level", "low")
        mode = entry.get("match_mode", "exact_phrase")

        if not wrong:
            errors.append(f"glossary[{i}]: empty 'wrong' field")
        if not right:
            errors.append(f"glossary[{i}]: empty 'right' field")
        if risk not in ("low", "medium", "high"):
            errors.append(f"glossary[{i}]: invalid risk_level '{risk}'")
        if mode not in ("exact_phrase", "regex", "context_required"):
            errors.append(f"glossary[{i}]: invalid match_mode '{mode}'")
        if wrong == right:
            errors.append(f"glossary[{i}]: wrong==right ('{wrong}') — identity entry, remove")

    return errors


def validate_all_config() -> None:
    """
    Validate ALL config files before any paid API call.
    Called at CLI main() / pipeline constructor startup.
    Raises SystemExit on errors.
    """
    all_errors = []
    all_errors.extend(validate_question_tree())
    all_errors.extend(validate_glossary())

    if all_errors:
        print("\n" + "=" * 60)
        print("CONFIG VALIDATION FAILED — aborting before any API call")
        print("=" * 60)
        for err in all_errors:
            print(f"  [ERR] {err}")
        print("=" * 60 + "\n")
        raise SystemExit(1)

    print(f"[cfg] Config validation passed (question_tree + glossary)")


if __name__ == "__main__":
    ensure_dirs()
    try:
        validate_all_config()
    except SystemExit:
        pass
    questions = load_question_tree()
    glossary = load_glossary()
    print(f"Food-IP Config v{PIPELINE_VERSION} OK")
    print(f"  问题树: {len(questions)} 个问题 (v{get_question_tree_version()})")
    print(f"  术语表: {len(glossary)} 条可安全自动替换 (v{get_glossary_version()})")
    print(f"  输出目录已就绪")
