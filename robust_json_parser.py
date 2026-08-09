#!/usr/bin/env python3
"""
Robust JSON Parser v2.0
=======================
Production-grade JSON extraction from LLM output.

Handles:
  - Raw JSON
  - Fenced JSON (```json ... ```)
  - Nested objects/arrays
  - Leading/trailing explanatory text
  - Empty content
  - Malformed JSON
  - Braces {} inside JSON string values

Anti-patterns explicitly forbidden:
  - Regex match first `{...}` as result
  - Silent None return on parseable but invalid content

Returns: (parsed_obj | None, error_message | None)
"""

import json as _json
import re
from typing import Any, Optional, Tuple


def parse_json(text: str) -> Tuple[Optional[Any], Optional[str]]:
    """
    Robustly parse JSON from LLM output.

    Args:
        text: Raw LLM output string

    Returns:
        (parsed_object, None) on success
        (None, error_message) on failure
    """
    if text is None:
        return None, "Input is None"

    text = text.strip()

    if not text:
        return None, "Input is empty after strip"

    # ── Strategy 0: Direct json.loads (DeepSeek JSON mode output) ──
    try:
        obj = _json.loads(text)
        if isinstance(obj, (dict, list)):
            return obj, None
        # Valid JSON but not dict/list — wrap if needed? No, return as-is for caller to decide
        return obj, None
    except _json.JSONDecodeError:
        pass

    # ── Strategy 1: Unwrap fenced code blocks ──
    # ```json ... ``` or ``` ... ```
    fenced_match = re.search(
        r'```(?:json)?\s*\n?(.*?)\n?```',
        text, re.DOTALL
    )
    if fenced_match:
        inner = fenced_match.group(1).strip()
        try:
            obj = _json.loads(inner)
            if isinstance(obj, (dict, list)):
                return obj, None
            return obj, None
        except _json.JSONDecodeError:
            pass  # Fall through to brace counting

    # ── Strategy 2: Brace/brace counting with string awareness ──
    # Find the first '{' or '[' that starts a JSON structure
    return _extract_by_bracket_counting(text)


def _extract_by_bracket_counting(text: str) -> Tuple[Optional[Any], Optional[str]]:
    """
    Extract JSON object/array by counting brackets while tracking string state.
    Correctly handles: { } inside strings, escaped quotes, backslashes.
    """
    # Find first structural character
    start_idx = -1
    bracket_type = None
    for i, ch in enumerate(text):
        if ch == '{':
            start_idx = i
            bracket_type = 'object'
            break
        elif ch == '[':
            start_idx = i
            bracket_type = 'array'
            break

    if start_idx == -1:
        return None, "No JSON object or array found in text"

    open_char = '{' if bracket_type == 'object' else '['
    close_char = '}' if bracket_type == 'object' else ']'

    depth = 0
    in_string = False
    string_quote = None  # " or '
    escape_next = False

    for i in range(start_idx, len(text)):
        ch = text[i]

        if escape_next:
            escape_next = False
            continue

        if ch == '\\':
            escape_next = True
            continue

        if in_string:
            if ch == string_quote:
                in_string = False
                string_quote = None
            continue

        if ch in ('"', "'"):
            in_string = True
            string_quote = ch
            continue

        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                candidate = text[start_idx:i + 1]
                try:
                    obj = _json.loads(candidate)
                    return obj, None
                except _json.JSONDecodeError as e:
                    return None, f"Brace-counted block is not valid JSON: {e}"
                except Exception as e:
                    return None, f"Unexpected error parsing brace-counted block: {e}"

    return None, f"Unterminated {bracket_type}: depth={depth} at end of text"


# ============================================================================
# Pydantic validation wrapper (used by food_ip_refine.py)
# ============================================================================

def parse_and_validate(
    text: str,
    model_class,
    max_retries: int = 1,
) -> Tuple[Optional[Any], Optional[str], bool]:
    """
    Parse JSON from LLM output and validate against Pydantic model.

    Args:
        text: Raw LLM output
        model_class: Pydantic BaseModel subclass
        max_retries: Number of retry attempts (1 = try once, retry once)

    Returns:
        (validated_model_instance, None, False) on success
        (None, error_message, True) if should_retry
        (None, error_message, False) if should_review/fail
    """
    # Step 1: Parse JSON
    obj, parse_error = parse_json(text)
    if obj is None:
        return None, f"JSON parse failed: {parse_error}", True  # retry

    if not isinstance(obj, dict):
        return None, f"Expected JSON object, got {type(obj).__name__}", True  # retry

    # Step 2: Pydantic validation
    try:
        validated = model_class.model_validate(obj)
        return validated, None, False  # success
    except Exception as e:
        # Validation failed — this is likely not fixable by retry
        return None, f"Pydantic validation failed: {e}", False  # review/fail


# ============================================================================
# Self-test
# ============================================================================

if __name__ == "__main__":
    tests_passed = 0
    tests_total = 0

    def _test(name, text, expect_ok=True, expect_key=None):
        global tests_passed, tests_total
        tests_total += 1
        obj, err = parse_json(text)
        if expect_ok:
            if obj is not None and err is None:
                tests_passed += 1
                print(f"  [PASS] {name}")
                if expect_key and isinstance(obj, dict):
                    assert expect_key in obj, f"Missing key: {expect_key}"
            else:
                print(f"  [FAIL] {name}: err={err}")
        else:
            if obj is None and err is not None:
                tests_passed += 1
                print(f"  [PASS] {name}")
            else:
                print(f"  [FAIL] {name}: unexpected success: {obj}")

    print("=== robust_json_parser self-test ===\n")

    # Basic
    _test("raw dict", '{"a": 1}')
    _test("raw list", '[1,2,3]')
    _test("nested", '{"a": {"b": [1,2,{"c":3}]}}')

    # Fenced
    _test("fenced json", '```json\n{"x": "y"}\n```')
    _test("fenced no lang", '```\n{"x": "y"}\n```')
    _test("fenced with newlines", '```json\n{\n  "a": 1\n}\n```')

    # Leading/trailing text
    _test("leading text", 'Some explanation here: {"result": 42} and more text.')
    _test("trailing text", '{"result": 42} and some more text here.')

    # P0-9 critical: braces inside JSON string
    _test("braces in string", '{"text": "some {nested} braces here", "ok": true}', expect_key="text")
    _test("multiple braces in string", '{"key": "a{b}c{d}e", "val": 1}', expect_key="key")
    _test("closing brace in string", '{"msg": "this is } not a close", "x": 1}', expect_key="msg")

    # Escaped characters
    _test("escaped quote in string", '{"t": "he said \\"hello\\" there"}', expect_key="t")
    _test("escaped backslash", '{"path": "C:\\\\Users\\\\HP"}', expect_key="path")

    # Empty / malformed
    _test("empty string", "", expect_ok=False)
    _test("none input", None, expect_ok=False)
    _test("malformed", "this is not json at all", expect_ok=False)
    _test("unterminated", '{"a": 1', expect_ok=False)
    _test("no json", "just some text without any brackets", expect_ok=False)

    print(f"\n{tests_passed}/{tests_total} passed")
