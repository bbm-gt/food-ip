"""Food-IP Canonical JSON v1 and its fixed hash envelopes."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


SQLITE_INT_MIN = -(2**63)
SQLITE_INT_MAX = 2**63 - 1


class CanonicalJSONError(ValueError):
    """Raised when a value cannot be represented by Canonical JSON v1."""


def normalize_text(value: str) -> str:
    """Apply the field-level request normalization allowed by v1."""
    _validate_unicode(value)
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def is_unicode_white_space(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x0009 <= codepoint <= 0x000D
        or codepoint == 0x0020
        or codepoint == 0x0085
        or codepoint == 0x00A0
        or codepoint == 0x1680
        or 0x2000 <= codepoint <= 0x200A
        or codepoint in {0x2028, 0x2029, 0x202F, 0x205F, 0x3000}
    )


def is_blank_text(value: str) -> bool:
    return not value or all(is_unicode_white_space(char) for char in value)


def _validate_unicode(value: str) -> None:
    for char in value:
        codepoint = ord(char)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise CanonicalJSONError("unpaired Unicode surrogate is not allowed")


def _validate(value: Any) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if not SQLITE_INT_MIN <= value <= SQLITE_INT_MAX:
            raise CanonicalJSONError("integer is outside SQLite signed 64-bit range")
        return
    if isinstance(value, float):
        raise CanonicalJSONError("floating-point numbers are not allowed")
    if isinstance(value, str):
        _validate_unicode(value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJSONError("object keys must be strings")
            _validate_unicode(key)
            _validate(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _validate(item)
        return
    raise CanonicalJSONError(f"unsupported JSON value type: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    _validate(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (UnicodeEncodeError, ValueError, TypeError) as exc:
        raise CanonicalJSONError(str(exc)) from exc


def canonical_text(value: Any) -> str:
    return canonical_bytes(value).decode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalJSONError(f"duplicate object key: {key}")
        result[key] = value
    return result


def parse_json(raw: str | bytes) -> Any:
    """Strictly parse JSON, rejecting BOM, duplicates, floats and trailing data."""
    if isinstance(raw, bytes):
        if raw.startswith(b"\xef\xbb\xbf"):
            raise CanonicalJSONError("UTF-8 BOM is not allowed")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CanonicalJSONError("invalid UTF-8") from exc
    elif raw.startswith("\ufeff"):
        raise CanonicalJSONError("BOM is not allowed")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=lambda _: (_ for _ in ()).throw(
                CanonicalJSONError("floating-point numbers are not allowed")
            ),
            parse_int=lambda text: int(text),
            parse_constant=lambda value: (_ for _ in ()).throw(
                CanonicalJSONError(f"invalid numeric constant: {value}")
            ),
        )
    except CanonicalJSONError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise CanonicalJSONError(str(exc)) from exc
    _validate(value)
    return value


def parse_canonical_object(raw: str | bytes) -> dict[str, Any]:
    value = parse_json(raw)
    if not isinstance(value, dict):
        raise CanonicalJSONError("JSON value must be an object")
    if canonical_bytes(value) != (raw.encode("utf-8") if isinstance(raw, str) else raw):
        raise CanonicalJSONError("JSON text is not canonical")
    return value


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_hex(canonical_bytes(value))


def normalized_request(owner_text: str, parameters: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(owner_text, str) or is_blank_text(owner_text):
        raise CanonicalJSONError("owner_text must not be blank")
    if type(parameters) is not dict:
        raise CanonicalJSONError("parameters must be an object")
    if parameters:
        raise CanonicalJSONError("request format v1 parameters must be an empty object")
    return {"owner_text": normalize_text(owner_text), "parameters": parameters}


def validate_normalized_request(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != {"owner_text", "parameters"}:
        raise CanonicalJSONError("normalized request v1 has unknown or missing fields")
    normalized = normalized_request(value["owner_text"], value["parameters"])
    if normalized != value:
        raise CanonicalJSONError("normalized request text is not LF/NFC normalized")
    return value


def state_envelope(state_version: int, stage: str, state_json: dict[str, Any]) -> dict[str, Any]:
    return {"state_version": state_version, "stage": stage, "state_json": state_json}


def state_sha256(state_version: int, stage: str, state_json: dict[str, Any]) -> str:
    return canonical_sha256(state_envelope(state_version, stage, state_json))


def checkpoint_sha256(
    session_id: str,
    covered_through_seq: int,
    checkpoint_json: dict[str, Any],
    *,
    format_version: int = 1,
) -> str:
    return canonical_sha256(
        {
            "format_version": format_version,
            "session_id": session_id,
            "covered_through_seq": covered_through_seq,
            "checkpoint_json": checkpoint_json,
        }
    )
