"""Regression tests for the tolerant LLM-response parser in classify.py."""

from __future__ import annotations

from mailllama.services.classify import _parse_classifications


def test_parses_expected_shape():
    r = {
        "classifications": [
            {"sender_index": 0, "label": "newsletter", "confidence": 0.9},
            {"sender_index": 1, "label": "personal", "confidence": 0.8},
        ]
    }
    out = _parse_classifications(r)
    assert out[0]["label"] == "newsletter"
    assert out[1]["label"] == "personal"


def test_bare_list_at_top_level():
    r = [
        {"sender_index": 0, "label": "spam"},
        {"sender_index": 1, "label": "promo"},
    ]
    out = _parse_classifications(r)
    assert out[0]["label"] == "spam"
    assert out[1]["label"] == "promo"


def test_dict_keyed_by_index():
    r = {"classifications": {"0": {"label": "spam"}, "1": {"label": "promo"}}}
    out = _parse_classifications(r)
    assert out[0]["label"] == "spam"
    assert out[1]["label"] == "promo"


def test_missing_sender_index_uses_position():
    r = {"classifications": [{"label": "spam"}, {"label": "promo"}]}
    out = _parse_classifications(r)
    assert out[0]["label"] == "spam"
    assert out[1]["label"] == "promo"


def test_non_dict_items_are_skipped():
    # This is the exact bug from the user's stack trace: items are ints.
    r = {"classifications": [0, 1, 2, {"sender_index": 3, "label": "spam"}]}
    out = _parse_classifications(r)
    assert out == {3: {"sender_index": 3, "label": "spam"}}


def test_empty_response():
    assert _parse_classifications({}) == {}
    assert _parse_classifications({"classifications": []}) == {}
    assert _parse_classifications(None) == {}


def test_results_key_variant():
    r = {"results": [{"sender_index": 0, "label": "spam"}]}
    out = _parse_classifications(r)
    assert out[0]["label"] == "spam"
