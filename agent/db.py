import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

# Matches the per-passage header emitted by knowledge_retriever_tool, e.g.
# "[source=mexican_revolution.pdf, page=3]" — used to recover page numbers
# from the plain-text retrieved_context stored on a blocked turn.
_PAGE_HEADER_RE = re.compile(r"\[source=[^,\]]*,\s*page=(\d+)\]")

_FLAGGED_COLUMNS = (
    "conversation_id",
    "query",
    "retrieved_context",
    "confidence_score",
    "reason",
    "raw_output",
)

COMPLIANCE_DB_PATH = "./agent_db/flagged_queries.sqlite"
CHECKPOINTER_DB_PATH = "./agent_db/checkpointer.sqlite"


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def init_compliance_db() -> None:
    """Create the flagged_queries table if absent. Idempotent."""
    _ensure_parent(COMPLIANCE_DB_PATH)
    with sqlite3.connect(COMPLIANCE_DB_PATH, check_same_thread=False) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flagged_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                query TEXT NOT NULL,
                retrieved_context TEXT,
                confidence_score REAL,
                reason TEXT NOT NULL,
                raw_output TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_flagged_conv ON flagged_queries(conversation_id)"
        )
        # Backfill for DBs created before raw_output was added.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(flagged_queries)")}
        if "raw_output" not in existing:
            conn.execute("ALTER TABLE flagged_queries ADD COLUMN raw_output TEXT")


def log_flagged_query(
    conversation_id: str,
    query: str,
    retrieved_context: str,
    confidence_score: float,
    reason: str,
    raw_output: str = "",
) -> int:
    """Insert a flagged query row, return its rowid.

    `raw_output` captures the LLM's final message before parsing — essential
    for diagnosing json_parse_error and similar guardrail blocks without
    having to reproduce the conversation.
    """
    with sqlite3.connect(COMPLIANCE_DB_PATH, check_same_thread=False) as conn:
        cur = conn.execute(
            """
            INSERT INTO flagged_queries
                (conversation_id, query, retrieved_context, confidence_score, reason, raw_output)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, query, retrieved_context, confidence_score, reason, raw_output),
        )
        return cur.lastrowid


def _parse_retrieved_context(retrieved_context: Optional[str]) -> tuple[list[int], list[str]]:
    """Split a stored retrieved_context blob into pages and short previews.

    retrieved_context is stored as plain text: passage blocks joined with
    "\\n---\\n", each prefixed by a `[source=..., page=<int>]` header (see
    knowledge_retriever_tool). NULL/empty/"NO_RESULTS" all yield empty lists.
    """
    if not retrieved_context or retrieved_context.strip() in ("", "NO_RESULTS"):
        return [], []

    pages: list[int] = []
    for match in _PAGE_HEADER_RE.finditer(retrieved_context):
        page = int(match.group(1))
        if page not in pages:  # dedupe, preserve order
            pages.append(page)

    previews = [block.strip()[:200] for block in retrieved_context.split("\n---\n") if block.strip()]
    return pages, previews


def get_last_flagged_query(conversation_id: str) -> Optional[dict]:
    """Return the most recent flagged row for a conversation, or None.

    Adds two derived fields parsed from the plain-text `retrieved_context`:
    `retrieved_pages` (list[int]) and `retrieved_snippets_preview`
    (list[str], each ~200 chars). `created_at` is surfaced as `blocked_at`.
    """
    with sqlite3.connect(COMPLIANCE_DB_PATH, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT * FROM flagged_queries
            WHERE conversation_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    pages, previews = _parse_retrieved_context(result.get("retrieved_context"))
    result["retrieved_pages"] = pages
    result["retrieved_snippets_preview"] = previews
    result["blocked_at"] = result.get("created_at")
    return result
