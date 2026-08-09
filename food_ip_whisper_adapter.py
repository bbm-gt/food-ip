#!/usr/bin/env python3
"""
LEGACY / DEPRECATED — monkey-patch 注入路径（改 transcribe_batch.ZH_PROMPT）。

生产路径已改为 food_ip_direct_transcribe.transcribe_single_video()，它直接把
FOOD_IP_PROMPT 传给 model.transcribe(initial_prompt=FOOD_IP_PROMPT)。
food_ip_transcribe.main() 不再调用本模块的 run_transcribe_with_food_ip()。

保留仅供历史审计 / 回滚参考，请勿在新代码中依赖。
"""

import sys
from pathlib import Path

# Food-IP domain prompt for faster-whisper decoder (≤100 chars for Whisper's 224-token limit)
FOOD_IP_PROMPT = (
    "餐饮短视频IP打造，到店理由破人群认知。"
    "口播旁白实拍记录摆拍还原短平快。"
    "完播率互动率信息密度，钩子开头停留。"
    "老板人设IP定位铁三角，团购矩阵号运营。"
)

_DOMAIN_LABEL = "food-ip"
_prompt_injected = False
_effective_prompt = None


def get_effective_prompt() -> str | None:
    """Returns the currently active domain prompt (for test verification)."""
    return _effective_prompt


def get_domain_label() -> str:
    """Returns the active domain label."""
    return _DOMAIN_LABEL


def inject_food_ip_prompt() -> bool:
    """
    Monkey-patch transcribe_batch.ZH_PROMPT with FOOD_IP_PROMPT.
    Must be called BEFORE transcribe_batch creates the WhisperModel.

    Returns True if injection succeeded.
    """
    global _prompt_injected, _effective_prompt

    # Ensure E:\ is in sys.path so we can import transcribe_batch
    e_root = str(Path("E:/"))
    if e_root not in sys.path:
        sys.path.insert(0, e_root)

    try:
        import transcribe_batch

        # Store original for rollback
        if not _prompt_injected:
            _ORIGINAL_ZH_PROMPT = transcribe_batch.ZH_PROMPT

        transcribe_batch.ZH_PROMPT = FOOD_IP_PROMPT
        _effective_prompt = FOOD_IP_PROMPT
        _prompt_injected = True

        # Log (but NOT the API key)
        print(f"[whisper-adapter] Domain: {_DOMAIN_LABEL}")
        print(f"[whisper-adapter] initial_prompt: {FOOD_IP_PROMPT[:80]}...")

        return True
    except ImportError as e:
        print(f"[whisper-adapter] ERROR: Cannot import transcribe_batch: {e}")
        return False
    except Exception as e:
        print(f"[whisper-adapter] ERROR: Injection failed: {e}")
        return False


def verify_injection() -> dict:
    """
    Verify that FOOD_IP_PROMPT was injected correctly.
    Returns dict with verification results (for test assertions).
    """
    result = {
        "injected": _prompt_injected,
        "effective_prompt": _effective_prompt,
        "matches_food_ip": _effective_prompt == FOOD_IP_PROMPT,
        "domain": _DOMAIN_LABEL,
    }
    return result


def run_transcribe_with_food_ip(input_dir: str, output_dir: str,
                                limit: int = None, relaxed: bool = False,
                                preprocess: bool = False) -> int:
    """
    Run transcribe_batch.main() with Food-IP prompt injected.
    This is the main entry point used by food_ip_transcribe.py.

    Returns: exit code (0 = success)
    """
    if not inject_food_ip_prompt():
        return 1

    try:
        import transcribe_batch
        # Override sys.argv to pass CLI args to transcribe_batch
        import sys as _sys
        _old_argv = _sys.argv[:]
        _sys.argv = [
            "transcribe_batch.py",
            "--input", str(input_dir),
            "--output", str(output_dir),
            "--no-interactive",
        ]
        if limit:
            _sys.argv.extend(["--limit", str(limit)])
        if relaxed:
            _sys.argv.append("--relaxed")
        if preprocess:
            _sys.argv.append("--preprocess")

        transcribe_batch.main()
        _sys.argv = _old_argv
        return 0
    except Exception as e:
        print(f"[whisper-adapter] Transcription failed: {e}")
        return 1


if __name__ == "__main__":
    # Self-test: verify injection works
    print("=== food_ip_whisper_adapter self-test ===\n")
    ok = inject_food_ip_prompt()
    print(f"Injection: {'OK' if ok else 'FAILED'}")
    result = verify_injection()
    for k, v in result.items():
        print(f"  {k}: {v}")
