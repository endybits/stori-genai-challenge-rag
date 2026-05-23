import os
import sqlite3
from pathlib import Path

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
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_flagged_conv ON flagged_queries(conversation_id)"
        )


def log_flagged_query(
    conversation_id: str,
    query: str,
    retrieved_context: str,
    confidence_score: float,
    reason: str,
) -> int:
    """Insert a flagged query row, return its rowid."""
    with sqlite3.connect(COMPLIANCE_DB_PATH, check_same_thread=False) as conn:
        cur = conn.execute(
            """
            INSERT INTO flagged_queries
                (conversation_id, query, retrieved_context, confidence_score, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conversation_id, query, retrieved_context, confidence_score, reason),
        )
        return cur.lastrowid
