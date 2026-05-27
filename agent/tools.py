import logging
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ingestion import get_parent_retriever
from agent.db import get_last_flagged_query, log_flagged_query
from agent.guardrail import CONFIDENCE_THRESHOLD

logger = logging.getLogger("agent")

# WARNING: do not run uvicorn with --reload; module-load opens Chroma and
# a reload will double-open and contend on the SQLite file.
_RETRIEVER = get_parent_retriever()


def get_retriever():
    return _RETRIEVER


@tool
def knowledge_retriever_tool(query: str) -> str:
    """Search the curated Mexican Revolution corpus.

    Args:
        query: A fully self-contained natural-language search query. On
            follow-up turns, the query MUST already include any context
            previously expressed via pronouns or references.

    Returns:
        Relevant passages from the corpus, each preceded by a header of the
        form `[source=<filename>, page=<integer>]`. Returns the literal
        string "NO_RESULTS" if nothing relevant was found.
    """
    logger.info("retriever query: %r", query)
    docs = _RETRIEVER.invoke(query)
    logger.info("retriever returned %d docs", len(docs))
    if not docs:
        return "NO_RESULTS"

    blocks = []
    for d in docs:
        source = d.metadata.get("source", "unknown")
        page = d.metadata.get("page", "?")
        blocks.append(f"[source={source}, page={page}]\n{d.page_content}")
    return "\n---\n".join(blocks)


@tool
def explain_block_tool(state: Annotated[dict, InjectedState]) -> dict:
    """Explain why a previous answer in THIS conversation was blocked.

    Takes no arguments — the conversation is resolved automatically from
    graph state. Call this only when the user's current message explicitly
    asks why a previous response was blocked, why you said you couldn't
    answer, or asks for clarification about a previous refusal in this same
    conversation. Do NOT call it for greetings, capability questions,
    acknowledgments, goodbyes, or new corpus questions.

    Returns the most recent guardrail-blocked query's structured reason:
      - reason: the block category verbatim (e.g. "low_confidence:0.00",
        "json_parse_error", "no_json_object_found")
      - confidence_score: score reported by the model on the blocked turn
      - threshold: the confidence threshold in effect at read time
      - retrieved_pages: pages that were retrieved (may be empty)
      - retrieved_snippets_preview: short previews of retrieved chunks
      - blocked_at: ISO timestamp of the block
      - blocked_query: the original user query that was blocked

    If no block exists for this conversation, returns
    {"reason": "no_recent_blocks", "message": ...}.
    """
    conversation_id = state["conversation_id"]
    row = get_last_flagged_query(conversation_id)
    if row is None:
        return {
            "reason": "no_recent_blocks",
            "message": "No blocked queries found in this conversation.",
        }
    return {
        "reason": row["reason"],
        "confidence_score": row["confidence_score"],
        "threshold": CONFIDENCE_THRESHOLD,
        "retrieved_pages": row["retrieved_pages"],
        "retrieved_snippets_preview": row["retrieved_snippets_preview"],
        "blocked_at": row["blocked_at"],
        "blocked_query": row["query"],
    }


def compliance_flag_tool(
    conversation_id: str,
    query: str,
    retrieved_context: str,
    confidence_score: float,
    reason: str,
    raw_output: str = "",
) -> int:
    """Persist a blocked query to the audit log.

    Not bound to the LLM — invoked deterministically from the graph guardrail
    node so the model cannot decide whether to self-flag.
    """
    return log_flagged_query(
        conversation_id=conversation_id,
        query=query,
        retrieved_context=retrieved_context,
        confidence_score=confidence_score,
        reason=reason,
        raw_output=raw_output,
    )
