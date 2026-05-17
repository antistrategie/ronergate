"""Grid-shape analysis for /girldle styles (snipers vs plodders)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .parser import GREEN

# Minimum solved games to appear on the styles board.
MIN_SOLVES = 3


@dataclass(frozen=True)
class PlayerGridScore:
    user_id: str
    display_name: str | None
    score: float
    solves: int


def _grid_auc(grid: str) -> float | None:
    """Normalised area under the green-count curve. None if grid is unusable."""
    rows = grid.split("\n")
    if not rows:
        return None
    width = len(rows[0])
    if width == 0:
        return None
    total_cells = width * len(rows)
    greens = sum(row.count(GREEN) for row in rows)
    return greens / total_cells


def _player_grid_scores(
    conn: sqlite3.Connection, *, ascending: bool, limit: int
) -> list[PlayerGridScore]:
    rows = list(
        conn.execute(
            """
            SELECT r.user_id, r.grid, p.display_name
            FROM girldle_results r
            LEFT JOIN girldle_players p ON p.user_id = r.user_id
            WHERE r.score IS NOT NULL
            """
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


def snipers(conn: sqlite3.Connection, limit: int = 10) -> list[PlayerGridScore]:
    """Players whose solved grids have the lowest green density (mostly red)."""
    return _player_grid_scores(conn, ascending=True, limit=limit)


def plodders(conn: sqlite3.Connection, limit: int = 10) -> list[PlayerGridScore]:
    """Players whose solved grids have the highest green density (mostly green)."""
    return _player_grid_scores(conn, ascending=False, limit=limit)
