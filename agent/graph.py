import logging
import os
import random
from typing import Annotated, TypedDict

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from agent.guardrail import parse_and_check

logger = logging.getLogger("agent")
from agent.prompts import FALLBACK_ANSWERS, SYSTEM_PROMPT
from agent.tools import compliance_flag_tool, knowledge_retriever_tool


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    conversation_id: str
    answer: str
    citations: list[dict]
    confidence_score: float
    blocked: bool


def _build_llm():
    api_key = os.getenv("GOOGLE_API_KEY")
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0,
        google_api_key=api_key,
    )
    # Gemini rejects function calling combined with response_mime_type=application/json
    # (400 INVALID_ARGUMENT). We rely on the system prompt + temperature=0 to keep the
    # final message JSON-clean; the guardrail blocks on parse failure.
    llm = llm.bind_tools([knowledge_retriever_tool])
    return llm


def _agent_node(state: AgentState) -> dict:
    messages = state["messages"]
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
    response = _LLM.invoke(messages)
    return {"messages": [response]}


def _message_text(content) -> str:
    """Flatten an AIMessage.content into a single text string.

    Gemini sometimes returns content as a list of parts (e.g. when the model
    emits a thought_signature alongside its text reply). In that case we
    concatenate the text of every text-typed part and ignore the rest.
    """
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


def _extract_turn_context(messages: list[BaseMessage]) -> tuple[str, str]:
    """Walk messages in reverse to recover the current turn's user query and
    any tool outputs produced before the final AIMessage.
    """
    original_query = ""
    tool_blocks: list[str] = []
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            original_query = m.content if isinstance(m.content, str) else str(m.content)
            break
        if isinstance(m, ToolMessage):
            tool_blocks.append(
                m.content if isinstance(m.content, str) else str(m.content)
            )
    retrieved_context = "\n---\n".join(reversed(tool_blocks))
    return original_query, retrieved_context


def _guardrail_node(state: AgentState) -> dict:
    last = state["messages"][-1]
    # Defensive: the graph routes here only when the final AIMessage has no
    # tool_calls, but Gemini occasionally emits both content and tool_calls.
    if getattr(last, "tool_calls", None):
        return {
            "answer": random.choice(FALLBACK_ANSWERS),
            "citations": [],
            "confidence_score": 0.0,
            "blocked": True,
        }

    content = _message_text(last.content)
    logger.info("final model output (pre-guardrail):\n%s", content)
    parsed, blocked, reason = parse_and_check(content)
    logger.info("guardrail: blocked=%s reason=%r", blocked, reason)

    if blocked:
        original_query, retrieved_context = _extract_turn_context(state["messages"])
        score = float(parsed.get("confidence_score", 0.0)) if isinstance(parsed, dict) else 0.0
        compliance_flag_tool(
            conversation_id=state["conversation_id"],
            query=original_query,
            retrieved_context=retrieved_context,
            confidence_score=score,
            reason=reason,
        )
        return {
            "answer": random.choice(FALLBACK_ANSWERS),
            "citations": [],
            "confidence_score": score,
            "blocked": True,
        }

    return {
        "answer": parsed["answer"],
        "citations": parsed["citations"],
        "confidence_score": parsed["confidence_score"],
        "blocked": False,
    }


_LLM = _build_llm()


def build_graph(checkpointer: BaseCheckpointSaver) -> object:
    """Compile the agent graph against a caller-provided checkpointer.

    The checkpointer's lifecycle (open/close, setup) is owned by the caller —
    typically the FastAPI lifespan, which enters AsyncSqliteSaver.from_conn_string
    as an async context manager.
    """
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", _agent_node)
    workflow.add_node("tools", ToolNode([knowledge_retriever_tool]))
    workflow.add_node("guardrail", _guardrail_node)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", END: "guardrail"},
    )
    workflow.add_edge("tools", "agent")
    workflow.add_edge("guardrail", END)

    return workflow.compile(checkpointer=checkpointer)
