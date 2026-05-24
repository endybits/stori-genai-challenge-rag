import os
import sqlite3
from pathlib import Path

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
