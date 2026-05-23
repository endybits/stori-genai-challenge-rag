import json

CONFIDENCE_THRESHOLD = 0.85

_REQUIRED_KEYS = {"answer", "citations", "confidence_score"}


def _extract_json_object(content: str) -> str | None:
    """Return the first balanced {...} block in `content`, or None.

    Gemini occasionally wraps the JSON in ```json fences or in a prose preface
    despite the system prompt. Scanning for a balanced object — while respecting
    string literals so braces inside `answer` don't throw off the depth counter —
    handles both cases without a regex over the full payload.
    """
    start = content.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(content)):
        ch = content[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    return None


def parse_and_check(ai_message_content: str) -> tuple[dict, bool, str]:
    """Parse the agent's final JSON payload and decide whether to block.

    Returns (parsed, blocked, reason). `parsed` is `{}` on parse failure.
    """
    if not ai_message_content or not ai_message_content.strip():
        return {}, True, "empty_response"

    raw = _extract_json_object(ai_message_content)
    if raw is None:
        return {}, True, "no_json_object_found"

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}, True, "json_parse_error"

    if not isinstance(parsed, dict):
        return {}, True, "not_a_json_object"

    missing = _REQUIRED_KEYS - parsed.keys()
    if missing:
        return parsed, True, f"missing_keys:{','.join(sorted(missing))}"

    try:
        score = float(parsed["confidence_score"])
    except (TypeError, ValueError):
        return parsed, True, "invalid_confidence_score"
    parsed["confidence_score"] = score

    if not isinstance(parsed.get("citations"), list):
        return parsed, True, "citations_not_a_list"

    if score < CONFIDENCE_THRESHOLD:
        return parsed, True, f"low_confidence:{score:.2f}"

    if not str(parsed.get("answer", "")).strip():
        return parsed, True, "empty_answer_with_high_confidence"

    return parsed, False, ""
