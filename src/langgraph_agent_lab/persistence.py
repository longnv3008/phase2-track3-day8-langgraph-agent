"""Checkpointer adapter supporting memory and SQLite backends."""

from __future__ import annotations

import sqlite3
from typing import Any


def build_checkpointer(kind: str = "memory", database_url: str | None = None) -> Any | None:  # noqa: ANN401
    """Return a LangGraph checkpointer for the given backend.

    Supported kinds:
    - "none"   — no persistence (useful for testing without state history)
    - "memory" — in-process MemorySaver (default, no infrastructure required)
    - "sqlite" — file-backed SQLite with WAL mode for durability
    - "postgres" — Postgres-backed (requires langgraph-checkpoint-postgres)
    """
    if kind == "none":
        return None

    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()

    if kind == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            raise RuntimeError(
                "SQLite checkpointer requires: pip install langgraph-checkpoint-sqlite"
            ) from exc
        db_path = database_url or "outputs/checkpoints.db"
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return SqliteSaver(conn=conn)

    if kind == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:
            raise RuntimeError(
                "Postgres checkpointer requires: pip install langgraph-checkpoint-postgres"
            ) from exc
        return PostgresSaver.from_conn_string(database_url or "")

    choices = "none, memory, sqlite, postgres"
    raise ValueError(f"Unknown checkpointer kind: {kind!r}. Choose from: {choices}")
