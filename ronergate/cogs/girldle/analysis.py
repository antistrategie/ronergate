"""Grid-shape analysis for /girldle styles (snipers vs plodders)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .parser import GREEN, YELLOW

# Minimum solved games to appear on the styles board.
MIN_SOLVES = 3
# Yellow squares (partial matches) count as half a green for the style metric.
YELLOW_WEIGHT = 0.5


@dataclass(frozen=True)
class PlayerGridScore:
    user_id: str
    display_name: str | None
    score: float
    solves: int


def _grid_auc(grid: str) -> float | None:
    """Normalised partial-match density across the grid. None if unusable."""
    rows = grid.split("\n")
    if not rows:
        return None
    width = len(rows[0])
    if width == 0:
        return None
    total_cells = width * len(rows)
    weighted = sum(row.count(GREEN) + YELLOW_WEIGHT * row.count(YELLOW) for row in rows)
    return weighted / total_cells


def _player_grid_scores(
    conn: sqlite3.Connection, *, ascending: bool, limit: int, guild_id: str
) -> list[PlayerGridScore]:
    rows = list(
        conn.execute(
            """
            SELECT r.user_id, r.grid, p.display_name
            FROM girldle_results r
            LEFT JOIN girldle_players p ON p.user_id = r.user_id
            WHERE r.score IS NOT NULL
              AND r.user_id IN (
                  SELECT user_id FROM girldle_posts WHERE guild_id = ?
              )
            """,
            (guild_id,),
        )
    )

    aggregated: dict[str, dict] = {}
    for row in rows:
        auc = _grid_auc(row["grid"])
        if auc is None:
            continue
        bucket = aggregated.setdefault(
            row["user_id"],
            {"display_name": row["display_name"], "total": 0.0, "count": 0},
        )
        bucket["total"] += auc
        bucket["count"] += 1

    scored = [
        PlayerGridScore(
            user_id=user_id,
            display_name=bucket["display_name"],
            score=bucket["total"] / bucket["count"],
            solves=bucket["count"],
        )
        for user_id, bucket in aggregated.items()
        if bucket["count"] >= MIN_SOLVES
    ]
    scored.sort(key=lambda s: s.score, reverse=not ascending)
    return scored[:limit]


def player_green_density(conn: sqlite3.Connection, user_id: str) -> float | None:
    """Average green-density across a player's solved grids. None if no solves.

    Matches the metric used in `/girldle styles` (yellow counted as half-green).
    """
    rows = list(
        conn.execute(
            "SELECT grid FROM girldle_results WHERE user_id = ? AND score IS NOT NULL",
            (user_id,),
        )
    )
    densities = [_grid_auc(row["grid"]) for row in rows]
    densities = [d for d in densities if d is not None]
    if not densities:
        return None
    return sum(densities) / len(densities)


def snipers(
    conn: sqlite3.Connection, *, guild_id: str, limit: int = 10
) -> list[PlayerGridScore]:
    """Players whose solved grids have the lowest green density (mostly red)."""
    return _player_grid_scores(conn, ascending=True, limit=limit, guild_id=guild_id)


def plodders(
    conn: sqlite3.Connection, *, guild_id: str, limit: int = 10
) -> list[PlayerGridScore]:
    """Players whose solved grids have the highest green density (mostly green)."""
    return _player_grid_scores(conn, ascending=False, limit=limit, guild_id=guild_id)
