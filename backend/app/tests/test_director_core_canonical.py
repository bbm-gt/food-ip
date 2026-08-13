import pytest

from backend.app.director_core.canonical import (
    CanonicalJSONError,
    canonical_bytes,
    canonical_sha256,
    normalized_request,
    parse_json,
    state_sha256,
)


def test_canonical_json_is_stable_and_preserves_array_order() -> None:
    left = {"餐厅": "好", "a": 1, "items": [1, 2]}
    right = {"items": [1, 2], "a": 1, "餐厅": "好"}
    assert canonical_bytes(left) == canonical_bytes(right)
    assert canonical_sha256(left) == canonical_sha256(right)
    assert canonical_sha256({**left, "items": [2, 1]}) != canonical_sha256(left)
    assert canonical_bytes(left).startswith(b'{"a":1')
    assert not canonical_bytes(left).startswith(b"\xef\xbb\xbf")


def test_request_normalization_is_lf_and_nfc_without_overwriting_raw_text() -> None:
    raw = "  Cafe\u0301\r\n第二行\r  "
    request = normalized_request(raw, {})
    assert request["owner_text"] == "  Café\n第二行\n  "
    assert raw == "  Cafe\u0301\r\n第二行\r  "


@pytest.mark.parametrize(
    "value",
    [1.0, float("nan"), float("inf"), 2**63, -(2**63) - 1, {1: "bad"}, b"bad"],
)
def test_canonical_json_rejects_illegal_values(value: object) -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_bytes(value)


@pytest.mark.parametrize(
    "raw",
    ['{"a":1,"a":2}', '{"n":1.5}', '{"n":NaN}', '{"a":1} trailing', '\ufeff{"a":1}', '"\ud800"'],
)
def test_strict_json_parser_rejects_invalid_input(raw: str) -> None:
    with pytest.raises(CanonicalJSONError):
        parse_json(raw)


def test_normalized_request_rejects_unicode_white_space_only() -> None:
    with pytest.raises(CanonicalJSONError):
        normalized_request("\u3000\n\t", {})


def test_state_and_snapshot_hash_use_same_envelope() -> None:
    state = {"format_version": 1}
    assert state_sha256(3, "CREATE", state) == canonical_sha256(
        {"state_version": 3, "stage": "CREATE", "state_json": state}
    )


def test_canonical_json_rejects_python_tuple_arrays() -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_bytes(("not", "a", "JSON", "array"))
