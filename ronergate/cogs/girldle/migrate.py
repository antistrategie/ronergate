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
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in cols:
        return
    log.info("adding %s.%s column", table, column)
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
