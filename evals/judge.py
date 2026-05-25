"""LLM-as-judge for faithfulness scoring in offline eval.

Decomposes an answer into atomic claims and decides, claim by claim, whether
each is reconstructible from the retrieved context. Runs OFFLINE during eval
only — NOT in the runtime serving path (see README §2.2: runtime guardrail
must remain single-pass).

The judge uses the same Gemini model family as the primary agent (one provider,
one API key) but runs with a different prompt and is not bound to any tools.
"""
import json
import logging
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

logger = logging.getLogger("evals.judge")

JUDGE_SYSTEM_PROMPT = """You are a strict faithfulness evaluator for a RAG system.

You receive:
  - CONTEXT: passages the RAG agent retrieved.
  - ANSWER: the response the agent produced.

Your job: decompose ANSWER into atomic factual claims, then for each claim
decide whether it is directly reconstructible from CONTEXT.

Rules:
1. Use ONLY the CONTEXT to judge support. Do NOT use general knowledge,
   even if the claim is historically true.
2. An atomic claim is one fact (one date, one event, one attribution).
   Split compound sentences into separate claims.
3. A claim is "supported" only if a specific span of CONTEXT entails it.
   Quote that span verbatim in `evidence`. If no span entails it, mark
   `supported: false` and set `evidence: ""`.
4. Generic framing sentences (e.g. "this is important", "as we will see")
   are not factual claims — skip them.

Return EXACTLY ONE JSON object, no prose, no code fences:

{
  "claims": [
    {"claim": "<atomic claim>", "supported": true|false, "evidence": "<verbatim quote from CONTEXT or ''>"}
  ]
}
"""


def _build_judge_llm():
    api_key = os.getenv("GOOGLE_API_KEY")
    model = os.getenv("JUDGE_MODEL", "gemini-2.5-flash")
    return ChatGoogleGenerativeAI(
        model=model,
        temperature=0,
        google_api_key=api_key,
    )


_JUDGE = None


def _get_judge():
    global _JUDGE
    if _JUDGE is None:
        _JUDGE = _build_judge_llm()
    return _JUDGE


def _extract_json_object(text: str) -> str | None:
    """Same balanced-brace scan used by agent/guardrail.py.

    Duplicated rather than imported to keep `evals/` decoupled from runtime
    internals — the judge is offline tooling, not part of the agent.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
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
                return text[start : i + 1]
    return None


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text", ""))
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts)
    return str(content)


def score_faithfulness(answer: str, context: str) -> dict:
    """Return {claims: [...], score: float, n_claims: int, n_supported: int}.

    `score` is supported / total claims. If the answer is empty or the judge
    returns no claims, score is None (not 0.0) so it can be distinguished
    from "judged and failed" in the aggregate.
    """
    if not answer.strip():
        return {"claims": [], "score": None, "n_claims": 0, "n_supported": 0, "error": None}
    if not context.strip():
        return {
            "claims": [],
            "score": None,
            "n_claims": 0,
            "n_supported": 0,
            "error": "empty_context",
        }

    user_prompt = f"CONTEXT:\n{context}\n\nANSWER:\n{answer}"
    response = _get_judge().invoke(
        [
            SystemMessage(content=JUDGE_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]
    )
    raw = _message_text(response.content)
    payload = _extract_json_object(raw)
    if payload is None:
        logger.warning("judge: no JSON object in response: %r", raw[:200])
        return {
            "claims": [],
            "score": None,
            "n_claims": 0,
            "n_supported": 0,
            "error": "no_json",
        }
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as e:
        logger.warning("judge: json parse error: %s", e)
        return {
            "claims": [],
            "score": None,
            "n_claims": 0,
            "n_supported": 0,
            "error": "json_parse",
        }

    claims = parsed.get("claims", [])
    if not isinstance(claims, list) or not claims:
        return {"claims": [], "score": None, "n_claims": 0, "n_supported": 0, "error": None}

    n_claims = len(claims)
    n_supported = sum(1 for c in claims if c.get("supported") is True)
    score = n_supported / n_claims
    return {
        "claims": claims,
        "score": score,
        "n_claims": n_claims,
        "n_supported": n_supported,
        "error": None,
    }
