"""Behavioral spec for `agent.guardrail.parse_and_check`.

Each test names a single branch or edge case. Read the test names as a
list to understand exactly what the guardrail blocks, what it lets
through, and what input shapes the JSON extractor tolerates.
"""
import json

import pytest

from agent.guardrail import CONFIDENCE_THRESHOLD, parse_and_check


# --- Happy path ---

def test_passes_when_well_formed_json_with_high_confidence(make_payload):
    parsed, blocked, reason = parse_and_check(make_payload())
    assert (blocked, reason) == (False, "")
    assert parsed["answer"].startswith("Pancho Villa")


# --- Pre-extraction: empty / no-JSON ---

def test_blocks_empty_string():
    parsed, blocked, reason = parse_and_check("")
    assert (parsed, blocked, reason) == ({}, True, "empty_response")


def test_blocks_whitespace_only():
    _, blocked, reason = parse_and_check("   \n\t  ")
    assert (blocked, reason) == (True, "empty_response")


def test_blocks_when_no_json_object_found():
    _, blocked, reason = parse_and_check("sorry, I cannot answer")
    assert (blocked, reason) == (True, "no_json_object_found")


# --- JSON parsing ---
# Note: `not_a_json_object` is unreachable through the public API —
# _extract_json_object only returns balanced {...} blocks, which json.loads
# always parses to a dict (or raises, falling into json_parse_error).
# Documented here for the reviewer; no test is possible without monkeypatching.

def test_blocks_on_json_parse_error():
    _, blocked, reason = parse_and_check('{"answer": "hi",,}')
    assert (blocked, reason) == (True, "json_parse_error")


# --- Schema: required keys ---

def test_blocks_when_missing_answer_key():
    _, blocked, reason = parse_and_check(
        json.dumps({"citations": [], "confidence_score": 0.9})
    )
    assert (blocked, reason) == (True, "missing_keys:answer")


def test_blocks_when_missing_citations_key():
    _, blocked, reason = parse_and_check(
        json.dumps({"answer": "x", "confidence_score": 0.9})
    )
    assert (blocked, reason) == (True, "missing_keys:citations")


def test_blocks_when_missing_confidence_score_key():
    _, blocked, reason = parse_and_check(
        json.dumps({"answer": "x", "citations": []})
    )
    assert (blocked, reason) == (True, "missing_keys:confidence_score")


def test_blocks_when_multiple_keys_missing_sorted_alphabetically():
    _, blocked, reason = parse_and_check("{}")
    assert (blocked, reason) == (
        True,
        "missing_keys:answer,citations,confidence_score",
    )


# --- Field types: confidence_score ---

@pytest.mark.parametrize(
    "bad_score",
    ["high", None, [0.9], {"v": 0.9}],
    ids=["string", "null", "list", "dict"],
)
def test_blocks_when_confidence_score_not_floatable(make_payload, bad_score):
    _, blocked, reason = parse_and_check(make_payload(confidence_score=bad_score))
    assert (blocked, reason) == (True, "invalid_confidence_score")


def test_confidence_score_is_replaced_with_float_in_parsed(make_payload):
    parsed, blocked, _ = parse_and_check(make_payload(confidence_score="0.9"))
    assert blocked is False
    assert parsed["confidence_score"] == 0.9 and isinstance(
        parsed["confidence_score"], float
    )


# --- Field types: citations ---

@pytest.mark.parametrize(
    "bad_citations",
    ["src", {"a": 1}, None, 42],
    ids=["string", "dict", "null", "int"],
)
def test_blocks_when_citations_not_a_list(make_payload, bad_citations):
    _, blocked, reason = parse_and_check(make_payload(citations=bad_citations))
    assert (blocked, reason) == (True, "citations_not_a_list")


# --- Confidence threshold ---

def test_blocks_when_confidence_below_threshold(make_payload):
    _, blocked, reason = parse_and_check(make_payload(confidence_score=0.84))
    assert (blocked, reason) == (True, "low_confidence:0.84")


def test_blocks_when_confidence_well_below_threshold(make_payload):
    _, blocked, reason = parse_and_check(make_payload(confidence_score=0.10))
    assert (blocked, reason) == (True, "low_confidence:0.10")


def test_passes_when_confidence_at_exact_threshold(make_payload):
    _, blocked, reason = parse_and_check(
        make_payload(confidence_score=CONFIDENCE_THRESHOLD)
    )
    assert (blocked, reason) == (False, "")


# --- Empty / whitespace answer ---

def test_blocks_empty_answer_with_high_confidence(make_payload):
    _, blocked, reason = parse_and_check(make_payload(answer=""))
    assert (blocked, reason) == (True, "empty_answer_with_high_confidence")


def test_blocks_whitespace_only_answer_with_high_confidence(make_payload):
    _, blocked, reason = parse_and_check(make_payload(answer="   \n  "))
    assert (blocked, reason) == (True, "empty_answer_with_high_confidence")


def test_blocks_when_answer_is_null_with_high_confidence(make_payload):
    """Regression: previously str(None) == 'None' bypassed the empty check."""
    _, blocked, reason = parse_and_check(make_payload(answer=None))
    assert (blocked, reason) == (True, "empty_answer_with_high_confidence")


# --- Extractor robustness (Gemini wrappers) ---

def test_extracts_json_when_wrapped_in_markdown_fence(make_payload):
    wrapped = f"```json\n{make_payload()}\n```"
    _, blocked, reason = parse_and_check(wrapped)
    assert (blocked, reason) == (False, "")


def test_extracts_json_when_prefixed_with_prose(make_payload):
    noisy = f"Here is the answer:\n{make_payload()}"
    _, blocked, reason = parse_and_check(noisy)
    assert (blocked, reason) == (False, "")


def test_extracts_json_when_followed_by_trailing_text(make_payload):
    noisy = f"{make_payload()}\n\n(end of response)"
    _, blocked, reason = parse_and_check(noisy)
    assert (blocked, reason) == (False, "")


def test_extracts_first_object_when_multiple_present(make_payload):
    blob = f'{make_payload()}\n{{"other": "obj"}}'
    parsed, blocked, _ = parse_and_check(blob)
    assert blocked is False and parsed["answer"].startswith("Pancho")


def test_handles_braces_inside_quoted_answer_field(make_payload):
    parsed, blocked, reason = parse_and_check(
        make_payload(answer="see {nested} mention {inside} text")
    )
    assert (blocked, reason) == (False, "")
    assert "{nested}" in parsed["answer"]


def test_handles_escaped_quotes_in_answer(make_payload):
    parsed, blocked, reason = parse_and_check(
        make_payload(answer='he said "hola"')
    )
    assert (blocked, reason) == (False, "")
    assert '"hola"' in parsed["answer"]


def test_handles_multiline_json():
    pretty = json.dumps(
        {"answer": "ok", "citations": [], "confidence_score": 0.9},
        indent=2,
    )
    _, blocked, reason = parse_and_check(pretty)
    assert (blocked, reason) == (False, "")
