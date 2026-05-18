"""Idempotent schema migrations for the girldle cog. Called once at boot."""

from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger(__name__)


def migrate(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "girldle_config", "name", "TEXT")


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    coltype: str,
) -> None:
    """Race-safe: relies on SQLite's duplicate-column error rather than a TOCTOU check.

    Multiple bot processes share the same SQLite file. A PRAGMA-then-ALTER check
    isn't atomic across processes, so we just ALTER and catch the duplicate error.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        log.info("added %s.%s column", table, column)
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise
