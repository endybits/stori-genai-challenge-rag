import logging

from langchain_core.tools import tool

from ingestion import get_parent_retriever
from agent.db import log_flagged_query

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


def compliance_flag_tool(
    conversation_id: str,
    query: str,
    retrieved_context: str,
    confidence_score: float,
    reason: str,
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
    )
