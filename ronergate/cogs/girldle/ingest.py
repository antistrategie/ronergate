"""Ingest Girldle messages into the database and manage passive reactions."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable

import discord

from ...db import Database
from . import rating
from .parser import GirldleResult, parse

log = logging.getLogger(__name__)

MEDAL = "\U0001f947"   # 🥇
SKULL = "\U0001f480"   # 💀


def store_result(db: Database, message: discord.Message, result: GirldleResult) -> None:
    """Insert a parsed result. Duplicates on either UNIQUE constraint are ignored."""
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO girldle_results
                (message_id, user_id, puzzle_date, posted_at, score, grid)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(message.id),
                str(message.author.id),
                result.puzzle_date.isoformat(),
                message.created_at.isoformat(),
                result.score,
                result.grid,
            ),
        )
        conn.execute(
            """
            INSERT INTO girldle_players (user_id, display_name, last_played)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name = excluded.display_name,
                last_played = MAX(girldle_players.last_played, excluded.last_played)
            """,
            (
                str(message.author.id),
                message.author.display_name,
                result.puzzle_date.isoformat(),
            ),
        )


async def update_reactions(
    db: Database, message: discord.Message, result: GirldleResult
) -> None:
    """Apply 🥇 (current best) and 💀 (X/8) reactions for this result."""
    if result.score is None:
        await _safe_add_reaction(message, SKULL)
        return

    rows = list(
        db.conn.execute(
            """
            SELECT message_id, user_id, score FROM girldle_results
            WHERE puzzle_date = ? AND score IS NOT NULL
            ORDER BY score ASC, posted_at ASC
            """,
            (result.puzzle_date.isoformat(),),
        )
    )
    if not rows:
        return

    best_message_id = rows[0]["message_id"]
    if str(message.id) != best_message_id:
        return

    await _safe_add_reaction(message, MEDAL)
    for row in rows[1:]:
        if row["message_id"] == str(message.id):
            continue
        await _safe_remove_reaction(message.channel, int(row["message_id"]), MEDAL)


async def _safe_add_reaction(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException as e:
        log.warning("failed to add %s to %s: %s", emoji, message.id, e)


async def _safe_remove_reaction(
    channel: discord.abc.Messageable, message_id: int, emoji: str
) -> None:
    try:
        old = await channel.fetch_message(message_id)
        me = old.guild.me if old.guild else None
        if me is not None:
            await old.remove_reaction(emoji, me)
    except discord.HTTPException as e:
        log.warning("failed to remove %s from %s: %s", emoji, message_id, e)


async def handle_message(db: Database, message: discord.Message) -> bool:
    """Parse, store, and react. Returns True if the message was a Girldle result."""
    result = parse(message.content)
    if result is None:
        return False
    store_result(db, message, result)
    await update_reactions(db, message, result)
    return True


def ingest_messages(
    db: Database, messages: Iterable[tuple[discord.Message, GirldleResult]]
) -> int:
    """Bulk-store results without reactions (used by backfill). Returns count actually stored."""
    stored = 0
    with db.transaction() as conn:
        for message, result in messages:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO girldle_results
                    (message_id, user_id, puzzle_date, posted_at, score, grid)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(message.id),
                    str(message.author.id),
                    result.puzzle_date.isoformat(),
                    message.created_at.isoformat(),
                    result.score,
                    result.grid,
                ),
            )
            if cursor.rowcount == 0:
                continue
            stored += 1
            conn.execute(
                """
                INSERT INTO girldle_players (user_id, display_name, last_played)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    last_played = MAX(girldle_players.last_played, excluded.last_played)
                """,
                (
                    str(message.author.id),
                    message.author.display_name,
                    result.puzzle_date.isoformat(),
                ),
            )
    return stored


def recompute_ratings(db: Database) -> None:
    """Replay every stored result through Glicko-2 and cache the result in girldle_players."""
    rows = list(
        db.conn.execute(
            "SELECT user_id, puzzle_date, score FROM girldle_results ORDER BY puzzle_date ASC"
        )
    )
    game_results = [
        rating.GameResult(
            user_id=row["user_id"],
            puzzle_date=_parse_iso_date(row["puzzle_date"]),
            score=row["score"],
        )
        for row in rows
    ]
    ratings_by_user = rating.recompute(game_results)

    counts = _games_per_user(db.conn)
    last_played = _last_played_per_user(db.conn)

    with db.transaction() as conn:
        for user_id, r in ratings_by_user.items():
            conn.execute(
                """
                INSERT INTO girldle_players
                    (user_id, rating, rd, volatility, games_played, last_played)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    rating = excluded.rating,
                    rd = excluded.rd,
                    volatility = excluded.volatility,
                    games_played = excluded.games_played,
                    last_played = excluded.last_played
                """,
                (
                    user_id,
                    r.rating,
                    r.rd,
                    r.volatility,
                    counts.get(user_id, 0),
                    last_played.get(user_id),
                ),
            )


def _parse_iso_date(s: str):
    from datetime import date

    return date.fromisoformat(s)


def _games_per_user(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["user_id"]: row["n"]
        for row in conn.execute(
            "SELECT user_id, COUNT(*) AS n FROM girldle_results GROUP BY user_id"
        )
    }


def _last_played_per_user(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["user_id"]: row["last_played"]
        for row in conn.execute(
            "SELECT user_id, MAX(puzzle_date) AS last_played FROM girldle_results GROUP BY user_id"
        )
    }
