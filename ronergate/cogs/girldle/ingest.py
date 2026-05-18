"""Ingest Girldle messages into the database."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

import discord
from discord.ext import commands

from ...db import Database
from . import rating
from .parser import GirldleResult, parse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoreOutcome:
    """Result of trying to store a parsed Girldle post.

    `canonical_score` is the score on the canonical result for this user+puzzle
    AFTER this store. If the post had a different score from an already-existing
    canonical row, `diverged_from` carries the canonical's score so the caller
    can warn.
    """

    stored_post: bool
    stored_canonical: bool
    canonical_score: int | None
    diverged_from: int | None


def store_result(
    db: Database, message: discord.Message, result: GirldleResult
) -> StoreOutcome:
    """Insert canonical result (first-seen wins) and a per-guild post record."""
    if message.guild is None:
        # We never ingest DMs, but guard anyway.
        return StoreOutcome(False, False, None, None)

    guild_id = str(message.guild.id)
    user_id = str(message.author.id)
    puzzle_date = result.puzzle_date.isoformat()
    posted_at = message.created_at.isoformat()

    with db.transaction() as conn:
        existing = conn.execute(
            "SELECT score FROM girldle_results WHERE user_id = ? AND puzzle_date = ?",
            (user_id, puzzle_date),
        ).fetchone()
        existing_score = existing["score"] if existing else None
        diverged_from = (
            existing_score
            if existing is not None and existing_score != result.score
            else None
        )

        canonical_cursor = conn.execute(
            """
            INSERT OR IGNORE INTO girldle_results
                (message_id, user_id, puzzle_date, posted_at, score, grid)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(message.id),
                user_id,
                puzzle_date,
                posted_at,
                result.score,
                result.grid,
            ),
        )
        stored_canonical = canonical_cursor.rowcount > 0

        post_cursor = conn.execute(
            """
            INSERT OR IGNORE INTO girldle_posts
                (message_id, guild_id, user_id, puzzle_date, posted_at, score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(message.id), guild_id, user_id, puzzle_date, posted_at, result.score),
        )
        stored_post = post_cursor.rowcount > 0

        conn.execute(
            """
            INSERT INTO girldle_players (user_id, display_name, last_played)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name = excluded.display_name,
                last_played = MAX(girldle_players.last_played, excluded.last_played)
            """,
            (user_id, message.author.display_name, puzzle_date),
        )

        canonical_score = result.score if stored_canonical else existing_score

    return StoreOutcome(
        stored_post=stored_post,
        stored_canonical=stored_canonical,
        canonical_score=canonical_score,
        diverged_from=diverged_from,
    )


async def handle_message(
    bot: commands.Bot, db: Database, message: discord.Message
) -> bool:
    """Parse, store, and warn on divergence. Returns True if it was a Girldle result."""
    result = parse(message.content)
    if result is None:
        return False
    outcome = store_result(db, message, result)
    if outcome.diverged_from is not None:
        await _report_divergence(bot, message, result, outcome.diverged_from)
    return True


async def _report_divergence(
    bot: commands.Bot,
    message: discord.Message,
    result: GirldleResult,
    canonical_score: int | None,
) -> None:
    detail = (
        f"score divergence for <@{message.author.id}> on {result.puzzle_date.isoformat()}: "
        f"canonical={_format_score(canonical_score)}, "
        f"new={_format_score(result.score)} in {message.jump_url}"
    )
    log.warning(detail)
    channel_id = bot.config.control_channel_id  # type: ignore[attr-defined]
    if channel_id is None:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        return
    try:
        await channel.send(f"⚠️ {detail}")
    except discord.HTTPException as e:
        log.warning("failed to post divergence notice: %s", e)


def _format_score(score: int | None) -> str:
    return f"{score}/8" if score is not None else "X/8"


def ingest_messages(
    db: Database, messages: Iterable[tuple[discord.Message, GirldleResult]]
) -> int:
    """Bulk-store results (used by backfill). Returns count of newly-stored canonical rows.

    Divergences are logged but not reported to the control channel (would spam on backfill).
    """
    stored = 0
    for message, result in messages:
        outcome = store_result(db, message, result)
        if outcome.diverged_from is not None:
            log.warning(
                "backfill divergence: user=%s date=%s canonical=%s new=%s",
                message.author.id,
                result.puzzle_date.isoformat(),
                outcome.diverged_from,
                result.score,
            )
        if outcome.stored_canonical:
            stored += 1
    return stored


def recompute_ratings(db: Database) -> None:
    """Replay every stored result through Glicko-2 and cache in girldle_players."""
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
